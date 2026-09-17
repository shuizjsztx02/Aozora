package com.aozora.app.db

import com.aozora.app.sync.BootstrapResponse
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
 * T-M7-01（三实体上云）：savings_goal / scheduled_rule / chat_persona 的
 * **云端→本地**合并路径 —— pull（applyServerChanges）与 bootstrap（applyBootstrap）。
 *
 * 三实体与既有五实体的关键差异：本地表带 created_at/updated_at 列（epoch 毫秒），
 * 而服务端出 ISO 8601，故合并时需转换；删除走归档（archived_at）而非物删。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class RepositoryCloudEntitySyncTest {

    private lateinit var db: AozoraDatabase
    private lateinit var repository: Repository

    private val photoAvatar = "data:image/png;base64," + "A".repeat(200)

    @Before
    fun setUp() {
        db = AozoraDatabase.createInMemory(RuntimeEnvironment.getApplication())
        repository = Repository(db)
    }

    @After
    fun tearDown() { db.close() }

    private fun change(
        type: String, id: String, op: SyncChangeOp, version: Int,
        payload: Map<String, String?>
    ) = ServerChange(
        entityType = type, entityId = id, operation = op, version = version,
        entityPayload = payload
    )

    private val goalPayload = mapOf(
        "id" to "g1", "name" to "旅行基金", "targetCents" to "500000",
        "currentCents" to "120000", "icon" to null,
        "periodValue" to "6", "periodUnit" to "month", "version" to "3",
        "createdAt" to "2026-09-04T12:00:00+00:00",
        "updatedAt" to "2026-09-05T12:00:00+00:00",
    )

    private val rulePayload = mapOf(
        "id" to "r1", "type" to "expense", "name" to "房租", "amountCents" to "350000",
        "bookId" to "book-1", "categoryId" to "cat-1", "accountId" to null,
        "includeInBudget" to "false", "note" to "每月一号", "repeat" to "monthly",
        "startDate" to "2026-01-01", "endDate" to "2026-12-31", "enabled" to "true",
        "lastRunDate" to "2026-09-01", "runHour" to "9", "runMinute" to "30",
        "version" to "5",
        "createdAt" to "2026-09-04T12:00:00+00:00",
        "updatedAt" to "2026-09-05T12:00:00+00:00",
    )

    private val personaPayload = mapOf(
        "id" to "p1", "name" to "小贝", "avatar" to "avatar-x",
        "persona" to "一段人设", "version" to "2",
        "createdAt" to "2026-09-04T12:00:00+00:00",
        "updatedAt" to "2026-09-05T12:00:00+00:00",
    )

    // ── pull：upsert ───

    @Test
    fun pull_savingsGoal_upsertsAllFields() = runBlocking {
        repository.applyServerChanges(
            listOf(change("savings_goal", "g1", SyncChangeOp.INSERT, 3, goalPayload)),
            newCursor = 10, ownerId = "u1"
        )
        val g = repository.getSavingsGoal("g1")!!
        assertEquals("旅行基金", g.name)
        assertEquals(500_000L, g.targetCents)
        assertEquals(120_000L, g.currentCents)
        assertEquals(6, g.periodValue)
        assertEquals("month", g.periodUnit)
        assertEquals(3, g.version)
        // ISO → epoch 毫秒（本地列语义）
        assertEquals(1788523200000L, g.createdAt)
        assertEquals(1788609600000L, g.updatedAt)
        assertNull(g.archivedAt)
        assertEquals(10L, repository.getSyncCursor())
    }

    @Test
    fun pull_scheduledRule_carriesBooleansAndDates() = runBlocking {
        repository.applyServerChanges(
            listOf(change("scheduled_rule", "r1", SyncChangeOp.INSERT, 5, rulePayload)),
            newCursor = 11, ownerId = "u1"
        )
        val r = repository.getScheduledRule("r1")!!
        assertEquals("expense", r.type)
        assertEquals(350_000L, r.amountCents)
        assertEquals("book-1", r.bookId)
        assertNull(r.accountId)
        assertFalse(r.includeInBudget)
        assertTrue(r.enabled)
        assertEquals("monthly", r.repeat)
        assertEquals("2026-01-01", r.startDate)
        assertEquals("2026-12-31", r.endDate)
        assertEquals("2026-09-01", r.lastRunDate)
        assertEquals(9, r.runHour)
        assertEquals(30, r.runMinute)
    }

    @Test
    fun pull_chatPersona_upsertsWithoutOwnerColumn() = runBlocking {
        repository.applyServerChanges(
            listOf(change("chat_persona", "p1", SyncChangeOp.INSERT, 2, personaPayload)),
            newCursor = 12, ownerId = "u1"
        )
        val p = repository.getChatPersona("p1")!!
        assertEquals("小贝", p.name)
        assertEquals("avatar-x", p.avatar)
        assertEquals("一段人设", p.persona)
        assertEquals(2, p.version)
    }

    @Test
    fun pull_missingTimestamps_fallBackToNow() = runBlocking {
        // 服务端恒返回时间戳；万一缺失也不能整条丢弃
        val before = System.currentTimeMillis()
        repository.applyServerChanges(
            listOf(change("savings_goal", "g1", SyncChangeOp.INSERT, 1, mapOf(
                "id" to "g1", "name" to "无时间戳", "targetCents" to "1000",
                "currentCents" to "0", "version" to "1",
            ))),
            newCursor = 13, ownerId = "u1"
        )
        val g = repository.getSavingsGoal("g1")!!
        assertTrue(g.createdAt >= before)
        assertTrue(g.updatedAt >= before)
    }

    // ─── pull：delete → 归档 ───

    @Test
    fun pull_delete_archivesAllThreeTypes() = runBlocking {
        repository.applyServerChanges(
            listOf(
                change("savings_goal", "g1", SyncChangeOp.INSERT, 3, goalPayload),
                change("scheduled_rule", "r1", SyncChangeOp.INSERT, 5, rulePayload),
                change("chat_persona", "p1", SyncChangeOp.INSERT, 2, personaPayload),
            ),
            newCursor = 20, ownerId = "u1"
        )
        repository.applyServerChanges(
            listOf(
                change("savings_goal", "g1", SyncChangeOp.DELETE, 4, emptyMap()),
                change("scheduled_rule", "r1", SyncChangeOp.DELETE, 6, emptyMap()),
                change("chat_persona", "p1", SyncChangeOp.DELETE, 3, emptyMap()),
            ),
            newCursor = 21, ownerId = "u1"
        )
        assertTrue(repository.listSavingsGoals().isEmpty())
        assertTrue(repository.listScheduledRules().isEmpty())
        assertTrue(repository.listChatPersonas().isEmpty())
        // 归档软删：行保留、版本与服务端对齐（引擎不再遍历 active 行）
        assertEquals(4, repository.getSavingsGoal("g1")!!.version)
        assertNotNull(repository.getSavingsGoal("g1")!!.archivedAt)
        assertEquals(6, repository.getScheduledRule("r1")!!.version)
        assertNotNull(repository.getScheduledRule("r1")!!.archivedAt)
        assertEquals(3, repository.getChatPersona("p1")!!.version)
        assertNotNull(repository.getChatPersona("p1")!!.archivedAt)
    }

    // ─── pull：本地待推送优先 ───

    @Test
    fun pull_skipsEntityWithActiveMutation() = runBlocking {
        repository.insertSavingsGoal(
            com.aozora.app.db.entity.SavingsGoalEntity(
                id = "g1", ownerId = "u1", name = "本地未推送", targetCents = 900_000,
                currentCents = 0, icon = null, createdAt = 1L, updatedAt = 1L,
                archivedAt = null, version = 1
            ),
            "m1"
        )
        val pulled = goalPayload.toMutableMap().apply { put("name", "服务端版本") }
        repository.applyServerChanges(
            listOf(change("savings_goal", "g1", SyncChangeOp.UPDATE, 3, pulled)),
            newCursor = 30, ownerId = "u1"
        )
        // 活跃 mutation 未清 → 本地待推送版本优先，不被 pull 覆盖（cursor 仍推进）
        assertEquals("本地未推送", repository.getSavingsGoal("g1")!!.name)
        assertEquals(30L, repository.getSyncCursor())
    }

    // ─── pull：照片头像防冲刷（镜像 book/account 决策 #7②） ───

    @Test
    fun pull_persona_nonDataAvatar_preservesLocalPhoto() = runBlocking {
        repository.insertChatPersona(
            com.aozora.app.db.entity.ChatPersonaEntity(
                id = "p1", name = "小贝", avatar = photoAvatar, persona = "人设",
                createdAt = 1L, updatedAt = 1L, archivedAt = null, version = 1
            ),
            "m1"
        )
        repository.removeMutation("m1")
        repository.applyServerChanges(
            listOf(change("chat_persona", "p1", SyncChangeOp.UPDATE, 2,
                personaPayload.toMutableMap().apply { put("name", "改名了") })),
            newCursor = 31, ownerId = "u1"
        )
        val p = repository.getChatPersona("p1")!!
        assertEquals("照片头像设备本地专属：服务端非 data: 值不得冲刷", photoAvatar, p.avatar)
        assertEquals("其余字段正常合并", "改名了", p.name)
    }

    @Test
    fun pull_savingsGoal_nonDataIcon_preservesLocalPhoto() = runBlocking {
        repository.insertSavingsGoal(
            com.aozora.app.db.entity.SavingsGoalEntity(
                id = "g1", ownerId = "u1", name = "旅行基金", targetCents = 1000,
                currentCents = 0, icon = photoAvatar, createdAt = 1L, updatedAt = 1L,
                archivedAt = null, version = 1
            ),
            "m1"
        )
        repository.removeMutation("m1")
        repository.applyServerChanges(
            listOf(change("savings_goal", "g1", SyncChangeOp.UPDATE, 2,
                goalPayload.toMutableMap().apply { put("icon", "") })),
            newCursor = 32, ownerId = "u1"
        )
        val g = repository.getSavingsGoal("g1")!!
        assertEquals(photoAvatar, g.icon)
        assertEquals(500_000L, g.targetCents)
    }

    // ─── pull：非 delete 变更里带 archivedAt（幂等上行的对齐路径） ───

    @Test
    fun pull_updateWithArchivedAt_archivesInsteadOfResurrecting() = runBlocking {
        // 服务端对"重复上行已存在实体"的幂等命中会记一条 update 变更；若该实体
        // 已归档，变更里的 entity 带 archived_at。客户端必须按归档合并 —— 硬编码
        // archivedAt=null 会把用户删掉的行复活。
        repository.insertChatPersona(
            com.aozora.app.db.entity.ChatPersonaEntity(
                id = "p1", name = "小贝", avatar = null, persona = "人设",
                createdAt = 1L, updatedAt = 1L, archivedAt = null, version = 1
            ),
            "m1"
        )
        repository.removeMutation("m1")
        repository.applyServerChanges(
            listOf(change("chat_persona", "p1", SyncChangeOp.UPDATE, 2, mapOf(
                "id" to "p1", "name" to "小贝", "avatar" to null, "persona" to "人设",
                "archivedAt" to "2026-09-17T00:00:00+00:00",
            ))),
            newCursor = 40, ownerId = "u1"
        )
        assertTrue("已归档行不得被 update 变更复活", repository.listChatPersonas().isEmpty())
        val row = repository.getChatPersona("p1")!!
        assertNotNull(row.archivedAt)
        assertEquals(2, row.version)
    }

    @Test
    fun pull_updateAlignsLocalVersionToCloud() = runBlocking {
        // 迁移上行的本地版本可能高于云端（幂等命中不覆盖云端）；echo-pull 的
        // update 变更把本地版本对齐到云端，否则下一次 update 的 expectedVersion
        // 不符会被判 conflict 终态失败。
        repository.insertSavingsGoal(
            com.aozora.app.db.entity.SavingsGoalEntity(
                id = "g1", ownerId = "u1", name = "本地名字", targetCents = 100_000,
                currentCents = 0, icon = null, createdAt = 1L, updatedAt = 1L,
                archivedAt = null, version = 5
            ),
            "m1"
        )
        repository.removeMutation("m1")
        repository.applyServerChanges(
            listOf(change("savings_goal", "g1", SyncChangeOp.UPDATE, 1, goalPayload)),
            newCursor = 41, ownerId = "u1"
        )
        val g = repository.getSavingsGoal("g1")!!
        assertEquals("版本对齐到云端（云端权威）", 1, g.version)
        assertEquals("旅行基金", g.name)
        assertEquals(500_000L, g.targetCents)
    }

    // ─── bootstrap 重建 ───

    @Test
    fun bootstrap_rebuildsAllThreeTypes() = runBlocking {
        val response = BootstrapResponse(
            books = emptyList(), categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            savingsGoals = listOf(goalPayload),
            scheduledRules = listOf(rulePayload),
            chatPersonas = listOf(personaPayload),
            cursor = 99, checksum = "x",
        )
        repository.clearAllEntities()
        assertTrue(repository.listAllActiveEntities()["savings_goals"]!!.isEmpty())
        repository.applyBootstrap(response, ownerId = "u1")

        assertEquals(1, repository.listSavingsGoals().size)
        assertEquals(1, repository.listScheduledRules().size)
        assertEquals(1, repository.listChatPersonas().size)
        assertEquals("旅行基金", repository.getSavingsGoal("g1")!!.name)
        assertEquals(1788523200000L, repository.getSavingsGoal("g1")!!.createdAt)
        assertEquals(99L, repository.getSyncCursor())
        // 计数校验口径（SyncEngine.runBootstrap 依赖）：三类各 1 条活跃实体
        val entities = repository.listAllActiveEntities()
        assertEquals(1, entities["savings_goals"]!!.size)
        assertEquals(1, entities["scheduled_rules"]!!.size)
        assertEquals(1, entities["chat_personas"]!!.size)
        // bootstrap 不入队任何 mutation（合并路径不产生上行）
        assertEquals(0, repository.listPendingMutations().size)
    }

    @Test
    fun bootstrap_emptyCloudLists_clearsLocalCopies() = runBlocking {
        // 服务端快照为空（如刚上云、或旧服务端不返回这三类）→ 本地副本清空后不再重建
        repository.insertChatPersona(
            com.aozora.app.db.entity.ChatPersonaEntity(
                id = "p1", name = "小贝", avatar = null, persona = "人设",
                createdAt = 1L, updatedAt = 1L, archivedAt = null, version = 1
            ),
            "m1"
        )
        repository.clearAllEntities()
        assertEquals(0, repository.listAllActiveEntities()["chat_personas"]!!.size)
    }

    /** 上行 payload 的 wire 形态断言：savings_goal 的 camelCase key 必须能被服务端读到。 */
    @Test
    fun pushPayload_wireKeysMatchServerContract() = runBlocking {
        repository.insertSavingsGoal(
            com.aozora.app.db.entity.SavingsGoalEntity(
                id = "g1", ownerId = "u1", name = "旅行基金", targetCents = 500_000,
                currentCents = 120_000, icon = "🎯", periodValue = 6, periodUnit = "month",
                createdAt = 1L, updatedAt = 1L, archivedAt = null, version = 1
            ),
            "m1"
        )
        val payload = JSONObject(repository.listPendingMutations().first().payload)
        val wire = com.aozora.app.sync.SyncPayloadMapper.toWirePayload(payload.toString())
        // 服务端 _apply_insert 直读这些 snake_case 键
        assertEquals("旅行基金", wire.getString("name"))
        assertEquals(500_000, wire.getInt("target_cents"))
        assertEquals(120_000, wire.getInt("current_cents"))
        assertEquals(6, wire.getInt("period_value"))
        assertEquals("month", wire.getString("period_unit"))
        assertEquals(1, wire.getInt("version"))
    }
}