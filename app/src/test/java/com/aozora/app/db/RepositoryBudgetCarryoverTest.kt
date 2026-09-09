package com.aozora.app.db

import com.aozora.app.db.entity.BookEntity
import com.aozora.app.db.entity.BudgetEntity
import com.aozora.app.db.entity.SyncMetaEntity
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import java.time.YearMonth

/**
 * T11-1（REQ-23 / 第十一轮决策 #2）：预算沿用 ensureBudgetCarryover 三态。
 *
 * 覆盖：
 * - 开关关（未设 / '0'）→ 无任何复制
 * - 开关开 + 本月空 + 上月有 → 逐条复制（新 id、period=本月、同分类/金额、
 *   OP_INSERT mutation 入队）并写 mark；年度预算与总账本不参与
 * - mark 已=本月 → 不重复（用户删光本月预算后不再生成的基础）
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class RepositoryBudgetCarryoverTest {

    private lateinit var db: AozoraDatabase
    private lateinit var repository: Repository

    private val curPeriod = YearMonth.now().toString()                    // "YYYY-MM"
    private val prevPeriod = YearMonth.now().minusMonths(1).toString()
    private val prevYear = YearMonth.now().minusMonths(1).year.toString() // 上月所属年（1 月时=去年）

    @Before
    fun setUp() {
        db = AozoraDatabase.createInMemory(RuntimeEnvironment.getApplication())
        repository = Repository(db)
    }

    @After
    fun tearDown() { db.close() }

    private fun book(id: String, name: String) = BookEntity(
        id = id, ownerId = "local", name = name, icon = "📒", version = 1, archivedAt = null
    )

    private fun budget(
        id: String, bookId: String, type: String, period: String,
        amountCents: Long, categoryId: String? = null
    ) = BudgetEntity(
        id = id, bookId = bookId, budgetType = type, categoryId = categoryId,
        period = period, amountCents = amountCents, version = 1
    )

    @Test
    fun carryover_switchOff_noop(): Unit = runBlocking {
        repository.insertBook(book("b1", "默认账本"), "m1")
        repository.insertBudget(budget("bud-p1", "b1", "monthly", prevPeriod, 50000), "m2")
        // 未设开关 → 直接返回
        repository.ensureBudgetCarryover()
        // 显式 '0' 同样不动作
        repository.setMeta(SyncMetaEntity.KEY_BUDGET_CARRYOVER, "0")
        repository.ensureBudgetCarryover()

        assertEquals(1, repository.listBudgets("b1").size)
        assertNull(repository.getMeta(SyncMetaEntity.KEY_BUDGET_CARRYOVER_MARK))
    }

    @Test
    fun carryover_emptyThisMonth_prevFilled_copiesAndMarks(): Unit = runBlocking {
        repository.ensureSeedDefaults()
        val masterId = repository.getMasterBookId()!!
        val bookId = repository.listBooks().first { it.id != masterId }.id
        val catId = repository.listCategories().first { it.kind == "expense" }.id

        repository.insertBudget(budget("bud-m1", bookId, "monthly", prevPeriod, 50000, catId), "m1a")
        repository.insertBudget(budget("bud-m2", bookId, "monthly", prevPeriod, 30000), "m1b")
        // 年度预算不参与沿用
        repository.insertBudget(budget("bud-y1", bookId, "annual", prevYear, 600000), "m1c")
        // 总账本即便挂了预算也不复制
        repository.insertBudget(budget("bud-x1", masterId, "monthly", prevPeriod, 9900), "m1d")

        repository.setMeta(SyncMetaEntity.KEY_BUDGET_CARRYOVER, "1")
        repository.ensureBudgetCarryover()

        val copied = repository.listBudgets(bookId).filter {
            it.budgetType == "monthly" && it.period == curPeriod
        }
        assertEquals(2, copied.size)
        val copy = copied.first { it.categoryId != null }
        assertEquals(prevPeriod, repository.getBudget("bud-m1")!!.period) // 原行 period 未被改写
        assertEquals(50000L, copy.amountCents)
        assertEquals(catId, copy.categoryId)
        assertNotEquals("bud-m1", copy.id)
        assertEquals(1, copy.version)
        // 本月行没有年度复制品；总账本不产生本月复制行
        assertTrue(repository.listBudgets(bookId).none { it.budgetType == "annual" && it.period == curPeriod })
        assertTrue(repository.listBudgets(masterId).none { it.period == curPeriod })

        // mark 写入当前月（仅普通账本）
        val mark = org.json.JSONObject(repository.getMeta(SyncMetaEntity.KEY_BUDGET_CARRYOVER_MARK))
        assertEquals(curPeriod, mark.getString(bookId))
        assertFalse(mark.has(masterId))

        // 复制行走 OP_INSERT mutation 正常上行
        val mutations = repository.listPendingMutations()
        for (c in copied) {
            assertTrue(
                "no insert mutation for copied budget ${c.id}",
                mutations.any {
                    it.entityType == "budget" && it.entityId == c.id && it.operation == "insert"
                }
            )
        }
    }

    @Test
    fun carryover_markAlreadyCurrent_noDuplicate(): Unit = runBlocking {
        repository.insertBook(book("b1", "默认账本"), "m1")
        repository.insertBudget(budget("bud-p1", "b1", "monthly", prevPeriod, 50000), "m2")
        repository.setMeta(SyncMetaEntity.KEY_BUDGET_CARRYOVER, "1")
        repository.setMeta(SyncMetaEntity.KEY_BUDGET_CARRYOVER_MARK, """{"b1":"$curPeriod"}""")

        repository.ensureBudgetCarryover()

        assertEquals(1, repository.listBudgets("b1").size)
    }

    @Test
    fun carryover_thisMonthNotEmpty_skipsButDoesNotMark(): Unit = runBlocking {
        // 决策 #2 字面语义：mark 仅在复制发生时更新；本月已有预算的账本本次跳过
        repository.insertBook(book("b1", "默认账本"), "m1")
        repository.insertBudget(budget("bud-c1", "b1", "monthly", curPeriod, 10000), "m2")
        repository.insertBudget(budget("bud-p1", "b1", "monthly", prevPeriod, 50000), "m3")
        repository.setMeta(SyncMetaEntity.KEY_BUDGET_CARRYOVER, "1")

        repository.ensureBudgetCarryover()

        assertEquals(2, repository.listBudgets("b1").size)
    }
}
