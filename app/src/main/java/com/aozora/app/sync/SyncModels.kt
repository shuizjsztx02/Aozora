package com.aozora.app.sync

import com.aozora.app.db.entity.*

/** 同步变更操作类型（服务端 wire 语义）。 */
enum class SyncChangeOp { INSERT, UPDATE, DELETE }

/**
 * 服务端 change 事件，对应 wire 协议 `changes[].entity` + 外层元数据。
 * 由 SyncPayloadMapper 从 HTTP 响应映射而来，交给 Repository.applyServerChanges。
 *
 * 注意：服务端返回 snake_case，本类为 JVM 内部传输结构，使用 camelCase。
 * `entityPayload` 已经是 camelCase 字段的 Map（snake→camel 在 mapper 中完成）。
 */
data class ServerChange(
    val entityType: String,
    val entityId: String,
    val operation: SyncChangeOp,
    val version: Int,
    /** camelCase 映射后的字段集合。 */
    val entityPayload: Map<String, String?>
) {
    fun toTransaction(): TransactionEntity? {
        if (entityType != "transaction") return null
        val id = entityPayload["id"] ?: return null
        val bookId = entityPayload["bookId"] ?: return null
        val categoryId = entityPayload["categoryId"] ?: return null
        val accountId = entityPayload["accountId"]
        val amountCents = entityPayload["amountCents"]?.toLongOrNull() ?: return null
        val type = entityPayload["type"] ?: return null
        val bizDate = entityPayload["bizDate"] ?: return null
        val occurredAt = entityPayload["occurredAt"]?.toLongOrNull() ?: return null
        val note = entityPayload["note"] ?: ""
        val source = entityPayload["source"] ?: "manual"
        // 服务端 transaction 无 includeInBudget 列（决策 #1）→ 缺省 true（计入）；
        // 实际本地值由 Repository.applyServerChanges 在 upsert 前防冲刷保留。
        val includeInBudget = entityPayload["includeInBudget"]?.toBoolean() ?: true
        return TransactionEntity(
            id = id, bookId = bookId, categoryId = categoryId, accountId = accountId,
            amountCents = amountCents, type = type, bizDate = bizDate,
            occurredAt = occurredAt, note = note, source = source, version = version,
            includeInBudget = includeInBudget,
            deletedAt = null
        )
    }

    fun toBook(ownerId: String): BookEntity? {
        if (entityType != "book") return null
        val id = entityPayload["id"] ?: return null
        val name = entityPayload["name"] ?: return null
        val icon = entityPayload["icon"] ?: "default"
        return BookEntity(
            id = id, ownerId = ownerId, name = name, icon = icon,
            version = version, archivedAt = null
        )
    }

    fun toCategory(ownerId: String): CategoryEntity? {
        if (entityType != "category") return null
        val id = entityPayload["id"] ?: return null
        val name = entityPayload["name"] ?: return null
        val kind = entityPayload["kind"] ?: return null
        val bookId = entityPayload["bookId"]
        val parentId = entityPayload["parentId"]
        return CategoryEntity(
            id = id, ownerId = ownerId, bookId = bookId, parentId = parentId,
            kind = kind, name = name, archivedAt = null, version = version
        )
    }

    fun toAccount(ownerId: String): AccountEntity? {
        if (entityType != "account") return null
        val id = entityPayload["id"] ?: return null
        val name = entityPayload["name"] ?: return null
        val icon = entityPayload["icon"] ?: "default"
        return AccountEntity(
            id = id, ownerId = ownerId, name = name, icon = icon,
            accountType = entityPayload["accountType"],
            archivedAt = null, version = version
        )
    }

    fun toBudget(): BudgetEntity? {
        if (entityType != "budget") return null
        val id = entityPayload["id"] ?: return null
        val bookId = entityPayload["bookId"] ?: return null
        val categoryId = entityPayload["categoryId"]
        val budgetType = entityPayload["budgetType"] ?: return null
        val period = entityPayload["period"] ?: return null
        val amountCents = entityPayload["amountCents"]?.toLongOrNull() ?: return null
        return BudgetEntity(
            id = id, bookId = bookId, categoryId = categoryId, budgetType = budgetType,
            period = period, amountCents = amountCents, version = version
        )
    }
}

/** 同步引擎状态（前端展示 + 桥接事件）。 */
enum class SyncState {
    /** 无同步活动。 */
    IDLE,
    /** 推送中。 */
    PUSHING,
    /** 拉取中。 */
    PULLING,
    /** P2-3: 全量重建中（bootstrap）。前端可展示"重建中"以区分常规拉取。 */
    REBUILDING,
    /** 认证丢失（需要用户重新登录 / 重新匿名注册）。 */
    AUTH_LOST,
    /** 网络错误 / 服务端错误。 */
    ERROR
}

/**
 * 同步引擎通过 Flow 向外发布的状态事件。
 *
 * @property failedMutations 本轮同步中被服务端拒绝的 mutation 列表（终态，不再自动重试）。
 *   前端可据此展示"X 条变更冲突，待处理"提示。详见 N-M3-5。
 */
data class SyncStatusEvent(
    val state: SyncState,
    /** 队列中待推送 mutation 数（最近一次快照）。 */
    val pendingCount: Int,
    /** 最近一次同步的毫秒时间戳（epoch）；从未成功过为 0。 */
    val lastSyncedAt: Long,
    /** 错误态下的最近错误信息；其它态为空。 */
    val lastError: String? = null,
    /** 终态失败的 mutation 列表（被服务端 rejected 且不再自动重试）。 */
    val failedMutations: List<FailedMutation> = emptyList()
)

/**
 * 被服务端拒绝的 mutation — 终态，不再自动重试。
 *
 * 由 SyncEngine.pushAllBatches 在收到 push 响应 rejected 列表时通过
 * Repository.listFailedMutations() 查询得到，随 [SyncStatusEvent] 发布。
 *
 * @property mutationId 本地 mutation 唯一 ID。
 * @property entityType 实体类型（transaction/book/category/account/budget）。
 * @property entityId 目标实体 ID。
 * @property operation 操作类型（insert/update/delete/archive）。
 * @property reason 服务端拒绝原因（expected_version_mismatch/not_found/forbidden/invalid_payload）。
 */
data class FailedMutation(
    val mutationId: String,
    val entityType: String,
    val entityId: String,
    val operation: String,
    val reason: String?
)
