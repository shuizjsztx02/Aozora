package com.aozora.app.widget

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.Context
import android.content.Intent
import android.view.View
import android.widget.RemoteViews
import com.aozora.app.MainActivity
import com.aozora.app.R

/**
 * 桌面小组件 Provider — 展示今日支出与本月预算进度。
 *
 * 数据来源：Room 数据库（通过 WidgetDataHelper），不依赖 Bridge。
 * 刷新时机：
 * - 系统定时 onUpdate（默认 30 分钟）
 * - 同步完成后由 SyncEngine 触发（通过 sendBroadcast UPDATE）
 * - 用户手动点击 widget
 *
 * 三种状态：
 * - NO_DATA：无账本/未记账 → 显示"开始记录"
 * - PENDING_SYNC：有待同步数据 → 显示金额 + 同步提示
 * - READY：正常展示
 */
class AozoraWidgetProvider : AppWidgetProvider() {

    override fun onUpdate(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetIds: IntArray
    ) {
        for (appWidgetId in appWidgetIds) {
            updateWidget(context, appWidgetManager, appWidgetId)
        }
    }

    override fun onReceive(context: Context, intent: Intent) {
        super.onReceive(context, intent)
        if (intent.action == ACTION_UPDATE) {
            val appWidgetManager = AppWidgetManager.getInstance(context)
            val ids = appWidgetManager.getAppWidgetIds(
                android.content.ComponentName(context, AozoraWidgetProvider::class.java)
            )
            for (id in ids) {
                updateWidget(context, appWidgetManager, id)
            }
        }
    }

    companion object {
        const val ACTION_UPDATE = "com.aozora.app.widget.UPDATE"

        /** 同步完成后调用，触发所有 widget 刷新 */
        fun requestUpdate(context: Context) {
            val intent = Intent(ACTION_UPDATE).apply {
                setPackage(context.packageName)
            }
            context.sendBroadcast(intent)
        }

        private fun updateWidget(
            context: Context,
            appWidgetManager: AppWidgetManager,
            appWidgetId: Int
        ) {
            val data = try {
                WidgetDataHelper.loadData(context)
            } catch (e: Exception) {
                // 数据库不可用时降级显示
                WidgetDataHelper.WidgetData(
                    state = WidgetDataHelper.WidgetState.NO_DATA,
                    todayExpenseCents = 0,
                    todayCount = 0,
                    budgetCents = null,
                    budgetProgress = 0
                )
            }

            appWidgetManager.updateAppWidget(appWidgetId, bindViews(context, data))
        }

        /**
         * 将数据绑定到 RemoteViews — 纯视图逻辑，与数据库无关，
         * 便于 JVM 单元测试覆盖三状态（R-M4-21）。
         */
        internal fun bindViews(
            context: Context,
            data: WidgetDataHelper.WidgetData
        ): RemoteViews {
            val views = RemoteViews(context.packageName, R.layout.widget_layout)

            // 点击 widget → 打开 app
            val openIntent = Intent(context, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            }
            val pendingIntent = PendingIntent.getActivity(
                context, 0, openIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            views.setOnClickPendingIntent(R.id.widget_root, pendingIntent)

            when (data.state) {
                WidgetDataHelper.WidgetState.NO_DATA -> {
                    views.setViewVisibility(R.id.widget_no_data, View.VISIBLE)
                    views.setViewVisibility(R.id.widget_content, View.GONE)
                    views.setViewVisibility(R.id.widget_sync_hint, View.GONE)
                }
                WidgetDataHelper.WidgetState.PENDING_SYNC,
                WidgetDataHelper.WidgetState.READY -> {
                    views.setViewVisibility(R.id.widget_no_data, View.GONE)
                    views.setViewVisibility(R.id.widget_content, View.VISIBLE)

                    views.setTextViewText(
                        R.id.widget_today_expense,
                        WidgetDataHelper.formatCents(data.todayExpenseCents)
                    )
                    views.setTextViewText(
                        R.id.widget_today_count,
                        "${data.todayCount} 笔"
                    )

                    // 预算进度
                    if (data.budgetCents != null) {
                        views.setViewVisibility(R.id.widget_budget_row, View.VISIBLE)
                        views.setTextViewText(
                            R.id.widget_budget_text,
                            "${data.budgetProgress}%"
                        )
                    } else {
                        views.setViewVisibility(R.id.widget_budget_row, View.GONE)
                    }

                    // 同步提示
                    if (data.state == WidgetDataHelper.WidgetState.PENDING_SYNC) {
                        views.setViewVisibility(R.id.widget_sync_hint, View.VISIBLE)
                    } else {
                        views.setViewVisibility(R.id.widget_sync_hint, View.GONE)
                    }
                }
            }

            return views
        }
    }
}
