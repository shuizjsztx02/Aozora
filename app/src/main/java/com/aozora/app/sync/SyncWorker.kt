package com.aozora.app.sync

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.aozora.app.BuildConfig
import com.aozora.app.AozoraApp
import com.aozora.app.widget.AozoraWidgetProvider

/**
 * WorkManager CoroutineWorker — 同步引擎的调度包装。
 *
 * 行为：
 * - 取 AozoraApp 单例中的 [SyncEngine]（在 Application 中构造）。
 * - 调用 [SyncEngine.runOnce]：
 *   - [SyncEngine.SyncOutcome.OK] → Result.success()
 *   - [SyncEngine.SyncOutcome.RETRYABLE] → Result.retry()（WorkManager 按 backoff 重试）
 *   - [SyncEngine.SyncOutcome.AUTH_LOST] → Result.success()（不再重试，等待用户干预）
 *
 * 约束：
 * - 由 [SyncScheduler.enqueue] 在合适的网络约束下入队。
 * - 唯一命名任务（REPLACE 策略）—— 多次入队仅保留一份。
 */
class SyncWorker(
    appContext: Context,
    params: WorkerParameters
) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val app = applicationContext as? AozoraApp ?: return Result.failure()
        val engine = app.syncEngine ?: return Result.failure()
        return when (engine.runOnce()) {
            SyncEngine.SyncOutcome.OK -> {
                // T-M4-05b: 同步成功后刷新桌面小组件
                AozoraWidgetProvider.requestUpdate(applicationContext)
                Result.success()
            }
            SyncEngine.SyncOutcome.RETRYABLE -> Result.retry()
            SyncEngine.SyncOutcome.AUTH_LOST -> Result.success()
        }
    }
}

/** 调试/测试：BuildConfig.SYNC_BASE_URL 的可见性桥。 */
@Suppress("unused")
internal object SyncDebug {
    val baseUrl: String = BuildConfig.SYNC_BASE_URL
}
