package com.aozora.app.db

import com.aozora.app.db.entity.ScheduledRuleEntity
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
 * 第十一轮 T11-4（REQ-26 / 决策 #4）：定时规则 Repository 层 JVM 测试。
 * 核心不变量（镜像 RepositorySavingsTest）：规则写操作**绝不产生 pending
 * mutation**（服务端不认 scheduled_rules，入队=422 毒丸）；引擎生成的交易
 * 由 ScheduledTxEngineTest 验证正常入队。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class RepositoryScheduledTest {

    private lateinit var db: AozoraDatabase
    private lateinit var repository: Repository

    @Before
    fun setUp() {
        db = AozoraDatabase.createInMemory(RuntimeEnvironment.getApplication())
        repository = Repository(db)
    }

    @After
    fun tearDown() { db.close() }

    private fun rule(
        id: String,
        version: Int = 1,
        repeat: String = "daily",
        startDate: String = "2026-09-01",
        endDate: String? = null,
        amountCents: Long = 1500,
        name: String = "早餐",
    ) = ScheduledRuleEntity(
        id = id, ownerId = "local", type = "expense", name = name,
        amountCents = amountCents, bookId = "b1", categoryId = "c1",
        accountId = null, includeInBudget = true, note = "公司楼下",
        repeat = repeat, startDate = startDate, endDate = endDate,
        enabled = true, lastRunDate = null,
        createdAt = 1000L, updatedAt = 1000L, archivedAt = null, version = version
    )

    @Test
    fun insert_thenList_roundTripsAllColumns() = runBlocking {
        repository.insertScheduledRule(
            rule("r1").copy(accountId = "a1", includeInBudget = false, lastRunDate = "2026-09-08")
        )
        val got = repository.listScheduledRules()[0]
        assertEquals("r1", got.id)
        assertEquals("a1", got.accountId)
        assertFalse(got.includeInBudget)
        assertEquals("2026-09-08", got.lastRunDate)
        val s = db.scheduledRuleDao().getById("r1")!!
        assertEquals("expense", s.type)
        assertEquals("早餐", s.name)
        assertEquals(1500L, s.amountCents)
        assertEquals("公司楼下", s.note)
        assertEquals("2026-09-01", s.startDate)
        assertNull(s.endDate)
        assertTrue(s.enabled)
    }

    @Test
    fun writes_never_enqueuePendingMutations() = runBlocking {
        repository.insertScheduledRule(rule("r1"))
        repository.updateScheduledRule(rule("r1", version = 2))
        repository.archiveScheduledRule("r1")
        // 关键：全程 0 条 pending mutation
        assertEquals(0, repository.listPendingMutations().size)
    }

    @Test
    fun update_sameVersion_rejected_noDoubleApply() = runBlocking {
        repository.insertScheduledRule(rule("r1"))
        repository.updateScheduledRule(rule("r1", version = 2, name = "晚餐"))
        assertEquals("晚餐", repository.getScheduledRule("r1")!!.name)
        // 版本不递增（重复提交同一 update）→ 拒绝，行保持 v2 状态
        var threw = false
        try { repository.updateScheduledRule(rule("r1", version = 2, name = "宵夜")) }
        catch (e: IllegalArgumentException) { threw = true }
        assertTrue(threw)
        assertEquals("晚餐", repository.getScheduledRule("r1")!!.name)
    }

    @Test
    fun archive_removesFromActive() = runBlocking {
        repository.insertScheduledRule(rule("r1"))
        repository.archiveScheduledRule("r1")
        assertTrue(repository.listScheduledRules().isEmpty())
        // 归档行仍可 get（引擎只遍历 active，行不物理删除）
        assertNotNull(repository.getScheduledRule("r1"))
        // 重复归档 → 拒绝
        var threw = false
        try { repository.archiveScheduledRule("r1") } catch (e: IllegalArgumentException) { threw = true }
        assertTrue(threw)
    }

    @Test
    fun insert_rejectsInvalidEnumAndAmount() = runBlocking {
        for (bad in listOf(
            rule("r1").copy(repeat = "fortnightly"),
            rule("r1").copy(type = "transfer"),
            rule("r1").copy(amountCents = 0),
            rule("r1").copy(amountCents = -5),
            rule("r1").copy(name = ""),
        )) {
            var threw = false
            try { repository.insertScheduledRule(bad) } catch (e: IllegalArgumentException) { threw = true }
            assertTrue("应拒绝: ${bad.repeat}/${bad.type}/${bad.amountCents}", threw)
        }
    }

    @Test
    fun insert_rejectsBadDates() = runBlocking {
        for (bad in listOf(
            rule("r1").copy(startDate = "2026/09/01"),          // 非 ISO
            rule("r1").copy(startDate = "2026-02-30"),          // 非法日期
            rule("r1").copy(startDate = "2026-09-10", endDate = "2026-09-09"), // end < start
            rule("r1").copy(endDate = "not-a-date"),
        )) {
            var threw = false
            try { repository.insertScheduledRule(bad) } catch (e: IllegalArgumentException) { threw = true }
            assertTrue("应拒绝: start=${bad.startDate} end=${bad.endDate}", threw)
        }
        // 边界合法：end == start（只跑一天）
        repository.insertScheduledRule(rule("r1", startDate = "2026-09-09", endDate = "2026-09-09"))
    }

    @Test
    fun clearAllData_wipesScheduledRules() = runBlocking {
        repository.insertScheduledRule(rule("r1"))
        repository.clearAllData()
        assertTrue(repository.listScheduledRules().isEmpty())
    }

    @Test
    fun clearAllEntities_keepsRules() = runBlocking {
        // bootstrap 重建只清同步实体；规则是设备本地配置（同 savings_goals 语义）
        repository.insertScheduledRule(rule("r1"))
        repository.clearAllEntities()
        assertEquals(1, repository.listScheduledRules().size)
    }
}
