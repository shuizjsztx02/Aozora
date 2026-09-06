package com.aozora.app.sync

import com.aozora.app.db.entity.PendingMutationEntity
import org.json.JSONArray
import org.json.JSONObject

/**
 * 同步引擎的 payload 映射层（纯函数，JVM 可测）。
 *
 * 两个方向：
 * - **push（local→wire）**：把本地 [PendingMutationEntity] 转换成服务端接受的
 *   [MutationWire]。本地 payload 已经是 camelCase JSON，直接透传。
 * - **pull（wire→local）**：把服务端返回的 snake_case changes 转换成
 *   [ServerChange]（camelCase）。
 *
 * 设计目标：所有映射逻辑集中在一处，便于单元测试覆盖（不依赖 Android 框架）。
 */
object SyncPayloadMapper {

    /**
     * 将本地 PendingMutationEntity 转成 wire 形态。
     * 注意：operation 枚举值在服务端契约中为小写字符串。
     *
     * NEW-1 修复：本地 OP_ARCHIVE 在 wire 层映射为 "delete"，因为服务端
     * _apply_mutation 仅识别 insert/update/delete；对 account/category，
     * _apply_delete 设置 archived_at（即"归档"语义）。
     */
    fun toMutationWire(pm: PendingMutationEntity): MutationWire {
        val op = when (pm.operation) {
            PendingMutationEntity.OP_INSERT -> "insert"
            PendingMutationEntity.OP_UPDATE -> "update"
            PendingMutationEntity.OP_DELETE -> "delete"
            PendingMutationEntity.OP_ARCHIVE -> "delete"
            else -> error("unknown mutation operation: ${pm.operation}")
        }
        return MutationWire(
            mutationId = pm.mutationId,
            entityType = pm.entityType,
            entityId = pm.entityId,
            operation = op,
            expectedVersion = pm.expectedVersion,
            payload = pm.payload
        )
    }

    /**
     * 解析服务端 pull 响应 JSON：
     * ```
     * { "changes": [ { "entity_type": "...", "entity_id": "...", "operation": "...",
     *                    "version": N, "entity": { "snake_case_field": ... } } ],
     *   "cursor": Long, "has_more": Boolean }
     * ```
     *
     * - `operation` 取值：`"insert"` | `"update"` | `"delete"`。
     * - `entity` 字段从 snake_case 映射为 camelCase。
     * - 未知 entity_type 直接丢弃（向前兼容：服务端新增类型时老客户端不崩溃）。
     */
    fun parsePullResponse(json: String): PullResponse {
        val root = JSONObject(json)
        val changes = mutableListOf<ServerChange>()
        val arr = root.optJSONArray("changes") ?: JSONArray()
        for (i in 0 until arr.length()) {
            val raw = arr.optJSONObject(i) ?: continue
            val entityType = raw.optString("entity_type", "")
            if (entityType !in KNOWN_ENTITY_TYPES) continue
            val op = when (raw.optString("operation", "")) {
                "insert" -> SyncChangeOp.INSERT
                "update" -> SyncChangeOp.UPDATE
                "delete" -> SyncChangeOp.DELETE
                else -> continue
            }
            val version = raw.optInt("version", 0)
            if (version <= 0 && op != SyncChangeOp.DELETE) continue
            val entityRaw = raw.optJSONObject("entity")
            val entityMap: Map<String, String?> = if (entityRaw != null) {
                snakeToCamelMap(entityRaw)
            } else emptyMap()
            val entityId = entityMap["id"] ?: raw.optString("entity_id", "")
            if (entityId.isEmpty()) continue
            changes.add(
                ServerChange(
                    entityType = entityType,
                    entityId = entityId,
                    operation = op,
                    version = version,
                    entityPayload = entityMap
                )
            )
        }
        val nextCursor = root.optLong("cursor", 0L)
        return PullResponse(changes = changes, nextCursor = nextCursor)
    }

    /**
     * 解析服务端 push 响应 JSON：
     * ```
     * { "accepted": ["mutation_id", ...],
     *   "rejected": [ { "mutation_id": "...", "reason": "..." }, ... ],
     *   "next_cursor": Long }
     * ```
     */
    fun parsePushResponse(json: String): PushResponse {
        val root = JSONObject(json)
        val accepted = mutableListOf<String>()
        root.optJSONArray("accepted")?.let { arr ->
            for (i in 0 until arr.length()) accepted += arr.optString(i)
        }
        val rejected = mutableListOf<RejectedMutation>()
        root.optJSONArray("rejected")?.let { arr ->
            for (i in 0 until arr.length()) {
                val r = arr.optJSONObject(i) ?: continue
                rejected += RejectedMutation(
                    mutationId = r.optString("mutation_id", ""),
                    reason = r.optString("reason", "unknown")
                )
            }
        }
        val nextCursor = root.optLong("next_cursor", 0L)
        return PushResponse(
            acceptedMutationIds = accepted,
            rejected = rejected,
            nextCursor = nextCursor
        )
    }

    /** 组装 push 请求体 JSON（camelCase 字段，与服务端契约一致）。 */
    fun buildPushRequestJson(request: PushRequest): String {
        val mutationsArr = JSONArray()
        for (m in request.mutations) {
            val obj = JSONObject().apply {
                put("mutation_id", m.mutationId)
                put("entity_type", m.entityType)
                put("entity_id", m.entityId)
                put("operation", m.operation)
                if (m.expectedVersion != null) put("expected_version", m.expectedVersion)
                put("payload", JSONObject(m.payload))
            }
            mutationsArr.put(obj)
        }
        return JSONObject().apply {
            put("mutations", mutationsArr)
            put("cursor", request.cursor)
        }.toString()
    }

    /** 组装 pull 请求体 JSON。 */
    fun buildPullRequestJson(request: PullRequest): String {
        return JSONObject().apply {
            put("cursor", request.cursor)
            put("limit", request.limit)
        }.toString()
    }

    /**
     * 解析服务端 bootstrap 响应 JSON：
     * ```
     * { "books": [...], "categories": [...], "accounts": [...],
     *   "transactions": [...], "budgets": [...],
     *   "cursor": Long, "checksum": "sha256hex" }
     * ```
     *
     * T-M3-03: 每个 entity 从 snake_case 映射为 camelCase Map。
     *
     * P0-1 修复：每个 entity 同时解析两遍 —
     * - 原始 snake_case Map 用于 [BootstrapResponse.computedChecksum] 重算
     *   （服务端用 snake_case 计算 checksum，见 service.py:compute_bootstrap_checksum）；
     * - camelCase Map 用于 Room upsert（与其他 pull 路径一致）。
     * 此前只对 camelCase Map 重算 checksum，因键名不同（created_at vs createdAt 等）
     * 与服务端哈希永远不一致，任何含真实数据的 bootstrap 都会死循环 RETRYABLE。
     */
    fun parseBootstrapResponse(json: String): BootstrapResponse {
        val root = JSONObject(json)
        val serverChecksum = root.optString("checksum", "")
        val cursor = root.optLong("cursor", 0L)

        val booksSnake = mutableListOf<Map<String, String?>>()
        val booksCamel = mutableListOf<Map<String, String?>>()
        val catsSnake = mutableListOf<Map<String, String?>>()
        val catsCamel = mutableListOf<Map<String, String?>>()
        val accsSnake = mutableListOf<Map<String, String?>>()
        val accsCamel = mutableListOf<Map<String, String?>>()
        val txsSnake = mutableListOf<Map<String, String?>>()
        val txsCamel = mutableListOf<Map<String, String?>>()
        val budsSnake = mutableListOf<Map<String, String?>>()
        val budsCamel = mutableListOf<Map<String, String?>>()

        parseEntityListDual(root, "books", booksSnake, booksCamel)
        parseEntityListDual(root, "categories", catsSnake, catsCamel)
        parseEntityListDual(root, "accounts", accsSnake, accsCamel)
        parseEntityListDual(root, "transactions", txsSnake, txsCamel)
        parseEntityListDual(root, "budgets", budsSnake, budsCamel)

        // P0-1: 用原始 snake_case Map 重算 checksum，与服务端算法输入一致
        val computedChecksum = BootstrapChecksum.compute(
            books = booksSnake, categories = catsSnake, accounts = accsSnake,
            transactions = txsSnake, budgets = budsSnake, cursor = cursor
        )

        return BootstrapResponse(
            books = booksCamel, categories = catsCamel, accounts = accsCamel,
            transactions = txsCamel, budgets = budsCamel,
            cursor = cursor, checksum = serverChecksum,
            computedChecksum = computedChecksum
        )
    }

    /**
     * P0-1 修复：将 JSONObject 转为 Map<String, String?>，保留原始键名（snake_case）。
     * 值处理规则与 [snakeToCamelMap] 一致（null → null，Number/Boolean → toString，其他 → toString）。
     * 用于 bootstrap checksum 重算，必须与服务端 _serialize_entity 输出的键一致。
     */
    internal fun rawEntityMap(obj: JSONObject): Map<String, String?> {
        val out = LinkedHashMap<String, String?>(obj.length())
        val keys = obj.keys()
        while (keys.hasNext()) {
            val key = keys.next()
            val raw = obj.opt(key)
            out[key] = when {
                raw == null || raw == JSONObject.NULL -> null
                raw is Boolean -> raw.toString()
                raw is Number -> raw.toString()
                else -> raw.toString()
            }
        }
        return out
    }

    private fun parseEntityListDual(
        root: JSONObject, key: String,
        snakeOut: MutableList<Map<String, String?>>,
        camelOut: MutableList<Map<String, String?>>
    ) {
        val arr = root.optJSONArray(key) ?: JSONArray()
        for (i in 0 until arr.length()) {
            val obj = arr.optJSONObject(i) ?: continue
            snakeOut += rawEntityMap(obj)
            camelOut += snakeToCamelMap(obj)
        }
    }

    // ─── internal ───

    private val KNOWN_ENTITY_TYPES = setOf("transaction", "book", "category", "account", "budget")

    private val SNAKE_TO_CAMEL = mapOf(
        "id" to "id",
        "book_id" to "bookId",
        "owner_id" to "ownerId",
        "category_id" to "categoryId",
        "account_id" to "accountId",
        "parent_id" to "parentId",
        "amount_cents" to "amountCents",
        "balance_cents" to "balanceCents",
        "biz_date" to "bizDate",
        "occurred_at" to "occurredAt",
        "created_at" to "createdAt",
        "updated_at" to "updatedAt",  // P0-2 修复：此前漏配导致 mixed snake/camel 风格
        "archived_at" to "archivedAt",
        "deleted_at" to "deletedAt",
        "sort_index" to "sortIndex",
        "imported_from" to "importedFrom",
        "budget_type" to "budgetType",
        // snake_case 同名直接透传字段
        "name" to "name",
        "icon" to "icon",
        "type" to "type",
        "kind" to "kind",
        "note" to "note",
        "source" to "source",
        "period" to "period",
        "currency" to "currency",
        "color" to "color",
        "version" to "version"
    )

    /** 将 snake_case JSON 对象转为 camelCase Map<String, String?>。 */
    internal fun snakeToCamelMap(obj: JSONObject): Map<String, String?> {
        val out = LinkedHashMap<String, String?>(obj.length())
        val keys = obj.keys()
        while (keys.hasNext()) {
            val snakeKey = keys.next()
            val camelKey = SNAKE_TO_CAMEL[snakeKey] ?: snakeKey
            val raw = obj.opt(snakeKey)
            out[camelKey] = when {
                raw == null || raw == JSONObject.NULL -> null
                raw is Boolean -> raw.toString()
                raw is Number -> raw.toString()
                else -> raw.toString()
            }
        }
        return out
    }
}
