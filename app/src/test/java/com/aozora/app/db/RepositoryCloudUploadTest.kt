package com.aozora.app.db

import com.aozora.app.db.entity.ChatPersonaEntity
import com.aozora.app.db.entity.PendingMutationEntity
import com.aozora.app.db.entity.SavingsGoalEntity
import com.aozora.app.db.entity.ScheduledRuleEntity
import com.aozora.app.db.entity.SyncMetaEntity
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
 * T-M7-01（三实体上云）：既有本地数据的一次性上行。
 *
 * 旧版本客户端把 savings_goals / scheduled_rules / chat_personas 仅存本地、从不入队
 * pending mutation。升级后这些数据在云端并不存在，必须一次性上行，否则换机/多设备
 * 就永远看不到它们。
 *
 * 验证口径（任务卡「迁移用旧版本 fixture 验证」）：
 * - 数据源 = `src/test/resources/fixtures/legacy-local-entities-v9.json`，即旧版本
 *   Room v9 的行快照，**直连 DAO 写回**（忠实还原"旧版本留下的、无 mutation 的状态"）。
 * - 断言上行 mutation 的 entityType / entityId / operation / expectedVersion / payload。
 * - 断言幂等：二次调用不再入队（meta 标记）。
 * - 断言归档行不上行（归档＝用户已删除，换机不恢复符合预期）。
 * - 断言 bootstrap 的清空不会吃掉尚未推送的数据（mutation 队列跨清空存活）。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class RepositoryCloudUploadTest {

    private lateinit var db: AozoraDatabase
    private lateinit var repository: Repository
    private lateinit var fixture: JSONObject

    @Before
    fun setUp() {
        db = AozoraDatabase.createInMemory(RuntimeEnvironment.getApplication())
        repository = Repository(db)
        val stream = javaClass.classLoader!!
            .getResourceAsStream("fixtures/legacy-local-entities-v9.json")
            ?: error("fixture fixtures/legacy-local-entities-v9.json missing from test resources")
        fixture = JSONObject(stream.bufferedReader(Charsets.UTF_8).readText())
    }

    @After
    fun tearDown() { db.close() }

    // ─── fixture 装载（模拟旧版本本地库状态） ───

    private fun optLong(o: JSONObject, key: String): Long? =
        if (o.isNull(key)) null else o.getLong(key)

    private suspend fun seedLegacyLocalData() {
        val goals = fixture.getJSONArray("savings_goals")
        for (i in 0 until goals.length()) {
            val o = goals.getJSONObject(i)
            db.savingsGoalDao().insert(
                SavingsGoalEntity(
                    id = o.getString("id"), ownerId = o.getString("ownerId"),
                    name = o.getString("name"), targetCents = o.getLong("targetCents"),
                    currentCents = o.getLong("currentCents"),
                    icon = if (o.isNull("icon")) null else o.getString("icon"),
                    periodValue = if (o.isNull("periodValue")) null else o.getInt("periodValue"),
                    periodUnit = if (o.isNull("periodUnit")) null else o.getString("periodUnit"),
                    createdAt = o.getLong("createdAt"), updatedAt = o.getLong("updatedAt"),
                    archivedAt = optLong(o, "archivedAt"), version = o.getInt("version")
                )
            )
        }
        val rules = fixture.getJSONArray("scheduled_rules")
        for (i in 0 until rules.length()) {
            val o = rules.getJSONObject(i)
            db.scheduledRuleDao().insert(
                ScheduledRuleEntity(
                    id = o.getString("id"), ownerId = o.getString("ownerId"),
                    type = o.getString("type"), name = o.getString("name"),
                    amountCents = o.getLong("amountCents"), bookId = o.getString("bookId"),
                    categoryId = o.getString("categoryId"),
                    accountId = if (o.isNull("accountId")) null else o.getString("accountId"),
                    includeInBudget = o.getBoolean("includeInBudget"), note = o.getString("note"),
                    repeat = o.getString("repeat"), startDate = o.getString("startDate"),
                    endDate = if (o.isNull("endDate")) null else o.getString("endDate"),
                    enabled = o.getBoolean("enabled"),
                    lastRunDate = if (o.isNull("lastRunDate")) null else o.getString("lastRunDate"),
                    runHour = o.getInt("runHour"), runMinute = o.getInt("runMinute"),
                    createdAt = o.getLong("createdAt"), updatedAt = o.getLong("updatedAt"),
                    archivedAt = optLong(o, "archivedAt"), version = o.getInt("version")
                )
            )
        }
        val personas = fixture.getJSONArray("chat_personas")
        for (i in 0 until personas.length()) {
            val o = personas.getJSONObject(i)
            db.chatPersonaDao().insert(
                ChatPersonaEntity(
                    id = o.getString("id"), name = o.getString("name"),
                    avatar = if (o.isNull("avatar")) null else o.getString("avatar"),
                    persona = o.getString("persona"),
                    createdAt = o.getLong("createdAt"), updatedAt = o.getLong("updatedAt"),
                    archivedAt = optLong(o, "archivedAt"), version = o.getInt("version")
                )
            )
        }
    }

    private fun pending() = runBlocking { repository.listPendingMutations() }

    // ─── 前置：旧版本确实一条 mutation 都没有 ───

    @Test
    fun legacyFixture_seedsWithoutMutations() = runBlocking {
        seedLegacyLocalData()
        // 旧版本不变量的忠实还原：数据在（每类 2 行，含 1 行已归档），队列空
        // —— 这正是需要一次性迁移的原因。
        assertEquals(2, db.savingsGoalDao().listAll().size)
        assertEquals(2, db.scheduledRuleDao().listAll().size)
        assertEquals(2, db.chatPersonaDao().listAll().size)
        assertEquals(1, repository.listSavingsGoals().size)
        assertEquals(1, repository.listScheduledRules().size)
        assertEquals(1, repository.listChatPersonas().size)
        assertEquals(0, repository.listPendingMutations().size)
    }

    // ─── 一次性上行 ───

    @Test
    fun migrate_enqueuesOneInsertPerActiveRow() = runBlocking {
        seedLegacyLocalData()
        repository.migrateCloudEntitiesUp()

        val byType = pending().groupBy { it.entityType }
        // fixture 每类各 2 行，其中 1 行已归档 → 只上行活跃行
        assertEquals(setOf("savings_goal", "scheduled_rule", "chat_persona"), byType.keys)
        for ((_, list) in byType) {
            assertEquals(1, list.size)
            assertEquals(PendingMutationEntity.OP_INSERT, list[0].operation)
            assertNull("insert 无期望版本", list[0].expectedVersion)
            assertEquals(PendingMutationEntity.STATUS_PENDING, list[0].status)
        }
        assertEquals(
            "11111111-1111-4111-8111-111111111111",
            byType.getValue("savings_goal")[0].entityId
        )
        assertEquals(
            "22222222-2222-4222-8222-111111111111",
            byType.getValue("scheduled_rule")[0].entityId
        )
        assertEquals(
            "33333333-3333-4333-8333-111111111111",
            byType.getValue("chat_persona")[0].entityId
        )
        // 归档行（fixture 里 suffix 2222 的三条）必须留在本地、不上行
        val uploadedIds = pending().map { it.entityId }.toSet()
        assertFalse(uploadedIds.contains("11111111-1111-4111-8111-222222222222"))
        assertFalse(uploadedIds.contains("22222222-2222-4222-8222-222222222222"))
        assertFalse(uploadedIds.contains("33333333-3333-4333-8333-222222222222"))
        // mutationId 互不相同（pending_mutations 的 PK）
        assertEquals(3, pending().map { it.mutationId }.toSet().size)
    }

    @Test
    fun migrate_payloadCarriesRoomValuesVerbatim() = runBlocking {
        seedLegacyLocalData()
        repository.migrateCloudEntitiesUp()
        val byType = pending().associateBy { it.entityType }

        val goal = JSONObject(byType.getValue("savings_goal").payload)
        assertEquals("旅行基金", goal.getString("name"))
        assertEquals(500_000L, goal.getLong("targetCents"))
        assertEquals(120_000L, goal.getLong("currentCents"))
        assertEquals(6, goal.getInt("periodValue"))
        assertEquals("month", goal.getString("periodUnit"))
        assertEquals(2, goal.getInt("version"))

        val rule = JSONObject(byType.getValue("scheduled_rule").payload)
        assertEquals("monthly", rule.getString("repeat"))
        assertEquals("2026-01-01", rule.getString("startDate"))
        assertEquals("2026-09-01", rule.getString("lastRunDate"))
        assertEquals(9, rule.getInt("runHour"))
        assertEquals(30, rule.getInt("runMinute"))
        assertTrue(rule.isNull("endDate"))
        assertTrue(rule.getBoolean("includeInBudget"))

        val persona = JSONObject(byType.getValue("chat_persona").payload)
        assertEquals("小贝", persona.getString("name"))
        assertEquals("😊", persona.getString("avatar"))
        assertEquals(1, persona.getInt("version"))
    }

    @Test
    fun migrate_isIdempotent_acrossCalls() = runBlocking {
        seedLegacyLocalData()
        repository.migrateCloudEntitiesUp()
        val first = pending().map { it.mutationId }.toSet()
        repository.migrateCloudEntitiesUp()
        repository.migrateCloudEntitiesUp()
        // 标记落盘后不再重复入队（并发/重复触发安全）
        assertEquals(first, pending().map { it.mutationId }.toSet())
        assertEquals("1", repository.getMeta(SyncMetaEntity.KEY_CLOUD_ENTITIES_UPLOADED))
    }

    @Test
    fun migrate_afterUpload_mutationsSurviveBootstrapWipe() = runBlocking {
        seedLegacyLocalData()
        repository.migrateCloudEntitiesUp()
        // bootstrap 重建会清空三张表 —— 本地独有数据只能靠已入队的 mutation 存活
        repository.clearAllEntities()
        assertTrue(repository.listSavingsGoals().isEmpty())
        assertTrue(repository.listScheduledRules().isEmpty())
        assertTrue(repository.listChatPersonas().isEmpty())
        assertEquals(3, pending().size)
        assertEquals(3, pending().count { it.operation == PendingMutationEntity.OP_INSERT })
    }

    @Test
    fun migrate_afterLogout_reRunsForNextAccount() = runBlocking {
        seedLegacyLocalData()
        repository.migrateCloudEntitiesUp()
        // 登出/删号 → clearAllData（clearAllTables）：标记随 sync_meta 一并清空
        repository.clearAllData()
        assertNull(repository.getMeta(SyncMetaEntity.KEY_CLOUD_ENTITIES_UPLOADED))
        // 新账号下本地为空 → 无可上行，但标记会重新落盘（不把上一账号的数据带过去）
        repository.migrateCloudEntitiesUp()
        assertEquals(0, pending().size)
        assertEquals("1", repository.getMeta(SyncMetaEntity.KEY_CLOUD_ENTITIES_UPLOADED))
    }

    @Test
    fun migrate_emptyLocalState_isNoOpButMarksDone() = runBlocking {
        repository.migrateCloudEntitiesUp()
        assertEquals(0, pending().size)
        assertEquals("1", repository.getMeta(SyncMetaEntity.KEY_CLOUD_ENTITIES_UPLOADED))
    }
}