package com.aozora.app.db

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
 * 核心不变量：存钱写操作**绝不产生 pending mutation**（否则服务端整批 422 毒丸）。
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
    fun insert_thenList() = runBlocking {
        repository.insertSavingsGoal(goal("g1"))
        val list = repository.listSavingsGoals()
        assertEquals(1, list.size)
        assertEquals("g1", list[0].id)
        assertEquals("✈️", list[0].icon)
    }

    @Test
    fun writes_never_enqueuePendingMutations() = runBlocking {
        repository.insertSavingsGoal(goal("g1"))
        repository.updateSavingsGoal(goal("g1", target = 100_000, current = 20_000, version = 2))
        repository.archiveSavingsGoal("g1")
        // 关键：全程 0 条 pending mutation
        assertEquals(0, repository.listPendingMutations().size)
    }

    @Test
    fun update_incrementsCurrent_andRequiresVersionIncrease() = runBlocking {
        repository.insertSavingsGoal(goal("g1", current = 0))
        repository.updateSavingsGoal(goal("g1", current = 30_000, version = 2))
        assertEquals(30_000, repository.listSavingsGoals()[0].currentCents)
        // 版本不递增应抛错
        var threw = false
        try { repository.updateSavingsGoal(goal("g1", current = 50_000, version = 2)) }
        catch (e: IllegalArgumentException) { threw = true }
        assertTrue(threw)
    }

    @Test
    fun archive_removesFromActive() = runBlocking {
        repository.insertSavingsGoal(goal("g1"))
        repository.archiveSavingsGoal("g1")
        assertTrue(repository.listSavingsGoals().isEmpty())
    }

    @Test
    fun insert_rejectsNonPositiveTarget() = runBlocking {
        var threw = false
        try { repository.insertSavingsGoal(goal("g1", target = 0)) }
        catch (e: IllegalArgumentException) { threw = true }
        assertTrue(threw)
    }

    @Test
    fun clearAllData_wipesSavings() = runBlocking {
        repository.insertSavingsGoal(goal("g1"))
        repository.clearAllData()
        assertTrue(repository.listSavingsGoals().isEmpty())
    }
}
