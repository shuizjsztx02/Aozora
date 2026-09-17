package com.aozora.app.bridge

import android.content.Context
import android.webkit.WebView
import com.aozora.app.db.AozoraDatabase
import com.aozora.app.db.Repository
import com.aozora.app.db.entity.TransactionEntity
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
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
 * T16-6（REQ-62 / 决策 #8）：transaction.listByBook 对 masterId 的聚合分支。
 *
 * 契约零变：命令名、入参（bookId）、返回结构（JSON 数组）与单账本路径完全一致；
 * 仅当 bookId == masterId 时走「全部账本聚合」，其余 bookId 原路径（含未知 id → 空）。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class AozoraBridgeMasterAggregateTest {

    private lateinit var db: AozoraDatabase
    private lateinit var repository: Repository
    private lateinit var masterId: String
    private lateinit var b1: String
    private lateinit var categoryId: String

    @Before
    fun setUp() {
        val context: Context = RuntimeEnvironment.getApplication()
        db = AozoraDatabase.createInMemory(context)
        repository = Repository(db)
        runBlocking {
            repository.ensureSeedDefaults()
            masterId = repository.getMasterBookId()!!
            b1 = repository.listBooks().first { it.id != masterId }.id
            categoryId = repository.listCategories().first { it.kind == "expense" }.id

            // b1 两条：当月 123.45（支出）+ 50.00（支出）
            repository.insertTransaction(tx("t1", b1, "2026-09-03", amountCents = 12345L), "m1")
            repository.insertTransaction(tx("t2", b1, "2026-09-05", amountCents = 5000L), "m2")
        }
    }

    @After
    fun tearDown() { db.close() }

    private fun tx(id: String, bookId: String, bizDate: String, amountCents: Long = 1000L) =
        TransactionEntity(
            id = id, bookId = bookId, type = "expense", amountCents = amountCents,
            categoryId = categoryId, accountId = null, note = "",
            bizDate = bizDate, occurredAt = 100L, source = "manual",
            includeInBudget = true, version = 1, deletedAt = null,
        )

    private fun newBridge() =
        AozoraBridge(repository, WebView(RuntimeEnvironment.getApplication()),
            syncContext = RuntimeEnvironment.getApplication())

    private fun request(bookId: String): org.json.JSONArray {
        val p = JSONObject().apply { put("bookId", bookId) }
        val resp = runBlocking {
            newBridge().dispatch("r", BridgeCommands.TX_LIST_BY_BOOK, p)
        }
        assertTrue("listByBook dispatch must succeed", resp.success)
        return org.json.JSONArray(resp.data ?: "[]")
    }

    @Test
    fun masterBookId_returnsAggregateOfAllNonMasterBooks() {
        val arr = request(masterId)
        val ids = (0 until arr.length()).map { arr.getJSONObject(it).getString("id") }.toSet()
        assertEquals(setOf("t1", "t2"), ids)
        // 金额合计 = 123.45 + 50.00
        val sum = (0 until arr.length()).sumOf { arr.getJSONObject(it).getLong("amountCents") }
        assertEquals(12345L + 5000L, sum)
    }

    @Test
    fun nonMasterBookId_returnsOnlyThatBook_transactions() {
        // 单账本路径零变（契约不变基线）
        val arr = request(b1)
        assertEquals(2, arr.length())
        val ids = (0 until arr.length()).map { arr.getJSONObject(it).getString("id") }.toSet()
        assertEquals(setOf("t1", "t2"), ids)
    }

    @Test
    fun unknownBookId_returnsEmptyArray() {
        val arr = request("does-not-exist")
        assertEquals(0, arr.length())
    }

    @Test
    fun masterAggregate_includesSecondBook() {
        // 追加第二本账本 + 一笔交易，确认聚合合并
        val b2 = UUID.randomUUID().toString()
        runBlocking {
            repository.insertBook(
                com.aozora.app.db.entity.BookEntity(
                    id = b2, ownerId = "local", name = "旅游账本",
                    icon = "✈️", version = 1, archivedAt = null,
                ),
                "m-book-$b2",
            )
            repository.insertTransaction(tx("t-b2", b2, "2026-09-09", amountCents = 7777L), "m-tb2")
        }

        val arr = request(masterId)
        val ids = (0 until arr.length()).map { arr.getJSONObject(it).getString("id") }.toSet()
        assertEquals(setOf("t1", "t2", "t-b2"), ids)
    }
}
