package com.aozora.app.db

import androidx.room.withTransaction
import com.aozora.app.db.entity.*
import com.aozora.app.sync.FailedMutation
import com.aozora.app.sync.ServerChange
import com.aozora.app.sync.SyncChangeOp
import org.json.JSONObject
import java.time.LocalDate

/**
 * Repository — 统一数据访问层。
 *
 * 关键约束：
 * - 所有写操作与 pending mutation 在同一 Room 事务内完成（§5.3）
 * - 不生成 AI 业务结论（§4.3 Kotlin platform 职责边界）
 * - 金额字段始终为 Long（整数分），禁止浮点
 * - T-M3-02：update/delete/archive 入队时捕获 expectedVersion（变更**前**
 *   版本号），推送时作为服务端期望版本；insert 时 expectedVersion = null。
 * - T-M3-02：server-merge 路径（applyServerChanges）仅由同步引擎使用，
 *   只更新实体与 cursor，不入队新 mutation，且跳过仍有活跃 mutation 的实体。
 */
class Repository(private val db: AozoraDatabase) {

    // ─── Transaction ───

    /** 原子事务：插入流水 + 写入 pending mutation */
    suspend fun insertTransaction(tx: TransactionEntity, mutationId: String) {
        require(mutationId.isNotBlank()) { "mutationId is required" }
        require(tx.id.isNotBlank() && tx.bookId.isNotBlank() && tx.categoryId.isNotBlank()) { "entity IDs are required" }
        require(tx.amountCents in 1..9007199254740991L) { "amountCents must be a positive safe integer" }
        require(tx.type in setOf("income", "expense")) { "invalid transaction type" }
        require(tx.source in setOf("manual", "nlu_local", "nlu_ai", "import")) { "invalid source" }
        require(tx.version > 0 && tx.deletedAt == null) { "invalid insert version or deletion state" }
        require(Regex("\\d{4}-\\d{2}-\\d{2}").matches(tx.bizDate)) { "invalid bizDate" }
        require(runCatching { LocalDate.parse(tx.bizDate) }.isSuccess) { "invalid bizDate" }
        db.withTransaction {
            require(db.bookDao().getById(tx.bookId) != null) { "book not found: ${tx.bookId}" }
            require(db.categoryDao().getById(tx.categoryId) != null) { "category not found: ${tx.categoryId}" }
            if (tx.accountId != null) {
                require(db.accountDao().getById(tx.accountId) != null) { "account not found: ${tx.accountId}" }
            }
            db.transactionDao().insert(tx)
            db.pendingMutationDao().insert(
                PendingMutationEntity(
                    mutationId = mutationId,
                    entityType = "transaction",
                    entityId = tx.id,
                    operation = PendingMutationEntity.OP_INSERT,
                    payload = transactionToJson(tx),
                    status = PendingMutationEntity.STATUS_PENDING,
                    retryCount = 0,
                    createdAt = System.currentTimeMillis(),
                    expectedVersion = null,
                    lastError = null
                )
            )
        }
    }

    /** 原子事务：软删除流水 + 写入 pending mutation */
    suspend fun softDeleteTransaction(id: String, mutationId: String) {
        require(mutationId.isNotBlank()) { "mutationId is required" }
        db.withTransaction {
            val current = db.transactionDao().getById(id)
            require(current != null && current.deletedAt == null) { "transaction not found or already deleted" }
            db.transactionDao().softDelete(id, System.currentTimeMillis())
            db.pendingMutationDao().insert(
                PendingMutationEntity(
                    mutationId = mutationId,
                    entityType = "transaction",
                    entityId = id,
                    operation = PendingMutationEntity.OP_DELETE,
                    payload = JSONObject().put("id", id).toString(),
                    status = PendingMutationEntity.STATUS_PENDING,
                    retryCount = 0,
                    createdAt = System.currentTimeMillis(),
                    expectedVersion = current.version,
                    lastError = null
                )
            )
        }
    }

    suspend fun getTransaction(id: String): TransactionEntity? =
        db.transactionDao().getById(id)

    suspend fun listTransactionsByBook(bookId: String): List<TransactionEntity> =
        db.transactionDao().listByBook(bookId)

    suspend fun listTransactionsByDateRange(start: String, end: String): List<TransactionEntity> =
        db.transactionDao().listByDateRange(start, end)

    // R-M4-29 修复：按账本 + 日期范围查询，与首页聚合口径一致
    suspend fun listTransactionsByBookAndDateRange(bookId: String, start: String, end: String): List<TransactionEntity> =
        db.transactionDao().listByBookAndDateRange(bookId, start, end)

    // ─── Book ───

    suspend fun insertBook(book: BookEntity, mutationId: String) {
        require(mutationId.isNotBlank() && book.id.isNotBlank() && book.ownerId.isNotBlank()) { "IDs are required" }
        require(book.version > 0 && book.archivedAt == null) { "invalid book insert" }
        db.withTransaction {
            db.bookDao().insert(book)
            db.pendingMutationDao().insert(
                PendingMutationEntity(
                    mutationId = mutationId,
                    entityType = "book",
                    entityId = book.id,
                    operation = PendingMutationEntity.OP_INSERT,
                    payload = bookToJson(book),
                    status = PendingMutationEntity.STATUS_PENDING,
                    retryCount = 0,
                    createdAt = System.currentTimeMillis(),
                    expectedVersion = null,
                    lastError = null
                )
            )
        }
    }

    suspend fun getBook(id: String): BookEntity? = db.bookDao().getById(id)
    suspend fun listBooks(): List<BookEntity> = db.bookDao().listActive()

    /** T7-1（REQ-5）：总账本 book id；未播种时为 null。 */
    suspend fun getMasterBookId(): String? =
        db.syncMetaDao().getValue(SyncMetaEntity.KEY_MASTER_BOOK_ID)

    private suspend fun setMasterBookId(id: String) {
        db.syncMetaDao().upsert(
            SyncMetaEntity(SyncMetaEntity.KEY_MASTER_BOOK_ID, id)
        )
    }

    /**
     * T7-1（决策 #2）：记账目标账本 = 活跃账本中第一个非 master 的 book
     * （listBooks 按 name 排序）；全部为 master 时回退第一个；无账本时 null。
     */
    suspend fun getEntryBook(): BookEntity? {
        val books = listBooks()
        if (books.isEmpty()) return null
        val masterId = getMasterBookId()
        return books.firstOrNull { it.id != masterId } ?: books.first()
    }

    // ─── 首启种子（第六轮 Bug-5.1；T7-1 扩展为双账本） ───

    /**
     * 幂等补种：默认分类 + 默认账本 + 总账本（REQ-5 决策 #1，三段各自独立判断）。
     *
     * 背景：此前默认数据完全依赖"服务端 guest 注册 → sync bootstrap"拉取；
     * 真机连不通服务端时分类九宫格为空，手动记账与聊天记账都无法完成。
     * 三段（同一事务内）：
     * 1. 分类表为空 → 种子分类
     * 2. 账本表为空 → 种"默认账本"（记账目标）
     * 3. master_book_id 未设 → 种"总账本"（📊）并写键；
     *    键存在但指向的 book 缺失/已归档 → 清键后重走补种
     *    （同事务内以新 id 覆写键，等效"清除 + 重写"，无中间态）
     * 约束：
     * - id / mutationId 必须为 UUID（服务端 sync/ai 契约，server/src/modules/sync/schemas.py）
     * - insert 同步产生 pending mutation，联网后 push 给服务端
     * - 幂等：任一段的既有数据非空即跳过，绝不覆盖用户已有数据
     */
    suspend fun ensureSeedDefaults() {
        db.withTransaction {
            // 段 1：分类
            if (db.categoryDao().listActive().isEmpty()) {
                DEFAULT_EXPENSE_CATEGORIES.forEach { seedCategory(it, "expense") }
                DEFAULT_INCOME_CATEGORIES.forEach { seedCategory(it, "income") }
            }
            // 段 2：默认账本
            if (db.bookDao().listActive().isEmpty()) {
                val book = BookEntity(
                    id = java.util.UUID.randomUUID().toString(),
                    ownerId = "local",
                    name = "默认账本",
                    icon = "📒",
                    version = 1,
                    archivedAt = null
                )
                db.bookDao().insert(book)
                enqueueInsert("book", book.id, bookToJson(book))
            }
            // 段 3：总账本 + master_book_id 键
            val masterId = db.syncMetaDao().getValue(SyncMetaEntity.KEY_MASTER_BOOK_ID)
            val masterBook = masterId?.let { db.bookDao().getById(it) }
            if (masterBook == null || masterBook.archivedAt != null) {
                val master = BookEntity(
                    id = java.util.UUID.randomUUID().toString(),
                    ownerId = "local",
                    name = "总账本",
                    icon = "📊",
                    version = 1,
                    archivedAt = null
                )
                db.bookDao().insert(master)
                enqueueInsert("book", master.id, bookToJson(master))
                setMasterBookId(master.id)
            }
        }
    }

    private suspend fun seedCategory(name: String, kind: String) {
        val cat = CategoryEntity(
            id = java.util.UUID.randomUUID().toString(),
            ownerId = "local",
            bookId = null,
            parentId = null,
            kind = kind,
            name = name,
            archivedAt = null,
            version = 1
        )
        db.categoryDao().insert(cat)
        enqueueInsert("category", cat.id, categoryToJson(cat))
    }

    private suspend fun enqueueInsert(entityType: String, entityId: String, payload: String) {
        db.pendingMutationDao().insert(
            PendingMutationEntity(
                mutationId = java.util.UUID.randomUUID().toString(),
                entityType = entityType,
                entityId = entityId,
                operation = PendingMutationEntity.OP_INSERT,
                payload = payload,
                status = PendingMutationEntity.STATUS_PENDING,
                retryCount = 0,
                createdAt = System.currentTimeMillis(),
                expectedVersion = null,
                lastError = null
            )
        )
    }

    // ─── Category ───

    suspend fun insertCategory(category: CategoryEntity, mutationId: String) {
        require(mutationId.isNotBlank() && category.id.isNotBlank()) { "IDs are required" }
        require(category.kind in setOf("expense", "income") && category.version > 0) { "invalid category" }
        db.withTransaction {
            db.categoryDao().insert(category)
            db.pendingMutationDao().insert(
                PendingMutationEntity(
                    mutationId = mutationId,
                    entityType = "category",
                    entityId = category.id,
                    operation = PendingMutationEntity.OP_INSERT,
                    payload = categoryToJson(category),
                    status = PendingMutationEntity.STATUS_PENDING,
                    retryCount = 0,
                    createdAt = System.currentTimeMillis(),
                    expectedVersion = null,
                    lastError = null
                )
            )
        }
    }

    suspend fun listCategories(): List<CategoryEntity> = db.categoryDao().listActive()

    // ─── Account ───

    /** 原子事务：插入账户 + 写入 pending mutation */
    suspend fun insertAccount(account: AccountEntity, mutationId: String) {
        require(mutationId.isNotBlank()) { "mutationId is required" }
        require(account.id.isNotBlank() && account.ownerId.isNotBlank()) { "entity IDs are required" }
        require(account.name.isNotBlank()) { "name is required" }
        require(account.version > 0 && account.archivedAt == null) { "invalid account insert" }
        db.withTransaction {
            db.accountDao().insert(account)
            db.pendingMutationDao().insert(
                PendingMutationEntity(
                    mutationId = mutationId,
                    entityType = "account",
                    entityId = account.id,
                    operation = PendingMutationEntity.OP_INSERT,
                    payload = accountToJson(account),
                    status = PendingMutationEntity.STATUS_PENDING,
                    retryCount = 0,
                    createdAt = System.currentTimeMillis(),
                    expectedVersion = null,
                    lastError = null
                )
            )
        }
    }

    /** T7-1（REQ-6）：原子事务：更新账户 + 写入 pending mutation（仿 updateBudget） */
    suspend fun updateAccount(account: AccountEntity, mutationId: String) {
        require(mutationId.isNotBlank()) { "mutationId is required" }
        require(account.id.isNotBlank()) { "entity ID is required" }
        require(account.name.isNotBlank()) { "name is required" }
        require(account.version > 0) { "invalid version" }
        db.withTransaction {
            val current = db.accountDao().getById(account.id)
            require(current != null) { "account not found" }
            require(current.archivedAt == null) { "account is archived" }
            require(account.version > current.version) { "version must increase" }
            db.accountDao().update(account)
            db.pendingMutationDao().insert(
                PendingMutationEntity(
                    mutationId = mutationId,
                    entityType = "account",
                    entityId = account.id,
                    operation = PendingMutationEntity.OP_UPDATE,
                    payload = accountToJson(account),
                    status = PendingMutationEntity.STATUS_PENDING,
                    retryCount = 0,
                    createdAt = System.currentTimeMillis(),
                    expectedVersion = current.version,
                    lastError = null
                )
            )
        }
    }

    /** 原子事务：归档账户 + 写入 pending mutation */
    suspend fun archiveAccount(id: String, mutationId: String) {
        require(mutationId.isNotBlank()) { "mutationId is required" }
        db.withTransaction {
            val current = db.accountDao().getById(id)
            require(current != null && current.archivedAt == null) { "account not found or already archived" }
            db.accountDao().archive(id, System.currentTimeMillis())
            db.pendingMutationDao().insert(
                PendingMutationEntity(
                    mutationId = mutationId,
                    entityType = "account",
                    entityId = id,
                    operation = PendingMutationEntity.OP_ARCHIVE,
                    payload = JSONObject().put("id", id).toString(),
                    status = PendingMutationEntity.STATUS_PENDING,
                    retryCount = 0,
                    createdAt = System.currentTimeMillis(),
                    expectedVersion = current.version,
                    lastError = null
                )
            )
        }
    }

    suspend fun getAccount(id: String): AccountEntity? = db.accountDao().getById(id)
    suspend fun listAccounts(): List<AccountEntity> = db.accountDao().listActive()

    // ─── Budget ───

    /** 原子事务：插入预算 + 写入 pending mutation */
    suspend fun insertBudget(budget: BudgetEntity, mutationId: String) {
        require(mutationId.isNotBlank()) { "mutationId is required" }
        require(budget.id.isNotBlank() && budget.bookId.isNotBlank()) { "entity IDs are required" }
        require(budget.budgetType in setOf("monthly", "annual")) { "invalid budgetType" }
        require(budget.amountCents in 1..9007199254740991L) { "amountCents must be a positive safe integer" }
        require(budget.version > 0) { "invalid version" }
        require(when (budget.budgetType) {
            "monthly" -> Regex("\\d{4}-\\d{2}").matches(budget.period)
            "annual" -> Regex("\\d{4}").matches(budget.period)
            else -> false
        }) { "invalid period format" }
        db.withTransaction {
            require(db.bookDao().getById(budget.bookId) != null) { "book not found: ${budget.bookId}" }
            if (budget.categoryId != null) {
                require(db.categoryDao().getById(budget.categoryId) != null) { "category not found: ${budget.categoryId}" }
            }
            db.budgetDao().insert(budget)
            db.pendingMutationDao().insert(
                PendingMutationEntity(
                    mutationId = mutationId,
                    entityType = "budget",
                    entityId = budget.id,
                    operation = PendingMutationEntity.OP_INSERT,
                    payload = budgetToJson(budget),
                    status = PendingMutationEntity.STATUS_PENDING,
                    retryCount = 0,
                    createdAt = System.currentTimeMillis(),
                    expectedVersion = null,
                    lastError = null
                )
            )
        }
    }

    /** 原子事务：更新预算 + 写入 pending mutation */
    suspend fun updateBudget(budget: BudgetEntity, mutationId: String) {
        require(mutationId.isNotBlank()) { "mutationId is required" }
        require(budget.id.isNotBlank()) { "entity ID is required" }
        require(budget.amountCents in 1..9007199254740991L) { "amountCents must be a positive safe integer" }
        require(budget.version > 0) { "invalid version" }
        db.withTransaction {
            val current = db.budgetDao().getById(budget.id)
            require(current != null) { "budget not found" }
            require(budget.version > current.version) { "version must increase" }
            require(db.bookDao().getById(budget.bookId) != null) { "book not found: ${budget.bookId}" }
            if (budget.categoryId != null) {
                require(db.categoryDao().getById(budget.categoryId) != null) { "category not found: ${budget.categoryId}" }
            }
            db.budgetDao().update(budget)
            db.pendingMutationDao().insert(
                PendingMutationEntity(
                    mutationId = mutationId,
                    entityType = "budget",
                    entityId = budget.id,
                    operation = PendingMutationEntity.OP_UPDATE,
                    payload = budgetToJson(budget),
                    status = PendingMutationEntity.STATUS_PENDING,
                    retryCount = 0,
                    createdAt = System.currentTimeMillis(),
                    expectedVersion = current.version,
                    lastError = null
                )
            )
        }
    }

    /** 原子事务：删除预算 + 写入 pending mutation */
    suspend fun deleteBudget(id: String, mutationId: String) {
        require(mutationId.isNotBlank()) { "mutationId is required" }
        db.withTransaction {
            val current = db.budgetDao().getById(id)
            require(current != null) { "budget not found" }
            db.budgetDao().delete(id)
            db.pendingMutationDao().insert(
                PendingMutationEntity(
                    mutationId = mutationId,
                    entityType = "budget",
                    entityId = id,
                    operation = PendingMutationEntity.OP_DELETE,
                    payload = JSONObject().put("id", id).toString(),
                    status = PendingMutationEntity.STATUS_PENDING,
                    retryCount = 0,
                    createdAt = System.currentTimeMillis(),
                    expectedVersion = current.version,
                    lastError = null
                )
            )
        }
    }

    suspend fun getBudget(id: String): BudgetEntity? = db.budgetDao().getById(id)
    suspend fun listBudgets(bookId: String): List<BudgetEntity> =
        db.budgetDao().listByBook(bookId)

    // ─── Pending Mutations ───

    suspend fun listPendingMutations(status: String? = null): List<PendingMutationEntity> =
        if (status != null) db.pendingMutationDao().listByStatus(status)
        else db.pendingMutationDao().listAll()

    suspend fun markMutationSyncing(mutationId: String) =
        db.pendingMutationDao().markSyncing(mutationId)

    suspend fun markMutationFailed(mutationId: String) =
        db.pendingMutationDao().markFailed(mutationId)

    suspend fun removeMutation(mutationId: String) =
        db.pendingMutationDao().remove(mutationId)

    // ─── T-M3-02: 同步引擎专用 ───

    /** 同步引擎批量移除成功推送的 mutation。 */
    suspend fun removeAppliedMutations(ids: List<String>) {
        if (ids.isEmpty()) return
        db.pendingMutationDao().removeBatch(ids)
    }

    /** 同步引擎查询待推送批次。 */
    suspend fun listPushableMutations(limit: Int): List<PendingMutationEntity> =
        db.pendingMutationDao().listPushable(limit)

    /** P2-1: 同步引擎查询待推送 mutation 数量（O(1) 而非 O(n) 全量加载）。 */
    suspend fun countPendingMutations(): Int =
        db.pendingMutationDao().countPending()

    /** R-M4-31: 统计终态失败的 mutation 数量，用于与首页 SyncBadge 口径对齐。 */
    suspend fun countFailedMutations(): Int =
        db.pendingMutationDao().countFailed()

    /** 同步引擎：将残留 syncing 状态重置为 pending（崩溃恢复）。 */
    suspend fun resetSyncingMutations() =
        db.pendingMutationDao().resetSyncingToPending()

    /** 同步引擎：标记 mutation 失败并记录原因。 */
    suspend fun markMutationFailedWithError(mutationId: String, lastError: String?) =
        db.pendingMutationDao().markFailedWithError(mutationId, lastError)

    /**
     * 同步引擎：查询所有终态失败的 mutation（被服务端 rejected）。
     * 由 SyncEngine 随 SyncStatusEvent 发布给前端。
     */
    suspend fun listFailedMutations(): List<FailedMutation> =
        db.pendingMutationDao().listFailed().map {
            FailedMutation(
                mutationId = it.mutationId,
                entityType = it.entityType,
                entityId = it.entityId,
                operation = it.operation,
                reason = it.lastError
            )
        }

    /** 同步引擎：查询指定实体类型/ID 中仍有活跃 mutation 的 ID 集合。 */
    suspend fun listActiveMutationEntityIds(entityType: String, ids: List<String>): Set<String> {
        if (ids.isEmpty()) return emptySet()
        return db.pendingMutationDao().listActiveIds(entityType, ids).toSet()
    }

    /** 同步引擎：读取当前 cursor。 */
    suspend fun getSyncCursor(): Long {
        val raw = db.syncMetaDao().getValue(SyncMetaEntity.KEY_CURSOR)
        return raw?.toLongOrNull() ?: 0L
    }

    /** 同步引擎：写入 cursor。必须在实体合并的同一事务内调用。 */
    suspend fun setSyncCursor(cursor: Long) {
        db.syncMetaDao().upsert(
            SyncMetaEntity(SyncMetaEntity.KEY_CURSOR, cursor.toString())
        )
    }

    /**
     * T-M3-03: 查询所有活跃实体（非归档/非删除）用于 bootstrap 后对账。
     * 返回按 entityType 分组的 Map，每组内按 id 排序。
     */
    suspend fun listAllActiveEntities(): Map<String, List<Any>> {
        val books = db.bookDao().listActive().sortedBy { it.id }
        val categories = db.categoryDao().listActive().sortedBy { it.id }
        val accounts = db.accountDao().listActive().sortedBy { it.id }
        val budgets = books.flatMap { book ->
            db.budgetDao().listByBook(book.id)
        }.sortedBy { it.id }
        val transactions = books.flatMap { book ->
            db.transactionDao().listByBook(book.id)
        }.sortedBy { it.id }
        return mapOf(
            "books" to books,
            "categories" to categories,
            "accounts" to accounts,
            "transactions" to transactions,
            "budgets" to budgets
        )
    }

    /**
     * T-M3-03: 清空所有实体数据，保留 pending mutations 与 sync_meta。
     *
     * 用于 bootstrap 重建前：清空 Room 后从服务端快照重建，保证与云端一致。
     * 必须在 Room 事务内调用；pending mutations 在 bootstrap 完成后由引擎
     * 自行比对 expectedVersion 决定丢弃或保留。
     *
     * P2-2：cursor 同步重置为 0 — 这是崩溃恢复所必需的：若 clearAllEntities 成功
     * 但 applyBootstrap 在独立事务开始前崩溃，下次启动时 needsBootstrap() 必须
     * 仍返回 true（cursor=0 + 无 book）。若保留旧 cursor，后续 pull 会跳过服务端
     * 已 bootstrap 的数据。注释澄清：不是"多余"，而是"崩溃恢复锚点"。
     */
    suspend fun clearAllEntities() {
        db.withTransaction {
            db.transactionDao().clearAll()
            db.bookDao().clearAll()
            db.categoryDao().clearAll()
            db.accountDao().clearAll()
            db.budgetDao().clearAll()
            // P2-2 注释：崩溃恢复锚点。若 applyBootstrap 未完成时崩溃，
            // cursor=0 + 无实体 ⇒ needsBootstrap()=true ⇒ 下次启动重试 bootstrap。
            db.syncMetaDao().upsert(
                SyncMetaEntity(SyncMetaEntity.KEY_CURSOR, "0")
            )
        }
    }

    /**
     * T-M3-03: 从 bootstrap 响应重建所有实体。
     *
     * 在单个 Room 事务内 upsert 全部实体 + 写入 cursor。不入队 pending mutation。
     * 调用方应在调用前 [clearAllEntities]。
     *
     * P2-1 防御断言：bootstrap 响应中的实体必须都是"活跃"的（archivedAt=null,
     * deletedAt=null）。listAllActiveEntities 计数依赖此不变量；若服务端错误地
     * 返回已归档/删除的实体，会导致计数校验漏检"skip 导致丢数据"。
     *
     * @param ownerId bootstrap 不包含 owner_id，用当前会话用户 ID 回填。
     */
    suspend fun applyBootstrap(
        response: com.aozora.app.sync.BootstrapResponse,
        ownerId: String
    ) {
        db.withTransaction {
            for (bookRaw in response.books) {
                val entity = serverChangeMapToBook(bookRaw, ownerId) ?: continue
                // P2-1: bootstrap 仅重建活跃实体；服务端不应返回已删除 book
                check(entity.archivedAt == null) {
                    "bootstrap book ${entity.id} has non-null archivedAt"
                }
                db.bookDao().upsertFromSync(entity)
            }
            for (catRaw in response.categories) {
                val entity = serverChangeMapToCategory(catRaw, ownerId) ?: continue
                check(entity.archivedAt == null) {
                    "bootstrap category ${entity.id} has non-null archivedAt"
                }
                db.categoryDao().upsertFromSync(entity)
            }
            for (accRaw in response.accounts) {
                val entity = serverChangeMapToAccount(accRaw, ownerId) ?: continue
                check(entity.archivedAt == null) {
                    "bootstrap account ${entity.id} has non-null archivedAt"
                }
                db.accountDao().upsertFromSync(entity)
            }
            for (txRaw in response.transactions) {
                val entity = serverChangeMapToTransaction(txRaw) ?: continue
                check(entity.deletedAt == null) {
                    "bootstrap transaction ${entity.id} has non-null deletedAt"
                }
                db.transactionDao().upsertFromSync(entity)
            }
            for (budgetRaw in response.budgets) {
                val entity = serverChangeMapToBudget(budgetRaw) ?: continue
                db.budgetDao().upsertFromSync(entity)
            }
            db.syncMetaDao().upsert(
                SyncMetaEntity(SyncMetaEntity.KEY_CURSOR, response.cursor.toString())
            )
        }
    }

    /**
     * T-M3-02: 同步合并原子事务。
     *
     * 由 SyncEngine 调用：收到服务端 pull 结果后，在单个 Room 事务内
     * 应用所有变更并推进 cursor。规则：
     * - 跳过仍有活跃 mutation 的实体（本地待处理版本优先）。
     * - insert/update：upsert（REPLACE）覆盖本地副本。
     * - delete → 归档/物删（N-M3-8 文档化）：
     *   服务端统一用 deleted_at 软删，客户端按 entity 语义转换——
     *   transaction/budget → soft delete (deletedAt)
     *   book/category/account → archive (archivedAt)
     * - ownerId：服务端不返回 owner_id，合并时用 [ownerId] 回填。
     * - 同事务推进 cursor；不入队新 mutation。
     */
    suspend fun applyServerChanges(
        changes: List<ServerChange>,
        newCursor: Long,
        ownerId: String
    ) {
        if (changes.isEmpty() && newCursor == getSyncCursor()) return
        db.withTransaction {
            // P2-3: 按 entityType 批量查询活跃 mutation，避免 N 次查询（最多 5 次）
            val changesByType = changes.groupBy { it.entityType }
            val activeIdsByType = mutableMapOf<String, Set<String>>()
            for ((entityType, typeChanges) in changesByType) {
                val ids = typeChanges.map { it.entityId }
                activeIdsByType[entityType] = db.pendingMutationDao()
                    .listActiveIds(entityType, ids).toSet()
            }

            for (change in changes) {
                val activeIds = activeIdsByType[change.entityType] ?: emptySet()
                if (activeIds.contains(change.entityId)) continue

                when (change.entityType) {
                    "transaction" -> {
                        if (change.operation == SyncChangeOp.DELETE) {
                            db.transactionDao().applyServerDelete(
                                change.entityId, System.currentTimeMillis(), change.version
                            )
                        } else {
                            val entity = change.toTransaction() ?: continue
                            db.transactionDao().upsertFromSync(entity)
                        }
                    }
                    "book" -> {
                        if (change.operation == SyncChangeOp.DELETE) {
                            // 服务端 book.delete 在客户端映射为归档（client 模型无 deleted_at）
                            db.bookDao().applyServerArchive(
                                change.entityId, System.currentTimeMillis(), change.version
                            )
                        } else {
                            val entity = change.toBook(ownerId) ?: continue
                            db.bookDao().upsertFromSync(entity)
                        }
                    }
                    "category" -> {
                        if (change.operation == SyncChangeOp.DELETE) {
                            db.categoryDao().applyServerArchive(
                                change.entityId, System.currentTimeMillis(), change.version
                            )
                        } else {
                            val entity = change.toCategory(ownerId) ?: continue
                            db.categoryDao().upsertFromSync(entity)
                        }
                    }
                    "account" -> {
                        if (change.operation == SyncChangeOp.DELETE) {
                            db.accountDao().applyServerArchive(
                                change.entityId, System.currentTimeMillis(), change.version
                            )
                        } else {
                            val entity = change.toAccount(ownerId) ?: continue
                            db.accountDao().upsertFromSync(entity)
                        }
                    }
                    "budget" -> {
                        if (change.operation == SyncChangeOp.DELETE) {
                            db.budgetDao().delete(change.entityId)
                        } else {
                            val entity = change.toBudget() ?: continue
                            db.budgetDao().upsertFromSync(entity)
                        }
                    }
                }
            }
            db.syncMetaDao().upsert(
                SyncMetaEntity(SyncMetaEntity.KEY_CURSOR, newCursor.toString())
            )
        }
    }

    // ─── JSON 序列化（用于 pending mutation payload） ───

    private fun transactionToJson(tx: TransactionEntity): String {
        return JSONObject().apply {
            put("id", tx.id)
            put("bookId", tx.bookId)
            put("type", tx.type)
            put("amountCents", tx.amountCents)
            put("categoryId", tx.categoryId)
            put("accountId", tx.accountId ?: JSONObject.NULL)
            put("note", tx.note)
            put("bizDate", tx.bizDate)
            put("occurredAt", tx.occurredAt)
            put("source", tx.source)
            put("version", tx.version)
        }.toString()
    }

    private fun bookToJson(book: BookEntity): String {
        return JSONObject().apply {
            put("id", book.id)
            put("ownerId", book.ownerId)
            put("name", book.name)
            put("icon", book.icon)
            put("version", book.version)
        }.toString()
    }

    private fun categoryToJson(cat: CategoryEntity): String {
        return JSONObject().apply {
            put("id", cat.id)
            put("ownerId", cat.ownerId)
            put("bookId", cat.bookId ?: JSONObject.NULL)
            put("parentId", cat.parentId ?: JSONObject.NULL)
            put("kind", cat.kind)
            put("name", cat.name)
            put("version", cat.version)
        }.toString()
    }

    private fun accountToJson(account: AccountEntity): String {
        return JSONObject().apply {
            put("id", account.id)
            put("ownerId", account.ownerId)
            put("name", account.name)
            put("icon", account.icon)
            put("accountType", account.accountType ?: JSONObject.NULL)
            put("version", account.version)
        }.toString()
    }

    private fun budgetToJson(budget: BudgetEntity): String {
        return JSONObject().apply {
            put("id", budget.id)
            put("bookId", budget.bookId)
            put("budgetType", budget.budgetType)
            put("categoryId", budget.categoryId ?: JSONObject.NULL)
            put("period", budget.period)
            put("amountCents", budget.amountCents)
            put("version", budget.version)
        }.toString()
    }

    // ─── T-M3-03: Bootstrap 重建 helper ───

    private fun serverChangeMapToTransaction(
        map: Map<String, String?>
    ): TransactionEntity? {
        val id = map["id"] ?: return null
        val bookId = map["bookId"] ?: return null
        val categoryId = map["categoryId"] ?: return null
        val accountId = map["accountId"]
        val amountCents = map["amountCents"]?.toLongOrNull() ?: return null
        val type = map["type"] ?: return null
        val bizDate = map["bizDate"] ?: return null
        val occurredAt = map["occurredAt"]?.toLongOrNull() ?: return null
        val note = map["note"] ?: ""
        val source = map["source"] ?: "manual"
        val version = map["version"]?.toIntOrNull() ?: 1
        return TransactionEntity(
            id = id, bookId = bookId, categoryId = categoryId, accountId = accountId,
            amountCents = amountCents, type = type, bizDate = bizDate,
            occurredAt = occurredAt, note = note, source = source, version = version,
            deletedAt = null
        )
    }

    private fun serverChangeMapToBook(
        map: Map<String, String?>, ownerId: String
    ): BookEntity? {
        val id = map["id"] ?: return null
        val name = map["name"] ?: return null
        val icon = map["icon"] ?: "default"
        val version = map["version"]?.toIntOrNull() ?: 1
        return BookEntity(
            id = id, ownerId = ownerId, name = name, icon = icon,
            version = version, archivedAt = null
        )
    }

    private fun serverChangeMapToCategory(
        map: Map<String, String?>, ownerId: String
    ): CategoryEntity? {
        val id = map["id"] ?: return null
        val name = map["name"] ?: return null
        val kind = map["kind"] ?: return null
        val bookId = map["bookId"]
        val parentId = map["parentId"]
        val version = map["version"]?.toIntOrNull() ?: 1
        return CategoryEntity(
            id = id, ownerId = ownerId, bookId = bookId, parentId = parentId,
            kind = kind, name = name, archivedAt = null, version = version
        )
    }

    private fun serverChangeMapToAccount(
        map: Map<String, String?>, ownerId: String
    ): AccountEntity? {
        val id = map["id"] ?: return null
        val name = map["name"] ?: return null
        val icon = map["icon"] ?: "default"
        val version = map["version"]?.toIntOrNull() ?: 1
        return AccountEntity(
            id = id, ownerId = ownerId, name = name, icon = icon,
            accountType = map["accountType"],
            archivedAt = null, version = version
        )
    }

    private fun serverChangeMapToBudget(map: Map<String, String?>): BudgetEntity? {
        val id = map["id"] ?: return null
        val bookId = map["bookId"] ?: return null
        val categoryId = map["categoryId"]
        val budgetType = map["budgetType"] ?: return null
        val period = map["period"] ?: return null
        val amountCents = map["amountCents"]?.toLongOrNull() ?: return null
        val version = map["version"]?.toIntOrNull() ?: 1
        return BudgetEntity(
            id = id, bookId = bookId, categoryId = categoryId, budgetType = budgetType,
            period = period, amountCents = amountCents, version = version
        )
    }

    // ─── T-M5-04a: Consent ───

    suspend fun insertConsent(consent: UserConsentEntity) {
        db.userConsentDao().insert(consent)
    }

    suspend fun getLatestConsent(): UserConsentEntity? =
        db.userConsentDao().getLatest()

    // ─── T-M5-04c: Account deletion — wipe local state ───

    /**
     * 清空所有 Room 表（账号删除后调用）。
     *
     * 调用方须同时清空 TokenStore / ConsentStore 与 localStorage，
     * 并导航回首启页，以完成完整的账号删除链路。
     */
    suspend fun clearAllData() {
        db.withTransaction {
            db.clearAllTables()
        }
    }

    companion object {
        // 与 domain/nlu/category.ts 内置别名对齐，保证聊天短句能命中真实分类行
        val DEFAULT_EXPENSE_CATEGORIES = listOf(
            "餐饮", "交通", "购物", "娱乐", "居住", "医疗", "通讯", "学习"
        )
        val DEFAULT_INCOME_CATEGORIES = listOf(
            "工资", "奖金", "红包收入", "退款", "报销"
        )
    }
}
