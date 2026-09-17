package com.aozora.app.db

import com.aozora.app.db.entity.PendingMutationEntity
import com.aozora.app.db.entity.SavingsGoalEntity
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * 第八轮 REQ-7：存钱目标 Repository 层 JVM 测试。
 * T-M7-01：存钱目标上云后，写操作**必须**与实体同事务入队 pending mutation
 * （上行路径反转自第八轮的"刻意不入队"，见 writes_enqueueMatchingMutations）。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class RepositorySavingsTest {

    private lateinit var db: AozoraDatabase
    private lateinit var repository: Repository

    @Before
    fun setUp() {
        db = AozoraDatabase.createInMemory(RuntimeEnvironment.getApplication())
        repository = Repository(db)
    }

    @After
    fun tearDown() { db.close() }

    private fun goal(id: String, target: Long = 100_000, current: Long = 0, version: Int = 1) =
        SavingsGoalEntity(
            id = id, ownerId = "local", name = "旅行基金",
            targetCents = target, currentCents = current, icon = "✈️",
            createdAt = 1000L, updatedAt = 1000L, archivedAt = null, version = version
        )

    @Test
    fun period_roundTrips() = runBlocking {
        repository.insertSavingsGoal(
            goal("g1").copy(periodValue = 3, periodUnit = "week"),
            "m1"
        )
        val g = repository.listSavingsGoals()[0]
        assertEquals(3, g.periodValue)
        assertEquals("week", g.periodUnit)
    }

    @Test
    fun period_nullMeansNoPeriod() = runBlocking {
        repository.insertSavingsGoal(goal("g1"), "m2") // 默认两列 null
        val g = repository.listSavingsGoals()[0]
        assertNull(g.periodValue)
        assertNull(g.periodUnit)
    }

    @Test
    fun insert_rejectsInvalidPeriodUnit() = runBlocking {
        var threw = false
        try { repository.insertSavingsGoal(goal("g1").copy(periodValue = 2, periodUnit = "decade"), "m3") }
        catch (e: IllegalArgumentException) { threw = true }
        assertTrue(threw)
    }

    @Test
    fun insert_rejectsHalfProvidedPeriod() = runBlocking {
        // 只给 value 不给 unit（或反之）→ 拒绝
        var threw = false
        try { repository.insertSavingsGoal(goal("g1").copy(periodValue = 5), "m4") }
        catch (e: IllegalArgumentException) { threw = true }
        assertTrue(threw)
    }

    @Test
    fun insert_thenList() = runBlocking {
        repository.insertSavingsGoal(goal("g1"), "m5")
        val list = repository.listSavingsGoals()
        assertEquals(1, list.size)
        assertEquals("g1", list[0].id)
        assertEquals("✈️", list[0].icon)
    }

    @Test
    fun writes_enqueueMatchingMutations() = runBlocking {
        repository.insertSavingsGoal(goal("g1"), "m6")
        repository.updateSavingsGoal(goal("g1", target = 100_000, current = 20_000, version = 2), "m7")
        repository.archiveSavingsGoal("g1", "m8")
        // T-M7-01：每次写都入队一条，entityType/entityId/operation/expectedVersion 全对齐
        val byId = repository.listPendingMutations().associateBy { it.mutationId }
        assertEquals(setOf("m6", "m7", "m8"), byId.keys)
        for (m in byId.values) {
            assertEquals("savings_goal", m.entityType)
            assertEquals("g1", m.entityId)
            assertEquals(PendingMutationEntity.STATUS_PENDING, m.status)
        }
        assertEquals(PendingMutationEntity.OP_INSERT, byId.getValue("m6").operation)
        assertEquals(PendingMutationEntity.OP_UPDATE, byId.getValue("m7").operation)
        assertEquals(PendingMutationEntity.OP_DELETE, byId.getValue("m8").operation)
        // insert 无期望版本；update/archive 捕获变更**前**版本（乐观锁）
        assertNull(byId.getValue("m6").expectedVersion)
        assertEquals(1, byId.getValue("m7").expectedVersion)
        assertEquals(2, byId.getValue("m8").expectedVersion)
        // payload 是 camelCase 全字段（push 时由 SyncPayloadMapper 转 wire 形态）
        assertTrue(byId.getValue("m7").payload.contains("\"targetCents\":100000"))
        assertTrue(byId.getValue("m8").payload.contains("\"id\":\"g1\""))
    }

    @Test
    fun update_incrementsCurrent_andRequiresVersionIncrease() = runBlocking {
        repository.insertSavingsGoal(goal("g1", current = 0), "m9")
        repository.updateSavingsGoal(goal("g1", current = 30_000, version = 2), "m10")
        assertEquals(30_000, repository.listSavingsGoals()[0].currentCents)
        // 版本不递增应抛错
        var threw = false
        try { repository.updateSavingsGoal(goal("g1", current = 50_000, version = 2), "m11") }
        catch (e: IllegalArgumentException) { threw = true }
        assertTrue(threw)
    }

    @Test
    fun archive_removesFromActive() = runBlocking {
        repository.insertSavingsGoal(goal("g1"), "m12")
        repository.archiveSavingsGoal("g1", "m13")
        assertTrue(repository.listSavingsGoals().isEmpty())
    }

    @Test
    fun insert_rejectsNonPositiveTarget() = runBlocking {
        var threw = false
        try { repository.insertSavingsGoal(goal("g1", target = 0), "m14") }
        catch (e: IllegalArgumentException) { threw = true }
        assertTrue(threw)
    }

    @Test
    fun clearAllData_wipesSavings() = runBlocking {
        repository.insertSavingsGoal(goal("g1"), "m15")
        repository.clearAllData()
        assertTrue(repository.listSavingsGoals().isEmpty())
    }
}
