package com.aozora.app.sync

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import java.util.concurrent.TimeUnit

/**
 * 同步调度器 — 将 [SyncWorker] 入队到 WorkManager。
 *
 * 设计要点（docs/architecture/overview.md §sync-reliability）：
 * - 唯一命名任务（[UNIQUE_NAME]），REPLACE 策略避免多 worker 并发。
 * - 网络约束：至少 CONNECTED。
 * - 退避策略：指数退避，初始 15s，最大 5 分钟。
 * - "连接可用时立即触发"：`enqueue()` 幂等——已存在则替换；否则立即调度。
 *
 * R-PROD-11: 60s SLO 为"best-effort"——WorkManager 受系统 Doze/电池策略影响，
 * 不承诺严格时限；引擎只保证在网络可用时尽快执行。
 */
object SyncScheduler {

    private const val UNIQUE_NAME = "aozora-sync-worker"
    private val INITIAL_BACKOFF_SEC = 15L

    /**
     * 立即触发一轮同步（有网时）。幂等——多次调用只保留一个 worker。
     */
    fun enqueue(context: Context) {
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build()
        val request = OneTimeWorkRequestBuilder<SyncWorker>()
            .setConstraints(constraints)
            .setBackoffCriteria(
                BackoffPolicy.EXPONENTIAL,
                INITIAL_BACKOFF_SEC,
                TimeUnit.SECONDS
            )
            .build()
        WorkManager.getInstance(context)
            .enqueueUniqueWork(UNIQUE_NAME, ExistingWorkPolicy.REPLACE, request)
    }

    /** 取消当前调度的同步 worker（登出 / 用户手动暂停时使用）。 */
    fun cancel(context: Context) {
        WorkManager.getInstance(context).cancelUniqueWork(UNIQUE_NAME)
    }
}
