package com.aozora.app.widget

import com.aozora.app.db.AozoraDatabase
import com.aozora.app.db.Repository
import com.aozora.app.db.entity.BookEntity
import com.aozora.app.db.entity.BudgetEntity
import com.aozora.app.db.entity.CategoryEntity
import com.aozora.app.db.entity.TransactionEntity
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
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/**
 * T-M4-05b: WidgetDataHelper 单元测试（R-M4-21 测试覆盖补充）。
 *
 * 使用 Robolectric + Room 内存数据库覆盖：
 * - formatCents 整数运算精度（R-M4-25 回归）
 * - loadData 三状态（NO_DATA / PENDING_SYNC / READY）
 * - 今日支出聚合口径（仅 expense、仅当日、排除软删除）
 * - 本月预算进度计算与 100% 封顶
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class WidgetDataHelperTest {

    private lateinit var db: AozoraDatabase
    private lateinit var repository: Repository

    private val today: String = LocalDate.now(ZoneId.of("Asia/Shanghai")).format(DateTimeFormatter.ISO_LOCAL_DATE)
    private val month: String = today.slice(0..6)

    @Before
    fun setUp() {
        db = AozoraDatabase.createInMemory(RuntimeEnvironment.getApplication())
        repository = Repository(db)
    }

    @After
    fun tearDown() {
        db.close()
    }

    // ─── formatCents：整数运算，无 Double 精度丢失（R-M4-25 回归） ───

    @Test
    fun formatCents_zero() {
        assertEquals("¥0.00", WidgetDataHelper.formatCents(0))
    }

    @Test
    fun formatCents_padsCentsToTwoDigits() {
        assertEquals("¥0.05", WidgetDataHelper.formatCents(5))
        assertEquals("¥1.50", WidgetDataHelper.formatCents(150))
    }

    @Test
    fun formatCents_wholeYuan() {
        assertEquals("¥12.00", WidgetDataHelper.formatCents(1200))
    }

    @Test
    fun formatCents_mixed() {
        assertEquals("¥12.34", WidgetDataHelper.formatCents(1234))
    }

    @Test
    fun formatCents_beyondDoubleSafeInteger_noPrecisionLoss() {
        // 2^53+1 分：Double 无法精确表示（旧实现 cents/100.0 输出 ...92，错误）
        assertEquals("¥90071992547409.93", WidgetDataHelper.formatCents(9007199254740993L))
    }

    @Test
    fun formatCents_negative_signBeforeCurrencySymbol() {
        assertEquals("-¥1.50", WidgetDataHelper.formatCents(-150))
    }

    // ─── loadData：三状态与聚合口径 ───

    @Test
    fun loadData_noBooks_isNoData(): Unit = runBlocking {
        val data = WidgetDataHelper.loadData(repository)
        assertEquals(WidgetDataHelper.WidgetState.NO_DATA, data.state)
        assertEquals(0L, data.todayExpenseCents)
        assertEquals(0, data.todayCount)
        assertNull(data.budgetCents)
        assertEquals(0, data.budgetProgress)
    }

    @Test
    fun loadData_sumsTodayExpensesOnly(): Unit = runBlocking {
        seedBookAndCategory()
        insertTx("t1", today, 1234, "expense")
        insertTx("t2", today, 100, "expense")
        insertTx("t3", today, 999, "income")          // 收入不计入支出
        insertTx("t4", "2020-01-01", 500, "expense")  // 非今日/非本月不计入

        val data = WidgetDataHelper.loadData(repository)
        assertEquals(1334L, data.todayExpenseCents)
        assertEquals(2, data.todayCount)
    }

    @Test
    fun loadData_excludesSoftDeleted(): Unit = runBlocking {
        seedBookAndCategory()
        insertTx("t1", today, 500, "expense")
        insertTx("t2", today, 300, "expense")
        repository.softDeleteTransaction("t1", "m-del-t1")

        val data = WidgetDataHelper.loadData(repository)
        assertEquals(300L, data.todayExpenseCents)
        assertEquals(1, data.todayCount)
    }

    @Test
    fun loadData_monthlyBudget_progressHalfUsed(): Unit = runBlocking {
        seedBookAndCategory()
        insertBudget("bud1", month, 200_000)
        insertTx("t1", today, 100_000, "expense")

        val data = WidgetDataHelper.loadData(repository)
        assertEquals(200_000L, data.budgetCents)
        assertEquals(50, data.budgetProgress)
    }

    @Test
    fun loadData_overBudget_capsAt100(): Unit = runBlocking {
        seedBookAndCategory()
        insertBudget("bud1", month, 100_000)
        insertTx("t1", today, 150_000, "expense")

        val data = WidgetDataHelper.loadData(repository)
        assertEquals(100, data.budgetProgress)
    }

    @Test
    fun loadData_noBudget_progressZero(): Unit = runBlocking {
        seedBookAndCategory()
        insertTx("t1", today, 150_000, "expense")

        val data = WidgetDataHelper.loadData(repository)
        assertNull(data.budgetCents)
        assertEquals(0, data.budgetProgress)
    }

    @Test
    fun loadData_otherMonthBudget_ignored(): Unit = runBlocking {
        seedBookAndCategory()
        insertBudget("bud1", "2020-01", 100_000)  // 非本月
        insertTx("t1", today, 50_000, "expense")

        val data = WidgetDataHelper.loadData(repository)
        assertNull(data.budgetCents)
        assertEquals(0, data.budgetProgress)
    }

    @Test
    fun loadData_pendingMutations_statePendingSync(): Unit = runBlocking {
        seedBookAndCategory()
        insertTx("t1", today, 100, "expense")  // insert 会入队 pending mutation

        val data = WidgetDataHelper.loadData(repository)
        assertEquals(WidgetDataHelper.WidgetState.PENDING_SYNC, data.state)
    }

    @Test
    fun loadData_noPendingMutations_stateReady(): Unit = runBlocking {
        seedBookAndCategory()
        insertTx("t1", today, 100, "expense")
        val ids = repository.listPendingMutations().map { it.mutationId }
        repository.removeAppliedMutations(ids)

        val data = WidgetDataHelper.loadData(repository)
        assertEquals(WidgetDataHelper.WidgetState.READY, data.state)
    }

    // ─── T11-2（REQ-24 / 决策 #5）：config 选择性取数 ───

    @Test
    fun loadData_monthExpense_sumsWholeMonth(): Unit = runBlocking {
        seedBookAndCategory()
        insertTx("t1", today, 100, "expense")            // 今日（同时计入本月）
        insertTx("t2", "${month}-15", 250, "expense")    // 本月内
        insertTx("t3", "2020-01-15", 999, "expense")     // 其他月份不计

        val data = WidgetDataHelper.loadData(repository, WidgetConfig(showMonthExpense = true))
        assertEquals(350L, data.monthExpenseCents)
        assertEquals(100L, data.todayExpenseCents)
    }

    @Test
    fun loadData_budgetProgressUsesMonthSum_regardlessOfMonthRow(): Unit = runBlocking {
        seedBookAndCategory()
        insertBudget("bud1", month, 200_000)
        insertTx("t1", today, 50_000, "expense")
        insertTx("t2", "${month}-15", 50_000, "expense")

        // showMonthExpense=false 但 showBudget=true → 本月聚合仍为进度计算取数
        val data = WidgetDataHelper.loadData(repository, WidgetConfig())
        assertEquals(50, data.budgetProgress)
        assertEquals(100_000L, data.monthExpenseCents)
    }

    @Test
    fun loadData_budgetOff_skipsBudgetAndProgress(): Unit = runBlocking {
        seedBookAndCategory()
        insertBudget("bud1", month, 200_000)
        insertTx("t1", today, 150_000, "expense")

        val data = WidgetDataHelper.loadData(repository, WidgetConfig(showBudget = false))
        assertNull(data.budgetCents)
        assertEquals(0, data.budgetProgress)
    }

    @Test
    fun loadData_monthAndBudgetBothOff_skipsMonthQueries(): Unit = runBlocking {
        seedBookAndCategory()
        insertBudget("bud1", month, 200_000)
        insertTx("t1", today, 100, "expense")

        val data = WidgetDataHelper.loadData(
            repository, WidgetConfig(showMonthExpense = false, showBudget = false)
        )
        assertEquals(0L, data.monthExpenseCents)
        assertNull(data.budgetCents)
        // 今日支出恒开：今日聚合不受影响
        assertEquals(100L, data.todayExpenseCents)
        assertEquals(1, data.todayCount)
    }

    @Test
    fun loadData_configSnapshotCarried(): Unit = runBlocking {
        seedBookAndCategory()
        val cfg = WidgetConfig(showMonthExpense = true, tapAction = "entry")
        assertEquals(cfg, WidgetDataHelper.loadData(repository, cfg).config)
    }

    @Test
    fun loadData_noBooks_carriesConfig(): Unit = runBlocking {
        val cfg = WidgetConfig(showBudget = false, tapAction = "entry")
        val data = WidgetDataHelper.loadData(repository, cfg)
        assertEquals(WidgetDataHelper.WidgetState.NO_DATA, data.state)
        assertEquals(cfg, data.config)
    }

    // ─── 测试数据 helper ───

    private suspend fun seedBookAndCategory() {
        repository.insertBook(
            BookEntity(
                id = "b1", ownerId = "owner1", name = "默认账本",
                icon = "default", version = 1, archivedAt = null
            ),
            "m-book"
        )
        repository.insertCategory(
            CategoryEntity(
                id = "c1", ownerId = "owner1", bookId = null, parentId = null,
                kind = "expense", name = "餐饮", archivedAt = null, version = 1
            ),
            "m-cat"
        )
    }

    private suspend fun insertTx(id: String, bizDate: String, cents: Long, type: String) {
        repository.insertTransaction(
            TransactionEntity(
                id = id, bookId = "b1", type = type, amountCents = cents,
                categoryId = "c1", accountId = null, note = "", bizDate = bizDate,
                occurredAt = System.currentTimeMillis(), source = "manual",
                version = 1, deletedAt = null
            ),
            "m-$id"
        )
    }

    private suspend fun insertBudget(id: String, period: String, cents: Long) {
        repository.insertBudget(
            BudgetEntity(
                id = id, bookId = "b1", budgetType = "monthly", categoryId = null,
                period = period, amountCents = cents, version = 1
            ),
            "m-$id"
        )
    }
}
