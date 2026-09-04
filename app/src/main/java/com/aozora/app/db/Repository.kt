package com.aozora.app.db

import androidx.room.withTransaction
import com.aozora.app.db.entity.*
import org.json.JSONObject
import java.time.LocalDate

/**
 * Repository — 统一数据访问层。
 *
 * 关键约束：
 * - 所有写操作与 pending mutation 在同一 Room 事务内完成（§5.3）
 * - 不生成 AI 业务结论（§4.3 Kotlin platform 职责边界）
 * - 金额字段始终为 Long（整数分），禁止浮点
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
                    createdAt = System.currentTimeMillis()
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
                    createdAt = System.currentTimeMillis()
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
                    createdAt = System.currentTimeMillis()
                )
            )
        }
    }

    suspend fun getBook(id: String): BookEntity? = db.bookDao().getById(id)
    suspend fun listBooks(): List<BookEntity> = db.bookDao().listActive()

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
                    createdAt = System.currentTimeMillis()
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
                    createdAt = System.currentTimeMillis()
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
                    createdAt = System.currentTimeMillis()
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
                    createdAt = System.currentTimeMillis()
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
                    createdAt = System.currentTimeMillis()
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
                    createdAt = System.currentTimeMillis()
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
}
