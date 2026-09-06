package com.aozora.app.reminder

import android.annotation.SuppressLint
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.aozora.app.MainActivity
import com.aozora.app.R

/**
 * T-M4-05a: 提醒广播接收器 — AlarmManager 回调，发送本地通知。
 *
 * 通知点击后打开 MainActivity，携带 deep link URI 到记账页。
 * Android 13+ 需要 POST_NOTIFICATIONS 权限；无权限时静默忽略（不崩溃）。
 *
 * 每次触发后自动调度次日闹钟：先链式调度再处理通知，
 * 通知权限被拒时每日调度链也不会中断。
 */
class ReminderReceiver : BroadcastReceiver() {

    @SuppressLint("MissingPermission") // hasNotificationPermission 已在上方检查
    override fun onReceive(context: Context, intent: Intent) {
        // AlarmManager 为单次闹钟：先登记下一次触发。
        // 直接以 applicationContext 重建调度器（状态在 SharedPreferences），
        // 避免依赖 Application 类型的运行时转换。
        ReminderScheduler(context.applicationContext).rescheduleIfEnabled()

        if (!hasNotificationPermission(context)) return

        ensureNotificationChannel(context)

        // 点击通知 → 打开 app（deep link 到快速记账）
        val openIntent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            data = android.net.Uri.parse("aozora://entry")
        }
        val pendingIntent = PendingIntent.getActivity(
            context, 0, openIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(context.getString(R.string.reminder_title))
            .setContentText(context.getString(R.string.reminder_text))
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setContentIntent(pendingIntent)
            .setAutoCancel(true)
            .build()

        NotificationManagerCompat.from(context).notify(NOTIFICATION_ID, notification)
    }

    companion object {
        const val CHANNEL_ID = "aozora_reminder"
        private const val NOTIFICATION_ID = 2001

        /** Android 13+ 需要 POST_NOTIFICATIONS 权限 */
        fun hasNotificationPermission(context: Context): Boolean {
            return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                ContextCompat.checkSelfPermission(
                    context, android.Manifest.permission.POST_NOTIFICATIONS
                ) == PackageManager.PERMISSION_GRANTED
            } else {
                true // Android 12 及以下不需要运行时权限
            }
        }

        private fun ensureNotificationChannel(context: Context) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
                if (manager.getNotificationChannel(CHANNEL_ID) == null) {
                    val channel = NotificationChannel(
                        CHANNEL_ID,
                        context.getString(R.string.reminder_channel_name),
                        NotificationManager.IMPORTANCE_DEFAULT
                    ).apply {
                        description = context.getString(R.string.reminder_channel_desc)
                    }
                    manager.createNotificationChannel(channel)
                }
            }
        }
    }
}
