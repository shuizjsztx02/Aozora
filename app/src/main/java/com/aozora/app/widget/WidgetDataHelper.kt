package com.aozora.app.widget

import android.content.Context
import com.aozora.app.db.AozoraDatabase
import com.aozora.app.db.Repository
import kotlinx.coroutines.runBlocking
import java.time.LocalDate
import java.time.format.DateTimeFormatter

/**
 * 小组件数据聚合 — 从 Room 读取今日支出和本月预算进度。
 *
 * 设计约束：
 * - 小组件运行在独立进程/上下文，不依赖 MainActivity 的 WebView
 * - 直接通过 Repository 读 Room，不经过 Bridge
 * - 金额使用 Long（整数分），与前端保持一致
 */
object WidgetDataHelper {

    /** 小组件展示数据 */
    data class WidgetData(
        val state: WidgetState,
        val todayExpenseCents: Long,
        val todayCount: Int,
        val budgetCents: Long?,
        val budgetProgress: Int  // 0-100
    )

    enum class WidgetState {
        /** 未登录或无账本 */
        NO_DATA,
        /** 有待同步的 mutation */
        PENDING_SYNC,
        /** 数据已同步，正常展示 */
        READY
    }

    /**
     * 加载小组件数据。
     *
     * 使用 runBlocking 因为 AppWidgetProvider.onUpdate 不支持协程。
     * Room 查询本身很快（本地数据库），不会阻塞主线程过久。
     */
    fun loadData(context: Context): WidgetData {
        val repository = Repository(AozoraDatabase.getInstance(context))
        return runBlocking { loadData(repository) }
    }

    /** 数据聚合核心 — Repository 注入，单元测试可用内存数据库覆盖（R-M4-21）。 */
    internal suspend fun loadData(repository: Repository): WidgetData {
        // 检查是否有账本（无账本 = 未初始化/游客未记账）
        val books = repository.listBooks()
        if (books.isEmpty()) {
            return WidgetData(
                state = WidgetState.NO_DATA,
                todayExpenseCents = 0,
                todayCount = 0,
                budgetCents = null,
                budgetProgress = 0
            )
        }

        val bookId = books[0].id
        val today = LocalDate.now().format(DateTimeFormatter.ISO_LOCAL_DATE)
        val month = today.slice(0..6) // "YYYY-MM"

        // 查询今日流水
        val todayTxs = repository.listTransactionsByDateRange(today, today)
        val todayExpenses = todayTxs.filter { it.type == "expense" && it.deletedAt == null }
        val todayExpenseCents = todayExpenses.sumOf { it.amountCents }

        // 查询本月预算
        val budgets = repository.listBudgets(bookId)
        val monthBudget = budgets.find { it.period == month && it.budgetType == "monthly" }
        val budgetCents = monthBudget?.amountCents

        // 本月支出
        val monthTxs = repository.listTransactionsByDateRange("$month-01", "$month-31")
        val monthExpenses = monthTxs.filter { it.type == "expense" && it.deletedAt == null }
        val monthExpenseCents = monthExpenses.sumOf { it.amountCents }

        // 预算进度
        val budgetProgress = if (budgetCents != null && budgetCents > 0) {
            (100 * monthExpenseCents / budgetCents).toInt().coerceIn(0, 100)
        } else 0

        // 检查是否有待同步的 mutation
        val pendingCount = repository.countPendingMutations()
        val state = if (pendingCount > 0) WidgetState.PENDING_SYNC else WidgetState.READY

        return WidgetData(
            state = state,
            todayExpenseCents = todayExpenseCents,
            todayCount = todayExpenses.size,
            budgetCents = budgetCents,
            budgetProgress = budgetProgress
        )
    }

    /**
     * 格式化金额为显示字符串（分 → 元）。
     *
     * 纯整数运算，避免 Double 精度丢失（R-M4-25）；
     * 不依赖 locale（"%.2f" 在部分 locale 下会输出逗号小数点）。
     */
    fun formatCents(cents: Long): String {
        val sign = if (cents < 0) "-" else ""
        val abs = kotlin.math.abs(cents)
        return "$sign¥${abs / 100}.${(abs % 100).toString().padStart(2, '0')}"
    }
}
