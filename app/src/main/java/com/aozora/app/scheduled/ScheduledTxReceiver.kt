package com.aozora.app.scheduled

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import com.aozora.app.db.AozoraDatabase
import com.aozora.app.db.Repository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import java.util.Calendar

/**
 * 定时记账广播接收器（第十一轮 REQ-26 / 决策 #4）— AlarmManager 每日 08:00 回调，
 * 触发 [ScheduledTxEngine.runDueToday]。v1 静默：不发通知（规则页"上次/下次执行"
 * 为唯一可见性）。
 *
 * 写法镜像 ReminderScheduler/ReminderReceiver（reminder/ 包），三要点：
 * - 独立 REQUEST_CODE(2002)，与提醒闹钟(2001)不冲突——两套 PendingIntent 互不覆盖；
 * - Android 12+ 无「闹钟和提醒」特殊权限时降级 setWindow，避免 SecurityException；
 * - 链式续命：onReceive 先登记次日闹钟再跑引擎，引擎异常不断链；
 *   时间/时区/日期变更 → 仅重排闹钟防漂移（R-M4-35 先例）。
 *
 * BootReceiver 在重启后调用 [scheduleNext] 恢复；AozoraApp.onCreate 冷启动跑引擎
 * 一次并起链。闹钟与冷启动重复触发由规则 lastRunDate 当日去重兜底。
 */
class ScheduledTxReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val appContext = context.applicationContext
        val action = intent.action

        if (action == Intent.ACTION_TIME_CHANGED
            || action == Intent.ACTION_TIMEZONE_CHANGED
            || action == Intent.ACTION_DATE_CHANGED
        ) {
            scheduleNext(appContext)
            return
        }

        // AlarmManager 为单次闹钟：先登记下一次触发，再做实际工作。
        scheduleNext(appContext)

        // goAsync + IO 协程：onReceive 同步窗口内做 DB 读写会 ANR/静默截断。
        // 直连 AozoraDatabase 单例（R-M4-26：不依赖 Application 类型转换）。
        val pendingResult = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                ScheduledTxEngine(Repository(AozoraDatabase.getInstance(appContext))).runDueToday()
            } catch (_: Exception) {
                // 引擎内部已逐条容错；这里兜底任何意外（如 DB 打开失败），
                // 接收器无 UI 可反馈，链式续命已保证明日照常。
            } finally {
                pendingResult.finish()
            }
        }
    }

    companion object {
        /** 独立于提醒闹钟的 REQUEST_CODE（ReminderScheduler 用 2001）。 */
        private const val REQUEST_CODE = 2002
        private const val TRIGGER_HOUR = 8

        /** 无精确闹钟权限时的非精确降级窗口：10 分钟（同 ReminderScheduler）。 */
        private const val INEXACT_WINDOW_MS = 10 * 60 * 1000L

        /**
         * 排定下一次 08:00 触发（今日未到点=今日，否则次日）。
         * FLAG_UPDATE_CURRENT：重复调用只覆盖同一闹钟，天然幂等。
         */
        fun scheduleNext(context: Context) {
            val triggerMs = nextTriggerTime()
            val alarmManager = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
            val pendingIntent = PendingIntent.getBroadcast(
                context, REQUEST_CODE,
                Intent(context, ScheduledTxReceiver::class.java),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && !alarmManager.canScheduleExactAlarms()) {
                alarmManager.setWindow(
                    AlarmManager.RTC_WAKEUP, triggerMs, INEXACT_WINDOW_MS, pendingIntent
                )
            } else {
                alarmManager.setExactAndAllowWhileIdle(
                    AlarmManager.RTC_WAKEUP, triggerMs, pendingIntent
                )
            }
        }

        private fun nextTriggerTime(): Long {
            val now = Calendar.getInstance()
            val target = Calendar.getInstance().apply {
                set(Calendar.HOUR_OF_DAY, TRIGGER_HOUR)
                set(Calendar.MINUTE, 0)
                set(Calendar.SECOND, 0)
                set(Calendar.MILLISECOND, 0)
            }
            if (target.timeInMillis <= now.timeInMillis) {
                target.add(Calendar.DAY_OF_YEAR, 1)
            }
            return target.timeInMillis
        }
    }
}
