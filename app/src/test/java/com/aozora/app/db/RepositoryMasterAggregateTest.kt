package com.aozora.app.db

import com.aozora.app.db.entity.BookEntity
import com.aozora.app.db.entity.TransactionEntity
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import java.util.UUID

/**
 * T16-6（REQ-62 / 决策 #8）：总账本聚合视图数据层。
 *
 * 不变量：
 * - 聚合 = 全部活跃（未归档）非 master 账本的交易合并，按 bizDate/occurredAt 倒序
 * - master 自身交易（理论上不可能，但 Repository 不强制拦截 insertTransaction）被排除
 * - 已归档账本的交易被排除
 * - 无活跃非 master 账本 → 空列表（避免 IN () 非法 SQL）
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class RepositoryMasterAggregateTest {

    private lateinit var db: AozoraDatabase
    private lateinit var repository: Repository

    private lateinit var masterId: String
    private lateinit var b1: String
    private lateinit var b2: String
    private lateinit var categoryId: String

    @Before
    fun setUp() {
        db = AozoraDatabase.createInMemory(RuntimeEnvironment.getApplication())
        repository = Repository(db)
        runBlocking {
            repository.ensureSeedDefaults()
            masterId = repository.getMasterBookId()!!
            // 默认账本 = seed 生成的第一个非 master 账本
            b1 = repository.listBooks().first { it.id != masterId }.id
            categoryId = repository.listCategories().first { it.kind == "expense" }.id
            // 追加第二本账本（便于断言多账本合并）
            b2 = UUID.randomUUID().toString()
            repository.insertBook(
                BookEntity(id = b2, ownerId = "local", name = "旅游账本", icon = "✈️",
                    version = 1, archivedAt = null),
                "m-book-$b2",
            )
        }
    }

    @After
    fun tearDown() { db.close() }

    private fun tx(id: String, bookId: String, bizDate: String, occurredAt: Long = 100L,
                   amountCents: Long = 1000L) =
        TransactionEntity(
            id = id, bookId = bookId, type = "expense", amountCents = amountCents,
            categoryId = categoryId, accountId = null, note = "",
            bizDate = bizDate, occurredAt = occurredAt, source = "manual",
            includeInBudget = true, version = 1, deletedAt = null,
        )

    @Test
    fun multiBook_mergedAndSortedByBizDateDescOccurredAtDesc() {
        // b1 两条：同一天不同 occurredAt，再加一条更早日期
        runBlocking {
            repository.insertTransaction(tx("t-b1-d1-a", b1, "2026-09-10", occurredAt = 300L), "m1")
            repository.insertTransaction(tx("t-b1-d1-b", b1, "2026-09-10", occurredAt = 100L), "m2")
            repository.insertTransaction(tx("t-b1-d0",   b1, "2026-09-08", occurredAt = 500L), "m3")
            // b2 一条：晚于 b1 最早，早于 b1 同日
            repository.insertTransaction(tx("t-b2",      b2, "2026-09-09", occurredAt = 400L), "m4")
        }

        val result = runBlocking { repository.listTransactionsByMasterAggregate() }
        val ids = result.map { it.id }

        // 期望排序：09-10 occ=300 → 09-10 occ=100 → 09-09 → 09-08
        assertEquals(listOf("t-b1-d1-a", "t-b1-d1-b", "t-b2", "t-b1-d0"), ids)
    }

    @Test
    fun excludesMasterBookTransactions() {
        // 理论路径：master 恒无行（业务层不记账到 master），但 Repository.insertTransaction 不拦截 master bookId。
        // 聚合必须显式排除 masterId，防止异常路径污染聚合视图。
        runBlocking {
            repository.insertTransaction(tx("t-master", masterId, "2026-09-10"), "m-master")
            repository.insertTransaction(tx("t-b1", b1, "2026-09-10"), "m-b1")
        }
        val result = runBlocking { repository.listTransactionsByMasterAggregate() }
        assertEquals(listOf("t-b1"), result.map { it.id })
    }

    @Test
    fun excludesArchivedBookTransactions() {
        // b2 归档后，其交易不再出现在聚合里；b1 不受影响
        runBlocking {
            repository.insertTransaction(tx("t-b1", b1, "2026-09-10"), "m-b1")
            repository.insertTransaction(tx("t-b2", b2, "2026-09-10"), "m-b2")
        }
        // 直接调 BookDao.archive —— 测试场景不需要走 pending mutation 链路
        runBlocking { db.bookDao().archive(b2, System.currentTimeMillis()) }

        val result = runBlocking { repository.listTransactionsByMasterAggregate() }
        assertEquals(listOf("t-b1"), result.map { it.id })
    }

    @Test
    fun emptyWhenNoActiveNonMasterBooks() {
        // 归档掉默认账本（b1），仅留 master 与已归档账本 → 聚合空
        runBlocking {
            repository.insertTransaction(tx("t-b1", b1, "2026-09-10"), "m-b1")
            db.bookDao().archive(b1, System.currentTimeMillis())
        }
        val result = runBlocking { repository.listTransactionsByMasterAggregate() }
        assertTrue(result.isEmpty())
    }
}
