package com.aozora.app.scheduled

import com.aozora.app.db.AozoraDatabase
import com.aozora.app.db.Repository
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
import java.time.LocalDate

/**
 * 第十一轮 T11-4（REQ-26 / 决策 #4）：定时记账引擎 JVM 测试
 * （createInMemory 直调，today 参数化）。
 *
 * 覆盖决策 #4 全语义：四周期命中 / disabled 跳过 / 起止日期区间 /
 * 短月钳制 / lastRunDate 当日去重 / note 拼装 / includeInBudget 透传 /
 * 生成交易正常入队上行 / 单规则失败不阻断。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ScheduledTxEngineTest {

    private lateinit var db: AozoraDatabase
    private lateinit var repository: Repository
    private lateinit var engine: ScheduledTxEngine

    private lateinit var bookId: String
    private lateinit var categoryId: String

    /** 固定基准日：2026-09-09（周三）。2026 非闰年、9 月 30 天。 */
    private val wed = LocalDate.of(2026, 9, 9)

    @Before
    fun setUp() {
        db = AozoraDatabase.createInMemory(RuntimeEnvironment.getApplication())
        repository = Repository(db)
        engine = ScheduledTxEngine(repository)
        runBlocking {
            repository.ensureSeedDefaults()
            val masterId = repository.getMasterBookId()
            bookId = repository.listBooks().first { it.id != masterId }.id
            categoryId = repository.listCategories().first { it.kind == "expense" }.id
        }
    }

    @After
    fun tearDown() { db.close() }

    private fun rule(
        id: String,
        repeat: String = "daily",
        startDate: String = "2026-09-01",
        endDate: String? = null,
        enabled: Boolean = true,
        lastRunDate: String? = null,
        note: String = "",
        includeInBudget: Boolean = true,
        bookId: String = this.bookId,
    ) = ScheduledRuleEntity(
        id = id, ownerId = "local", type = "expense", name = "早餐",
        amountCents = 1500, bookId = bookId, categoryId = categoryId,
        accountId = null, includeInBudget = includeInBudget, note = note,
        repeat = repeat, startDate = startDate, endDate = endDate,
        enabled = enabled, lastRunDate = lastRunDate,
        createdAt = 1000L, updatedAt = 1000L, archivedAt = null, version = 1
    )

    private fun txs() = runBlocking { repository.listTransactionsByBook(bookId) }

    // ─── daily + 交易字段 ───

    @Test
    fun daily_inWindow_generatesOneTransaction_withCorrectFields() = runBlocking {
        repository.insertScheduledRule(rule("r1", note = "公司楼下"))
        val count = engine.runDueToday(wed)
        assertEquals(1, count)
        val tx = txs().single()
        assertEquals("scheduled", tx.source)
        assertEquals("2026-09-09", tx.bizDate)
        assertEquals("早餐｜公司楼下", tx.note)
        assertEquals(1500L, tx.amountCents)
        assertEquals(bookId, tx.bookId)
        assertEquals(categoryId, tx.categoryId)
        assertEquals(1, tx.version)
        // 生成交易必须正常入队上行（规则本身不入队，见 RepositoryScheduledTest）
        assertEquals(1, repository.listPendingMutations().count { it.entityType == "transaction" })
        // lastRunDate 锚点更新 + 版本递增
        val r = repository.getScheduledRule("r1")!!
        assertEquals("2026-09-09", r.lastRunDate)
        assertEquals(2, r.version)
    }

    @Test
    fun daily_noNote_noteIsNameOnly() = runBlocking {
        repository.insertScheduledRule(rule("r1", note = ""))
        engine.runDueToday(wed)
        assertEquals("早餐", txs().single().note)
    }

    @Test
    fun includeInBudget_passesThrough() = runBlocking {
        repository.insertScheduledRule(rule("r1", includeInBudget = false))
        engine.runDueToday(wed)
        assertFalse(txs().single().includeInBudget)
    }

    // ─── 周期命中 ───

    @Test
    fun weekly_onlyOnSameWeekdayAsStart() = runBlocking {
        // 2026-09-02 与基准 09-09 同为周三
        repository.insertScheduledRule(rule("r1", repeat = "weekly", startDate = "2026-09-02"))
        assertEquals(0, engine.runDueToday(wed.plusDays(1))) // 周四不触发
        assertEquals(1, engine.runDueToday(wed))             // 周三触发
    }

    @Test
    fun monthly_sameDayOfStart() = runBlocking {
        repository.insertScheduledRule(rule("r1", repeat = "monthly", startDate = "2026-08-09"))
        assertEquals(0, engine.runDueToday(wed.plusDays(1)))
        assertEquals(1, engine.runDueToday(wed))
    }

    @Test
    fun monthly_shortMonthClampsToLastDay() = runBlocking {
        repository.insertScheduledRule(rule("r1", repeat = "monthly", startDate = "2026-01-31"))
        // 2 月无 31 日 → 钳到 2026-02-28（非闰年）
        assertEquals(1, engine.runDueToday(LocalDate.of(2026, 2, 28)))
        assertEquals(0, engine.runDueToday(LocalDate.of(2026, 3, 30)))
        assertEquals(1, engine.runDueToday(LocalDate.of(2026, 3, 31)))
    }

    @Test
    fun yearly_sameMonthAndDay() = runBlocking {
        repository.insertScheduledRule(rule("r1", repeat = "yearly", startDate = "2025-09-09"))
        assertEquals(0, engine.runDueToday(wed.plusDays(1)))
        assertEquals(1, engine.runDueToday(wed))
    }

    // ─── 开关与区间 ───

    @Test
    fun disabledRule_skipped() = runBlocking {
        repository.insertScheduledRule(rule("r1", enabled = false))
        assertEquals(0, engine.runDueToday(wed))
        assertTrue(txs().isEmpty())
    }

    @Test
    fun beforeStartOrAfterEnd_skipped_endInclusive() = runBlocking {
        repository.insertScheduledRule(rule("r1", startDate = "2026-09-10"))
        assertEquals(0, engine.runDueToday(wed)) // 尚未开始

        repository.insertScheduledRule(rule("r2", startDate = "2026-09-01", endDate = "2026-09-08"))
        assertEquals(0, engine.runDueToday(wed)) // 已结束

        repository.insertScheduledRule(rule("r3", startDate = "2026-09-01", endDate = "2026-09-09"))
        assertEquals(1, engine.runDueToday(wed)) // 结束日当天仍命中（闭区间）
    }

    // ─── lastRunDate 当日去重 ───

    @Test
    fun alreadyRunToday_skipped_yesterdayRuns() = runBlocking {
        repository.insertScheduledRule(rule("r1", lastRunDate = "2026-09-09"))
        assertEquals(0, engine.runDueToday(wed))
        assertEquals(1, engine.runDueToday(wed.minusDays(1))) // 昨日补跑锚点不拦
    }

    @Test
    fun secondRunSameDay_noDoubleInsert() = runBlocking {
        repository.insertScheduledRule(rule("r1"))
        assertEquals(1, engine.runDueToday(wed))
        assertEquals(0, engine.runDueToday(wed)) // 闹钟+冷启动双触发幂等
        assertEquals(1, txs().size)
    }

    @Test
    fun multipleRules_eachFires() = runBlocking {
        repository.insertScheduledRule(rule("r1"))
        repository.insertScheduledRule(rule("r2"))
        assertEquals(2, engine.runDueToday(wed))
        assertEquals(2, txs().size)
    }

    // ─── 单条失败不阻断 ───

    @Test
    fun brokenRule_skipped_othersStillRun_lastRunDateUntouched() = runBlocking {
        repository.insertScheduledRule(rule("ghost", bookId = "deleted-book"))
        repository.insertScheduledRule(rule("ok"))
        assertEquals(1, engine.runDueToday(wed))
        assertEquals(1, txs().size)
        assertNull(repository.getScheduledRule("ghost")!!.lastRunDate) // 失败不写锚点，明日重试
        assertEquals("2026-09-09", repository.getScheduledRule("ok")!!.lastRunDate)
    }

    @Test
    fun archivedRule_notIterated() = runBlocking {
        repository.insertScheduledRule(rule("r1"))
        repository.archiveScheduledRule("r1")
        assertEquals(0, engine.runDueToday(wed))
    }
}
