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
        // T18-2：服务端 `occurred_at` 出网是 **ISO 8601**（`_serialize_entity`），Room 存
        // epoch 毫秒——只认 Long 会把每一条服务端下行的交易**静默丢光**（调用方 `?: continue`）。
        // 双收：epoch（旧 fixture / 旧服务端）与 ISO（当前服务端）都接。
        val occurredAt = entityPayload["occurredAt"]?.toLongOrNull()
            ?: SyncPayloadMapper.isoToEpochMillis(entityPayload["occurredAt"])
            ?: return null
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
            // REQ-69（T18-5）：服务端 account_type / balance_cents 两端化后的落库读口。
            // balanceCents 缺键（旧服务端）按 0 回落；accountType 的防冲刷在 Repository。
            balanceCents = entityPayload["balanceCents"]?.toLongOrNull() ?: 0L,
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

    // ─── T-M7-01: 三实体上云 ───

    /**
     * 服务端的 created_at/updated_at 是 ISO 8601，本地列是 epoch 毫秒。
     * 服务端恒返回二者，解析失败（格式意外）才回退当前时刻，避免整条被丢弃。
     */
    private fun serverTime(key: String): Long =
        SyncPayloadMapper.isoToEpochMillis(entityPayload[key]) ?: System.currentTimeMillis()

    /**
     * 归档时间戳同样从 payload 读取（ISO → epoch 毫秒），**不硬编码 null**。
     *
     * 既有五实体的 pull 路径恒置 null 是因为它们的归档只经 `applyServerArchive`
     * 分支落库（服务端 delete → 客户端归档）；三实体上云后，服务端对"重复上行
     * 已存在实体"的幂等命中会记一条 **update** 变更（见 sync/service.py），此时
     * 已归档行会出现在非 delete 变更里 —— 硬编码 null 会把它复活。活跃行的
     * archived_at 恒为 null，行为不变。
     */
    private fun archivedAt(): Long? =
        SyncPayloadMapper.isoToEpochMillis(entityPayload["archivedAt"])

    fun toSavingsGoal(ownerId: String): SavingsGoalEntity? {
        if (entityType != "savings_goal") return null
        val id = entityPayload["id"] ?: return null
        val name = entityPayload["name"] ?: return null
        val targetCents = entityPayload["targetCents"]?.toLongOrNull() ?: return null
        return SavingsGoalEntity(
            id = id, ownerId = ownerId, name = name, targetCents = targetCents,
            currentCents = entityPayload["currentCents"]?.toLongOrNull() ?: 0L,
            icon = entityPayload["icon"],
            periodValue = entityPayload["periodValue"]?.toIntOrNull(),
            periodUnit = entityPayload["periodUnit"],
            createdAt = serverTime("createdAt"), updatedAt = serverTime("updatedAt"),
            archivedAt = archivedAt(), version = version
        )
    }

    fun toScheduledRule(ownerId: String): ScheduledRuleEntity? {
        if (entityType != "scheduled_rule") return null
        val id = entityPayload["id"] ?: return null
        val name = entityPayload["name"] ?: return null
        val type = entityPayload["type"] ?: return null
        val amountCents = entityPayload["amountCents"]?.toLongOrNull() ?: return null
        val bookId = entityPayload["bookId"] ?: return null
        val categoryId = entityPayload["categoryId"] ?: return null
        val repeat = entityPayload["repeat"] ?: return null
        val startDate = entityPayload["startDate"] ?: return null
        return ScheduledRuleEntity(
            id = id, ownerId = ownerId, type = type, name = name,
            amountCents = amountCents, bookId = bookId, categoryId = categoryId,
            accountId = entityPayload["accountId"],
            includeInBudget = entityPayload["includeInBudget"]?.toBoolean() ?: true,
            note = entityPayload["note"] ?: "",
            repeat = repeat, startDate = startDate, endDate = entityPayload["endDate"],
            enabled = entityPayload["enabled"]?.toBoolean() ?: true,
            lastRunDate = entityPayload["lastRunDate"],
            runHour = entityPayload["runHour"]?.toIntOrNull() ?: 8,
            runMinute = entityPayload["runMinute"]?.toIntOrNull() ?: 0,
            createdAt = serverTime("createdAt"), updatedAt = serverTime("updatedAt"),
            archivedAt = archivedAt(), version = version
        )
    }

    fun toChatPersona(): ChatPersonaEntity? {
        if (entityType != "chat_persona") return null
        val id = entityPayload["id"] ?: return null
        val name = entityPayload["name"] ?: return null
        return ChatPersonaEntity(
            id = id, name = name,
            avatar = entityPayload["avatar"],
            persona = entityPayload["persona"] ?: "",
            createdAt = serverTime("createdAt"), updatedAt = serverTime("updatedAt"),
            archivedAt = archivedAt(), version = version
        )
    }

    // ─── T-M7-05j: 偏好项上云 ───

    /**
     * 偏好行没有 archived_at/deleted_at，也没有时间戳列 —— 就是 (id, key, value, version)。
     *
     * 这里**不**按白名单过滤：服务端只发 SYNCABLE_KEYS 内的键，客户端再挡一次只会
     * 把"未知键"变成 bootstrap 计数失配（RETRYABLE 死循环）。白名单口径统一放在
     * 上行入队与 sync_meta 回灌两处（Repository）。
     */
    fun toUserPreference(): UserPreferenceEntity? {
        if (entityType != "user_preference") return null
        val id = entityPayload["id"] ?: return null
        val key = entityPayload["key"] ?: return null
        return UserPreferenceEntity(
            id = id, key = key,
            value = entityPayload["value"] ?: "",
            version = version
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
