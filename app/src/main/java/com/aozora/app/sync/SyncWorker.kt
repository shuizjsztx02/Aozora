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
        // T-M5-04a S0-1: 防御纵深——即便 SyncScheduler 入队时已同意，
        // 实际执行时也可能被清数据 / 登出清空，再校一次避免未同意即上行。
        if (!app.consentStore.hasConsented()) {
            return Result.success()
        }
        // T21-6（REQ-83）: 自动记账的**在线档**编排——本地规则先行，未命中 / 字段不完整
        // 的行才上行（ADR-016 决策 7）。**复用本 worker 的调度形态**（`SyncScheduler`
        // 的联网约束 + 唯一任务 + 指数退避），不新造调度器；采集侧落行后
        // `SyncScheduler.enqueue` 即把这一步带起来。放在同步之前：本轮自动入账的账目
        // 随即被同一轮同步推给服务端。
        // 行级失败已在 `auto_captures.error` 留痕，故这里只兜底意外异常——**绝不让
        // 自动记账把同步本身带崩**。
        runCatching { app.autoBookEngine.processPendingCaptures(allowRemote = true) }
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
