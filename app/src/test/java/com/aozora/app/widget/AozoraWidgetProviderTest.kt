package com.aozora.app.widget

import android.content.Context
import android.view.View
import android.widget.TextView
import com.aozora.app.R
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/**
 * T-M4-05b: AozoraWidgetProvider 单元测试（R-M4-21 测试覆盖补充）。
 *
 * 覆盖：
 * - requestUpdate 发送包内 UPDATE 广播（SyncWorker 同步完成后刷新入口）
 * - bindViews 三状态 RemoteViews 绑定（apply 膨胀后断言可见性与文本）
 *
 * 不覆盖：onUpdate → Room 单例路径（避免 JVM 测试污染静态单例），
 * 数据聚合逻辑已由 WidgetDataHelperTest 以内存数据库覆盖。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class AozoraWidgetProviderTest {

    private lateinit var context: Context

    @Before
    fun setUp() {
        context = RuntimeEnvironment.getApplication()
    }

    @Test
    fun requestUpdate_sendsPackageScopedBroadcast() {
        AozoraWidgetProvider.requestUpdate(context)

        val intents = shadowOf(RuntimeEnvironment.getApplication()).broadcastIntents
        val update = intents.lastOrNull { it.action == AozoraWidgetProvider.ACTION_UPDATE }
        assertNotNull("应发送 UPDATE 广播", update)
        // setPackage 限定包内广播，防止被其他应用监听
        assertEquals(context.packageName, update!!.`package`)
    }

    @Test
    fun bindViews_noData_showsEmptyStateOnly() {
        val views = AozoraWidgetProvider.bindViews(context, data(WidgetDataHelper.WidgetState.NO_DATA))
        val root = views.apply(context, null)

        assertEquals(View.VISIBLE, root.findViewById<View>(R.id.widget_no_data).visibility)
        assertEquals(View.GONE, root.findViewById<View>(R.id.widget_content).visibility)
        assertEquals(View.GONE, root.findViewById<View>(R.id.widget_sync_hint).visibility)
    }

    @Test
    fun bindViews_ready_showsAmountAndCount_hidesSyncHint() {
        val views = AozoraWidgetProvider.bindViews(
            context,
            data(
                WidgetDataHelper.WidgetState.READY,
                todayExpenseCents = 123_456,
                todayCount = 3
            )
        )
        val root = views.apply(context, null)

        assertEquals(View.GONE, root.findViewById<View>(R.id.widget_no_data).visibility)
        assertEquals(View.VISIBLE, root.findViewById<View>(R.id.widget_content).visibility)
        assertEquals(
            "¥1234.56",
            root.findViewById<TextView>(R.id.widget_today_expense).text.toString()
        )
        assertEquals(
            "3 笔",
            root.findViewById<TextView>(R.id.widget_today_count).text.toString()
        )
        // 无预算 → 预算行隐藏
        assertEquals(View.GONE, root.findViewById<View>(R.id.widget_budget_row).visibility)
        assertEquals(View.GONE, root.findViewById<View>(R.id.widget_sync_hint).visibility)
    }

    @Test
    fun bindViews_pendingSync_showsBudgetRowAndSyncHint() {
        val views = AozoraWidgetProvider.bindViews(
            context,
            data(
                WidgetDataHelper.WidgetState.PENDING_SYNC,
                todayExpenseCents = 500,
                todayCount = 1,
                budgetCents = 200_000,
                budgetProgress = 42
            )
        )
        val root = views.apply(context, null)

        assertEquals(View.VISIBLE, root.findViewById<View>(R.id.widget_budget_row).visibility)
        assertEquals(
            "42%",
            root.findViewById<TextView>(R.id.widget_budget_text).text.toString()
        )
        assertEquals(View.VISIBLE, root.findViewById<View>(R.id.widget_sync_hint).visibility)
    }

    private fun data(
        state: WidgetDataHelper.WidgetState,
        todayExpenseCents: Long = 0,
        todayCount: Int = 0,
        budgetCents: Long? = null,
        budgetProgress: Int = 0
    ) = WidgetDataHelper.WidgetData(
        state = state,
        todayExpenseCents = todayExpenseCents,
        todayCount = todayCount,
        budgetCents = budgetCents,
        budgetProgress = budgetProgress
    )
}
