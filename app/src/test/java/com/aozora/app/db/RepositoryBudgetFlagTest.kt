package com.aozora.app.db

import com.aozora.app.db.entity.TransactionEntity
import com.aozora.app.sync.ServerChange
import com.aozora.app.sync.SyncChangeOp
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
 * 第十轮 T10-1（REQ-17 / 决策 #1）：includeInBudget 同步三件套——
 * push payload 带该键（经 toWirePayload 转 snake 后服务端白名单外静默忽略），
 * pull 侧服务端永不返回该键 → upsert 前读本地行保留标记（镜像 category.icon 防冲刷）。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class RepositoryBudgetFlagTest {

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

    private fun tx(id: String, include: Boolean, version: Int = 1) = TransactionEntity(
        id = id, bookId = bookId, type = "expense", amountCents = 1250,
        categoryId = categoryId, accountId = null, note = "午饭",
        bizDate = "2026-09-09", occurredAt = 1788845613031L, source = "manual",
        includeInBudget = include, version = version, deletedAt = null,
    )

    /** 服务端 pull 形态的 entity map（无 includeInBudget 键——服务端无此列）。 */
    private fun serverTxMap(id: String, version: Int, note: String = "服务端改名") = mapOf(
        "id" to id, "bookId" to bookId, "categoryId" to categoryId,
        "accountId" to null, "amountCents" to "1250", "type" to "expense",
        "bizDate" to "2026-09-09", "occurredAt" to "1788845613031",
        "note" to note, "source" to "manual", "version" to version.toString(),
    )

    // ─── push 侧：payload 带键 ───

    @Test
    fun insertTransaction_excludedFlag_payloadCarriesIt() = runBlocking {
        repository.insertTransaction(tx("t1", include = false), "m1")
        val payload = JSONObject(
            repository.listPendingMutations().first { it.entityId == "t1" }.payload
        )
        assertFalse(payload.getBoolean("includeInBudget"))
    }

    @Test
    fun insertTransaction_defaultEntity_includedInPayload() = runBlocking {
        // 实体默认值 = true（存量语义：聊天/批量路径不传 → 计入）
        val defaultFlag = TransactionEntity(
            id = "t2", bookId = bookId, type = "expense", amountCents = 100,
            categoryId = categoryId, accountId = null, note = "",
            bizDate = "2026-09-09", occurredAt = 1L, source = "manual",
            version = 1, deletedAt = null,
        ).includeInBudget
        assertTrue("实体缺省必须为计入", defaultFlag)
        repository.insertTransaction(tx("t2", include = true), "m2")
        val payload = JSONObject(
            repository.listPendingMutations().first { it.entityId == "t2" }.payload
        )
        assertTrue(payload.getBoolean("includeInBudget"))
    }

    // ─── pull 侧：防冲刷 ───

    @Test
    fun transactionPull_preservesLocalExcludedFlag() = runBlocking {
        repository.insertTransaction(tx("t1", include = false), "m1")
        repository.removeMutation("m1") // 模拟 push 成功，解除活跃 mutation 跳过条件

        repository.applyServerChanges(
            listOf(ServerChange(
                entityType = "transaction", entityId = "t1",
                operation = SyncChangeOp.UPDATE, version = 2,
                entityPayload = serverTxMap("t1", version = 2),
            )),
            newCursor = 0, ownerId = "u1"
        )
        val local = db.transactionDao().getById("t1")!!
        assertFalse("服务端不返回该键：本地不计入标记不得被冲刷", local.includeInBudget)
        assertEquals("其余字段正常合并", "服务端改名", local.note)
        assertEquals(2, local.version)
    }

    @Test
    fun transactionPull_newRow_defaultsToIncluded() = runBlocking {
        repository.applyServerChanges(
            listOf(ServerChange(
                entityType = "transaction", entityId = "t9",
                operation = SyncChangeOp.INSERT, version = 1,
                entityPayload = serverTxMap("t9", version = 1),
            )),
            newCursor = 0, ownerId = "u1"
        )
        assertTrue("本地无行的新交易缺省计入", db.transactionDao().getById("t9")!!.includeInBudget)
    }
}
