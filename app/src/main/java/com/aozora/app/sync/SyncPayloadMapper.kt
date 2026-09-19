package com.aozora.app.sync

import com.aozora.app.db.entity.PendingMutationEntity
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneOffset

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
     * 解析服务端 push 响应 JSON（`SyncService.push` 的真实契约）：
     * ```
     * { "results": [ { "mutation_id": "...", "status": "applied"|"duplicate"|"conflict",
     *                  "cursor": N, "version": N, "current_version": N }, ... ],
     *   "cursor": Long }
     * ```
     *
     * 映射到客户端 [PushResponse]：
     * - `applied` / `duplicate` → accepted（服务端已提交，本地可安全移除该 mutation）；
     *   duplicate 是重推幂等命中，同样代表"已在上行"，必须移除否则本地队列永不收敛。
     * - `conflict` → rejected（版本冲突，标记失败供上层/用户处理）。
     */
    fun parsePushResponse(json: String): PushResponse {
        val root = JSONObject(json)
        val accepted = mutableListOf<String>()
        val rejected = mutableListOf<RejectedMutation>()
        val arr = root.optJSONArray("results") ?: JSONArray()
        for (i in 0 until arr.length()) {
            val r = arr.optJSONObject(i) ?: continue
            val mutationId = r.optString("mutation_id", "")
            if (mutationId.isEmpty()) continue
            when (r.optString("status", "")) {
                "applied", "duplicate" -> accepted += mutationId
                "conflict" -> {
                    val reason = if (r.has("current_version"))
                        "version_conflict@${r.opt("current_version")}" else "version_conflict"
                    rejected += RejectedMutation(mutationId, reason)
                }
                else -> {}  // 未知 status：保守处理，既不移除也不判失败，下轮重试
            }
        }
        val nextCursor = root.optLong("cursor", 0L)
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
                // T12-7 前置修复：server MutationItem 字段名为 version（无 alias，
                // extra 键被 pydantic 忽略）——此前发 expected_version 恒 version=None，
                // 任何 update/delete push 整体 422。transaction.update 联调暴露，改齐契约。
                if (m.expectedVersion != null) put("version", m.expectedVersion)
                put("payload", toWirePayload(m.payload))
            }
            mutationsArr.put(obj)
        }
        return JSONObject().apply {
            put("mutations", mutationsArr)
            put("cursor", request.cursor)
            if (request.pendingCount != null) put("pending_count", request.pendingCount)
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
        // T-M7-01: 旧服务端无此键 → 空串，SyncEngine 回退到 legacy checksum 校验。
        val serverChecksumV2 = root.optString("checksum_v2", "")
        val cursor = root.optLong("cursor", 0L)

        val books = parseEntityListDual(root, "books")
        val categories = parseEntityListDual(root, "categories")
        val accounts = parseEntityListDual(root, "accounts")
        val transactions = parseEntityListDual(root, "transactions")
        val budgets = parseEntityListDual(root, "budgets")
        // T-M7-01: 三实体上云（旧服务端不返回这三个键 → 空列表）
        val savingsGoals = parseEntityListDual(root, "savings_goals")
        val scheduledRules = parseEntityListDual(root, "scheduled_rules")
        val chatPersonas = parseEntityListDual(root, "chat_personas")

        // P0-1: 用原始 snake_case Map 重算 checksum，与服务端算法输入一致
        val computedChecksum = BootstrapChecksum.compute(
            books = books.snake, categories = categories.snake, accounts = accounts.snake,
            transactions = transactions.snake, budgets = budgets.snake, cursor = cursor
        )
        val computedChecksumV2 = BootstrapChecksum.computeV2(
            books = books.snake, categories = categories.snake, accounts = accounts.snake,
            transactions = transactions.snake, budgets = budgets.snake,
            savingsGoals = savingsGoals.snake, scheduledRules = scheduledRules.snake,
            chatPersonas = chatPersonas.snake, cursor = cursor
        )

        return BootstrapResponse(
            books = books.camel, categories = categories.camel, accounts = accounts.camel,
            transactions = transactions.camel, budgets = budgets.camel,
            savingsGoals = savingsGoals.camel, scheduledRules = scheduledRules.camel,
            chatPersonas = chatPersonas.camel,
            cursor = cursor, checksum = serverChecksum, checksumV2 = serverChecksumV2,
            computedChecksum = computedChecksum, computedChecksumV2 = computedChecksumV2
        )
    }

    /**
     * P0-1 修复：将 JSONObject 转为 Map<String, Any?>，保留原始键名（snake_case）。
     *
     * T-M7-01 修复：**值不再预先 toString**。此前统一转成 String 后，[BootstrapChecksum]
     * 只能靠内容反推类型，字符串字段内容形如数字/布尔时（存钱目标名「2026」、备注「123」）
     * 会被当成数字/布尔出网，与服务端 json.dumps 对 str 的带引号形式不等 → bootstrap
     * checksum 恒不匹配。这里保留 JSON 原始类型（Boolean / Number / String / null），
     * 序列化方按类型出字面量。
     *
     * 用于 bootstrap checksum 重算，必须与服务端 _serialize_entity 输出的键与类型一致。
     */
    internal fun rawEntityMap(obj: JSONObject): Map<String, Any?> {
        val out = LinkedHashMap<String, Any?>(obj.length())
        val keys = obj.keys()
        while (keys.hasNext()) {
            val key = keys.next()
            val raw = obj.opt(key)
            out[key] = if (raw == null || raw == JSONObject.NULL) null else raw
        }
        return out
    }

    /** 同一实体列表的两套视图：snake_case（checksum 重算，保留 JSON 原始类型）与 camelCase（Room upsert，统一为字符串）。 */
    internal data class EntityListDual(
        val snake: List<Map<String, Any?>>,
        val camel: List<Map<String, String?>>,
    )

    private fun parseEntityListDual(root: JSONObject, key: String): EntityListDual {
        val snake = mutableListOf<Map<String, Any?>>()
        val camel = mutableListOf<Map<String, String?>>()
        val arr = root.optJSONArray(key) ?: JSONArray()
        for (i in 0 until arr.length()) {
            val obj = arr.optJSONObject(i) ?: continue
            snake += rawEntityMap(obj)
            camel += snakeToCamelMap(obj)
        }
        return EntityListDual(snake, camel)
    }

    // ─── internal ───

    private val KNOWN_ENTITY_TYPES = setOf(
        "transaction", "book", "category", "account", "budget",
        // T-M7-01: 三实体上云
        "savings_goal", "scheduled_rule", "chat_persona",
    )

    /**
     * 把服务端的 ISO 8601 时间戳（`created_at` / `updated_at`）转成 epoch 毫秒。
     *
     * 客户端 Room 的时间列统一是 epoch ms，而服务端 `_serialize_entity` 出 ISO 8601。
     * 生产（PostgreSQL，带时区）形如 `2026-01-01T00:00:00+00:00`；SQLite 测试环境
     * 可能不带偏移（`2026-01-01T00:00:00`），故两条解析路径都试，失败返回 null
     * 由调用方决定兜底。
     */
    fun isoToEpochMillis(value: String?): Long? {
        if (value.isNullOrEmpty()) return null
        return runCatching { OffsetDateTime.parse(value).toInstant().toEpochMilli() }
            .getOrNull()
            ?: runCatching {
                LocalDateTime.parse(value).toInstant(ZoneOffset.UTC).toEpochMilli()
            }.getOrNull()
    }

    /**
     * push（local→wire）方向：把客户端 Room 里以 camelCase 存储、且 datetime 为
     * epoch 毫秒的 mutation payload，转换成服务端 `_apply_insert` 期望的
     * **snake_case + ISO 8601** wire 形态。
     *
     * 契约对称性：pull 方向用 [snakeToCamelMap] 把服务端 snake→camel 落到 Room；
     * 这里 push 方向做反向 camel→snake。字段名映射直接取 [SNAKE_TO_CAMEL] 的逆，
     * 未知字段用 [camelToSnake] 启发式（与 snakeToCamel 对称），保证往返一致。
     *
     * 服务端 transaction/account/budget 的 insert 用直接下标读 snake 键
     * （如 `payload["book_id"]`），camel 键会 KeyError→422；datetime 字段
     * （occurred_at 等）服务端用 `datetime.fromisoformat`，故 epoch 毫秒必须转 ISO。
     */
    internal fun toWirePayload(camelJson: String): JSONObject {
        val src = JSONObject(camelJson)
        val out = JSONObject()
        val keys = src.keys()
        while (keys.hasNext()) {
            val key = keys.next()
            val wireKey = CAMEL_TO_SNAKE[key] ?: camelToSnake(key)
            val raw = src.get(key)
            // epoch 毫秒（Long）→ ISO 8601（UTC），仅限 datetime 语义字段
            val value = if (raw is Number && key in DATETIME_FIELDS)
                Instant.ofEpochMilli(raw.toLong()).atOffset(ZoneOffset.UTC).toString()
            else raw
            out.put(wireKey, value)
        }
        return out
    }

    /** [SNAKE_TO_CAMEL] 的逆映射（camel→snake），仅覆盖已知字段。lazy 规避 object 属性初始化顺序。 */
    private val CAMEL_TO_SNAKE: Map<String, String> by lazy {
        SNAKE_TO_CAMEL.entries.associate { (snake, camel) -> camel to snake }
    }

    /** payload 中以 epoch 毫秒承载、需转 ISO 的 datetime 字段（camel 形态键名）。 */
    private val DATETIME_FIELDS = setOf(
        "occurredAt", "createdAt", "updatedAt", "archivedAt", "deletedAt",
    )

    /** 简单驼峰转蛇形：`fooBarBaz → foo_bar_baz`。仅用于 [CAMEL_TO_SNAKE] 未覆盖的未知字段。 */
    private fun camelToSnake(s: String): String {
        val sb = StringBuilder()
        for (ch in s) {
            if (ch.isUpperCase()) {
                if (sb.isNotEmpty()) sb.append('_')
                sb.append(ch.lowercaseChar())
            } else {
                sb.append(ch)
            }
        }
        return sb.toString()
    }

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
        // T-M7-01: 三实体上云（savings_goal / scheduled_rule / chat_persona）
        "target_cents" to "targetCents",
        "current_cents" to "currentCents",
        "period_value" to "periodValue",
        "period_unit" to "periodUnit",
        "include_in_budget" to "includeInBudget",
        "start_date" to "startDate",
        "end_date" to "endDate",
        "last_run_date" to "lastRunDate",
        "run_hour" to "runHour",
        "run_minute" to "runMinute",
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
        // 三实体上云的同名透传字段（显式登记，避免依赖启发式转换）
        "repeat" to "repeat",
        "enabled" to "enabled",
        "persona" to "persona",
        "version" to "version"
    )

    /** 将 snake_case JSON 对象转为 camelCase Map<String, String?>。
     *
     * 已知字段使用 [SNAKE_TO_CAMEL] 精确映射；未知字段用启发式规则
     * `foo_bar → fooBar` 转换为 camelCase，确保未来 Room entity 新增列时
     * 键名可直接匹配（N-M3-7 修复）。
     */
    internal fun snakeToCamelMap(obj: JSONObject): Map<String, String?> {
        val out = LinkedHashMap<String, String?>(obj.length())
        val keys = obj.keys()
        while (keys.hasNext()) {
            val snakeKey = keys.next()
            val camelKey = SNAKE_TO_CAMEL[snakeKey] ?: snakeToCamel(snakeKey)
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

    /**
     * 简单蛇形转驼峰：`foo_bar_baz` → `fooBarBaz`。
     * 仅用于 [SNAKE_TO_CAMEL] 未覆盖的未知字段。
     */
    private fun snakeToCamel(s: String): String {
        val sb = StringBuilder()
        var upper = false
        for (ch in s) {
            if (ch == '_') { upper = true; continue }
            sb.append(if (upper) ch.uppercaseChar() else ch)
            upper = false
        }
        return sb.toString()
    }
}
