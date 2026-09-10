package com.aozora.app.db

import com.aozora.app.db.entity.PendingMutationEntity
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

/**
 * 第十二轮 T12-7（REQ-36 / 决策 #4）：transaction.update 全字段真编辑。
 *
 * 核心不变量：
 * - 成功：行更新（id 不变）+ 入队 OP_UPDATE，payload 全字段（含 bookId/includeInBudget），
 *   expectedVersion = 变更前版本（与 updateAccount 同契约）
 * - version 冲突（传 version != 当前+1）→ 抛错、行不动、不入队
 * - 软删行拒绝编辑
 * - clearAllData 与编辑链路正交（clearAllTables 连 pending 一并清空，无残留）
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class RepositoryTxUpdateTest {

    private lateinit var db: AozoraDatabase
    private lateinit var repository: Repository

    private lateinit var bookId: String
    private lateinit var categoryId: String

    @Before
    fun setUp() {
        db = AozoraDatabase.createInMemory(RuntimeEnvironment.getApplication())
        repository = Repository(db)
        runBlocking {
            repository.ensureSeedDefaults()
            val masterId = repository.getMasterBookId()
            bookId = repository.listBooks().first { it.id != masterId }.id
            categoryId = repository.listCategories().first { it.kind == "expense" }.id
        }
    }

    @After
    fun tearDown() { db.close() }

    private fun tx(id: String, version: Int = 1, amountCents: Long = 1250) = TransactionEntity(
        id = id, bookId = bookId, type = "expense", amountCents = amountCents,
        categoryId = categoryId, accountId = null, note = "午饭",
        bizDate = "2026-09-09", occurredAt = 1788845613031L, source = "manual",
        includeInBudget = true, version = version, deletedAt = null,
    )

    private fun seed(id: String, include: Boolean = true) = runBlocking {
        repository.insertTransaction(tx(id).copy(includeInBudget = include), "m-seed-$id")
    }

    // ─── 成功路径：行更新 + 入队内容 ───

    @Test
    fun update_success_writesRowAndQueuesFullFieldOpUpdate() {
        seed("t1")
        val updated = tx("t1", version = 2, amountCents = 4200).copy(
            note = "晚饭", includeInBudget = false,
        )
        runBlocking { repository.updateTransaction(updated, "m-update-t1") }

        val row = runBlocking { repository.getTransaction("t1") }!!
        assertEquals(4200L, row.amountCents)
        assertEquals("晚饭", row.note)
        assertEquals(2, row.version)
        assertFalse(row.includeInBudget)

        val mutation = runBlocking { repository.listPendingMutations() }
            .first { it.mutationId == "m-update-t1" }
        assertEquals("transaction", mutation.entityType)
        assertEquals(PendingMutationEntity.OP_UPDATE, mutation.operation)
        // expectedVersion 契约（T-M3-02）：变更**前**版本
        assertEquals(1, mutation.expectedVersion)
        // payload 全字段：bookId / includeInBudget 必须带（服务端白名单外静默忽略，不毒丸）
        val payload = JSONObject(mutation.payload)
        assertEquals("t1", payload.getString("id"))
        assertEquals(bookId, payload.getString("bookId"))
        assertEquals(4200L, payload.getLong("amountCents"))
        assertFalse(payload.getBoolean("includeInBudget"))
        assertEquals(2, payload.getInt("version"))
    }

    @Test
    fun update_crossBookMove_keepsIdAndPersistsNewBook() {
        seed("t1")
        // seed 只有一个非 master 账本 → 另建一个作移动目标
        val otherBook = com.aozora.app.db.entity.BookEntity(
            id = java.util.UUID.randomUUID().toString(),
            ownerId = "local", name = "账本B", icon = "📗", version = 1, archivedAt = null,
        )
        runBlocking {
            repository.insertBook(otherBook, "m-seed-bookB")
            repository.updateTransaction(tx("t1", version = 2).copy(bookId = otherBook.id), "m-move-t1")
        }
        val row = runBlocking { repository.getTransaction("t1") }!!
        assertEquals(otherBook.id, row.bookId)
        val payload = JSONObject(
            runBlocking { repository.listPendingMutations() }.first { it.mutationId == "m-move-t1" }.payload
        )
        assertEquals(otherBook.id, payload.getString("bookId"))
    }

    // ─── version 冲突：不写行、不入队 ───

    @Test
    fun update_versionNotIncreased_throwsAndWritesNothing() {
        seed("t1")
        // 传 version=1（= 当前，未 +1）→ require 拒绝；模拟前端拿的是陈旧数据
        var threw = false
        try {
            runBlocking { repository.updateTransaction(tx("t1", version = 1, amountCents = 999), "m-bad") }
        } catch (e: IllegalArgumentException) { threw = true }
        assertTrue(threw)
        val row = runBlocking { repository.getTransaction("t1") }!!
        assertEquals(1250L, row.amountCents)
        assertEquals(1, row.version)
        assertTrue(runBlocking { repository.listPendingMutations() }.none { it.mutationId == "m-bad" })
    }

    @Test
    fun update_staleExpectedVersion_rowsZero_rollsBackWithoutQueue() {
        seed("t1")
        // 直接走 DAO 冲突分支：构造与 current.version 不匹配的期望（并发下另一路已推进版本）。
        // Repository 同事务内先 getById 再 updateWithVersion(expected=current)，Room 事务串行化
        // 使 Kotlin 侧 require 先行拦截；这里验证 DAO 层乐观锁本身：WHERE id AND version 不中 → 0 行、不写。
        val rows = runBlocking {
            db.transactionDao().updateWithVersion(
                id = "t1", bookId = bookId, type = "expense", amountCents = 777,
                categoryId = categoryId, accountId = null, note = "x",
                bizDate = "2026-09-09", occurredAt = 1788845613031L, source = "manual",
                includeInBudget = true, newVersion = 5, expectedVersion = 42,
            )
        }
        assertEquals(0, rows)
        val row = runBlocking { repository.getTransaction("t1") }!!
        assertEquals(1250L, row.amountCents)
    }

    // ─── 软删行拒绝 ───

    @Test
    fun update_softDeletedRow_rejected() {
        seed("t1")
        runBlocking { repository.softDeleteTransaction("t1", "m-del") }
        var threw = false
        try {
            runBlocking { repository.updateTransaction(tx("t1", version = 3), "m-after-del") }
        } catch (e: IllegalArgumentException) { threw = true }
        assertTrue(threw)
        assertTrue(runBlocking { repository.listPendingMutations() }.none { it.mutationId == "m-after-del" })
    }

    // ─── 校验镜像 insert ───

    @Test
    fun update_rejectsInvalidFields() {
        seed("t1")
        val bad = listOf(
            tx("t1", version = 2).copy(amountCents = 0L),          // 非正金额
            tx("t1", version = 2).copy(type = "refund"),           // 未知类型
            tx("t1", version = 2).copy(source = "voice"),          // 未知源
            tx("t1", version = 2).copy(bizDate = "2026-9-9"),      // 非法日期格式
            tx("t1", version = 2).copy(categoryId = ""),           // 空分类
            tx("t1", version = 2).copy(bookId = "no-such-book"),   // 账本不存在
        )
        for ((i, b) in bad.withIndex()) {
            var threw = false
            try { runBlocking { repository.updateTransaction(b, "m-bad-$i") } }
            catch (e: IllegalArgumentException) { threw = true }
            assertTrue("case $i should reject", threw)
        }
        assertEquals(1, runBlocking { repository.getTransaction("t1") }!!.version)
    }

    // ─── 与 clearAllData 正交 ───

    @Test
    fun update_thenClearAllData_leavesNoRowsAndNoMutations() {
        seed("t1")
        runBlocking { repository.updateTransaction(tx("t1", version = 2), "m-update") }
        runBlocking { repository.clearAllData() }
        assertNull(runBlocking { repository.getTransaction("t1") })
        assertTrue(runBlocking { repository.listPendingMutations() }.isEmpty())
    }
}
