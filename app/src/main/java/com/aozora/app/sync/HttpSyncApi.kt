package com.aozora.app.sync

import com.aozora.app.auth.AuthClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException

/**
 * OkHttp 实现 [SyncApi]。
 *
 * - 每次请求前通过 [AuthClient] 取当前 access_token，作为 `Authorization: Bearer` 携带。
 * - 不自行实现重试；WorkManager 的 backoff policy 负责调度重试。
 * - HTTP 401/403 显式抛 [AuthClient.AuthLostException]（token 失效/被吊销），
 *   由 [SyncEngine] 清空本地 auth 并引导用户重新注册。
 * - 其他非 2xx 抛 [SyncHttpException]，冒泡到 [SyncEngine] 按状态码判定 retryable。
 */
class HttpSyncApi(
    private val baseUrl: String,
    private val httpClient: OkHttpClient,
    private val authClient: AuthClient
) : SyncApi {

    override suspend fun push(request: PushRequest): PushResponse {
        val body = SyncPayloadMapper.buildPushRequestJson(request)
        val responseJson = executeAuthorized("$baseUrl/sync/push", body)
        return SyncPayloadMapper.parsePushResponse(responseJson)
    }

    override suspend fun pull(request: PullRequest): PullResponse {
        val body = SyncPayloadMapper.buildPullRequestJson(request)
        val responseJson = executeAuthorized("$baseUrl/sync/pull", body)
        return SyncPayloadMapper.parsePullResponse(responseJson)
    }

    override suspend fun bootstrap(): BootstrapResponse {
        // Bootstrap 是 POST 但无 body，携带 auth header 即可。
        val responseJson = executeAuthorized("$baseUrl/sync/bootstrap", "{}")
        return SyncPayloadMapper.parseBootstrapResponse(responseJson)
    }

    private suspend fun executeAuthorized(url: String, bodyJson: String): String =
        withContext(Dispatchers.IO) {
            val token = authClient.ensureValidAccessToken()
            val request = Request.Builder()
                .url(url)
                .post(bodyJson.toRequestBody(JSON))
                .header("Authorization", "Bearer $token")
                .header("Content-Type", "application/json")
                .build()
            httpClient.newCall(request).execute().use { response ->
                val bodyStr = response.body?.string().orEmpty()
                if (!response.isSuccessful) {
                    // N-M3-6 修复：401/403 显式视为认证丢失，抛 AuthLostException。
                    // 职责边界清晰：HttpSyncApi 层直接判定语义，SyncEngine 无需知道 HTTP 状态码含义。
                    // NEW-3 修复：仅传递状态码，不传递响应体。服务端错误页面可能包含
                    // HTML、堆栈或其他敏感内容，避免通过 SyncStatusEvent → 前端/日志泄露。
                    if (response.code in 401..403) {
                        throw AuthClient.AuthLostException("HTTP ${response.code}")
                    }
                    throw SyncHttpException(response.code, "HTTP ${response.code}")
                }
                bodyStr
            }
        }

    companion object {
        private val JSON = "application/json".toMediaType()
    }
}

/** 假实现基类，仅用于单元测试构造（androidTest 中提供具体实现）。 */
@Suppress("unused")
internal open class FakeSyncApiBase : SyncApi {
    override suspend fun push(request: PushRequest): PushResponse =
        throw IOException("fake")

    override suspend fun pull(request: PullRequest): PullResponse =
        throw IOException("fake")

    override suspend fun bootstrap(): BootstrapResponse =
        throw IOException("fake")
}
