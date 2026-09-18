package com.aozora.app.sync

import androidx.room.Room
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.aozora.app.auth.AuthClient
import com.aozora.app.auth.TokenStore
import com.aozora.app.db.AozoraDatabase
import com.aozora.app.db.Repository
import com.aozora.app.db.entity.BookEntity
import com.aozora.app.db.entity.CategoryEntity
import com.aozora.app.db.entity.PendingMutationEntity
import com.aozora.app.db.entity.TransactionEntity
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/**
 * T-M3-02: SyncEngine 集成测试（in-memory Room + fake SyncApi/AuthClient）。
 *
 * 验收：
 * - ① 飞行模式后恢复 → push 成功
 * - ③ 连续点击不重复（expectedVersion 校验 + pull-echo 合并）
 */
@RunWith(AndroidJUnit4::class)
class SyncEngineTest {

    private lateinit var db: AozoraDatabase
    private lateinit var repo: Repository
    private lateinit var authClient: FakeAuthClient
    private lateinit var syncApi: FakeSyncApi
    private lateinit var engine: SyncEngine

    @Before
    fun setUp() {
        val ctx = InstrumentationRegistry.getInstrumentation().targetContext
        db = Room.inMemoryDatabaseBuilder(ctx, AozoraDatabase::class.java).build()
        repo = Repository(db)
        authClient = FakeAuthClient(userId = "u1")
        syncApi = FakeSyncApi()
        engine = SyncEngine(repo, syncApi, authClient)
    }

    @After
    fun tearDown() {
        db.close()
    }

    /**
     * 验收①：飞行模式恢复后同步成功。
     *
     * 步骤：
     * 1. 用户离线时写入本地 transaction（mutation 入队）。
     * 2. "恢复网络"：让 fake SyncApi 开始接受 push。
     * 3. 触发 SyncEngine.runOnce()。
     * 4. 验证：mutation 被移除，cursor 推进，push 被调用至少一次。
     */
    @Test
    fun offlineRecordSyncsAfterNetworkRecovery() = runBlocking {
        seedBookAndCategory()
        val tx = TransactionEntity(
            id = "t-offline", bookId = "b1", categoryId = "c1", accountId = null,
            amountCents = 500L, type = "expense", bizDate = "2026-09-05",
            occurredAt = 1000L, note = "offline lunch", source = "manual",
            version = 1, deletedAt = null
        )
        repo.insertTransaction(tx, "m-offline")
        assertEquals(1, repo.listPendingMutations().size)

        // "恢复网络"：fake 接受所有 push
        syncApi.pushBehavior = FakeSyncApi.AcceptAll(nextCursor = 5L)
        syncApi.pullBehavior = FakeSyncApi.EmptyPull(nextCursor = 5L)

        val outcome = engine.runOnce()

        assertEquals(SyncEngine.SyncOutcome.OK, outcome)
        assertTrue("push should have been called", syncApi.pushCalls >= 1)
        assertEquals("mutation should be removed after accept", 0, repo.listPendingMutations().size)
        assertEquals(5L, repo.getSyncCursor())
    }

    /**
     * 验收③：pull-echo 不重复创建实体。
     *
     * 步骤：
     * 1. 本地 push 一个 transaction。
     * 2. 服务端 push 响应 accepted；pull 响应 echo 同一个 change。
     * 3. 但本地有活跃 mutation 时，merge 应跳过该实体。
     * 4. push 接受后 mutation 被移除 → 后续 pull 才真正合并（但实体已在本地，upsert 幂等）。
     */
    @Test
    fun pullEchoDoesNotDuplicateWhileMutationActive() = runBlocking {
        seedBookAndCategory()
        val tx = TransactionEntity(
            id = "t-dup", bookId = "b1", categoryId = "c1", accountId = null,
            amountCents = 100L, type = "expense", bizDate = "2026-09-05",
            occurredAt = 2000L, note = "dup test", source = "manual",
            version = 1, deletedAt = null
        )
        repo.insertTransaction(tx, "m-dup")

        // push 阶段接受；pull 阶段 echo 同一 change
        syncApi.pushBehavior = FakeSyncApi.AcceptAll(nextCursor = 10L)
        val echoChange = ServerChange(
            entityType = "transaction",
            entityId = "t-dup",
            operation = SyncChangeOp.INSERT,
            version = 1,
            entityPayload = mapOf(
                "id" to "t-dup",
                "bookId" to "b1",
                "categoryId" to "c1",
                "amountCents" to "100",
                "type" to "expense",
                "bizDate" to "2026-09-05",
                "occurredAt" to "2000",
                "note" to "dup test",
                "source" to "manual"
            )
        )
        syncApi.pullBehavior = FakeSyncApi.FixedPull(
            changesByCall = listOf(
                // 第一次 pull：echo（mutation 仍在）→ 跳过
                listOf(echoChange) to 5L,
                // 第二次 pull（push 后）：仍 echo → 现在可合并
                listOf(echoChange) to 10L,
                // 第三次 pull：空（caught-up）
                emptyList<ServerChange>() to 10L
            )
        )

        val outcome = engine.runOnce()
        assertEquals(SyncEngine.SyncOutcome.OK, outcome)

        // P1-9: 断言 pullCalls >= 1，确保 pull 真的被调用
        assertTrue("pull should have been called at least once", syncApi.pullCalls >= 1)

        // 本地 transaction 应存在且唯一（没有重复）
        val persisted = repo.getTransaction("t-dup")
        assertNotNull(persisted)
        assertEquals(100L, persisted!!.amountCents)
    }

    /**
     * P1-8: 连续快速点击同一实体不应产生重复实体（Room ABORT 拒绝重复写入）。
     *
     * 步骤：
     * 1. 快速连续两次 insertTransaction 同一 id（模拟用户连点）。
     * 2. 验证第二次写入被拒绝，原实体和 mutation 均不被覆盖。
     */
    @Test
    fun rapidClickDoesNotDuplicateEntity() = runBlocking {
        seedBookAndCategory()
        val tx1 = TransactionEntity(
            id = "t-rapid", bookId = "b1", categoryId = "c1", accountId = null,
            amountCents = 100L, type = "expense", bizDate = "2026-09-05",
            occurredAt = 5000L, note = "first click", source = "manual",
            version = 1, deletedAt = null
        )
        repo.insertTransaction(tx1, "m-rapid-1")

        // 模拟第二次快速点击（同一 entityId，不同 mutationId）
        val tx2 = TransactionEntity(
            id = "t-rapid", bookId = "b1", categoryId = "c1", accountId = null,
            amountCents = 200L, type = "expense", bizDate = "2026-09-05",
            occurredAt = 5000L, note = "second click", source = "manual",
            version = 2, deletedAt = null
        )
        try {
            repo.insertTransaction(tx2, "m-rapid-2")
            fail("duplicate transaction id should be rejected")
        } catch (_: android.database.sqlite.SQLiteConstraintException) {
            // ABORT preserves the first write and does not enqueue the duplicate mutation.
        }

        // 验证：实体与 mutation 均保留第一次写入。
        val mutations = repo.listPendingMutations()
        assertEquals("only the accepted mutation should be queued", 1, mutations.size)

        val tx = repo.getTransaction("t-rapid")
        assertNotNull(tx)
        assertEquals("entity should preserve the first accepted write", 100L, tx!!.amountCents)
    }

    /**
     * P1-10: applyServerChanges DELETE 分支按 entity_type 映射。
     *
     * 验证：
     * - transaction DELETE → 软删（设 deleted_at）
     * - book/category/account DELETE → 归档（设 archived_at）
     * - budget DELETE → 物删
     */
    @Test
    fun applyServerChangesDeleteBranches() = runBlocking {
        seedBookAndCategory()

        // 插入测试实体
        val tx = TransactionEntity(
            id = "t-del", bookId = "b1", categoryId = "c1", accountId = null,
            amountCents = 100L, type = "expense", bizDate = "2026-09-05",
            occurredAt = 6000L, note = "", source = "manual",
            version = 1, deletedAt = null
        )
        db.transactionDao().insert(tx)

        val budget = com.aozora.app.db.entity.BudgetEntity(
            id = "bg-del", bookId = "b1", budgetType = "monthly",
            categoryId = null, period = "2026-09", amountCents = 1000L,
            version = 1
        )
        db.budgetDao().insert(budget)

        // 插入额外的 book/category/account 用于测试归档分支
        db.bookDao().insert(BookEntity("b-del", "u1", "ToDelete", "", 1, null))
        db.categoryDao().insert(CategoryEntity("c-del", "u1", null, null, "expense", "ToDelete", null, null, 1))
        db.accountDao().insert(com.aozora.app.db.entity.AccountEntity("a-del", "u1", "ToDelete", "", null, null, 1))

        // 模拟服务端 DELETE 变更
        val changes = listOf(
            ServerChange(
                entityType = "transaction", entityId = "t-del",
                operation = SyncChangeOp.DELETE, version = 2,
                entityPayload = emptyMap()
            ),
            ServerChange(
                entityType = "budget", entityId = "bg-del",
                operation = SyncChangeOp.DELETE, version = 2,
                entityPayload = emptyMap()
            ),
            ServerChange(
                entityType = "book", entityId = "b-del",
                operation = SyncChangeOp.DELETE, version = 2,
                entityPayload = emptyMap()
            ),
            ServerChange(
                entityType = "category", entityId = "c-del",
                operation = SyncChangeOp.DELETE, version = 2,
                entityPayload = emptyMap()
            ),
            ServerChange(
                entityType = "account", entityId = "a-del",
                operation = SyncChangeOp.DELETE, version = 2,
                entityPayload = emptyMap()
            )
        )

        repo.applyServerChanges(changes, newCursor = 10L, ownerId = "u1")

        // transaction 应被软删（deleted_at != null）
        val deletedTx = repo.getTransaction("t-del")
        assertNotNull("transaction should still exist (soft-deleted)", deletedTx)
        assertNotNull("deletedAt should be set", deletedTx!!.deletedAt)

        // budget 应被物删（不存在）
        val deletedBudget = repo.getBudget("bg-del")
        assertNull("budget should be physically deleted", deletedBudget)

        // book 应被归档（archived_at != null）
        val deletedBook = repo.getBook("b-del")
        assertNotNull("book should still exist (archived)", deletedBook)
        assertNotNull("book archivedAt should be set", deletedBook!!.archivedAt)

        // category 应被归档（archived_at != null）
        val deletedCategory = db.categoryDao().getById("c-del")
        assertNotNull("category should still exist (archived)", deletedCategory)
        assertNotNull("category archivedAt should be set", deletedCategory!!.archivedAt)

        // account 应被归档（archived_at != null）
        val deletedAccount = repo.getAccount("a-del")
        assertNotNull("account should still exist (archived)", deletedAccount)
        assertNotNull("account archivedAt should be set", deletedAccount!!.archivedAt)
    }

    /**
     * P1-11: applyServerChanges 跳过活跃 mutation 的实体。
     *
     * 验证：当本地有 pending mutation 时，服务端 pull 的同一实体变更应被跳过。
     */
    @Test
    fun applyServerChangesSkipsActiveMutation() = runBlocking {
        seedBookAndCategory()
        val tx = TransactionEntity(
            id = "t-active", bookId = "b1", categoryId = "c1", accountId = null,
            amountCents = 100L, type = "expense", bizDate = "2026-09-05",
            occurredAt = 7000L, note = "local version", source = "manual",
            version = 1, deletedAt = null
        )
        repo.insertTransaction(tx, "m-active")

        // 服务端推送同一实体的更新（version 2）
        val serverChange = ServerChange(
            entityType = "transaction",
            entityId = "t-active",
            operation = SyncChangeOp.UPDATE,
            version = 2,
            entityPayload = mapOf(
                "id" to "t-active",
                "bookId" to "b1",
                "categoryId" to "c1",
                "amountCents" to "999",  // 服务端修改了金额
                "type" to "expense",
                "bizDate" to "2026-09-05",
                "occurredAt" to "7000",
                "note" to "server version",
                "source" to "manual"
            )
        )

        repo.applyServerChanges(listOf(serverChange), newCursor = 10L, ownerId = "u1")

        // 本地应保留原版本（amountCents = 100），而非服务端版本（999）
        val persisted = repo.getTransaction("t-active")
        assertNotNull(persisted)
        assertEquals("local version should be preserved", 100L, persisted!!.amountCents)
        assertEquals("local note should be preserved", "local version", persisted.note)
    }

    /**
     * 验收②：认证丢失 → AUTH_LOST 返回 → 本地 auth 被清空。
     */
    @Test
    fun authLostClearsLocalTokens() = runBlocking {
        authClient.behavior = FakeAuthClient.Behavior.AuthLost
        val outcome = engine.runOnce()
        assertEquals(SyncEngine.SyncOutcome.AUTH_LOST, outcome)
        assertTrue(authClient.cleared)
    }

    /**
     * 服务端 5xx → RETRYABLE。
     */
    @Test
    fun serverErrorYieldsRetryable() = runBlocking {
        seedBookAndCategory()
        repo.insertTransaction(
            TransactionEntity(
                id = "t-err", bookId = "b1", categoryId = "c1", accountId = null,
                amountCents = 100L, type = "expense", bizDate = "2026-09-05",
                occurredAt = 3000L, note = "", source = "manual",
                version = 1, deletedAt = null
            ),
            "m-err"
        )
        syncApi.pushBehavior = FakeSyncApi.AcceptAll(nextCursor = 0L)
        syncApi.pullBehavior = FakeSyncApi.PullError(httpCode = 503)
        val outcome = engine.runOnce()
        assertEquals(SyncEngine.SyncOutcome.RETRYABLE, outcome)
        // mutation 仍 pending（未接受也未失败）
        assertEquals(1, repo.listPendingMutations().size)
    }

    /**
     * 崩溃恢复：syncing 状态的 mutation 被重置为 pending。
     */
    @Test
    fun syncingMutationsResetToPendingOnRunOnce() = runBlocking {
        seedBookAndCategory()
        repo.insertTransaction(
            TransactionEntity(
                id = "t-stale", bookId = "b1", categoryId = "c1", accountId = null,
                amountCents = 100L, type = "expense", bizDate = "2026-09-05",
                occurredAt = 4000L, note = "", source = "manual",
                version = 1, deletedAt = null
            ),
            "m-stale"
        )
        // 模拟崩溃：mutation 留在 syncing
        repo.markMutationSyncing("m-stale")
        assertEquals("syncing", repo.listPendingMutations().first().status)

        syncApi.pushBehavior = FakeSyncApi.AcceptAll(nextCursor = 0L)
        syncApi.pullBehavior = FakeSyncApi.EmptyPull(nextCursor = 0L)
        engine.runOnce()

        // mutation 应该被移除（被 push 接受）
        assertEquals(0, repo.listPendingMutations().size)
    }

    /**
     * NEW-1: account.archive 操作端到端同步。
     *
     * 验证：
     * - Repository.archiveAccount 创建 OP_ARCHIVE 的 pending mutation
     * - SyncPayloadMapper 在 wire 层将 OP_ARCHIVE 映射为 "delete"
     * - FakeSyncApi 收到 operation = "delete"（非 "archive"）
     * - 服务端 echo 回 SyncChangeOp.DELETE + archived entity payload
     * - 本地 account 被归档（archived_at 设置）
     * - cursor 被推进
     */
    @Test
    fun archiveAccountSyncsAsDeleteOperation() = runBlocking {
        seedBookAndCategory()
        // 直接通过 DAO 插入一个 account（避免入队 mutation）
        db.accountDao().insert(com.aozora.app.db.entity.AccountEntity("a1", "u1", "Cash", "", null, null, 1))

        // 调用 archiveAccount：应创建 OP_ARCHIVE 的 pending mutation
        repo.archiveAccount("a1", "m-archive")
        val pending = repo.listPendingMutations()
        assertEquals(1, pending.size)
        assertEquals(PendingMutationEntity.OP_ARCHIVE, pending[0].operation)

        // 配置 FakeSyncApi：
        // - 第 1 次 pull（runOnce 起始）：空
        // - push：接受；捕获 push 请求以验证 operation 映射
        // - 第 2 次 pull（echo）：返回 account DELETE 变更（带 archived_at）
        var capturedPushOperation: String? = null
        syncApi.pushInterceptor = { req ->
            capturedPushOperation = req.mutations.firstOrNull()?.operation
        }
        syncApi.pushBehavior = FakeSyncApi.AcceptAll(nextCursor = 10L)
        syncApi.pullBehavior = FakeSyncApi.FixedPull(listOf(
            emptyList<ServerChange>() to 1L,  // 第 1 次 pull：空
            listOf(
                ServerChange(
                    entityType = "account",
                    entityId = "a1",
                    operation = SyncChangeOp.DELETE,
                    version = 2,
                    entityPayload = mapOf(
                        "id" to "a1",
                        "ownerId" to "u1",
                        "name" to "Cash",
                        "icon" to "",
                        "archivedAt" to "1234567890",
                        "version" to "2"
                    )
                )
            ) to 10L  // 第 2 次 pull（echo）：返回归档变更
        ))

        val outcome = engine.runOnce()

        assertEquals(SyncEngine.SyncOutcome.OK, outcome)
        // 关键断言：wire 层 operation 必须是 "delete"（不是 "archive"）
        assertEquals("delete", capturedPushOperation)
        // mutation 应已被移除（被 push 接受）
        assertEquals(0, repo.listPendingMutations().size)
        // account 应被归档（archived_at 设置）
        val archivedAccount = repo.getAccount("a1")
        assertNotNull("account should still exist (archived)", archivedAccount)
        assertNotNull("archivedAt should be set", archivedAccount!!.archivedAt)
    }

    // ─── T-M3-03: Bootstrap 端到端测试 ───

    /**
     * 构建 BootstrapResponse，自动根据响应数据计算一致的 checksum。
     * 测试 helper，避免每个测试手写 checksum。
     *
     * P0-1 修复后：同时设置 computedChecksum = checksum（绕过 JSON 解析路径）。
     * 跨语言一致性由 SyncPayloadMapperTest + BootstrapChecksumTest 单独覆盖。
     */
    private fun buildBootstrapResponse(
        books: List<Map<String, String?>> = emptyList(),
        categories: List<Map<String, String?>> = emptyList(),
        accounts: List<Map<String, String?>> = emptyList(),
        transactions: List<Map<String, String?>> = emptyList(),
        budgets: List<Map<String, String?>> = emptyList(),
        cursor: Long = 0L
    ): BootstrapResponse {
        val checksum = BootstrapChecksum.compute(
            books, categories, accounts, transactions, budgets, cursor
        )
        return BootstrapResponse(
            books, categories, accounts, transactions, budgets,
            // 命名传参：T-M7-01 在 cursor 之前插入了三实体字段（savingsGoals /
            // scheduledRules / chatPersonas），位置传参会把 cursor: Long 落到
            // List 类型的槽上，导致 androidTest 源码集编译失败。
            cursor = cursor,
            checksum = checksum, computedChecksum = checksum
        )
    }

    /**
     * T-M3-03 验收：bootstrap 成功后实体完整重建，checksum + 计数均通过。
     *
     * 步骤：
     * 1. 本地为空 + cursor=0 → needsBootstrap() = true → 触发 bootstrap。
     * 2. 服务端返回 1 book + 1 category + 有效 checksum。
     * 3. Room 重建后计数与响应一致。
     * 4. cursor 推进到响应值。
     */
    @Test
    fun bootstrapSuccess_rebuildsEntitiesAndVerifiesChecksum() = runBlocking {
        val response = buildBootstrapResponse(
            books = listOf(mapOf("id" to "b1", "name" to "Main", "icon" to "", "version" to "1")),
            categories = listOf(mapOf("id" to "c1", "name" to "Food", "kind" to "expense", "version" to "1")),
            cursor = 5L
        )
        syncApi.bootstrapBehavior = FakeSyncApi.BootstrapSuccess(response)
        syncApi.pullBehavior = FakeSyncApi.EmptyPull(nextCursor = 5L)

        val outcome = engine.runOnce()

        assertEquals(SyncEngine.SyncOutcome.OK, outcome)
        assertEquals(5L, repo.getSyncCursor())
        assertEquals(1, repo.listBooks().size)
        assertEquals("Main", repo.listBooks().first().name)
        assertEquals(1, repo.listCategories().size)
        assertEquals(1, syncApi.bootstrapCalls)
    }

    /**
     * P1-2 修复验证：bootstrap API 失败时本地数据不被清空。
     *
     * 步骤：
     * 1. 本地已有 book + category（cursor 无关，forced 路径不走 needsBootstrap）。
     * 2. bootstrap API 返回 503。
     * 3. 预期：runBootstrapForced 返回 RETRYABLE，本地数据保持原样。
     *
     * 旧顺序（先 clearAll 再 bootstrap）下，本地数据会在 API 失败时丢失。
     */
    @Test
    fun bootstrapApiFailure_preservesLocalData() = runBlocking {
        seedBookAndCategory()
        assertEquals("precondition: local book exists", 1, repo.listBooks().size)
        assertEquals("precondition: local category exists", 1, repo.listCategories().size)

        syncApi.bootstrapBehavior = FakeSyncApi.BootstrapError(httpCode = 503)

        val outcome = engine.runBootstrapForced()

        assertEquals(SyncEngine.SyncOutcome.RETRYABLE, outcome)
        // 关键断言：本地数据必须保留
        assertEquals("local books must survive bootstrap failure", 1, repo.listBooks().size)
        assertEquals("local categories must survive bootstrap failure", 1, repo.listCategories().size)
    }

    /**
     * P1-1 修复验证：response 与 Room 实体计数不一致时返回 RETRYABLE。
     *
     * 步骤：
     * 1. 响应声明 2 个 book，但第 2 个 name=null（serverChangeMapToBook 会 skip）。
     * 2. Checksum 仍匹配（response 原始数据计算），但 Room 只写入 1 个 book。
     * 3. 计数校验应捕获此差异，返回 RETRYABLE。
     */
    @Test
    fun bootstrapEntityCountMismatch_returnsRetryable() = runBlocking {
        seedBookAndCategory()
        val response = buildBootstrapResponse(
            books = listOf(
                mapOf("id" to "b-new", "name" to "Valid", "icon" to "", "version" to "1"),
                // name=null → serverChangeMapToBook 返回 null → skip → Room 少 1 条
                mapOf("id" to "b-missing-name", "name" to null, "icon" to "", "version" to "1")
            ),
            cursor = 3L
        )
        syncApi.bootstrapBehavior = FakeSyncApi.BootstrapSuccess(response)

        val outcome = engine.runBootstrapForced()

        assertEquals(SyncEngine.SyncOutcome.RETRYABLE, outcome)
    }

    /**
     * Checksum 不匹配时返回 RETRYABLE（既有行为，补端到端覆盖）。
     */
    @Test
    fun bootstrapChecksumMismatch_returnsRetryable() = runBlocking {
        seedBookAndCategory()
        val response = buildBootstrapResponse(
            books = listOf(mapOf("id" to "b1", "name" to "Main", "icon" to "", "version" to "1")),
            cursor = 3L
        )
        // 损坏 checksum，使客户端重算结果与服务端声明不一致
        val badResponse = response.copy(checksum = "0".repeat(64))
        syncApi.bootstrapBehavior = FakeSyncApi.BootstrapSuccess(badResponse)

        val outcome = engine.runBootstrapForced()

        assertEquals(SyncEngine.SyncOutcome.RETRYABLE, outcome)
    }

    /**
     * P0-1 回归测试：真实 JSON 路径（snake_case 原始数据 → parseBootstrapResponse → checksum）。
     *
     * 此前审查漏报：FakeSyncApi 直接构造 BootstrapResponse（camelCase Map），绕过了 JSON 解析。
     * 此测试使用与 Python 服务端 _serialize_entity 输出完全一致的 snake_case JSON，
     * 走 FakeSyncApi.BootstrapJsonResponse → parseBootstrapResponse → SyncEngine.runBootstrapForced。
     *
     * 验收：computedChecksum 必须等于 Python 已知输出，runBootstrapForced 返回 OK。
     */
    @Test
    fun bootstrap_realJsonPath_snakeCaseMatchesPythonChecksum() = runBlocking {
        // 与 Python _serialize_entity(Book) + FastAPI 序列化完全一致的 JSON
        val realServerJson = """
            {
              "books": [
                {
                  "id": "book-uuid-1",
                  "name": "Alice Book",
                  "icon": null,
                  "version": 1,
                  "created_at": "2026-09-05T10:00:00+00:00",
                  "updated_at": "2026-09-05T10:00:00+00:00",
                  "deleted_at": null
                }
              ],
              "categories": [],
              "accounts": [],
              "transactions": [],
              "budgets": [],
              "cursor": 7,
              "checksum": "a0834b8383acd061f4ed09c24214708a9803c03326849b603d0134f6056c05d4"
            }
        """.trimIndent()
        syncApi.bootstrapBehavior = FakeSyncApi.BootstrapJsonResponse(realServerJson)
        syncApi.pullBehavior = FakeSyncApi.EmptyPull(nextCursor = 7L)

        val outcome = engine.runBootstrapForced()

        assertEquals(
            "bootstrap with real snake_case JSON must succeed (computedChecksum matches Python reference)",
            SyncEngine.SyncOutcome.OK, outcome
        )
        assertEquals(1, repo.listBooks().size)
        assertEquals("Alice Book", repo.listBooks().first().name)
        assertEquals(7L, repo.getSyncCursor())
    }

    /**
     * P0-1 反向验证：服务端 checksum 错误（与 Python 参考实现不一致）时返回 RETRYABLE。
     *
     * 走真实 JSON 路径，但 checksum 故意与服务端 Python 不一致。
     */
    @Test
    fun bootstrap_realJsonPath_serverChecksumWrong_returnsRetryable() = runBlocking {
        // checksum 故意错误（非 Python 参考实现输出）
        val realServerJsonWithWrongChecksum = """
            {
              "books": [
                {
                  "id": "book-uuid-1",
                  "name": "Alice Book",
                  "icon": null,
                  "version": 1,
                  "created_at": "2026-09-05T10:00:00+00:00",
                  "updated_at": "2026-09-05T10:00:00+00:00",
                  "deleted_at": null
                }
              ],
              "categories": [], "accounts": [], "transactions": [], "budgets": [],
              "cursor": 7,
              "checksum": "0000000000000000000000000000000000000000000000000000000000000000"
            }
        """.trimIndent()
        syncApi.bootstrapBehavior = FakeSyncApi.BootstrapJsonResponse(realServerJsonWithWrongChecksum)

        val outcome = engine.runBootstrapForced()

        assertEquals(SyncEngine.SyncOutcome.RETRYABLE, outcome)
    }

    // ─── T-M3-03: needsBootstrap 边界条件测试 ───

    /**
     * needsBootstrap 边界条件：cursor=0 + 无 book → 触发 bootstrap。
     *
     * 这是正常的新设备首次同步场景。
     */
    @Test
    fun needsBootstrap_cursorZeroNoBooks_triggersBootstrap() = runBlocking {
        // precondition: cursor=0, no books (default state)
        assertEquals(0L, repo.getSyncCursor())
        assertEquals(0, repo.listBooks().size)

        syncApi.pullBehavior = FakeSyncApi.EmptyPull(nextCursor = 0L)

        val outcome = engine.runOnce()

        assertEquals(SyncEngine.SyncOutcome.OK, outcome)
        // 关键断言：bootstrap 应该被调用
        assertEquals("bootstrap should have been called", 1, syncApi.bootstrapCalls)
    }

    /**
     * needsBootstrap 边界条件：cursor=0 但有 book → 不触发 bootstrap。
     *
     * 防止"有本地数据但 cursor 被意外重置"时误清数据。
     * 注释见 SyncEngine.needsBootstrap：两个条件同时满足才触发。
     */
    @Test
    fun needsBootstrap_cursorZeroWithBooks_skipsBootstrap() = runBlocking {
        seedBookAndCategory()
        // 手动重置 cursor 到 0（模拟异常场景）
        repo.setSyncCursor(0L)
        assertEquals(0L, repo.getSyncCursor())
        assertEquals("precondition: local book exists", 1, repo.listBooks().size)

        syncApi.pullBehavior = FakeSyncApi.EmptyPull(nextCursor = 0L)
        syncApi.pushBehavior = FakeSyncApi.AcceptAll(nextCursor = 0L)

        val outcome = engine.runOnce()

        assertEquals(SyncEngine.SyncOutcome.OK, outcome)
        // 关键断言：bootstrap 不应被调用（即使 cursor=0）
        assertEquals("bootstrap should NOT be called when books exist", 0, syncApi.bootstrapCalls)
        // 本地数据应保留
        assertEquals("local books must be preserved", 1, repo.listBooks().size)
    }

    /**
     * needsBootstrap 边界条件：cursor>0 + 无 book → 不触发 bootstrap。
     *
     * 可能是所有 book 被删除后的状态，此时不应触发 bootstrap（cursor 已经推进过）。
     */
    @Test
    fun needsBootstrap_cursorPositiveNoBooks_skipsBootstrap() = runBlocking {
        // 先设置 cursor > 0
        repo.setSyncCursor(10L)
        assertEquals(10L, repo.getSyncCursor())
        assertEquals("no books", 0, repo.listBooks().size)

        syncApi.pullBehavior = FakeSyncApi.EmptyPull(nextCursor = 10L)
        syncApi.pushBehavior = FakeSyncApi.AcceptAll(nextCursor = 10L)

        val outcome = engine.runOnce()

        assertEquals(SyncEngine.SyncOutcome.OK, outcome)
        // 关键断言：bootstrap 不应被调用（因为 cursor>0）
        assertEquals("bootstrap should NOT be called when cursor > 0", 0, syncApi.bootstrapCalls)
    }

    // ─── T-M3-03: Repository.applyBootstrap 独立测试 ───

    /**
     * applyBootstrap 独立测试：成功写入所有实体类型 + cursor。
     *
     * 验证：
     * - book/category/account/transaction/budget 全部正确写入 Room
     * - ownerId 被回填（bootstrap 响应中不包含 owner_id）
     * - cursor 被正确写入 sync_meta
     */
    @Test
    fun applyBootstrap_writesAllEntityTypes() = runBlocking {
        val response = buildBootstrapResponse(
            books = listOf(mapOf("id" to "b1", "name" to "Main", "icon" to "book", "version" to "1")),
            categories = listOf(mapOf("id" to "c1", "name" to "Food", "kind" to "expense", "version" to "1")),
            accounts = listOf(mapOf("id" to "a1", "name" to "Cash", "icon" to "wallet", "version" to "1")),
            transactions = listOf(mapOf(
                "id" to "t1", "bookId" to "b1", "categoryId" to "c1",
                "amountCents" to "500", "type" to "expense", "bizDate" to "2026-09-05",
                "occurredAt" to "1000", "source" to "manual", "version" to "1"
            )),
            budgets = listOf(mapOf(
                "id" to "bg1", "bookId" to "b1", "budgetType" to "monthly",
                "period" to "2026-09", "amountCents" to "1000", "version" to "1"
            )),
            cursor = 42L
        )

        repo.applyBootstrap(response, ownerId = "u1")

        // 验证实体写入
        assertEquals(1, repo.listBooks().size)
        assertEquals("Main", repo.listBooks().first().name)
        assertEquals("u1", repo.listBooks().first().ownerId)  // ownerId 回填

        assertEquals(1, repo.listCategories().size)
        assertEquals("Food", repo.listCategories().first().name)
        assertEquals("u1", repo.listCategories().first().ownerId)

        assertEquals(1, repo.listAccounts().size)
        assertEquals("Cash", repo.listAccounts().first().name)

        val tx = repo.getTransaction("t1")
        assertNotNull(tx)
        assertEquals(500L, tx!!.amountCents)

        val budget = repo.getBudget("bg1")
        assertNotNull(budget)
        assertEquals(1000L, budget!!.amountCents)

        // 验证 cursor 写入
        assertEquals(42L, repo.getSyncCursor())
    }

    /**
     * applyBootstrap 独立测试：空响应正确处理（不写入任何实体，cursor 写入 0）。
     */
    @Test
    fun applyBootstrap_emptyResponse_writesCursor() = runBlocking {
        val response = buildBootstrapResponse(cursor = 0L)

        repo.applyBootstrap(response, ownerId = "u1")

        assertEquals(0, repo.listBooks().size)
        assertEquals(0, repo.listCategories().size)
        assertEquals(0, repo.listAccounts().size)
        assertEquals(0L, repo.getSyncCursor())
    }

    /**
     * applyBootstrap 独立测试：防御断言 - book 含 archivedAt 应抛异常。
     *
     * P2-1 防御：bootstrap 仅重建活跃实体；服务端不应返回已归档 book。
     */
    @Test(expected = IllegalStateException::class)
    fun applyBootstrap_archivedBook_throwsIllegalStateException() = runBlocking {
        // 构造含 archivedAt 的 book（违反不变量）
        val response = BootstrapResponse(
            books = listOf(mapOf(
                "id" to "b-archived", "name" to "Archived", "icon" to "",
                "version" to "1", "archivedAt" to "1234567890"
            )),
            categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            cursor = 1L, checksum = "", computedChecksum = ""
        )

        repo.applyBootstrap(response, ownerId = "u1")
    }

    /**
     * applyBootstrap 独立测试：防御断言 - transaction 含 deletedAt 应抛异常。
     */
    @Test(expected = IllegalStateException::class)
    fun applyBootstrap_deletedTransaction_throwsIllegalStateException() = runBlocking {
        // 先写入 book 和 category（transaction 外键依赖）
        val responseWithDeps = buildBootstrapResponse(
            books = listOf(mapOf("id" to "b1", "name" to "Main", "icon" to "", "version" to "1")),
            categories = listOf(mapOf("id" to "c1", "name" to "Food", "kind" to "expense", "version" to "1")),
            cursor = 1L
        )
        repo.applyBootstrap(responseWithDeps, ownerId = "u1")

        // 构造含 deletedAt 的 transaction（违反不变量）
        val response = BootstrapResponse(
            books = emptyList(), categories = emptyList(), accounts = emptyList(),
            transactions = listOf(mapOf(
                "id" to "t-deleted", "bookId" to "b1", "categoryId" to "c1",
                "amountCents" to "100", "type" to "expense", "bizDate" to "2026-09-05",
                "occurredAt" to "1000", "source" to "manual", "version" to "1",
                "deletedAt" to "1234567890"
            )),
            budgets = emptyList(),
            cursor = 2L, checksum = "", computedChecksum = ""
        )

        repo.applyBootstrap(response, ownerId = "u1")
    }

    // ─── helpers ───

    private suspend fun seedBookAndCategory() {
        // 直接通过 DAO 插入 seed 数据（避免入队 mutation）
        db.bookDao().insert(BookEntity("b1", "u1", "Main", "", 1, null))
        db.categoryDao().insert(CategoryEntity("c1", "u1", null, null, "expense", "Food", null, null, 1))
    }
}

// ─── Fakes ───

/** Fake SyncApi：按测试场景返回固定行为。 */
class FakeSyncApi : SyncApi {
    var pushBehavior: PushBehavior? = null
    var pullBehavior: PullBehavior? = null
    var pushCalls = 0
    var pullCalls = 0
    var bootstrapCalls = 0
    // NEW-1: 测试钩子，允许测试捕获 push 请求（验证 operation 映射等）
    var pushInterceptor: ((PushRequest) -> Unit)? = null

    // T-M3-03: bootstrap 行为配置。默认为空成功响应（向后兼容 T-M3-02 测试）。
    sealed interface BootstrapBehavior
    data class BootstrapSuccess(val response: BootstrapResponse) : BootstrapBehavior
    data class BootstrapError(val httpCode: Int) : BootstrapBehavior
    // P0-1 回归测试：允许测试构造原始 JSON 并走真实 parseBootstrapResponse 路径
    data class BootstrapJsonResponse(val json: String) : BootstrapBehavior

    var bootstrapBehavior: BootstrapBehavior = BootstrapSuccess(
        BootstrapResponse(
            books = emptyList(), categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(), cursor = 0L,
            checksum = BootstrapChecksum.compute(
                emptyList(), emptyList(), emptyList(), emptyList(), emptyList(), 0L
            ),
            // P0-1 修复后：FakeSyncApi 默认响应绕过 JSON 解析，直接设置 computedChecksum = checksum
            computedChecksum = BootstrapChecksum.compute(
                emptyList(), emptyList(), emptyList(), emptyList(), emptyList(), 0L
            )
        )
    )

    sealed interface PushBehavior
    data class AcceptAll(val nextCursor: Long) : PushBehavior
    data class ServerError(val httpCode: Int) : PushBehavior

    sealed interface PullBehavior
    data class EmptyPull(val nextCursor: Long) : PullBehavior
    data class PullError(val httpCode: Int) : PullBehavior
    data class FixedPull(val changesByCall: List<Pair<List<ServerChange>, Long>>) : PullBehavior

    override suspend fun push(request: PushRequest): PushResponse {
        pushCalls++
        pushInterceptor?.invoke(request)
        return when (val b = pushBehavior) {
            is AcceptAll -> PushResponse(
                acceptedMutationIds = request.mutations.map { it.mutationId },
                rejected = emptyList(),
                nextCursor = b.nextCursor
            )
            is ServerError -> throw SyncHttpException(b.httpCode, "fake")
            null -> throw IllegalStateException("no push behavior configured")
        }
    }

    override suspend fun pull(request: PullRequest): PullResponse {
        pullCalls++
        return when (val b = pullBehavior) {
            is EmptyPull -> PullResponse(emptyList(), b.nextCursor)
            is PullError -> throw SyncHttpException(b.httpCode, "fake")
            is FixedPull -> {
                val idx = (pullCalls - 1).coerceAtMost(b.changesByCall.size - 1)
                val (changes, cursor) = b.changesByCall[idx]
                PullResponse(changes, cursor)
            }
            null -> PullResponse(emptyList(), request.cursor)
        }
    }

    override suspend fun bootstrap(): BootstrapResponse {
        bootstrapCalls++
        return when (val b = bootstrapBehavior) {
            is BootstrapSuccess -> b.response
            is BootstrapError -> throw SyncHttpException(b.httpCode, "fake bootstrap error")
            // P0-1 回归测试：走真实 parseBootstrapResponse 路径（snake_case → camelCase + checksum）
            is BootstrapJsonResponse -> SyncPayloadMapper.parseBootstrapResponse(b.json)
        }
    }
}

/** Fake AuthClient：绕过真实 HTTP，模拟 token 行为。 */
class FakeAuthClient(
    private val userId: String = "u1",
    var behavior: Behavior = Behavior.Success
) : AuthClient(
    baseUrl = "http://fake",
    tokenStore = FakeTokenStore(userId),
    httpClient = OkHttpClient()
) {
    var cleared = false

    enum class Behavior { Success, AuthLost }

    override suspend fun ensureValidAccessToken(): String {
        if (behavior == Behavior.AuthLost) throw AuthLostException("fake auth lost")
        return "fake-token"
    }

    override fun clearAuth() {
        cleared = true
    }

    override fun currentUserId(): String? = if (behavior == Behavior.AuthLost) null else userId
}

/** Fake TokenStore：仅满足 AuthClient 抽象方法的最低实现。 */
class FakeTokenStore(private val userId: String) : TokenStore(
    InstrumentationRegistry.getInstrumentation().targetContext
) {
    override val deviceId: String = "fake-device"
    override val isRegistered: Boolean = true
    override fun getAccessToken(): String = "fake-access"
    override fun getRefreshToken(): String = "fake-refresh"
    override fun getExpiresAt(): Long = Long.MAX_VALUE / 1000
    override fun getUserId(): String = userId
}
