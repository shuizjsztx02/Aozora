package com.aozora.app.db

import android.util.Log
import androidx.room.withTransaction
import com.aozora.app.db.entity.*
import com.aozora.app.sync.FailedMutation
import com.aozora.app.sync.ServerChange
import com.aozora.app.sync.SyncChangeOp
import com.aozora.app.sync.SyncPayloadMapper
import org.json.JSONException
import org.json.JSONObject
import java.time.LocalDate
import java.util.UUID

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

    /**
     * 第十八轮 REQ-70（T18-7）：一笔交易对账户余额的净影响 —— **收入为正、支出为负**。
     *
     * 这是**跨端契约**（服务端 `transactions/service.py` 的 `balance_delta` 同式）：
     * 两端符号必须一致，否则「AI 记一笔」与「手动记一笔」会把同一个账户的余额推向
     * 相反方向。两处各有用例钉住（`RepositoryTransactionBalanceLinkTest` /
     * `test_transaction_balance_link.py`）。
     */
    private fun balanceDelta(type: String, amountCents: Long): Long =
        if (type == "income") amountCents else -amountCents

    /**
     * REQ-70：在**当前事务内**把 [delta] 加到账户余额上，并同事务入队一条 account 的
     * upsert mutation —— payload 带**新余额的绝对值**而非增量（决策 #4 的幂等铁律：
     * 重放同一 mutation 不会重复累加）。
     *
     * 账户不存在时**静默跳过**：联动是记账的附属效果，不该让一笔合法流水因为找不到
     * 账户而写不进去。归档账户照样联动 —— 余额是事实，归档只是不再可选。
     */
    private suspend fun applyBalanceDelta(accountId: String, delta: Long) {
        if (delta == 0L) return
        val current = db.accountDao().getById(accountId) ?: return
        val updated = current.copy(
            balanceCents = current.balanceCents + delta,
            version = current.version + 1,
        )
        db.accountDao().update(updated)
        db.pendingMutationDao().insert(
            PendingMutationEntity(
                mutationId = UUID.randomUUID().toString(),
                entityType = "account",
                entityId = accountId,
                operation = PendingMutationEntity.OP_UPDATE,
                payload = accountToJson(updated),
                status = PendingMutationEntity.STATUS_PENDING,
                retryCount = 0,
                createdAt = System.currentTimeMillis(),
                expectedVersion = current.version,
                lastError = null
            )
        )
    }

    /** 原子事务：插入流水 + 写入 pending mutation */
    suspend fun insertTransaction(tx: TransactionEntity, mutationId: String) {
        require(mutationId.isNotBlank()) { "mutationId is required" }
        require(tx.id.isNotBlank() && tx.bookId.isNotBlank() && tx.categoryId.isNotBlank()) { "entity IDs are required" }
        require(tx.amountCents in 1..9007199254740991L) { "amountCents must be a positive safe integer" }
        require(tx.type in setOf("income", "expense")) { "invalid transaction type" }
        // 第十一轮 T11-4（REQ-26/决策 #4）：+'scheduled'（定时引擎生成）；服务端
        // TxSource 同步取并集（server/src/models/ledger.py），否则 push 恒 422 毒丸。
        require(tx.source in setOf("manual", "nlu_local", "nlu_ai", "import", "scheduled")) { "invalid source" }
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
            // REQ-70：记账联动账户余额（同事务，无账户则不动）
            tx.accountId?.let { applyBalanceDelta(it, balanceDelta(tx.type, tx.amountCents)) }
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
            // REQ-70：软删回补 —— 这笔钱不再占用该账户
            current.accountId?.let {
                applyBalanceDelta(it, -balanceDelta(current.type, current.amountCents))
            }
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

    /**
     * 第十二轮 T12-7（REQ-36）：原子事务：全字段编辑流水 + 写入 pending mutation
     * （OP_UPDATE）。真 update 保 id / 保附件关联（决策 #4，区别于聊天卡的替换写）。
     *
     * 版本契约镜像 updateAccount：调用方传 version = 当前 +1，入队 expectedVersion =
     * 变更前版本；SQL 级 where id AND version 乐观锁兜底（0 行 = 冲突 → 抛错回滚，
     * mutation 不入队）。payload = transactionToJson 全字段（含 bookId/includeInBudget：
     * 服务端 _apply_update 白名单外的键静默忽略不毒丸，跨账本移动语义 server 暂不支持）。
     */
    suspend fun updateTransaction(tx: TransactionEntity, mutationId: String) {
        require(mutationId.isNotBlank()) { "mutationId is required" }
        require(tx.id.isNotBlank() && tx.bookId.isNotBlank() && tx.categoryId.isNotBlank()) { "entity IDs are required" }
        require(tx.amountCents in 1..9007199254740991L) { "amountCents must be a positive safe integer" }
        require(tx.type in setOf("income", "expense")) { "invalid transaction type" }
        // 源枚举与 insertTransaction 同步（T11-4 加 scheduled）
        require(tx.source in setOf("manual", "nlu_local", "nlu_ai", "import", "scheduled")) { "invalid source" }
        require(tx.version > 0) { "invalid version" }
        require(Regex("\\d{4}-\\d{2}-\\d{2}").matches(tx.bizDate)) { "invalid bizDate" }
        require(runCatching { LocalDate.parse(tx.bizDate) }.isSuccess) { "invalid bizDate" }
        db.withTransaction {
            val current = db.transactionDao().getById(tx.id)
            require(current != null && current.deletedAt == null) { "transaction not found or already deleted" }
            require(tx.version > current.version) { "version must increase" }
            require(db.bookDao().getById(tx.bookId) != null) { "book not found: ${tx.bookId}" }
            require(db.categoryDao().getById(tx.categoryId) != null) { "category not found: ${tx.categoryId}" }
            if (tx.accountId != null) {
                require(db.accountDao().getById(tx.accountId) != null) { "account not found: ${tx.accountId}" }
            }
            val rows = db.transactionDao().updateWithVersion(
                id = tx.id,
                bookId = tx.bookId,
                type = tx.type,
                amountCents = tx.amountCents,
                categoryId = tx.categoryId,
                accountId = tx.accountId,
                note = tx.note,
                bizDate = tx.bizDate,
                occurredAt = tx.occurredAt,
                source = tx.source,
                includeInBudget = tx.includeInBudget,
                newVersion = tx.version,
                expectedVersion = current.version,
            )
            require(rows == 1) { "transaction version conflict" }
            // REQ-70：编辑必须**重算**而非叠加增量 —— 旧账户回补 + 新账户加计。
            // 同一账户时只做净差额（金额 / 类型改动的两种情形一并覆盖）。
            val oldSigned = balanceDelta(current.type, current.amountCents)
            val newSigned = balanceDelta(tx.type, tx.amountCents)
            if (current.accountId == tx.accountId) {
                tx.accountId?.let { applyBalanceDelta(it, newSigned - oldSigned) }
            } else {
                current.accountId?.let { applyBalanceDelta(it, -oldSigned) }
                tx.accountId?.let { applyBalanceDelta(it, newSigned) }
            }
            db.pendingMutationDao().insert(
                PendingMutationEntity(
                    mutationId = mutationId,
                    entityType = "transaction",
                    entityId = tx.id,
                    operation = PendingMutationEntity.OP_UPDATE,
                    payload = transactionToJson(tx),
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

    /**
     * T16-6（REQ-62 / 决策 #8）：总账本聚合 —— 全部活跃（未归档）非 master 账本的交易合并，
     * 排序与单账本 listByBook 一致（bizDate/occurredAt 倒序）。
     * master 自身恒无行（listBooks 已过滤 archived、且 bookIds 排除 masterId）；
     * 全空（无活跃非 master 账本）时直接返回空列表，避免 IN () 非法 SQL。
     */
    suspend fun listTransactionsByMasterAggregate(): List<TransactionEntity> {
        val masterId = getMasterBookId()
        val bookIds = listBooks().map { it.id }.filter { it != masterId }
        if (bookIds.isEmpty()) return emptyList()
        return db.transactionDao().listByBooks(bookIds)
    }

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

    /**
     * 第十四轮 T14-2（REQ-39 / 决策 #3）：原子事务：更新账本 + 写入 pending mutation
     * （镜像 updateAccount：局部合并由 handler 构造完整实体；version 必须递增，
     * expectedVersion = 变更前版本）。master 总账本只读（isMaster 系 meta 键
     * master_book_id 派生，对齐 handleBookList 现有语义），拒绝编辑。
     */
    suspend fun updateBook(book: BookEntity, mutationId: String) {
        require(mutationId.isNotBlank()) { "mutationId is required" }
        require(book.id.isNotBlank()) { "entity ID is required" }
        require(book.name.isNotBlank()) { "name is required" }
        require(book.version > 0) { "invalid version" }
        db.withTransaction {
            val current = db.bookDao().getById(book.id)
            require(current != null) { "book not found" }
            require(current.archivedAt == null) { "book is archived" }
            require(book.version > current.version) { "version must increase" }
            require(book.id != getMasterBookId()) { "master book is read-only" }
            db.bookDao().update(book)
            db.pendingMutationDao().insert(
                PendingMutationEntity(
                    mutationId = mutationId,
                    entityType = "book",
                    entityId = book.id,
                    operation = PendingMutationEntity.OP_UPDATE,
                    payload = bookToJson(book),
                    status = PendingMutationEntity.STATUS_PENDING,
                    retryCount = 0,
                    createdAt = System.currentTimeMillis(),
                    expectedVersion = current.version,
                    lastError = null
                )
            )
        }
    }

    suspend fun getBook(id: String): BookEntity? = db.bookDao().getById(id)
    suspend fun listBooks(): List<BookEntity> = db.bookDao().listActive()

    /**
     * T9-5a（REQ-11）：同步 meta 键值读写（sync_meta 表）。
     * key 白名单校验在桥 handler 层（AozoraBridge.handleMetaSet），此处为通用存取。
     */
    suspend fun getMeta(key: String): String? = db.syncMetaDao().getValue(key)

    suspend fun setMeta(key: String, value: String) {
        db.syncMetaDao().upsert(SyncMetaEntity(key, value))
    }

    /**
     * T-M7-05j（偏好项上云）：面向 UI/桥的写入口 —— 写 sync_meta，白名单内的键
     * 同事务入队上行 mutation。
     *
     * 白名单 = [SyncMetaEntity.SYNCABLE_PREFERENCE_KEYS]（6 键，与服务端 SYNCABLE_KEYS
     * 对应）；白名单外的键（avatar_uri / display_name / active_persona_id / 各类
     * 迁移与预算标记）**保持原样只写本地**，一个都不上行。
     *
     * 这与 [setMeta] 的分工：setMeta 是"通用存取"（内部自管键也走它），本方法才是
     * 用户可见偏好键的写路径。桥的 meta.set 走这里。
     */
    suspend fun setMetaWithPreferenceUpload(key: String, value: String) {
        if (key !in SyncMetaEntity.SYNCABLE_PREFERENCE_KEYS) {
            setMeta(key, value)
            return
        }
        db.withTransaction {
            setMeta(key, value)
            enqueuePreferenceUpsert(key, value)
        }
    }

    /**
     * T-M7-05j：入队一条 user_preference 上行 mutation（本地镜像行同步维护）。
     *
     * 行 id 契约：镜像行的 id **就是服务端行 id**（pull/bootstrap 直接带回）。
     * 本地首次写入时还没有镜像行 → 自铸 UUID 当 entityId —— 服务端 insert 用的是
     * 客户端给的 entity_id（service.py `_upsert_preference`），故两侧 id 天然一致。
     *
     * 幂等/自愈：多设备撞同键时服务端不覆盖（返回既有行 id），回程 pull 的
     * REPLACE upsert 按 key 唯一索引把本地自铸行换掉 → 之后的写会带正确的
     * expectedVersion。
     *
     * 版本语义镜像既有实体：首次写 insert（expectedVersion=null，本地 version=1），
     * 之后 update（expectedVersion=当前版本，本地 version+1）。
     */
    private suspend fun enqueuePreferenceUpsert(key: String, value: String) {
        val current = db.userPreferenceDao().getByKey(key)
        val id = current?.id ?: java.util.UUID.randomUUID().toString()
        db.userPreferenceDao().upsert(
            UserPreferenceEntity(
                id = id, key = key, value = value,
                version = (current?.version ?: 0) + 1
            )
        )
        db.pendingMutationDao().insert(
            PendingMutationEntity(
                mutationId = java.util.UUID.randomUUID().toString(),
                entityType = "user_preference",
                entityId = id,
                operation = if (current == null) PendingMutationEntity.OP_INSERT
                else PendingMutationEntity.OP_UPDATE,
                payload = JSONObject().apply {
                    put("key", key)
                    put("value", value)
                }.toString(),
                status = PendingMutationEntity.STATUS_PENDING,
                retryCount = 0,
                createdAt = System.currentTimeMillis(),
                expectedVersion = current?.version,
                lastError = null
            )
        )
    }

    /** T10-3（决策 #4）：删除指定 meta 键（登出先清清身份键，双保险于 clearAllData）。 */
    suspend fun deleteMeta(key: String) {
        db.syncMetaDao().delete(key)
    }

    /** T7-1（REQ-5）：总账本 book id；未播种时为 null。 */
    suspend fun getMasterBookId(): String? =
        db.syncMetaDao().getValue(SyncMetaEntity.KEY_MASTER_BOOK_ID)

    private suspend fun setMasterBookId(id: String) {
        db.syncMetaDao().upsert(
            SyncMetaEntity(SyncMetaEntity.KEY_MASTER_BOOK_ID, id)
        )
    }

    /**
     * T7-1（决策 #2）+ T9-5a（决策 #3）：记账目标账本。
     * 优先 default_book_id（须存在、未归档、非 master），否则活跃账本中
     * 第一个非 master 的 book（listBooks 按 name 排序）；全部为 master 时回退第一个。
     */
    suspend fun getEntryBook(): BookEntity? {
        val books = listBooks()
        if (books.isEmpty()) return null
        val masterId = getMasterBookId()
        getDefaultBookId()
            ?.let { def -> books.firstOrNull { it.id == def && it.id != masterId } }
            ?.let { return it }
        return books.firstOrNull { it.id != masterId } ?: books.first()
    }

    /** T9-5a（REQ-11）：默认账本 id（未设置时 null）。 */
    suspend fun getDefaultBookId(): String? =
        db.syncMetaDao().getValue(SyncMetaEntity.KEY_DEFAULT_BOOK_ID)

    /**
     * T9-5a（决策 #3）：默认账户实体。
     * default_account_id 命中且未归档 → 用它；否则回退第一个活跃账户（id 升序）；
     * 无账户时 null。
     */
    suspend fun getDefaultAccount(): AccountEntity? {
        val accs = listAccounts()
        if (accs.isEmpty()) return null
        db.syncMetaDao().getValue(SyncMetaEntity.KEY_DEFAULT_ACCOUNT_ID)
            ?.let { def -> accs.firstOrNull { it.id == def } }
            ?.let { return it }
        return accs.first()
    }

    /**
     * T9-5a（决策 #2）：book.list UI 出口 —— 按 book_order 整形后的列表。
     * 总账本恒列首位；order 数组内的项按序在前，未列项按 name 序垫后。
     */
    suspend fun listBooksForUi(): List<BookEntity> {
        val masterId = getMasterBookId()
        return applyOrder(listBooks(), { it.id }, getMeta(SyncMetaEntity.KEY_BOOK_ORDER), masterId)
    }

    /** T9-5a（决策 #2）：account.list UI 出口 —— 按 account_order 整形（无 master 特例）。 */
    suspend fun listAccountsForUi(): List<AccountEntity> =
        applyOrder(listAccounts(), { it.id }, getMeta(SyncMetaEntity.KEY_ACCOUNT_ORDER), null)

    /**
     * 列表整形：orderJson（JSON id 数组）内列出的项按序在前，其余按原序垫后；
     * headId（如总账本）无论是否在 order 中恒列首位。orderJson 非法 → 原序返回。
     */
    private fun <T> applyOrder(
        items: List<T>,
        idOf: (T) -> String,
        orderJson: String?,
        headId: String?
    ): List<T> {
        val head = items.firstOrNull { headId != null && idOf(it) == headId }
        val rest = items.filterNot { it === head }
        val ordered = if (orderJson == null) rest else try {
            val arr = org.json.JSONArray(orderJson)
            val remaining = rest.toMutableList()
            val picked = ArrayList<T>(rest.size)
            for (i in 0 until arr.length()) {
                val id = arr.optString(i, null) ?: continue
                val idx = remaining.indexOfFirst { idOf(it) == id }
                if (idx >= 0) picked.add(remaining.removeAt(idx))
            }
            picked + remaining
        } catch (e: org.json.JSONException) {
            rest
        }
        return listOfNotNull(head) + ordered
    }

    // ─── 首启种子（第六轮 Bug-5.1；T7-1 扩展为双账本） ───

    /**
     * 幂等补种：默认分类 + 默认账本 + 总账本（REQ-5 决策 #1，三段各自独立判断）。
     *
     * 背景：此前默认数据完全依赖"服务端 guest 注册 → sync bootstrap"拉取；
     * 真机连不通服务端时分类九宫格为空，手动记账与聊天记账都无法完成。
     * 三段（同一事务内）：
     * 1. 分类：新账户全量种；存量账户按「kind + name」diff 补齐缺的默认分类
     *    （REQ-79 / T20-5，版本键 = [SyncMetaEntity.KEY_CATEGORY_SEED_VERSION]，
     *    **只增不改不删**——用户已有分类一行不动）
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
            val existingCats = db.categoryDao().listActive().associateBy { it.kind to it.name }
            if (existingCats.isEmpty()) {
                DEFAULT_EXPENSE_CATEGORIES.forEach { seedCategoryTree(it, "expense") }
                DEFAULT_INCOME_CATEGORIES.forEach { seedCategoryTree(it, "income") }
                setCategorySeedVersion()
            } else if (
                db.syncMetaDao().getValue(SyncMetaEntity.KEY_CATEGORY_SEED_VERSION)
                != CATEGORY_SEED_VERSION
            ) {
                // 存量账户：按名字 diff 补齐（同名一律跳过，二级挂到**已有**的那条一级上）
                DEFAULT_EXPENSE_CATEGORIES.forEach { backfillSeedTree(it, "expense", existingCats) }
                DEFAULT_INCOME_CATEGORIES.forEach { backfillSeedTree(it, "income", existingCats) }
                setCategorySeedVersion()
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

    /**
     * REQ-79（T20-5）：插入一条种子分类并返回其 id——父 id 是随机 UUID，不能预置，
     * 二级必须在父插入拿到真实 id 之后再插。
     */
    private suspend fun seedCategory(
        name: String,
        kind: String,
        icon: String? = null,
        parentId: String? = null,
    ): String {
        val cat = CategoryEntity(
            id = java.util.UUID.randomUUID().toString(),
            ownerId = "local",
            bookId = null,
            parentId = parentId,
            kind = kind,
            name = name,
            icon = icon,
            archivedAt = null,
            version = 1
        )
        db.categoryDao().insert(cat)
        enqueueInsert("category", cat.id, categoryToJson(cat))
        return cat.id
    }

    /** 新账户：种一棵完整的一级分类树（先父后子）。 */
    private suspend fun seedCategoryTree(seed: SeedCategory, kind: String) {
        val parentId = seedCategory(seed.name, kind, seed.icon)
        seed.children.forEach { seedCategory(it.name, kind, it.icon, parentId) }
    }

    /**
     * 存量账户：只补「名字还不在库里」的分类（决策 #9：**只增不改不删**）。
     *
     * 一级已存在时用**已有那条**的 id 作二级的 parentId（不造影子父），
     * 已存在的分类也不补 icon——用户可见的既有行一行不动（旧分类的空图标
     * 由渲染层 [getCategoryEmoji] 兜底）。
     */
    private suspend fun backfillSeedTree(
        seed: SeedCategory,
        kind: String,
        existing: Map<Pair<String, String>, CategoryEntity>,
    ) {
        val parentId = existing[kind to seed.name]?.id
            ?: seedCategory(seed.name, kind, seed.icon)
        seed.children.forEach { child ->
            if (existing.containsKey(kind to child.name)) return@forEach
            seedCategory(child.name, kind, child.icon, parentId)
        }
    }

    private suspend fun setCategorySeedVersion() {
        db.syncMetaDao().upsert(
            SyncMetaEntity(SyncMetaEntity.KEY_CATEGORY_SEED_VERSION, CATEGORY_SEED_VERSION)
        )
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
        require(category.name.isNotBlank() && category.name.length <= 64) { "invalid name" }
        db.withTransaction {
            // 第八轮（REQ-8）二级分类校验：父须存在、父为顶级（限两级）、同 kind、非自引用
            category.parentId?.let { parentId ->
                require(parentId != category.id) { "category cannot be its own parent" }
                val parent = db.categoryDao().getById(parentId)
                require(parent != null && parent.archivedAt == null) { "parent category not found" }
                require(parent.parentId == null) { "only two-level categories are supported" }
                require(parent.kind == category.kind) { "parent kind mismatch" }
            }
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

    suspend fun getCategory(id: String): CategoryEntity? = db.categoryDao().getById(id)

    /**
     * 第八轮（REQ-8）：更新分类（改名/图标/父级）+ 写入 pending mutation。
     * 仿 updateAccount：局部合并由调用方构造完整实体；version 必须递增。
     * parentId 若非空须指向同 kind 的顶级分类（限两级）。
     */
    suspend fun updateCategory(category: CategoryEntity, mutationId: String) {
        require(mutationId.isNotBlank()) { "mutationId is required" }
        require(category.id.isNotBlank()) { "entity ID is required" }
        require(category.name.isNotBlank() && category.name.length <= 64) { "invalid name" }
        require(category.kind in setOf("expense", "income")) { "invalid kind" }
        require(category.version > 0) { "invalid version" }
        db.withTransaction {
            val current = db.categoryDao().getById(category.id)
            require(current != null) { "category not found" }
            require(current.archivedAt == null) { "category is archived" }
            require(category.version > current.version) { "version must increase" }
            val parentId = category.parentId
            if (parentId != null) {
                require(parentId != category.id) { "category cannot be its own parent" }
                val parent = db.categoryDao().getById(parentId)
                require(parent != null && parent.archivedAt == null) { "parent category not found" }
                require(parent.parentId == null) { "only two-level categories are supported" }
                require(parent.kind == category.kind) { "parent kind mismatch" }
            }
            db.categoryDao().update(category)
            db.pendingMutationDao().insert(
                PendingMutationEntity(
                    mutationId = mutationId,
                    entityType = "category",
                    entityId = category.id,
                    operation = PendingMutationEntity.OP_UPDATE,
                    payload = categoryToJson(category),
                    status = PendingMutationEntity.STATUS_PENDING,
                    retryCount = 0,
                    createdAt = System.currentTimeMillis(),
                    expectedVersion = current.version,
                    lastError = null
                )
            )
        }
    }

    // ─── Savings Goals（T-M7-01 起上云：云端权威 + 本地副本） ──
    //
    // 与 book/account 等一致：insert/update/archive 与 pending mutation 在同一
    // Room 事务内完成。上行 payload 省略超长的照片 icon（服务端 64 上限）。
    // name 上限对齐服务端 savings_goals.name String(128)：超长会在 push 时
    // 422 中止整批（毒丸），故在此唯一把关。

    suspend fun listSavingsGoals(): List<SavingsGoalEntity> = db.savingsGoalDao().listActive()

    suspend fun getSavingsGoal(id: String): SavingsGoalEntity? = db.savingsGoalDao().getById(id)

    /**
     * T9-4（REQ-14/决策 #8）：存款期限底线校验——unit 仅 'week'|'month'，
     * value/unit 必须成对（同空或同非空）。bridge 侧 parseSavingsPeriod 已做
     * 范围校验（周 1-521 / 月 1-120），此处防任何写入路径绕过。
     */
    private fun requireValidSavingsPeriod(goal: SavingsGoalEntity) {
        require((goal.periodValue == null) == (goal.periodUnit == null)) {
            "periodValue/periodUnit must be provided together"
        }
        require(goal.periodUnit == null || goal.periodUnit == "week" || goal.periodUnit == "month") {
            "invalid periodUnit: ${goal.periodUnit}"
        }
    }

    suspend fun insertSavingsGoal(goal: SavingsGoalEntity, mutationId: String) {
        require(mutationId.isNotBlank()) { "mutationId is required" }
        require(goal.id.isNotBlank() && goal.name.isNotBlank()) { "goal id/name required" }
        require(goal.name.length <= NAME_WIRE_MAX_LEN) { "goal name too long" }
        require(goal.targetCents >= 1) { "targetCents must be positive" }
        require(goal.currentCents >= 0) { "currentCents must be non-negative" }
        require(goal.version > 0 && goal.archivedAt == null) { "invalid goal insert" }
        requireValidSavingsPeriod(goal)
        db.withTransaction {
            db.savingsGoalDao().insert(goal)
            db.pendingMutationDao().insert(
                PendingMutationEntity(
                    mutationId = mutationId,
                    entityType = "savings_goal",
                    entityId = goal.id,
                    operation = PendingMutationEntity.OP_INSERT,
                    payload = savingsGoalToJson(goal),
                    status = PendingMutationEntity.STATUS_PENDING,
                    retryCount = 0,
                    createdAt = System.currentTimeMillis(),
                    expectedVersion = null,
                    lastError = null
                )
            )
        }
    }

    suspend fun updateSavingsGoal(goal: SavingsGoalEntity, mutationId: String) {
        require(mutationId.isNotBlank()) { "mutationId is required" }
        require(goal.id.isNotBlank() && goal.name.isNotBlank()) { "goal id/name required" }
        require(goal.name.length <= NAME_WIRE_MAX_LEN) { "goal name too long" }
        require(goal.targetCents >= 1) { "targetCents must be positive" }
        require(goal.currentCents >= 0) { "currentCents must be non-negative" }
        requireValidSavingsPeriod(goal)
        db.withTransaction {
            val current = db.savingsGoalDao().getById(goal.id)
            require(current != null) { "savings goal not found" }
            require(current.archivedAt == null) { "savings goal is archived" }
            require(goal.version > current.version) { "version must increase" }
            db.savingsGoalDao().update(goal)
            db.pendingMutationDao().insert(
                PendingMutationEntity(
                    mutationId = mutationId,
                    entityType = "savings_goal",
                    entityId = goal.id,
                    operation = PendingMutationEntity.OP_UPDATE,
                    payload = savingsGoalToJson(goal),
                    status = PendingMutationEntity.STATUS_PENDING,
                    retryCount = 0,
                    createdAt = System.currentTimeMillis(),
                    expectedVersion = current.version,
                    lastError = null
                )
            )
        }
    }

    suspend fun archiveSavingsGoal(id: String, mutationId: String) {
        require(mutationId.isNotBlank()) { "mutationId is required" }
        db.withTransaction {
            val current = db.savingsGoalDao().getById(id)
            require(current != null && current.archivedAt == null) { "savings goal not found or already archived" }
            db.savingsGoalDao().archive(id, System.currentTimeMillis())
            db.pendingMutationDao().insert(
                PendingMutationEntity(
                    mutationId = mutationId,
                    entityType = "savings_goal",
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

    // ─── Scheduled Rules（T-M7-01 起上云：云端权威 + 本地副本） ──
    //
    // 与 book/account 等一致：写操作与 pending mutation 同事务。引擎按规则
    // **生成的交易**走交易表（source='scheduled'），规则本身的上行不与之冲突。
    // last_run_date 上云后多设备共享——设备 A 生成后上行，设备 B pull 到即
    // 不再重复生成（跨设备去重），这是期望语义而非数据污染。

    suspend fun listScheduledRules(): List<ScheduledRuleEntity> = db.scheduledRuleDao().listActive()

    suspend fun getScheduledRule(id: String): ScheduledRuleEntity? = db.scheduledRuleDao().getById(id)

    /**
     * 规则底线校验——repeat/type 枚举、日期格式与 endDate≥startDate 成序、
     * 执行时刻值域（T14-5 / REQ-43），以及**上云列宽上限**（T-M7-01：
     * name ≤64 / note ≤2000，对齐服务端 scheduled_rules 列宽——超长会在 push
     * 时 422 中止整批，故在此唯一把关）。
     * 桥层（handleScheduledInsert/Update）只做形状与必填解析，枚举与成序
     * 在此唯一把关（镜像 requireValidSavingsPeriod 的"防绕过"定位）。
     */
    private fun requireValidScheduledRule(rule: ScheduledRuleEntity) {
        require(rule.id.isNotBlank() && rule.name.isNotBlank()) { "rule id/name required" }
        require(rule.name.length <= SCHEDULED_NAME_WIRE_MAX_LEN) { "rule name too long" }
        require(rule.note.length <= NOTE_WIRE_MAX_LEN) { "rule note too long" }
        require(rule.amountCents in 1..9007199254740991L) { "amountCents must be a positive safe integer" }
        require(rule.type in setOf("income", "expense")) { "invalid rule type: ${rule.type}" }
        require(rule.repeat in setOf("daily", "weekly", "monthly", "yearly")) { "invalid repeat: ${rule.repeat}" }
        require(rule.runHour in 0..23) { "invalid runHour: ${rule.runHour}" }
        require(rule.runMinute in 0..59) { "invalid runMinute: ${rule.runMinute}" }
        require(isoDateOrNullOr(rule.startDate)) { "invalid startDate: ${rule.startDate}" }
        if (rule.endDate != null) {
            require(isoDateOrNullOr(rule.endDate)) { "invalid endDate: ${rule.endDate}" }
            require(LocalDate.parse(rule.endDate) >= LocalDate.parse(rule.startDate)) {
                "endDate must not precede startDate"
            }
        }
        if (rule.lastRunDate != null) {
            require(isoDateOrNullOr(rule.lastRunDate)) { "invalid lastRunDate: ${rule.lastRunDate}" }
        }
    }

    private fun isoDateOrNullOr(value: String): Boolean =
        Regex("\\d{4}-\\d{2}-\\d{2}").matches(value) && runCatching { LocalDate.parse(value) }.isSuccess

    suspend fun insertScheduledRule(rule: ScheduledRuleEntity, mutationId: String) {
        require(mutationId.isNotBlank()) { "mutationId is required" }
        require(rule.version > 0 && rule.archivedAt == null) { "invalid rule insert" }
        requireValidScheduledRule(rule)
        db.withTransaction {
            db.scheduledRuleDao().insert(rule)
            db.pendingMutationDao().insert(
                PendingMutationEntity(
                    mutationId = mutationId,
                    entityType = "scheduled_rule",
                    entityId = rule.id,
                    operation = PendingMutationEntity.OP_INSERT,
                    payload = scheduledRuleToJson(rule),
                    status = PendingMutationEntity.STATUS_PENDING,
                    retryCount = 0,
                    createdAt = System.currentTimeMillis(),
                    expectedVersion = null,
                    lastError = null
                )
            )
        }
    }

    suspend fun updateScheduledRule(rule: ScheduledRuleEntity, mutationId: String) {
        require(mutationId.isNotBlank()) { "mutationId is required" }
        requireValidScheduledRule(rule)
        db.withTransaction {
            val current = db.scheduledRuleDao().getById(rule.id)
            require(current != null) { "scheduled rule not found" }
            require(current.archivedAt == null) { "scheduled rule is archived" }
            require(rule.version > current.version) { "version must increase" }
            db.scheduledRuleDao().update(rule)
            db.pendingMutationDao().insert(
                PendingMutationEntity(
                    mutationId = mutationId,
                    entityType = "scheduled_rule",
                    entityId = rule.id,
                    operation = PendingMutationEntity.OP_UPDATE,
                    payload = scheduledRuleToJson(rule),
                    status = PendingMutationEntity.STATUS_PENDING,
                    retryCount = 0,
                    createdAt = System.currentTimeMillis(),
                    expectedVersion = current.version,
                    lastError = null
                )
            )
        }
    }

    suspend fun archiveScheduledRule(id: String, mutationId: String) {
        require(mutationId.isNotBlank()) { "mutationId is required" }
        db.withTransaction {
            val current = db.scheduledRuleDao().getById(id)
            require(current != null && current.archivedAt == null) { "scheduled rule not found or already archived" }
            db.scheduledRuleDao().archive(id, System.currentTimeMillis())
            db.pendingMutationDao().insert(
                PendingMutationEntity(
                    mutationId = mutationId,
                    entityType = "scheduled_rule",
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

    // ─── Chat Personas（T-M7-01 起上云：云端权威 + 本地副本） ───
    //
    // 与 book/account 等一致：写操作与 pending mutation 同事务。本地表无 ownerId
    // 列（服务端按 token 归属），故 pull/bootstrap 回填不带归属字段。
    // 播种的 5 个内置角色同样入队上行——换机后可恢复默认角色集。

    /**
     * T11-7（决策 #8）：内置 5 角色幂等播种，标记 = meta personas_seeded='1'：
     * 首装播种；升级安装（键缺失）补种；用户删光后 mark 仍为 '1' → 不重播
     * （删除即意愿，镜像 master_book_id 的"自管键"模式，不开放前端写）。
     * 种子角色全部可编辑——mark 只锁"重播"，不锁行。
     */
    suspend fun ensureSeedPersonas() {
        if (getMeta(SyncMetaEntity.KEY_PERSONAS_SEEDED) == "1") return
        db.withTransaction {
            if (db.chatPersonaDao().listActive().isEmpty()) {
                val now = System.currentTimeMillis()
                SEED_PERSONAS.forEach { (name, avatar, text) ->
                    val entity = ChatPersonaEntity(
                        id = java.util.UUID.randomUUID().toString(),
                        name = name,
                        avatar = avatar,
                        persona = text,
                        createdAt = now,
                        updatedAt = now,
                        archivedAt = null,
                        version = 1
                    )
                    db.chatPersonaDao().insert(entity)
                    // T-M7-01：种子角色同样上行（换机恢复默认角色集）。此处在
                    // Room 事务内直接入队（不经 insertChatPersona），避免逐条嵌套事务。
                    db.pendingMutationDao().insert(
                        PendingMutationEntity(
                            mutationId = java.util.UUID.randomUUID().toString(),
                            entityType = "chat_persona",
                            entityId = entity.id,
                            operation = PendingMutationEntity.OP_INSERT,
                            payload = chatPersonaToJson(entity),
                            status = PendingMutationEntity.STATUS_PENDING,
                            retryCount = 0,
                            createdAt = now,
                            expectedVersion = null,
                            lastError = null
                        )
                    )
                }
            }
            setMeta(SyncMetaEntity.KEY_PERSONAS_SEEDED, "1")
        }
    }

    suspend fun listChatPersonas(): List<ChatPersonaEntity> = db.chatPersonaDao().listActive()

    suspend fun getChatPersona(id: String): ChatPersonaEntity? = db.chatPersonaDao().getById(id)

    /**
     * 角色底线校验——昵称 1–16 字、人设文本 ≤2000 字（决策 #8）。
     * 桥层只做形状解析，长度上限在此唯一把关（镜像 requireValidScheduledRule）。
     */
    private fun requireValidChatPersona(persona: ChatPersonaEntity) {
        require(persona.id.isNotBlank() && persona.name.isNotBlank()) { "persona id/name required" }
        require(persona.name.length <= MAX_PERSONA_NAME_LEN) {
            "persona name too long (max $MAX_PERSONA_NAME_LEN chars)"
        }
        require(persona.persona.length <= MAX_PERSONA_TEXT_LEN) {
            "persona text too long (max $MAX_PERSONA_TEXT_LEN chars)"
        }
    }

    suspend fun insertChatPersona(persona: ChatPersonaEntity, mutationId: String) {
        require(mutationId.isNotBlank()) { "mutationId is required" }
        require(persona.version > 0 && persona.archivedAt == null) { "invalid persona insert" }
        requireValidChatPersona(persona)
        db.withTransaction {
            db.chatPersonaDao().insert(persona)
            db.pendingMutationDao().insert(
                PendingMutationEntity(
                    mutationId = mutationId,
                    entityType = "chat_persona",
                    entityId = persona.id,
                    operation = PendingMutationEntity.OP_INSERT,
                    payload = chatPersonaToJson(persona),
                    status = PendingMutationEntity.STATUS_PENDING,
                    retryCount = 0,
                    createdAt = System.currentTimeMillis(),
                    expectedVersion = null,
                    lastError = null
                )
            )
        }
    }

    suspend fun updateChatPersona(persona: ChatPersonaEntity, mutationId: String) {
        require(mutationId.isNotBlank()) { "mutationId is required" }
        requireValidChatPersona(persona)
        db.withTransaction {
            val current = db.chatPersonaDao().getById(persona.id)
            require(current != null) { "chat persona not found" }
            require(current.archivedAt == null) { "chat persona is archived" }
            require(persona.version > current.version) { "version must increase" }
            db.chatPersonaDao().update(persona)
            db.pendingMutationDao().insert(
                PendingMutationEntity(
                    mutationId = mutationId,
                    entityType = "chat_persona",
                    entityId = persona.id,
                    operation = PendingMutationEntity.OP_UPDATE,
                    payload = chatPersonaToJson(persona),
                    status = PendingMutationEntity.STATUS_PENDING,
                    retryCount = 0,
                    createdAt = System.currentTimeMillis(),
                    expectedVersion = current.version,
                    lastError = null
                )
            )
        }
    }

    suspend fun archiveChatPersona(id: String, mutationId: String) {
        require(mutationId.isNotBlank()) { "mutationId is required" }
        db.withTransaction {
            val current = db.chatPersonaDao().getById(id)
            require(current != null && current.archivedAt == null) { "chat persona not found or already archived" }
            db.chatPersonaDao().archive(id, System.currentTimeMillis())
            db.pendingMutationDao().insert(
                PendingMutationEntity(
                    mutationId = mutationId,
                    entityType = "chat_persona",
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
        // T11-1：handler 现允许改 budgetType/period，补齐与 insertBudget 同级的形态校验
        require(budget.budgetType in setOf("monthly", "annual")) { "invalid budgetType" }
        require(when (budget.budgetType) {
            "monthly" -> Regex("\\d{4}-\\d{2}").matches(budget.period)
            "annual" -> Regex("\\d{4}").matches(budget.period)
            else -> false
        }) { "invalid period format" }
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

    /**
     * T11-1（REQ-23 / 第十一轮决策 #2）：预算沿用 —— 双触发幂等复制。
     * 开关（budget_carryover='1'）ON 时，对每个非 master 账本：
     * mark[book] ≠ 当前月 且 本月 monthly 预算为空 且 上月 monthly 预算非空
     * → 逐条复制（新 UUID id、period=当前月、同分类/金额，OP_INSERT mutation 正常上行）
     * 并更新 mark=当前月。年度预算不参与；用户手动删光本月预算后（mark 已=当月）不再生成。
     * 触发点：AozoraApp.onCreate（冷启动）。
     */
    suspend fun ensureBudgetCarryover() {
        if (getMeta(SyncMetaEntity.KEY_BUDGET_CARRYOVER) != "1") return
        val now = java.time.YearMonth.now()
        val curPeriod = now.toString()          // "YYYY-MM"
        val prevPeriod = now.minusMonths(1).toString()
        val masterId = getMasterBookId()
        val mark = try {
            JSONObject(getMeta(SyncMetaEntity.KEY_BUDGET_CARRYOVER_MARK) ?: "{}")
        } catch (e: JSONException) {
            JSONObject()
        }
        for (book in listBooks()) {
            if (book.id == masterId) continue
            if (mark.optString(book.id, null) == curPeriod) continue
            val cur = db.budgetDao().listByBookAndPeriod(book.id, curPeriod)
                .filter { it.budgetType == "monthly" }
            if (cur.isNotEmpty()) continue
            val prev = db.budgetDao().listByBookAndPeriod(book.id, prevPeriod)
                .filter { it.budgetType == "monthly" }
            if (prev.isEmpty()) continue
            // 同一事务：复制 + 写 mark 原子落，避免半程崩溃后 mark 未更新但已复制
            db.withTransaction {
                prev.forEach { src ->
                    insertBudget(
                        src.copy(
                            id = java.util.UUID.randomUUID().toString(),
                            period = curPeriod,
                            version = 1
                        ),
                        java.util.UUID.randomUUID().toString()
                    )
                }
                mark.put(book.id, curPeriod)
                setMeta(SyncMetaEntity.KEY_BUDGET_CARRYOVER_MARK, mark.toString())
            }
        }
    }

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
            "budgets" to budgets,
            // T-M7-01: 三实体上云（bootstrap 计数校验需要同一口径）
            "savings_goals" to db.savingsGoalDao().listActive().sortedBy { it.id },
            "scheduled_rules" to db.scheduledRuleDao().listActive().sortedBy { it.id },
            "chat_personas" to db.chatPersonaDao().listActive().sortedBy { it.id },
            // T-M7-05j: 偏好行无归档概念 → 全量即活跃
            "user_preferences" to db.userPreferenceDao().listAll().sortedBy { it.id }
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
            // T-M7-01: 三实体上云后同样属于"云端权威"数据，随快照重建。
            // 调用方（SyncEngine）必须先执行 migrateCloudEntitiesUp()，把本地
            // 独有的存量数据入队为 insert mutation —— 否则本地独有数据会在
            // 清空后永久丢失。
            db.savingsGoalDao().clearAll()
            db.scheduledRuleDao().clearAll()
            db.chatPersonaDao().clearAll()
            // T-M7-05j: 偏好镜像行同属"云端权威"数据。注意 sync_meta 里的偏好值是
            // 真身、不在此清空范围内 —— 本地的值由 migratePreferencesUp 入队上行，
            // 云端快照里的值由 applyBootstrap 覆盖回 sync_meta。
            db.userPreferenceDao().clearAll()
            // P2-2 注释：崩溃恢复锚点。若 applyBootstrap 未完成时崩溃，
            // cursor=0 + 无实体 ⇒ needsBootstrap()=true ⇒ 下次启动重试 bootstrap。
            db.syncMetaDao().upsert(
                SyncMetaEntity(SyncMetaEntity.KEY_CURSOR, "0")
            )
        }
    }

    /**
     * T-M7-01: 存量本地数据一次性上行（旧版本客户端把 savings/scheduled/personas
     * 仅存本地，升级后这些数据在云端尚不存在）。
     *
     * 契约：
     * - meta 标记 `cloud_entities_uploaded_v1` 一旦写入即不再重复入队；标记与
     *   mutation 在同一 Room 事务内落盘，崩溃不会留下"标记已写但 mutation 未入队"。
     * - **只上行活跃实体**：归档＝用户已删除，换机不恢复符合预期，也避免为每条
     *   归档行多产生 insert+delete 两条 mutation。
     * - 必须在 [clearAllEntities]（bootstrap 重建）之前调用——清空会连同这三张表
     *   一起清掉，本地独有数据只能靠已入队的 mutation 存活到 push 之后。
     * - 服务端对这三类的 insert 是幂等的（同 id 已存在即返回现有版本、不覆盖），
     *   故第二台设备推同一批 id 既不会冲突也不会覆盖云端副本。
     * - 登出/删号走 [clearAllData]（clearAllTables），标记随 sync_meta 一并清空；
     *   换账号后按新账号重新判定，不会把上一账号的数据带给新账号。
     */
    suspend fun migrateCloudEntitiesUp() {
        if (getMeta(SyncMetaEntity.KEY_CLOUD_ENTITIES_UPLOADED) == "1") return
        db.withTransaction {
            // 事务内二次检查：并发触发时以先落盘者为准
            if (db.syncMetaDao().getValue(SyncMetaEntity.KEY_CLOUD_ENTITIES_UPLOADED) == "1") {
                return@withTransaction
            }
            for (goal in db.savingsGoalDao().listActive()) {
                enqueueInsert("savings_goal", goal.id, savingsGoalToJson(goal))
            }
            for (rule in db.scheduledRuleDao().listActive()) {
                enqueueInsert("scheduled_rule", rule.id, scheduledRuleToJson(rule))
            }
            for (persona in db.chatPersonaDao().listActive()) {
                enqueueInsert("chat_persona", persona.id, chatPersonaToJson(persona))
            }
            setMeta(SyncMetaEntity.KEY_CLOUD_ENTITIES_UPLOADED, "1")
        }
    }

    /**
     * T-M7-05j: 存量本地偏好值一次性上行（旧版本客户端把 6 个白名单键只存 sync_meta，
     * 从不上行；升级后云端没有这些行）。
     *
     * 契约（镜像 [migrateCloudEntitiesUp]）：
     * - 标记 `preferences_uploaded_v1` 一旦写入即不再重复入队；标记与 mutation 在
     *   同一 Room 事务内落盘，崩溃不会留下"标记已写但 mutation 未入队"。
     * - **只上行实际存在的键**：book_order / account_order / widget_config /
     *   user_profile 多数账号根本没写过，缺失即跳过（缺键不等于"空值"）。
     * - `default_book_id` / `default_account_id` 用的就是它们在 sync_meta 里的存储键名。
     * - 必须在 [clearAllEntities]（bootstrap 重建）之前调用 —— 它清空 user_preferences
     *   镜像表，本地独有值只能靠已入队的 mutation 存活到 push 之后。
     * - 服务端对该类的首推幂等（命中 (user_id, key) 已存在时不覆盖），第二台设备推
     *   同一批键既不会冲突也不会覆盖云端副本。
     * - 登出/删号走 clearAllData（clearAllTables），标记随 sync_meta 一并清空。
     */
    suspend fun migratePreferencesUp() {
        if (getMeta(SyncMetaEntity.KEY_PREFERENCES_UPLOADED) == "1") return
        db.withTransaction {
            // 事务内二次检查：并发触发时以先落盘者为准
            if (db.syncMetaDao().getValue(SyncMetaEntity.KEY_PREFERENCES_UPLOADED) == "1") {
                return@withTransaction
            }
            for (key in SyncMetaEntity.SYNCABLE_PREFERENCE_KEYS) {
                val value = db.syncMetaDao().getValue(key) ?: continue
                enqueuePreferenceUpsert(key, value)
            }
            setMeta(SyncMetaEntity.KEY_PREFERENCES_UPLOADED, "1")
        }
    }

    /**
     * T18-10（REQ-72）：一次性游标重置——让解析修复（ISO→epoch 双收）后，
     * 历史已丢失的服务端 transaction 通过全量重放回到本地 Room。
     *
     * 背景：自 T-M3 起，服务端 `_serialize_entity` 把 `occurred_at` 出网成 ISO 字符串，
     * 而客户端 `toTransaction()` 只认 `toLongOrNull()` → null → 调用方 `?: continue`
     * 静默丢弃，**游标照常推进** → 数据永久丢失。解析修复（epoch/ISO 双收）让新数据
     * 正常落地，但历史漏掉的行不会自己回来。本方法把 cursor 置 0，触发一次从起点
     * 开始的全量 pull，让修复后的路径把漏掉的行全部拉回来。
     *
     * 契约（镜像 [migrateCloudEntitiesUp] / [migratePreferencesUp]）：
     * - 标记与 cursor 重置在同一事务内，崩溃安全。
     * - 只做一次：`pull_iso_fix_applied_v1 = '1'` 之后永不再重置。
     * - 调用方必须保证解析修复已生效（否则等于再丢一次）。SyncEngine 在 pull 之前调用，
     *   此时 pull 路径的 ISO 双收已编译进二进制，满足顺序要求。
     * - 重放安全：applyServerChanges 的 upsert 天然幂等；DELETE 分支走归档/物删，
     *   已删除的行再删一次仍是删除态。
     */
    suspend fun applyOneTimeCursorResetIfNeeded() {
        if (getMeta(SyncMetaEntity.KEY_PULL_ISO_FIX_APPLIED) == "1") return
        db.withTransaction {
            // 事务内二次检查：并发触发时以先落盘者为准
            if (db.syncMetaDao().getValue(SyncMetaEntity.KEY_PULL_ISO_FIX_APPLIED) == "1") {
                return@withTransaction
            }
            db.syncMetaDao().upsert(
                SyncMetaEntity(SyncMetaEntity.KEY_CURSOR, "0")
            )
            db.syncMetaDao().upsert(
                SyncMetaEntity(SyncMetaEntity.KEY_PULL_ISO_FIX_APPLIED, "1")
            )
            Log.i(
                "Repository",
                "T18-10: sync_cursor reset to 0 for REQ-72 pull fix backfill; " +
                    "next pull will replay all server changes from origin"
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
            // T-M7-01: 三实体上云 —— 同 P2-1 断言：bootstrap 只重建活跃实体。
            for (goalRaw in response.savingsGoals) {
                val entity = serverChangeMapToSavingsGoal(goalRaw, ownerId) ?: continue
                check(entity.archivedAt == null) {
                    "bootstrap savings goal ${entity.id} has non-null archivedAt"
                }
                db.savingsGoalDao().upsertFromSync(entity)
            }
            for (ruleRaw in response.scheduledRules) {
                val entity = serverChangeMapToScheduledRule(ruleRaw, ownerId) ?: continue
                check(entity.archivedAt == null) {
                    "bootstrap scheduled rule ${entity.id} has non-null archivedAt"
                }
                db.scheduledRuleDao().upsertFromSync(entity)
            }
            for (personaRaw in response.chatPersonas) {
                val entity = serverChangeMapToChatPersona(personaRaw) ?: continue
                check(entity.archivedAt == null) {
                    "bootstrap chat persona ${entity.id} has non-null archivedAt"
                }
                db.chatPersonaDao().upsertFromSync(entity)
            }
            // T-M7-05j: 偏好行（无归档/删除语义，故无 P2-1 断言）+ 值回灌 sync_meta
            for (prefRaw in response.userPreferences) {
                val entity = serverChangeMapToUserPreference(prefRaw) ?: continue
                db.userPreferenceDao().upsert(entity)
                applyPreferenceToMeta(entity.key, entity.value)
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
                            // 服务端 Transaction 无 includeInBudget 列：保留本地标记，避免被 pull 冲刷
                            // （第十轮决策 #1，镜像 category.icon 防冲刷写法）
                            val localFlag = db.transactionDao().getById(entity.id)?.includeInBudget
                            db.transactionDao().upsertFromSync(entity.copy(includeInBudget = localFlag ?: entity.includeInBudget))
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
                            // 服务端 icon ≤64 字符：照片版本地专属，本地 data: 而服务端非 data: → 保留本地（决策 #7②）
                            val localIcon = db.bookDao().getById(entity.id)?.icon
                            val mergedIcon = if (
                                localIcon != null && localIcon.startsWith("data:") &&
                                !entity.icon.startsWith("data:")
                            ) localIcon else entity.icon
                            db.bookDao().upsertFromSync(entity.copy(icon = mergedIcon))
                        }
                    }
                    "category" -> {
                        if (change.operation == SyncChangeOp.DELETE) {
                            db.categoryDao().applyServerArchive(
                                change.entityId, System.currentTimeMillis(), change.version
                            )
                        } else {
                            val entity = change.toCategory(ownerId) ?: continue
                            // REQ-73（T19-1，ADR-014）：icon 两端化后的合并口径，三态 ——
                            //   ① 本地是照片（data:）→ 保留本地：照片图标仅本机可见（决策 #3）；
                            //   ② 服务端有值 → 服务端权威：AI 改图标 / 跨设备改图标只走这条；
                            //   ③ 服务端为 null → 保留本地。category.icon 的 null 有语义
                            //      （按名映射 emoji），且存量行服务端全是 NULL；把「无信息」
                            //      当「清空」会在一次 pull 后抹掉本地已有的 emoji 图标。
                            //      与 accounts.account_type 同口径（T18-5）。
                            val localIcon = db.categoryDao().getById(entity.id)?.icon
                            val mergedIcon = if (
                                localIcon != null && localIcon.startsWith("data:") &&
                                entity.icon?.startsWith("data:") != true
                            ) localIcon else entity.icon ?: localIcon
                            db.categoryDao().upsertFromSync(entity.copy(icon = mergedIcon))
                        }
                    }
                    "account" -> {
                        if (change.operation == SyncChangeOp.DELETE) {
                            db.accountDao().applyServerArchive(
                                change.entityId, System.currentTimeMillis(), change.version
                            )
                        } else {
                            val entity = change.toAccount(ownerId) ?: continue
                            // 同 book：本地照片 icon 不被 pull 冲刷（决策 #7②）
                            val local = db.accountDao().getById(entity.id)
                            val mergedIcon = if (
                                local != null && local.icon.startsWith("data:") &&
                                !entity.icon.startsWith("data:")
                            ) local.icon else entity.icon
                            // REQ-69：accountType 两端化后服务端是权威，但**服务端空值
                            // 不得冲掉本地值** —— 存量行 account_type 仍是 NULL，而本地
                            // 早就存着用户选的分类（「储蓄卡」），照单全收就让它在一次
                            // pull 后消失（这正是本卡之前它在同步中丢掉的形态）。
                            // balanceCents 刻意**不做**这层保护：0 是合法的服务端值
                            // （未校准），保护它会永远吃不下服务端的 0；本地乐观写入
                            // 由上方「活跃 mutation 跳过」兜住（决策 #4：冲突以服务端为权威）。
                            val mergedType = entity.accountType ?: local?.accountType
                            db.accountDao().upsertFromSync(
                                entity.copy(icon = mergedIcon, accountType = mergedType)
                            )
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
                    // ─── T-M7-01: 三实体上云（delete → 归档，镜像 category/account） ───
                    "savings_goal" -> {
                        if (change.operation == SyncChangeOp.DELETE) {
                            db.savingsGoalDao().applyServerArchive(
                                change.entityId, System.currentTimeMillis(), change.version
                            )
                        } else {
                            val entity = change.toSavingsGoal(ownerId) ?: continue
                            // 照片 icon 本地专属：服务端存的 64 字符版不冲刷本地 data: 版本（决策 #7②）
                            val localIcon = db.savingsGoalDao().getById(entity.id)?.icon
                            val mergedIcon = if (
                                localIcon != null && localIcon.startsWith("data:") &&
                                entity.icon?.startsWith("data:") != true
                            ) localIcon else entity.icon
                            db.savingsGoalDao().upsertFromSync(entity.copy(icon = mergedIcon))
                        }
                    }
                    "scheduled_rule" -> {
                        if (change.operation == SyncChangeOp.DELETE) {
                            db.scheduledRuleDao().applyServerArchive(
                                change.entityId, System.currentTimeMillis(), change.version
                            )
                        } else {
                            val entity = change.toScheduledRule(ownerId) ?: continue
                            db.scheduledRuleDao().upsertFromSync(entity)
                        }
                    }
                    "chat_persona" -> {
                        if (change.operation == SyncChangeOp.DELETE) {
                            db.chatPersonaDao().applyServerArchive(
                                change.entityId, System.currentTimeMillis(), change.version
                            )
                        } else {
                            val entity = change.toChatPersona() ?: continue
                            // 同 book/account：本地照片头像不被 pull 冲刷（决策 #7②）
                            val localAvatar = db.chatPersonaDao().getById(entity.id)?.avatar
                            val mergedAvatar = if (
                                localAvatar != null && localAvatar.startsWith("data:") &&
                                entity.avatar?.startsWith("data:") != true
                            ) localAvatar else entity.avatar
                            db.chatPersonaDao().upsertFromSync(entity.copy(avatar = mergedAvatar))
                        }
                    }
                    // ─── T-M7-05j: 偏好项上云（delete → 物理删，镜像 budget 而非三实体） ───
                    "user_preference" -> {
                        if (change.operation == SyncChangeOp.DELETE) {
                            // 服务端无软删，客户端同样物删。删除变更不带 entity，
                            // 故先按 id 取本地镜像行拿 key，才能把 sync_meta 里的
                            // 同名键一起清掉 —— 否则云端已无此行、本地却继续按旧值
                            // 走（例如默认账本），两侧永久分叉。
                            val local = db.userPreferenceDao().getById(change.entityId)
                            db.userPreferenceDao().deleteById(change.entityId)
                            if (local != null &&
                                local.key in SyncMetaEntity.SYNCABLE_PREFERENCE_KEYS
                            ) {
                                db.syncMetaDao().delete(local.key)
                            }
                        } else {
                            val entity = change.toUserPreference() ?: continue
                            db.userPreferenceDao().upsert(entity)
                            // 值回灌 sync_meta —— "AI 改了默认账本 → 本机 UI 跟着变"的路径
                            applyPreferenceToMeta(entity.key, entity.value)
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
            // 第十轮 REQ-17（决策 #1 同步安全）：服务端 _apply_insert 白名单不读
            // 该键 → 静默忽略（accountType 先例）；经 toWirePayload 自动转
            // include_in_budget，push 不炸。
            put("includeInBudget", tx.includeInBudget)
            put("version", tx.version)
        }.toString()
    }

    private fun bookToJson(book: BookEntity): String {
        return JSONObject().apply {
            put("id", book.id)
            put("ownerId", book.ownerId)
            put("name", book.name)
            // 照片版 icon（data: URI 可达 48KB）超服务端 64 上限 → 键省略，防毒丸（决策 #7①）
            if (book.icon.length <= ICON_WIRE_MAX_LEN) put("icon", book.icon)
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
            // REQ-73（T19-1，ADR-014）：icon 两端化。三种情形分开表态 ——
            //   * 照片版（data: URI 可达 48KB）超服务端 64 上限 → 省略键，防整批 push
            //     422 毒丸（决策 #3），照片图标保持「仅本机可见」；
            //   * null → 显式上行（服务端「键在场才改」）：「清除自定义图标」是 IconPicker
            //     里的真实操作，省略键会让它静默失效（旧值下次 pull 又回来）；
            //   * ≤64 字符 → 原样上行。
            val icon = cat.icon
            if (icon == null) {
                put("icon", JSONObject.NULL)
            } else if (icon.length <= ICON_WIRE_MAX_LEN) {
                put("icon", icon)
            }
            put("version", cat.version)
        }.toString()
    }

    private fun accountToJson(account: AccountEntity): String {
        return JSONObject().apply {
            put("id", account.id)
            put("ownerId", account.ownerId)
            put("name", account.name)
            // 同 bookToJson：照片 icon 超长省略键，防 push 422（决策 #7①）
            if (account.icon.length <= ICON_WIRE_MAX_LEN) put("icon", account.icon)
            put("accountType", account.accountType ?: JSONObject.NULL)
            // REQ-69（决策 #4）：余额恒以**绝对值**上行，绝不发增量 ——
            // 重放同一 mutation（离线队列重试 / pull-push 重放）不会重复累加。
            put("balanceCents", account.balanceCents)
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

    // ─── T-M7-01: 三实体上行 payload（camelCase；SyncPayloadMapper 转 snake_case + ISO） ───
    //
    // createdAt/updatedAt 不入 payload —— 服务端用 AuditMixin 自管（镜像 transactionToJson）。
    // startDate/endDate/lastRunDate 已是 'YYYY-MM-DD'，不经 epoch→ISO 转换
    //（DATETIME_FIELDS 只含 datetime 语义键）。

    private fun savingsGoalToJson(goal: SavingsGoalEntity): String {
        return JSONObject().apply {
            put("id", goal.id)
            put("name", goal.name)
            put("targetCents", goal.targetCents)
            put("currentCents", goal.currentCents)
            // 照片版 icon（data: URI）超服务端 64 上限 → 键省略，防毒丸（镜像 bookToJson 决策 #7①）
            if (goal.icon != null && goal.icon.length <= ICON_WIRE_MAX_LEN) put("icon", goal.icon)
            put("periodValue", goal.periodValue ?: JSONObject.NULL)
            put("periodUnit", goal.periodUnit ?: JSONObject.NULL)
            put("version", goal.version)
        }.toString()
    }

    private fun scheduledRuleToJson(rule: ScheduledRuleEntity): String {
        return JSONObject().apply {
            put("id", rule.id)
            put("type", rule.type)
            put("name", rule.name)
            put("amountCents", rule.amountCents)
            put("bookId", rule.bookId)
            put("categoryId", rule.categoryId)
            put("accountId", rule.accountId ?: JSONObject.NULL)
            put("includeInBudget", rule.includeInBudget)
            put("note", rule.note)
            put("repeat", rule.repeat)
            put("startDate", rule.startDate)
            put("endDate", rule.endDate ?: JSONObject.NULL)
            put("enabled", rule.enabled)
            put("lastRunDate", rule.lastRunDate ?: JSONObject.NULL)
            put("runHour", rule.runHour)
            put("runMinute", rule.runMinute)
            put("version", rule.version)
        }.toString()
    }

    private fun chatPersonaToJson(persona: ChatPersonaEntity): String {
        return JSONObject().apply {
            put("id", persona.id)
            put("name", persona.name)
            // 照片头像（data: URI）超服务端 64 上限 → 键省略，照片头像保持设备本地专属
            if (persona.avatar != null && persona.avatar.length <= ICON_WIRE_MAX_LEN) {
                put("avatar", persona.avatar)
            }
            put("persona", persona.persona)
            put("version", persona.version)
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
        // T18-2：同 SyncModels.toTransaction —— 服务端 `occurred_at` 是 ISO 8601，Room 存
        // epoch 毫秒；只认 Long 会让 bootstrap 把快照里的交易**全部跳过**
        // （真机实测：`transactions: expected=4 actual=0`）。epoch 与 ISO 双收。
        val occurredAt = map["occurredAt"]?.toLongOrNull()
            ?: SyncPayloadMapper.isoToEpochMillis(map["occurredAt"])
            ?: return null
        val note = map["note"] ?: ""
        val source = map["source"] ?: "manual"
        val version = map["version"]?.toIntOrNull() ?: 1
        // 第十轮决策 #1：服务端永不返回该键 → 缺省 true（计入）。
        val includeInBudget = map["includeInBudget"]?.toBoolean() ?: true
        return TransactionEntity(
            id = id, bookId = bookId, categoryId = categoryId, accountId = accountId,
            amountCents = amountCents, type = type, bizDate = bizDate,
            occurredAt = occurredAt, note = note, source = source, version = version,
            includeInBudget = includeInBudget,
            deletedAt = map["deletedAt"]?.toLongOrNull()
        )
    }

    private fun serverChangeMapToBook(
        map: Map<String, String?>, ownerId: String
    ): BookEntity? {
        val id = map["id"] ?: return null
        val name = map["name"] ?: return null
        val icon = map["icon"] ?: DefaultIcons.BOOK
        val version = map["version"]?.toIntOrNull() ?: 1
        return BookEntity(
            id = id, ownerId = ownerId, name = name, icon = icon,
            version = version, archivedAt = map["archivedAt"]?.toLongOrNull()
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
            kind = kind, name = name, icon = map["icon"],
            archivedAt = map["archivedAt"]?.toLongOrNull(), version = version
        )
    }

    private fun serverChangeMapToAccount(
        map: Map<String, String?>, ownerId: String
    ): AccountEntity? {
        val id = map["id"] ?: return null
        val name = map["name"] ?: return null
        val icon = map["icon"] ?: DefaultIcons.ACCOUNT
        val version = map["version"]?.toIntOrNull() ?: 1
        return AccountEntity(
            id = id, ownerId = ownerId, name = name, icon = icon,
            accountType = map["accountType"],
            // REQ-69：服务端列缺省（旧服务端 / 未写入）时按 0 回落。
            balanceCents = map["balanceCents"]?.toLongOrNull() ?: 0L,
            archivedAt = map["archivedAt"]?.toLongOrNull(), version = version
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

    // ─── T-M7-01: 三实体 bootstrap 重建 helper ───
    //
    // 服务端的 created_at/updated_at 是 ISO 8601，本地列是 epoch 毫秒，需转换
    //（区别于既有五实体：它们的本地表没有时间戳列）。archived_at 在 bootstrap
    // 中恒为 null（服务端只返回活跃实体），仍走同一解析以防未来放宽。

    private fun isoToEpochMillis(value: String?): Long? =
        SyncPayloadMapper.isoToEpochMillis(value)

    private fun serverChangeMapToSavingsGoal(
        map: Map<String, String?>, ownerId: String
    ): SavingsGoalEntity? {
        val id = map["id"] ?: return null
        val name = map["name"] ?: return null
        val targetCents = map["targetCents"]?.toLongOrNull() ?: return null
        val version = map["version"]?.toIntOrNull() ?: 1
        return SavingsGoalEntity(
            id = id, ownerId = ownerId, name = name, targetCents = targetCents,
            currentCents = map["currentCents"]?.toLongOrNull() ?: 0L,
            icon = map["icon"],
            periodValue = map["periodValue"]?.toIntOrNull(),
            periodUnit = map["periodUnit"],
            createdAt = isoToEpochMillis(map["createdAt"]) ?: System.currentTimeMillis(),
            updatedAt = isoToEpochMillis(map["updatedAt"]) ?: System.currentTimeMillis(),
            archivedAt = isoToEpochMillis(map["archivedAt"]),
            version = version
        )
    }

    private fun serverChangeMapToScheduledRule(
        map: Map<String, String?>, ownerId: String
    ): ScheduledRuleEntity? {
        val id = map["id"] ?: return null
        val name = map["name"] ?: return null
        val type = map["type"] ?: return null
        val amountCents = map["amountCents"]?.toLongOrNull() ?: return null
        val bookId = map["bookId"] ?: return null
        val categoryId = map["categoryId"] ?: return null
        val repeat = map["repeat"] ?: return null
        val startDate = map["startDate"] ?: return null
        val version = map["version"]?.toIntOrNull() ?: 1
        return ScheduledRuleEntity(
            id = id, ownerId = ownerId, type = type, name = name,
            amountCents = amountCents, bookId = bookId, categoryId = categoryId,
            accountId = map["accountId"],
            includeInBudget = map["includeInBudget"]?.toBoolean() ?: true,
            note = map["note"] ?: "",
            repeat = repeat, startDate = startDate, endDate = map["endDate"],
            enabled = map["enabled"]?.toBoolean() ?: true,
            lastRunDate = map["lastRunDate"],
            runHour = map["runHour"]?.toIntOrNull() ?: 8,
            runMinute = map["runMinute"]?.toIntOrNull() ?: 0,
            createdAt = isoToEpochMillis(map["createdAt"]) ?: System.currentTimeMillis(),
            updatedAt = isoToEpochMillis(map["updatedAt"]) ?: System.currentTimeMillis(),
            archivedAt = isoToEpochMillis(map["archivedAt"]),
            version = version
        )
    }

    private fun serverChangeMapToChatPersona(map: Map<String, String?>): ChatPersonaEntity? {
        val id = map["id"] ?: return null
        val name = map["name"] ?: return null
        val version = map["version"]?.toIntOrNull() ?: 1
        return ChatPersonaEntity(
            id = id, name = name,
            avatar = map["avatar"],
            persona = map["persona"] ?: "",
            createdAt = isoToEpochMillis(map["createdAt"]) ?: System.currentTimeMillis(),
            updatedAt = isoToEpochMillis(map["updatedAt"]) ?: System.currentTimeMillis(),
            archivedAt = isoToEpochMillis(map["archivedAt"]),
            version = version
        )
    }

    // ─── T-M7-05j: 偏好项 ───

    private fun serverChangeMapToUserPreference(
        map: Map<String, String?>
    ): UserPreferenceEntity? {
        val id = map["id"] ?: return null
        val key = map["key"] ?: return null
        return UserPreferenceEntity(
            id = id, key = key,
            value = map["value"] ?: "",
            version = map["version"]?.toIntOrNull() ?: 1
        )
    }

    /**
     * T-M7-05j：把云端偏好值回灌本地 sync_meta —— 偏好值的真身在 sync_meta
     * （user_preferences 表只是云端镜像），不写回的话"AI 改了默认账本"落不到 UI。
     *
     * **只写白名单 6 键**：服务端只发这些键，此处再挡一次是"白名单外一个都不动"
     * 这条不变式的落点（设备自管量如 avatar_uri / display_name 永不被云端覆盖）。
     */
    private suspend fun applyPreferenceToMeta(key: String, value: String) {
        if (key !in SyncMetaEntity.SYNCABLE_PREFERENCE_KEYS) return
        db.syncMetaDao().upsert(SyncMetaEntity(key, value))
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
        // 与 domain/nlu/category.ts 内置别名、components/category-emoji.ts 兜底表对齐，
        // 保证聊天短句能命中真实分类行、图标可渲染——三处任一改动都要同步另外两处。
        /** REQ-79（T20-5）：种子分类版本。'1' = 旧的 13 个扁平分类（无二级/无图标），
         *  '2' = 28 支出 + 15 收入 + 二级清单。存量账户按此键判断是否需补齐。 */
        const val CATEGORY_SEED_VERSION = "2"

        /** REQ-79：支出默认分类（28 个一级，照人工测试参考图 #417–#420）。 */
        val DEFAULT_EXPENSE_CATEGORIES: List<SeedCategory> = listOf(
            SeedCategory("餐饮", "🍽️"),
            SeedCategory("休闲娱乐", "🎮"),
            SeedCategory("购物", "🛍️"),
            SeedCategory("穿搭美容", "💄"),
            SeedCategory("水果零食", "🍎"),
            SeedCategory(
                "交通", "🚌",
                listOf(
                    SeedCategory("公交", "🚌"),
                    SeedCategory("打车", "🚕"),
                    SeedCategory("地铁", "🚇"),
                    SeedCategory("火车", "🚆"),
                    SeedCategory("飞机", "✈️"),
                )
            ),
            SeedCategory("生活日用", "🧴"),
            SeedCategory(
                "人情社交", "🎁",
                listOf(
                    SeedCategory("职场", "💼"),
                    SeedCategory("亲戚", "👪"),
                    SeedCategory("借用", "🤝"),
                    SeedCategory("借出", "📤"),
                    SeedCategory("回礼", "🎀"),
                )
            ),
            SeedCategory("宠物", "🐱"),
            SeedCategory("养娃", "🍼"),
            SeedCategory("运动", "⚽"),
            SeedCategory("生活服务", "🛠️"),
            SeedCategory("买菜", "🥬"),
            SeedCategory("住房", "🏠"),
            SeedCategory("爱车", "🚗"),
            SeedCategory("发红包", "🧧"),
            SeedCategory("转账", "💸"),
            SeedCategory("学习教育", "📚"),
            SeedCategory("网络虚拟", "💻"),
            SeedCategory("烟酒", "🚬"),
            SeedCategory("医疗保健", "🏥"),
            SeedCategory("金融保险", "🏦"),
            SeedCategory("家居家电", "🛋️"),
            SeedCategory("酒店旅行", "🏨"),
            SeedCategory("公益", "❤️"),
            SeedCategory("互助保障", "🤲"),
            SeedCategory("其他", "📌"),
            SeedCategory("约会", "💕"),
        )

        /** REQ-79：收入默认分类（15 个一级）。 */
        val DEFAULT_INCOME_CATEGORIES: List<SeedCategory> = listOf(
            SeedCategory("工资", "💰"),
            SeedCategory("兼职", "🧰"),
            SeedCategory("投资理财", "📈"),
            SeedCategory("人情社交", "🎁"),
            SeedCategory("奖金补贴", "🏆"),
            SeedCategory("报销", "🧾"),
            SeedCategory("生意", "🏪"),
            SeedCategory("卖二手", "📦"),
            SeedCategory("生活费", "🍚"),
            SeedCategory("中奖", "🎰"),
            SeedCategory("收红包", "🧧"),
            SeedCategory("收转账", "💸"),
            SeedCategory("保险理赔", "🛡️"),
            SeedCategory("退款", "↩️"),
            SeedCategory("其他", "📌"),
        )
        /** 服务端 book/account icon 列上限（String(64)，_validate_name 强校验）——
         *  照片 data URI 超长 push 必 422 成毒丸，见第九轮决策 #7。 */
        private const val ICON_WIRE_MAX_LEN = 64

        /** T-M7-01：三实体上云的列宽上限（对齐服务端列，超长 push 必 422 毒丸）。
         *  savings_goals.name / scheduled_rules.name / scheduled_rules.note /
         *  chat_personas.persona 分别见 migrations/versions/0016_cloud_entities.py。 */
        private const val NAME_WIRE_MAX_LEN = 128
        private const val SCHEDULED_NAME_WIRE_MAX_LEN = 64
        private const val NOTE_WIRE_MAX_LEN = 2000

        /** T11-7（REQ-28 / 决策 #8）：角色昵称 ≤16 字、人设文本 ≤2000 字（应用层校验）。 */
        const val MAX_PERSONA_NAME_LEN = 16
        const val MAX_PERSONA_TEXT_LEN = 2000

        /**
         * T11-7：首版内置 5 角色（决策 #8，全部可编辑）。人设文本 100–200 字，
         * 注入在线 AI system 消息尾部（persona 段，≤2000 服务端二次截断）。
         * avatar 统一用 emoji（照片头像由用户自建/编辑后经 IconPicker 覆写）。
         */
        val SEED_PERSONAS: List<Triple<String, String, String>> = listOf(
            Triple(
                "小贝", "😊",
                "你是小贝，一个温柔简洁的记账搭子。你说话轻声细语，从不评判或说教用户的花销，" +
                    "而是先共情再给一句贴心建议。你擅长把复杂的账目说得轻松好懂，遇到用户焦虑时" +
                    "会安抚情绪、鼓励小额坚持。每次回复都很短，通常一两句话点到位，不啰嗦、不堆砌数据。" +
                    "你记得用户说过的生活细节，会在合适的时候温柔提起，让记账像和朋友聊天一样自然放松。"
            ),
            Triple(
                "老陈", "🧐",
                "你是老陈，一名严谨的财务顾问，尤其爱抠细节。你会追问每一笔支出的合理性：这笔钱" +
                    "有没有凭证、能不能报销、是不是重复消费、有没有更省的方式。你习惯用具体数字和" +
                    "百分比说话，喜欢把账目拆到分类和周期，指出预算超支的苗头。你的语气专业、直接、" +
                    "略带较真，但出发点始终帮用户守住钱袋子。发现漏洞你会明确点出，不含糊、不敷衍。"
            ),
            Triple(
                "饭团", "😋",
                "你是饭团，一个幽默的吃货伙伴。你把所有消费都往吃喝玩乐上联想，说话俏皮爱开玩笑，" +
                    "气氛担当。一笔奶茶你能夸成人生赢家，一顿火锅你能认真计算有几碗米饭的钱。" +
                    "可玩笑归玩笑，你也会在用户疯狂剁手时用段子轻轻拽一把，提醒留着下顿饭钱。" +
                    "你让用户记账时不那么紧绷，把枯燥的收支变成一桩好笑的生活小事，陪ta边笑边把钱花明白。"
            ),
            Triple(
                "阿娘", "🧵",
                "你是阿娘，一位爱操心、节俭的长辈。你把用户的每一笔开销都当成自家的日子来盘算，" +
                    "嘴上念叨着别乱花钱、要攒钱过日子，心里全是关心。你会回忆从前物资紧缺的日子" +
                    "提醒用户珍惜，会惦记用户吃没吃好、冷暖、有没有乱熬夜花冤枉钱。你的语气絮叨" +
                    "又温暖，带着家长式的唠叨和实打实的省钱建议，让人一边嫌啰嗦一边心里发暖。"
            ),
            Triple(
                "冷面管家", "🧊",
                "你是冷面管家，一个极简的数据流。你惜字如金，只报告事实：金额、分类、余额、" +
                    "预算剩余、趋势变化，一律用最短的陈述句给出，不带情绪、不说废话、不寒暄。" +
                    "你偶尔用一句冷幽默点缀，但绝不铺陈。你把复杂账目压缩成几行干净的数据，" +
                    "让用户一眼看清钱去哪了。多余修饰会被你删掉，专业、克制、准确是你的信条。" +
                    "若用户追问，你才多给一句解释，点到为止。"
            ),
        )
    }
}

/**
 * REQ-79（T20-5）：种子分类 = 名称 + 图标 + 二级。
 *
 * 图标随种子直接上行（emoji 远小于服务端 icon 列 64 字符上限），新账户开箱即有
 * 图标；二级在父插入拿到真实 UUID **之后**再插（父 id 是随机 UUID，不能预置）。
 */
data class SeedCategory(
    val name: String,
    val icon: String,
    val children: List<SeedCategory> = emptyList(),
)
