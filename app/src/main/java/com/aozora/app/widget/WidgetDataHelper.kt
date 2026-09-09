package com.aozora.app.widget

import android.content.Context
import com.aozora.app.db.AozoraDatabase
import com.aozora.app.db.Repository
import com.aozora.app.db.entity.SyncMetaEntity
import kotlinx.coroutines.runBlocking
import org.json.JSONException
import org.json.JSONObject
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/**
 * 桌面小组件全局配置（第十一轮 T11-2 / REQ-24 / 决策 #5）。
 *
 * 存 sync_meta 键 widget_config（JSON，所有 widget 实例共享一份；每实例配置留 v2）。
 * 读取端容错：整体坏 JSON 回退全默认；缺键/坏值逐键回退默认；
 * tapAction 仅认 'home'|'entry'，其余（含非法类型）回退 'home'。
 * 今日支出恒显示（无开关），v0 语义。
 */
data class WidgetConfig @JvmOverloads constructor(
    val showTodayCount: Boolean = true,
    val showMonthExpense: Boolean = false,
    val showBudget: Boolean = true,
    val tapAction: String = TAP_HOME,
) {
    companion object {
        const val TAP_HOME = "home"
        const val TAP_ENTRY = "entry"

        fun parse(raw: String?): WidgetConfig {
            if (raw.isNullOrBlank()) return WidgetConfig()
            return try {
                val o = JSONObject(raw)
                WidgetConfig(
                    showTodayCount = o.optBoolean("showTodayCount", true),
                    showMonthExpense = o.optBoolean("showMonthExpense", false),
                    showBudget = o.optBoolean("showBudget", true),
                    tapAction = if (o.optString("tapAction", TAP_HOME) == TAP_ENTRY) TAP_ENTRY else TAP_HOME,
                )
            } catch (e: JSONException) {
                WidgetConfig()
            }
        }
    }
}

/**
 * 小组件数据聚合 — 从 Room 读取今日支出和本月预算进度。
 *
 * 设计约束：
 * - 小组件运行在独立进程/上下文，不依赖 MainActivity 的 WebView
 * - 直接通过 Repository 读 Room，不经过 Bridge
 * - 金额使用 Long（整数分），避免 Double 精度丢失
 *
 * R-M4-29 修复：与首页聚合口径对齐
 * - 使用 bookId 过滤（单账本，与首页一致）
 * - 使用 Asia/Shanghai 时区（与首页 todayBizDate 一致）
 * - 预算筛选仅按 period 匹配（与首页一致，不额外检查 budgetType）
 */
object WidgetDataHelper {

    /** 小组件展示数据（含配置快照，bindViews 据此控元素可见性与点击行为） */
    data class WidgetData(
        val state: WidgetState,
        val todayExpenseCents: Long,
        val todayCount: Int,
        val budgetCents: Long?,
        val budgetProgress: Int,  // 0-100
        // T11-2：本月支出聚合（showMonthExpense 或 showBudget 时有效值，否则 0）
        val monthExpenseCents: Long = 0,
        val config: WidgetConfig = WidgetConfig(),
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
     * T11-2（决策 #5）：先读 widget_config（sync_meta，读取端容错），按配置选择性取数。
     */
    fun loadData(context: Context): WidgetData {
        val repository = Repository(AozoraDatabase.getInstance(context))
        return runBlocking {
            val config = WidgetConfig.parse(repository.getMeta(SyncMetaEntity.KEY_WIDGET_CONFIG))
            loadData(repository, config)
        }
    }

    /**
     * 数据聚合核心 — Repository 注入，单元测试可用内存数据库覆盖（R-M4-21）。
     * config 控取数范围（决策 #5）：今日支出恒查；本月聚合仅"本月支出"或"预算进度"
     * 任一开启才查（预算进度依赖本月支出）；预算行仅 showBudget 开启才查。
     */
    internal suspend fun loadData(
        repository: Repository,
        config: WidgetConfig = WidgetConfig(),
    ): WidgetData {
        // 检查是否有账本（无账本 = 未初始化/游客未记账）
        val books = repository.listBooks()
        if (books.isEmpty()) {
            return WidgetData(
                state = WidgetState.NO_DATA,
                todayExpenseCents = 0,
                todayCount = 0,
                budgetCents = null,
                budgetProgress = 0,
                config = config,
            )
        }

        // T7-1（决策 #2）：记账目标账本（排除总账本），回退第一个账本
        val bookId = (repository.getEntryBook() ?: books.first()).id
        // R-M4-29 修复：使用 Asia/Shanghai 时区，与前端 todayBizDate 一致
        val shanghaiZone = ZoneId.of("Asia/Shanghai")
        val today = LocalDate.now(shanghaiZone).format(DateTimeFormatter.ISO_LOCAL_DATE)
        val month = today.slice(0..6) // "YYYY-MM"

        // R-M4-29 修复：按 bookId 过滤，与首页单账本口径一致
        val todayTxs = repository.listTransactionsByBookAndDateRange(bookId, today, today)
        val todayExpenses = todayTxs.filter { it.type == "expense" && it.deletedAt == null }
        val todayExpenseCents = todayExpenses.sumOf { it.amountCents }

        // 本月支出（按 bookId 过滤）— 预算进度也依赖它，二者任一开启即查
        val monthExpenseCents =
            if (config.showMonthExpense || config.showBudget) {
                val monthTxs =
                    repository.listTransactionsByBookAndDateRange(bookId, "$month-01", "$month-31")
                val monthExpenses = monthTxs.filter { it.type == "expense" && it.deletedAt == null }
                monthExpenses.sumOf { it.amountCents }
            } else {
                0L
            }

        // 查询本月预算（R-M4-29 修复：仅按 period 匹配，不额外检查 budgetType，与首页一致）
        val budgetCents = if (config.showBudget) {
            val budgets = repository.listBudgets(bookId)
            budgets.find { it.period == month }?.amountCents
        } else {
            null
        }

        // 预算进度
        val budgetProgress = if (budgetCents != null && budgetCents > 0) {
            (100 * monthExpenseCents / budgetCents).toInt().coerceIn(0, 100)
        } else 0

        // R-M4-31 修复：pending + failed 汇总，与首页 SyncBadge 口径一致
        val pendingCount = repository.countPendingMutations()
        val failedCount = repository.countFailedMutations()
        val hasUnsynced = pendingCount > 0 || failedCount > 0
        val state = if (hasUnsynced) WidgetState.PENDING_SYNC else WidgetState.READY

        return WidgetData(
            state = state,
            todayExpenseCents = todayExpenseCents,
            todayCount = todayExpenses.size,
            budgetCents = budgetCents,
            budgetProgress = budgetProgress,
            monthExpenseCents = monthExpenseCents,
            config = config,
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
