package com.aozora.app.reminder

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import java.util.Calendar

/**
 * T-M4-05a: 提醒调度器（AlarmManager 实现）。
 *
 * 使用 AlarmManager.setExactAndAllowWhileIdle() 实现精确每日提醒（Doze 下仍触发）；
 * Android 12+ 未授予「闹钟和提醒」特殊权限（canScheduleExactAlarms() == false）时
 * 降级 setWindow() 非精确触发，避免 SecurityException 崩溃。
 * 配置持久化到 SharedPreferences，重启后通过 BootReceiver 恢复。
 * AlarmManager 为单次闹钟，由 ReminderReceiver 触发后链式调度次日。
 */
class ReminderScheduler(private val context: Context) {

    private val prefs by lazy {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    /**
     * 获取当前提醒状态。
     */
    fun getStatus(): ReminderStatus {
        return ReminderStatus(
            enabled = prefs.getBoolean(KEY_ENABLED, false),
            hour = prefs.getInt(KEY_HOUR, DEFAULT_HOUR),
            minute = prefs.getInt(KEY_MINUTE, DEFAULT_MINUTE)
        )
    }

    /**
     * 设置或更新提醒。
     *
     * @throws IllegalArgumentException 如果 hour 或 minute 超出有效范围
     */
    fun setReminder(enabled: Boolean, hour: Int, minute: Int) {
        require(hour in 0..23) { "Hour must be 0-23, got $hour" }
        require(minute in 0..59) { "Minute must be 0-59, got $minute" }

        prefs.edit()
            .putBoolean(KEY_ENABLED, enabled)
            .putInt(KEY_HOUR, hour)
            .putInt(KEY_MINUTE, minute)
            .apply()

        if (enabled) {
            scheduleAlarm(hour, minute)
        } else {
            cancelAlarm()
        }
    }

    /**
     * 取消提醒（保留时间配置）。
     */
    fun cancelReminder() {
        prefs.edit().putBoolean(KEY_ENABLED, false).apply()
        cancelAlarm()
    }

    /**
     * 如果提醒已启用，重新调度闹钟（用于 BootReceiver 恢复）。
     */
    fun rescheduleIfEnabled() {
        val status = getStatus()
        if (status.enabled) {
            scheduleAlarm(status.hour, status.minute)
        }
    }

    /**
     * BootReceiver 入口 — 重启后恢复调度。
     */
    fun scheduleAll() {
        rescheduleIfEnabled()
    }

    /**
     * 计算下一次触发的 Unix 毫秒时间戳。
     */
    fun getNextTriggerTime(hour: Int, minute: Int): Long {
        val now = Calendar.getInstance()
        val target = Calendar.getInstance().apply {
            set(Calendar.HOUR_OF_DAY, hour)
            set(Calendar.MINUTE, minute)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }
        // 如果今天的时间已过，设为明天
        if (target.timeInMillis <= now.timeInMillis) {
            target.add(Calendar.DAY_OF_YEAR, 1)
        }
        return target.timeInMillis
    }

    private fun scheduleAlarm(hour: Int, minute: Int) {
        val triggerMs = getNextTriggerTime(hour, minute)
        val alarmManager = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager

        val intent = Intent(context, ReminderReceiver::class.java)
        val pendingIntent = PendingIntent.getBroadcast(
            context, REQUEST_CODE, intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && !alarmManager.canScheduleExactAlarms()) {
            // Android 12+ 未授予 SCHEDULE_EXACT_ALARM 特殊权限：
            // 直接 setExact* 会抛 SecurityException，降级为窗口触发。
            alarmManager.setWindow(
                AlarmManager.RTC_WAKEUP,
                triggerMs,
                INEXACT_WINDOW_MS,
                pendingIntent
            )
        } else {
            alarmManager.setExactAndAllowWhileIdle(
                AlarmManager.RTC_WAKEUP,
                triggerMs,
                pendingIntent
            )
        }
    }

    private fun cancelAlarm() {
        val alarmManager = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val intent = Intent(context, ReminderReceiver::class.java)
        val pendingIntent = PendingIntent.getBroadcast(
            context, REQUEST_CODE, intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        alarmManager.cancel(pendingIntent)
    }

    companion object {
        private const val PREFS_NAME = "aozora_reminder"
        private const val KEY_ENABLED = "enabled"
        private const val KEY_HOUR = "hour"
        private const val KEY_MINUTE = "minute"
        private const val DEFAULT_HOUR = 21
        private const val DEFAULT_MINUTE = 0
        private const val REQUEST_CODE = 2001

        /** 无精确闹钟权限时的非精确降级窗口：10 分钟。 */
        private const val INEXACT_WINDOW_MS = 10 * 60 * 1000L
    }
}

/**
 * 提醒状态数据类。
 */
data class ReminderStatus(
    val enabled: Boolean,
    val hour: Int,
    val minute: Int
)
