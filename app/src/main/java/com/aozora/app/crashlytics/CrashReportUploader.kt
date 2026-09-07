package com.aozora.app.crashlytics

import com.aozora.app.auth.AuthClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import java.util.UUID
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.suspendCancellableCoroutine

/**
 * T-M5-05b: 崩溃报告 HTTP 上传器。
 *
 * 复用与 [com.aozora.app.export.ExportClient] 相同的 [AuthClient]，
 * 仅调用 `POST /telemetry/crash`。上传成功后由调用方删除本地记录。
 *
 * 失败分类：
 * - [UploadError.AuthLost]：token 不可恢复，丢弃本条报告（避免永久重试）
 * - [UploadError.RateLimited]：设备配额超限，延后到下一启动周期
 * - [UploadError.Network]：网络错误，保留以重试
 * - [UploadError.ServerError]：5xx，保留以重试
 */
class CrashReportUploader(
    private val baseUrl: String,
    private val httpClient: OkHttpClient,
    private val authClient: AuthClient,
) {

    sealed class UploadError(message: String, cause: Throwable? = null) : Exception(message, cause) {
        class AuthLost(cause: Throwable? = null) : UploadError("auth lost", cause)
        class RateLimited : UploadError("rate limited")
        class Network(message: String, cause: Throwable? = null) : UploadError(message, cause)
        class ServerError(val httpCode: Int, message: String) : UploadError("HTTP $httpCode: $message")
    }

    /**
     * 上传单条崩溃报告。成功后返回服务端 id（客户端无需持久化）。
     */
    suspend fun upload(report: CrashReportEntity): String = withContext(Dispatchers.IO) {
        val token = try {
            authClient.ensureValidAccessToken()
        } catch (e: AuthClient.AuthLostException) {
            throw UploadError.AuthLost(e)
        }

        val body = JSONObject()
            .put("crash_signature", report.crashSignature)
            .put("app_version", report.appVersion)
            .put("android_version", report.androidVersion)
            .put("device_model", report.deviceModel)
            .put("exception_class", report.exceptionClass)
            .put("exception_message", report.exceptionMessage)
            .put("stacktrace", report.stacktrace)
            .put("reported_at", report.reportedAt)
            .apply { report.deviceIdHash?.let { put("device_id_hash", it) } }
            .toString()

        val request = Request.Builder()
            .url("$baseUrl/telemetry/crash")
            .post(body.toRequestBody(JSON))
            .header("Authorization", "Bearer $token")
            .header("Content-Type", "application/json")
            .header("X-Request-Id", UUID.randomUUID().toString())
            .build()

        val response = suspendSend(request)
        response.use { resp ->
            when {
                resp.isSuccessful -> {
                    val respBody = resp.body?.string().orEmpty()
                    JSONObject(respBody).optString("id", "")
                }
                resp.code == 401 -> throw UploadError.AuthLost()
                resp.code == 429 -> throw UploadError.RateLimited()
                resp.code in 400..499 -> {
                    // 客户端错误（4xx 非 401/429）：报告本身有问题，丢弃
                    throw UploadError.AuthLost()
                }
                else -> throw UploadError.ServerError(
                    resp.code,
                    resp.body?.string().orEmpty()
                )
            }
        }
    }

    private suspend fun suspendSend(request: Request) = suspendCancellableCoroutine<okhttp3.Response> { cont ->
        val call = httpClient.newCall(request)
        cont.invokeOnCancellation { call.cancel() }
        try {
            call.enqueue(object : okhttp3.Callback {
                override fun onFailure(call: okhttp3.Call, e: IOException) {
                    if (!cont.isActive) return
                    cont.resumeWithException(UploadError.Network(e.message ?: "network error", e))
                }
                override fun onResponse(call: okhttp3.Call, response: okhttp3.Response) {
                    if (!cont.isActive) {
                        response.close()
                        return
                    }
                    cont.resume(response)
                }
            })
        } catch (e: Exception) {
            cont.resumeWithException(UploadError.Network(e.message ?: "request failed", e))
        }
    }

    companion object {
        private val JSON = "application/json".toMediaType()
    }
}
