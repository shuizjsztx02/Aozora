package com.aozora.app.crashlytics

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * T-M5-05b: 本地崩溃报告实体。
 *
 * 由 [CrashReporter] 在全局 UncaughtExceptionHandler 中写入，下一次启动时
 * 由 [CrashReportUploader] 经 `POST /telemetry/crash` 上报。上报成功后删除。
 *
 * 字段均为脱敏后的值 —— 崩溃写入本表前已通过 [CrashSanitizer] 过滤掉
 * `token`、`payload`、`note` 等敏感字段。
 */
@Entity(tableName = "crash_reports")
data class CrashReportEntity(
    @PrimaryKey val id: String,
    /** 稳定哈希（exception class + 首帧）—— 服务端用于聚合相同崩溃。 */
    val crashSignature: String,
    val appVersion: String,
    val androidVersion: String,
    val deviceModel: String,
    val exceptionClass: String,
    val exceptionMessage: String,
    val stacktrace: String,
    val reportedAt: Long,   // epoch millis
    val deviceIdHash: String?,
    /** 已尝试上传次数；用于上限防护，避免永久重试循环。 */
    val attempts: Int = 0
)
