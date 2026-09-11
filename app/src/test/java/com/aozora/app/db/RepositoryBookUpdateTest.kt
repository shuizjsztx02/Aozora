package com.aozora.app.db

import com.aozora.app.db.entity.BookEntity
import com.aozora.app.db.entity.PendingMutationEntity
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
 * 第十四轮 T14-2（REQ-39 / 决策 #3）：book.update 全链路 — Repository 层。
 *
 * 镜像 updateAccount 契约（RepositorySeedTest REQ-6 组 + RepositoryTxUpdateTest 骨架）：
 * - 成功：行更新（id 不变）+ 入队 OP_UPDATE，payload = bookToJson 全字段，
 *   expectedVersion = 变更前版本
 * - version 冲突（未递增）→ 抛错、行不动、不入队
 * - 不存在 / 已归档拒绝
 * - master 总账本只读（meta 键 master_book_id 派生，对齐 handleBookList 语义）拒绝编辑
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class RepositoryBookUpdateTest {

    private lateinit var db: AozoraDatabase
    private lateinit var repository: Repository

    @Before
    fun setUp() {
        db = AozoraDatabase.createInMemory(RuntimeEnvironment.getApplication())
        repository = Repository(db)
    }

    @After
    fun tearDown() { db.close() }

    private fun book(id: String, version: Int = 1, name: String = "旅行", icon: String = "✈️") =
        BookEntity(
            id = id, ownerId = "local", name = name, icon = icon,
            version = version, archivedAt = null
        )

    private fun seed(id: String) = runBlocking { repository.insertBook(book(id), "m-seed-$id") }

    // ─── 成功路径：行更新 + 入队内容 ───

    @Test
    fun updateBook_success_writesRowAndQueuesFullFieldOpUpdate() {
        seed("b1")
        runBlocking { repository.updateBook(book("b1", version = 2, name = "出差", icon = "💼"), "m-upd") }

        val row = runBlocking { repository.getBook("b1") }!!
        assertEquals("出差", row.name)
        assertEquals("💼", row.icon)
        assertEquals(2, row.version)

        val mutation = runBlocking { repository.listPendingMutations() }
            .single { it.mutationId == "m-upd" }
        assertEquals("book", mutation.entityType)
        assertEquals("b1", mutation.entityId)
        assertEquals(PendingMutationEntity.OP_UPDATE, mutation.operation)
        // expectedVersion 契约（T-M3-02）：变更**前**版本
        assertEquals(1, mutation.expectedVersion)
        // payload = bookToJson 全字段（BookEntity 可编辑字段即 name/icon）
        val payload = JSONObject(mutation.payload)
        assertEquals("b1", payload.getString("id"))
        assertEquals("local", payload.getString("ownerId"))
        assertEquals("出差", payload.getString("name"))
        assertEquals("💼", payload.getString("icon"))
        assertEquals(2, payload.getInt("version"))
    }

    // ─── version 冲突：不写行、不入队 ───

    @Test
    fun updateBook_versionNotIncreased_throwsAndWritesNothing() {
        seed("b1")
        // 传 version=1（= 当前，未 +1）→ require 拒绝；模拟前端拿的是陈旧数据
        try {
            runBlocking { repository.updateBook(book("b1", version = 1, name = "改名"), "m-eq") }
            fail("equal version accepted")
        } catch (_: IllegalArgumentException) { }
        try {
            runBlocking { repository.updateBook(book("b1", version = 0), "m-zero") }
            fail("zero version accepted")
        } catch (_: IllegalArgumentException) { }

        assertEquals("旅行", runBlocking { repository.getBook("b1") }!!.name)
        assertEquals(1, runBlocking { repository.getBook("b1") }!!.version)
        assertTrue(
            runBlocking { repository.listPendingMutations() }
                .none { it.mutationId == "m-eq" || it.mutationId == "m-zero" }
        )
    }

    @Test
    fun updateBook_notFound_throws() {
        try {
            runBlocking { repository.updateBook(book("missing", version = 2), "m-x") }
            fail("missing book accepted")
        } catch (_: IllegalArgumentException) { }
    }

    @Test
    fun updateBook_archived_throws() {
        seed("b-arch")
        runBlocking { db.bookDao().update(book("b-arch").copy(archivedAt = 1L)) }

        try {
            runBlocking { repository.updateBook(book("b-arch", version = 2), "m-x") }
            fail("archived book accepted")
        } catch (_: IllegalArgumentException) { }
        assertTrue(
            runBlocking { repository.listPendingMutations() }.none { it.mutationId == "m-x" }
        )
    }

    // ─── master 系统账本只读（决策 #3，对齐现有 master_book_id 语义） ───

    @Test
    fun updateBook_masterBook_rejected() {
        runBlocking { repository.ensureSeedDefaults() }
        val masterId = runBlocking { repository.getMasterBookId() }!!
        val current = runBlocking { repository.getBook(masterId) }!!

        try {
            runBlocking {
                repository.updateBook(current.copy(name = "改总账本", version = current.version + 1), "m-master")
            }
            fail("master book accepted for update")
        } catch (_: IllegalArgumentException) { }

        // 行不动、不入队
        assertEquals("总账本", runBlocking { repository.getBook(masterId) }!!.name)
        assertTrue(
            runBlocking { repository.listPendingMutations() }.none { it.mutationId == "m-master" }
        )
    }

    // ─── 校验镜像 insertAccount/updateAccount ───

    @Test
    fun updateBook_rejectsInvalidFields() {
        seed("b1")
        val bad = listOf(
            book("b1", version = 2).copy(name = ""),   // 空名
            book("", version = 2, name = "x"),         // 空 id
        )
        for ((i, b) in bad.withIndex()) {
            try {
                runBlocking { repository.updateBook(b, "m-bad-$i") }
                fail("case $i should reject")
            } catch (_: IllegalArgumentException) { }
        }
        // mutationId 空也拒绝
        try {
            runBlocking { repository.updateBook(book("b1", version = 2), "") }
            fail("blank mutationId accepted")
        } catch (_: IllegalArgumentException) { }
        assertEquals(1, runBlocking { repository.getBook("b1") }!!.version)
    }
}
