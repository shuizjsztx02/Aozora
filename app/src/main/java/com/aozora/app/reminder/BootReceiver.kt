package com.aozora.app.reminder

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.aozora.app.scheduled.ScheduledTxReceiver
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/**
 * BOOT_COMPLETED 接收器 — 重启后恢复所有提醒调度。
 *
 * 需要在 AndroidManifest.xml 中声明 RECEIVE_BOOT_COMPLETED 权限和 receiver。
 * 直接以 applicationContext 重建调度器（状态在 SharedPreferences），
 * 避免依赖 Application 类型的运行时转换（R-M4-26）。
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        ReminderScheduler(context.applicationContext).scheduleAll()
        // 第十一轮 T11-4（REQ-26/决策 #4）：重启恢复定时记账闹钟（链式续命起点）。
        // T14-5（REQ-43）：scheduleNext 查 Room 决定触发时刻，改 goAsync 包裹防进程早死。
        val appContext = context.applicationContext
        val pendingResult = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                ScheduledTxReceiver.scheduleNext(appContext)
            } catch (_: Exception) {
                // 与 ScheduledTxReceiver 同款兜底：失败等冷启动重排
            } finally {
                pendingResult.finish()
            }
        }
    }
}
