package com.aozora.app.crashlytics

import android.content.Context
import android.os.Build
import com.aozora.app.auth.AuthClient
import com.aozora.app.consent.ConsentStore
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import java.io.PrintWriter
import java.io.StringWriter
import java.util.UUID

/**
 * T-M5-05b: 全局崩溃捕获 → 脱敏 → 本地 SQLite → 下次启动上报。
 *
 * 生命周期：
 * 1. 在 [com.aozora.app.AozoraApp.onCreate] 中调用 [install]，
 *    注册为 [Thread.getDefaultUncaughtExceptionHandler] 的下游 handler。
 * 2. 任何未捕获异常触发 → [sanitize] → 写入 [CrashReportDatabase]。
 * 3. 调用系统默认 handler（让进程正常终止）。
 * 4. 下一次应用启动时 [uploadPending] 被调用 —— 仅在用户已同意隐私政策
 *    （[ConsentStore.hasConsented]）且已登录（[AuthClient] 可取到 token）时上传。
 *
 * 关键约束：
 * - 写入路径在 UncaughtExceptionHandler 内，必须同步完成（不能 suspend）。
 * - 不能依赖业务 Room 数据库（可能正处于不一致状态）。
 * - 脱敏在写入前完成，服务端收到的消息/堆栈已无敏感原文。
 */
class CrashReporter(
    private val context: Context,
    private val crashDb: CrashReportDatabase,
    private val consentStore: ConsentStore,
) {

    private val dao = crashDb.crashReportDao()

    private var previousHandler: Thread.UncaughtExceptionHandler? = null

    /**
     * 注册全局 handler。多次调用幂等（只替换一次）。
     */
    fun install() {
        if (previousHandler != null) return
        previousHandler = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
            try {
                captureSync(thread, throwable)
            } catch (_: Throwable) {
                // 捕获阶段绝不能再次抛出，否则系统会跳过后续 handler。
            }
            // 链式调用前一个 handler（通常是系统的，负责终止进程）。
            previousHandler?.uncaughtException(thread, throwable)
        }
    }

    /**
     * 同步写入一条崩溃报告。由 handler 调用 —— 不能用协程。
     */
    private fun captureSync(thread: Thread, throwable: Throwable) {
        val sw = StringWriter()
        throwable.printStackTrace(PrintWriter(sw))
        val rawMessage = throwable.message ?: ""
        val (cleanMessage, cleanStacktrace) = CrashSanitizer.sanitize(rawMessage, sw.toString())

        val entity = CrashReportEntity(
            id = UUID.randomUUID().toString(),
            crashSignature = CrashSanitizer.signature(throwable),
            appVersion = appVersion(),
            androidVersion = Build.VERSION.RELEASE,
            deviceModel = "${Build.MANUFACTURER} ${Build.MODEL}".trim(),
            exceptionClass = throwable.javaClass.name,
            exceptionMessage = cleanMessage,
            stacktrace = cleanStacktrace,
            reportedAt = System.currentTimeMillis(),
            deviceIdHash = null,  // 写入时取不到 deviceIdHash，留待上传时补
        )
        dao.insertBlocking(entity)
    }

    /**
     * 下一次启动时调用：尝试上传所有挂起的崩溃报告。
     *
     * 由 [com.aozora.app.AozoraApp.onCreate] 在协程中调用。
     * 仅在用户已同意隐私政策且已登录时执行。
     */
    fun uploadPending(
        authClient: AuthClient,
        httpClient: OkHttpClient,
        baseUrl: String,
    ) {
        if (!consentStore.hasConsented()) return
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        scope.launch {
            val uploader = CrashReportUploader(baseUrl, httpClient, authClient)
            val pending = try {
                dao.listPending()
            } catch (_: Exception) {
                return@launch
            }
            for (report in pending) {
                if (report.attempts >= MAX_UPLOAD_ATTEMPTS) {
                    // 放弃：永久失败（auth lost 或达到重试上限）
                    dao.deleteById(report.id)
                    continue
                }
                try {
                    uploader.upload(report)
                    dao.deleteById(report.id)
                } catch (e: CrashReportUploader.UploadError.AuthLost) {
                    // 用户已登出或 token 不可恢复 —— 丢弃报告
                    dao.deleteById(report.id)
                } catch (e: CrashReportUploader.UploadError.RateLimited) {
                    // 设备配额超限 —— 延后，不计入 attempt
                } catch (e: Exception) {
                    // 网络/服务器错误 —— 累加 attempt，下次再试
                    dao.update(report.copy(attempts = report.attempts + 1))
                }
            }
        }
    }

    private fun appVersion(): String {
        return try {
            val pInfo = context.packageManager.getPackageInfo(context.packageName, 0)
            pInfo.versionName ?: "unknown"
        } catch (_: Exception) {
            "unknown"
        }
    }

    companion object {
        /** 单条报告最大上传尝试次数（超过后丢弃）。 */
        const val MAX_UPLOAD_ATTEMPTS = 5
    }
}
