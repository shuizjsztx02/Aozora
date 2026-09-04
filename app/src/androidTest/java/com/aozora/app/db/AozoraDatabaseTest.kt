package com.aozora.app.db

import androidx.room.Room
import android.webkit.WebView
import android.database.sqlite.SQLiteDatabase
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.aozora.app.db.entity.*
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Room DAO 与 Repository 集成测试。
 *
 * 验收标准（§T-M1-03）：
 * - 杀进程重启数据完整 → test_dataSurvivesCloseAndReopen
 * - 写入和 pending mutation 同事务 → test_insertTransactionAtomicity
 * - WebView 清缓存不丢 Room 数据 → Room 独立于 WebView 存储
 */
@RunWith(AndroidJUnit4::class)
class AozoraDatabaseTest {

    @Test
    fun webViewCacheClearPreservesDiskDataAndQueue() = runBlocking {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val name = "test-web-cache.db"
        context.deleteDatabase(name)
        val disk = Room.databaseBuilder(context, AozoraDatabase::class.java, name).build()
        try {
            Repository(disk).insertTransaction(sample(), "cache-test")
            instrumentation.runOnMainSync {
                val webView = WebView(context)
                webView.clearCache(true)
                webView.destroy()
            }
            assertNotNull(disk.transactionDao().getById("guard"))
            assertNotNull(disk.pendingMutationDao().getById("cache-test"))
        } finally {
            disk.close()
            context.deleteDatabase(name)
        }
    }

    @Test
    fun missingMigrationDoesNotEraseExistingData() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val name = "test-missing-migration.db"
        context.deleteDatabase(name)
        val disk = Room.databaseBuilder(context, AozoraDatabase::class.java, name).build()
        Repository(disk).insertTransaction(sample(), "keep-me")
        disk.close()
        val path = context.getDatabasePath(name).path
        SQLiteDatabase.openDatabase(path, null, SQLiteDatabase.OPEN_READWRITE).use { it.version = 2 }
        val unsupported = AozoraDatabase.open(context, name)
        try {
            try { unsupported.transactionDao().getById("guard"); fail("missing migration accepted") }
            catch (_: IllegalStateException) { }
        } finally {
            unsupported.close()
        }
        try {
            SQLiteDatabase.openDatabase(path, null, SQLiteDatabase.OPEN_READONLY).use { raw ->
                raw.rawQuery("SELECT COUNT(*) FROM pending_mutations WHERE mutationId = 'keep-me'", null).use {
                    assertTrue(it.moveToFirst())
                    assertEquals(1, it.getInt(0))
                }
            }
        } finally {
            context.deleteDatabase(name)
        }
    }

    private fun sample(id: String = "guard") = TransactionEntity(
        id, "b1", "expense", 100, "c1", null, "", "2026-09-04", 0L, "manual", 1, null
    )

    @Test
    fun invalidTransactionsNeverWriteDataOrQueue() = runBlocking {
        for (tx in listOf(
            sample().copy(amountCents = -1), sample().copy(amountCents = 0),
            sample().copy(amountCents = 9007199254740992L),
            sample().copy(type = "transfer"), sample().copy(source = "unknown"),
            sample().copy(bizDate = "2026-02-30"), sample().copy(version = 0)
        )) {
            try { repository.insertTransaction(tx, "invalid"); fail("invalid input accepted") }
            catch (_: IllegalArgumentException) { }
            assertNull(repository.getTransaction(tx.id))
            assertTrue(repository.listPendingMutations().isEmpty())
        }
    }

    @Test
    fun mutationConflictRollsBackEntityWrite() = runBlocking {
        repository.insertTransaction(sample("first"), "same-mutation")
        try { repository.insertTransaction(sample("second"), "same-mutation"); fail("conflict accepted") }
        catch (_: android.database.sqlite.SQLiteConstraintException) { }
        assertNull(repository.getTransaction("second"))
        assertEquals("first", repository.listPendingMutations().single().entityId)
    }

    @Test
    fun duplicateEntityDoesNotOverwriteAmountOrEnqueue() = runBlocking {
        repository.insertTransaction(sample(), "first")
        try { repository.insertTransaction(sample().copy(amountCents = 999), "second"); fail("overwrite accepted") }
        catch (_: android.database.sqlite.SQLiteConstraintException) { }
        assertEquals(100L, repository.getTransaction("guard")!!.amountCents)
        assertEquals(1, repository.listPendingMutations().size)
    }

    private lateinit var db: AozoraDatabase
    private lateinit var repository: Repository

    @Before
    fun setup() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        db = AozoraDatabase.createInMemory(context)
        repository = Repository(db)
    }

    @After
    fun teardown() {
        db.close()
    }

    // ─── Transaction 原子事务 ───

    @Test
    fun test_insertTransaction_createsPendingMutation() = runBlocking {
        val tx = TransactionEntity(
            id = "tx-1", bookId = "book-1", type = "expense",
            amountCents = 3500, categoryId = "cat-food", accountId = null,
            note = "午餐", bizDate = "2026-09-03", occurredAt = 1756900800000L,
            source = "manual", version = 1, deletedAt = null
        )
        repository.insertTransaction(tx, "mutation-1")

        // 流水已写入
        val fetched = repository.getTransaction("tx-1")
        assertNotNull(fetched)
        assertEquals(3500L, fetched!!.amountCents)

        // pending mutation 已写入
        val mutations = repository.listPendingMutations("pending")
        assertEquals(1, mutations.size)
        assertEquals("mutation-1", mutations[0].mutationId)
        assertEquals("transaction", mutations[0].entityType)
        assertEquals("tx-1", mutations[0].entityId)
        assertEquals("insert", mutations[0].operation)
    }

    @Test
    fun test_softDelete_createsPendingMutation() = runBlocking {
        val tx = TransactionEntity(
            id = "tx-2", bookId = "book-1", type = "expense",
            amountCents = 1000, categoryId = "cat-food", accountId = null,
            note = "", bizDate = "2026-09-03", occurredAt = System.currentTimeMillis(),
            source = "manual", version = 1, deletedAt = null
        )
        repository.insertTransaction(tx, "mutation-2")
        repository.softDeleteTransaction("tx-2", "mutation-3")

        val fetched = repository.getTransaction("tx-2")
        assertNotNull(fetched)
        assertNotNull(fetched!!.deletedAt) // 已软删除

        val mutations = repository.listPendingMutations()
        assertEquals(2, mutations.size) // insert + delete
    }

    // ─── 数据持久性 ───

    @Test
    fun test_dataSurvivesCloseAndReopen() {
        runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        context.deleteDatabase("test-persist.db")
        val testDb = Room.databaseBuilder(context, AozoraDatabase::class.java, "test-persist.db")
            .allowMainThreadQueries()
            .build()
        val repo = Repository(testDb)

        val tx = TransactionEntity(
            id = "tx-persist", bookId = "book-1", type = "income",
            amountCents = 10000, categoryId = "cat-salary", accountId = null,
            note = "工资", bizDate = "2026-09-01", occurredAt = System.currentTimeMillis(),
            source = "manual", version = 1, deletedAt = null
        )
        repo.insertTransaction(tx, "mutation-persist")
        testDb.close()

        // 重新打开数据库
        val reopened = Room.databaseBuilder(context, AozoraDatabase::class.java, "test-persist.db")
            .allowMainThreadQueries()
            .build()
        val fetched = reopened.transactionDao().getById("tx-persist")
        assertNotNull("数据在关闭重开后应存在", fetched)
        assertEquals(10000L, fetched!!.amountCents)
        val pending = reopened.pendingMutationDao().getById("mutation-persist")
        assertNotNull("pending mutation 也必须在重开后存在", pending)
        assertEquals("tx-persist", pending!!.entityId)
        reopened.close()

        // 清理
        context.deleteDatabase("test-persist.db")
        }
    }

    // ─── 查询 ───

    @Test
    fun test_listByDateRange() = runBlocking {
        val txs = listOf(
            TransactionEntity("tx-a", "b1", "expense", 1000, "c1", null, "", "2026-09-01", 0L, "manual", 1, null),
            TransactionEntity("tx-b", "b1", "expense", 2000, "c1", null, "", "2026-09-15", 0L, "manual", 1, null),
            TransactionEntity("tx-c", "b1", "expense", 3000, "c1", null, "", "2026-10-01", 0L, "manual", 1, null),
        )
        for (tx in txs) {
            repository.insertTransaction(tx, "m-${tx.id}")
        }

        val septTxs = repository.listTransactionsByDateRange("2026-09-01", "2026-09-30")
        assertEquals(2, septTxs.size)
    }

    @Test
    fun test_listByBook_excludesSoftDeleted() = runBlocking {
        val tx = TransactionEntity("tx-del", "b1", "expense", 1000, "c1", null, "", "2026-09-03", 0L, "manual", 1, null)
        repository.insertTransaction(tx, "m-del")
        repository.softDeleteTransaction("tx-del", "m-del2")

        val result = repository.listTransactionsByBook("b1")
        assertTrue(result.isEmpty())
    }

    // ─── Pending Mutation 状态管理 ───

    @Test
    fun test_pendingMutation_lifecycle() = runBlocking {
        val tx = TransactionEntity("tx-m", "b1", "expense", 500, "c1", null, "", "2026-09-03", 0L, "manual", 1, null)
        repository.insertTransaction(tx, "m-lifecycle")

        // pending
        var pending = repository.listPendingMutations("pending")
        assertEquals(1, pending.size)

        // syncing
        repository.markMutationSyncing("m-lifecycle")
        pending = repository.listPendingMutations("pending")
        assertEquals(0, pending.size)
        val syncing = repository.listPendingMutations("syncing")
        assertEquals(1, syncing.size)

        // failed
        repository.markMutationFailed("m-lifecycle")
        val failed = repository.listPendingMutations("failed")
        assertEquals(1, failed.size)
        assertEquals(1, failed[0].retryCount)

        // remove
        repository.removeMutation("m-lifecycle")
        val all = repository.listPendingMutations()
        assertTrue(all.isEmpty())
    }

    // ─── 金额安全 ───

    @Test
    fun test_amountCents_isInteger() = runBlocking {
        val tx = TransactionEntity("tx-int", "b1", "expense", 1, "c1", null, "", "2026-09-03", 0L, "manual", 1, null)
        repository.insertTransaction(tx, "m-int")

        val fetched = repository.getTransaction("tx-int")
        assertNotNull(fetched)
        // Long 类型确保整数
        assertEquals(1L, fetched!!.amountCents)
    }

    // ─── Account 原子写 ───

    @Test
    fun test_insertAccount_createsPendingMutation() = runBlocking {
        val account = AccountEntity(
            id = "acc-1", ownerId = "owner-1", name = "现金",
            icon = "cash", archivedAt = null, version = 1
        )
        repository.insertAccount(account, "m-acc-1")

        val fetched = repository.getAccount("acc-1")
        assertNotNull(fetched)
        assertEquals("现金", fetched!!.name)

        val mutations = repository.listPendingMutations("pending")
        assertEquals(1, mutations.size)
        assertEquals("account", mutations[0].entityType)
        assertEquals("acc-1", mutations[0].entityId)
        assertEquals("insert", mutations[0].operation)
    }

    @Test
    fun test_archiveAccount_createsPendingMutation() = runBlocking {
        val account = AccountEntity(
            id = "acc-arch", ownerId = "owner-1", name = "银行卡",
            icon = "card", archivedAt = null, version = 1
        )
        repository.insertAccount(account, "m-acc-arch-insert")
        repository.archiveAccount("acc-arch", "m-acc-arch")

        val fetched = repository.getAccount("acc-arch")
        assertNotNull(fetched)
        assertNotNull(fetched!!.archivedAt)

        val mutations = repository.listPendingMutations("pending")
        assertEquals(2, mutations.size) // insert + archive
        val archiveMutation = mutations.find { it.operation == "archive" }
        assertNotNull(archiveMutation)
    }

    @Test
    fun test_invalidAccount_doesNotWrite() = runBlocking {
        // 空 ID
        try {
            repository.insertAccount(AccountEntity("", "o", "n", "i", null, 1), "m-bad")
            fail("empty id accepted")
        } catch (_: IllegalArgumentException) { }

        // 负数金额（如果未来有金额字段）
        // 版本为 0
        try {
            repository.insertAccount(AccountEntity("bad", "o", "n", "i", null, 0), "m-bad")
            fail("version 0 accepted")
        } catch (_: IllegalArgumentException) { }

        assertNull(repository.getAccount("bad"))
        assertTrue(repository.listPendingMutations().isEmpty())
    }

    // ─── Budget 原子写 ───

    @Test
    fun test_insertBudget_createsPendingMutation() = runBlocking {
        val budget = BudgetEntity(
            id = "bud-1", bookId = "b1", budgetType = "monthly",
            categoryId = null, period = "2026-09", amountCents = 500000, version = 1
        )
        repository.insertBudget(budget, "m-bud-1")

        val fetched = repository.getBudget("bud-1")
        assertNotNull(fetched)
        assertEquals(500000L, fetched!!.amountCents)
        assertNull(fetched.categoryId) // 整体预算

        val mutations = repository.listPendingMutations("pending")
        assertEquals(1, mutations.size)
        assertEquals("budget", mutations[0].entityType)
        assertEquals("bud-1", mutations[0].entityId)
    }

    @Test
    fun test_updateBudget_createsPendingMutation() = runBlocking {
        val budget = BudgetEntity(
            id = "bud-upd", bookId = "b1", budgetType = "monthly",
            categoryId = "cat-food", period = "2026-09", amountCents = 100000, version = 1
        )
        repository.insertBudget(budget, "m-bud-upd-insert")

        val updated = budget.copy(amountCents = 150000, version = 2)
        repository.updateBudget(updated, "m-bud-upd")

        val fetched = repository.getBudget("bud-upd")
        assertNotNull(fetched)
        assertEquals(150000L, fetched!!.amountCents)
        assertEquals(2, fetched.version)

        val mutations = repository.listPendingMutations("pending")
        assertEquals(2, mutations.size) // insert + update
        val updateMutation = mutations.find { it.operation == "update" }
        assertNotNull(updateMutation)
    }

    @Test
    fun test_deleteBudget_createsPendingMutation() = runBlocking {
        val budget = BudgetEntity(
            id = "bud-del", bookId = "b1", budgetType = "annual",
            categoryId = null, period = "2026", amountCents = 1200000, version = 1
        )
        repository.insertBudget(budget, "m-bud-del-insert")
        repository.deleteBudget("bud-del", "m-bud-del")

        assertNull(repository.getBudget("bud-del"))

        val mutations = repository.listPendingMutations("pending")
        assertEquals(2, mutations.size) // insert + delete
        val deleteMutation = mutations.find { it.operation == "delete" }
        assertNotNull(deleteMutation)
    }

    @Test
    fun test_invalidBudget_doesNotWrite() = runBlocking {
        // 空 ID
        try {
            repository.insertBudget(BudgetEntity("", "b", "monthly", null, "2026-09", 100, 1), "m-bad")
            fail("empty id accepted")
        } catch (_: IllegalArgumentException) { }

        // 非法 budgetType
        try {
            repository.insertBudget(BudgetEntity("bad", "b", "weekly", null, "2026-09", 100, 1), "m-bad")
            fail("weekly type accepted")
        } catch (_: IllegalArgumentException) { }

        // 负数金额
        try {
            repository.insertBudget(BudgetEntity("bad", "b", "monthly", null, "2026-09", -100, 1), "m-bad")
            fail("negative amount accepted")
        } catch (_: IllegalArgumentException) { }

        // 非法 period 格式
        try {
            repository.insertBudget(BudgetEntity("bad", "b", "monthly", null, "2026", 100, 1), "m-bad")
            fail("wrong period format accepted")
        } catch (_: IllegalArgumentException) { }

        assertNull(repository.getBudget("bad"))
        assertTrue(repository.listPendingMutations().isEmpty())
    }

    @Test
    fun test_budgetUpdate_versionMustIncrease() = runBlocking {
        val budget = BudgetEntity(
            id = "bud-ver", bookId = "b1", budgetType = "monthly",
            categoryId = null, period = "2026-09", amountCents = 100000, version = 1
        )
        repository.insertBudget(budget, "m-bud-ver-insert")

        // 版本号不增加应该失败
        try {
            repository.updateBudget(budget.copy(amountCents = 200000, version = 1), "m-bud-ver-bad")
            fail("same version accepted")
        } catch (_: IllegalArgumentException) { }

        // 版本号减少应该失败
        try {
            repository.updateBudget(budget.copy(amountCents = 200000, version = 0), "m-bud-ver-bad2")
            fail("decreased version accepted")
        } catch (_: IllegalArgumentException) { }

        // 版本增加应该成功
        repository.updateBudget(budget.copy(amountCents = 200000, version = 2), "m-bud-ver-good")
        val fetched = repository.getBudget("bud-ver")
        assertEquals(200000L, fetched!!.amountCents)
        assertEquals(2, fetched.version)
    }
}
