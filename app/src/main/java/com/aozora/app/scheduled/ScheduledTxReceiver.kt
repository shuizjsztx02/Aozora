package com.aozora.app.scheduled

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.annotation.VisibleForTesting
import com.aozora.app.db.AozoraDatabase
import com.aozora.app.db.Repository
import com.aozora.app.db.entity.ScheduledRuleEntity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import java.util.Calendar

/**
 * 定时记账广播接收器（第十一轮 REQ-26 / 决策 #4；第十四轮 T14-5 REQ-43 时刻化）。
 * — AlarmManager 按「全部 enabled 规则的最近未来触发时刻」单槽回调（B2 策略，
 * 决策 #4），触发 [ScheduledTxEngine.runDueNow]。v1 静默：不发通知（规则页
 * "上次/下次执行"为唯一可见性）。
 *
 * 写法镜像 ReminderScheduler/ReminderReceiver（reminder/ 包），三要点：
 * - 独立 REQUEST_CODE(2002)，与提醒闹钟(2001)不冲突——两套 PendingIntent 互不覆盖；
 * - Android 12+ 无「闹钟和提醒」特殊权限时降级 setWindow，避免 SecurityException；
 * - 链式续命：onReceive 先重排下一次触发再做实际工作，引擎异常不断链；
 *   时间/时区/日期变更 → 仅重排闹钟防漂移（R-M4-35 先例）。
 *
 * BootReceiver 在重启后调用 [scheduleNext] 恢复；AozoraApp.onCreate 冷启动跑引擎
 * 一次并重排。闹钟与冷启动重复触发由规则 lastRunDate 当日去重兜底。
 */
class ScheduledTxReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val appContext = context.applicationContext
        val action = intent.action
        val runEngine = action != Intent.ACTION_TIME_CHANGED
            && action != Intent.ACTION_TIMEZONE_CHANGED
            && action != Intent.ACTION_DATE_CHANGED

        // goAsync + IO 协程：scheduleNext 要查 Room（决定下一次触发时刻），
        // onReceive 同步窗口内做 DB 读写会 ANR/静默截断（同引擎段原写法）。
        // 直连 AozoraDatabase 单例（R-M4-26：不依赖 Application 类型转换）。
        val pendingResult = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                // AlarmManager 为单次闹钟：先重排下一次触发，再做实际工作。
                scheduleNext(appContext)
                if (runEngine) {
                    ScheduledTxEngine(Repository(AozoraDatabase.getInstance(appContext))).runDueNow()
                }
            } catch (_: Exception) {
                // 引擎内部已逐条容错；这里兜底任何意外（如 DB 打开失败），
                // 接收器无 UI 可反馈，链式续命已保证到点照常。
            } finally {
                pendingResult.finish()
            }
        }
    }

    companion object {
        /** 独立于提醒闹钟的 REQUEST_CODE（ReminderScheduler 用 2001）。 */
        private const val REQUEST_CODE = 2002

        /** 无精确闹钟权限时的非精确降级窗口：10 分钟（同 ReminderScheduler）。 */
        private const val INEXACT_WINDOW_MS = 10 * 60 * 1000L

        /**
         * 排定下一次触发（REQ-43 / 决策 #4 B2 单闹钟）：取「所有 enabled（未归档）
         * 规则的最近未来执行时刻」min——每条规则当日时刻未到=今天该时刻，否则明天
         * 该时刻。无 enabled 规则 → 取消闹钟断链（规则再启用时由桥侧重排）。
         *
         * 需查 Room 故为 suspend：Receiver 调用方以 goAsync 包裹（本类 onReceive /
         * BootReceiver），App 内调用方在协程里直调（AozoraApp.onCreate / 桥 handler）。
         * FLAG_UPDATE_CURRENT：重复调用只覆盖同一闹钟，天然幂等。
         */
        suspend fun scheduleNext(context: Context) {
            val appContext = context.applicationContext
            val rules = Repository(AozoraDatabase.getInstance(appContext)).listScheduledRules()
                .filter { it.enabled }
            val alarmManager = appContext.getSystemService(Context.ALARM_SERVICE) as AlarmManager
            val pendingIntent = PendingIntent.getBroadcast(
                appContext, REQUEST_CODE,
                Intent(appContext, ScheduledTxReceiver::class.java),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            val triggerMs = nextTriggerOverRules(rules)
            if (triggerMs == null) {
                alarmManager.cancel(pendingIntent)
                return
            }
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

        /**
         * B2 单闹钟 min 计算（决策 #4，纯函数供单测直调）：全部规则（调用方
         * 已过滤 enabled）各自的最近未来触发时刻取最早；空表 = null → 不排/取消。
         */
        @JvmStatic
        @VisibleForTesting
        internal fun nextTriggerOverRules(
            rules: List<ScheduledRuleEntity>,
            nowMillis: Long = System.currentTimeMillis(),
        ): Long? = rules.minOfOrNull { nextTriggerTimeFor(it.runHour, it.runMinute, nowMillis) }

        /**
         * 某时刻的最近未来触发 ms：今日 HH:mm 未到=今日，已到/恰至视为已过=次日
         * （原 nextTriggerTime 语义，时刻参数化 REQ-43；now 参数化供单测钉死时钟）。
         */
        @JvmStatic
        @VisibleForTesting
        internal fun nextTriggerTimeFor(
            hour: Int,
            minute: Int,
            nowMillis: Long = System.currentTimeMillis(),
        ): Long {
            val target = Calendar.getInstance().apply {
                timeInMillis = nowMillis
                set(Calendar.HOUR_OF_DAY, hour)
                set(Calendar.MINUTE, minute)
                set(Calendar.SECOND, 0)
                set(Calendar.MILLISECOND, 0)
            }
            if (target.timeInMillis <= nowMillis) {
                target.add(Calendar.DAY_OF_YEAR, 1)
            }
            return target.timeInMillis
        }
    }
}
