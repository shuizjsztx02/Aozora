package com.aozora.app.auth

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException

/**
 * AuthClient — 同步引擎的认证门面。
 *
 * 职责：
 * - 在每次同步开始前保证 access_token 有效（未过期或已成功刷新）。
 * - 首次同步时自动完成 /auth/guest 匿名注册（R-PROD-13）。
 * - /auth/refresh 单飞（Mutex），避免多个并发 worker 各自刷新导致 refresh_token
 *   竞争失败（服务端采用 refresh 轮换，旧 refresh_token 立即失效）。
 * - refresh 失败（4xx）时抛 [AuthLostException]，上层清空本地状态并视为"需要用户干预"。
 *
 * 非职责：
 * - 不负责同步业务的 HTTP（push/pull）—— 由 [com.aozora.app.sync.SyncApi] 实现。
 * - 不缓存 token；每次调用 [ensureValidAccessToken] 均读取 [TokenStore]。
 */
open class AuthClient(
    private val baseUrl: String,
    private val tokenStore: TokenStore,
    private val httpClient: OkHttpClient
) {

    class AuthLostException(message: String, cause: Throwable? = null) :
        IOException(message, cause)

    private val refreshMutex = Mutex()

    /**
     * 返回当前有效的 access_token（必要时自动刷新或首次注册）。
     *
     * - 未注册：自动 POST /auth/guest → 写入 TokenStore → 返回 access_token。
     * - 已注册但过期：POST /auth/refresh（单飞）→ 写入 TokenStore → 返回新 access_token。
     * - refresh 失败（4xx / IO 失败）：抛 [AuthLostException]，调用方清空本地状态。
     *
     * 标记 `open` 供 androidTest 子类化（fake 实现）。
     */
    open suspend fun ensureValidAccessToken(): String {
        // 快速路径：未注册 → 走注册
        if (!tokenStore.isRegistered) {
            return registerGuest()
        }
        val nowSec = System.currentTimeMillis() / 1000L
        if (!tokenStore.isAccessTokenExpired(nowSec)) {
            return tokenStore.getAccessToken()
                ?: throw AuthLostException("access token missing despite registered")
        }
        return refreshAccessToken()
    }

    /** 清空本地认证状态（外部调用方在 refresh 失败后使用）。 */
    open fun clearAuth() {
        tokenStore.clear()
    }

    /** 当前 user_id；未注册时返回 null。 */
    open fun currentUserId(): String? = tokenStore.getUserId()

    // ─── internal ───

    private suspend fun registerGuest(): String = refreshMutex.withLock {
        // 双检：并发场景下另一协程可能已经完成注册
        if (tokenStore.isRegistered) {
            val nowSec = System.currentTimeMillis() / 1000L
            if (!tokenStore.isAccessTokenExpired(nowSec)) {
                // P2-5: 避免 NPE（并发 clearAuth() 可能已清空 token）
                return@withLock tokenStore.getAccessToken()
                    ?: throw AuthLostException("access token missing after concurrent clear")
            }
            return@withLock doRefresh()
        }
        val body = JSONObject().apply {
            put("device_id", tokenStore.deviceId)
            put("timezone", java.util.TimeZone.getDefault().id)
        }
        val request = Request.Builder()
            .url("$baseUrl/auth/guest")
            .post(body.toString().toRequestBody(JSON))
            .header("Content-Type", "application/json")
            .build()
        httpClient.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                throw AuthLostException("guest register failed: HTTP ${response.code}")
            }
            val payload = response.body?.string()
                ?: throw AuthLostException("guest register: empty body")
            val json = JSONObject(payload)
            val tokens = json.getJSONObject("tokens")
            try {
                tokenStore.saveTokens(
                    accessToken = tokens.getString("access_token"),
                    refreshToken = tokens.getString("refresh_token"),
                    expiresAtSec = nowSec() + tokens.getLong("expires_in"),
                    userId = json.getString("user_id"),
                    markRegistered = true
                )
            } catch (e: KeystoreCrypto.KeystoreUnavailableException) {
                // P1-2: Keystore 写入失败 → wrap 为 AuthLostException，触发上层清理
                throw AuthLostException("failed to save tokens after guest register", e)
            }
            return@withLock tokens.getString("access_token")
        }
    }

    /**
     * /auth/refresh 单飞刷新。
     * - 4xx（无效/过期 refresh_token）→ 抛 [AuthLostException]。
     * - 5xx/IO → 抛 [AuthLostException]（调用方决定是否重试）。
     */
    private suspend fun refreshAccessToken(): String = refreshMutex.withLock {
        // 双检：并发刷新场景下另一协程可能已拿到新 token
        val nowSec = System.currentTimeMillis() / 1000L
        if (!tokenStore.isAccessTokenExpired(nowSec)) {
            return@withLock tokenStore.getAccessToken()
                ?: throw AuthLostException("access token missing after refresh")
        }
        return@withLock doRefresh()
    }

    private fun doRefresh(): String {
        val refreshToken = tokenStore.getRefreshToken()
            ?: throw AuthLostException("no refresh token available")
        val body = JSONObject().apply {
            put("refresh_token", refreshToken)
            put("device_id", tokenStore.deviceId)
        }
        val request = Request.Builder()
            .url("$baseUrl/auth/refresh")
            .post(body.toString().toRequestBody(JSON))
            .header("Content-Type", "application/json")
            .build()
        return httpClient.newCall(request).execute().use { response ->
            when {
                response.isSuccessful -> {
                    val payload = response.body?.string()
                        ?: throw AuthLostException("refresh: empty body")
                    val json = JSONObject(payload)
                    try {
                        tokenStore.saveTokens(
                            accessToken = json.getString("access_token"),
                            refreshToken = json.getString("refresh_token"),
                            expiresAtSec = nowSec() + json.getLong("expires_in")
                        )
                    } catch (e: KeystoreCrypto.KeystoreUnavailableException) {
                        // P1-2: Keystore 写入失败 → wrap 为 AuthLostException
                        throw AuthLostException("failed to save tokens after refresh", e)
                    }
                    json.getString("access_token")
                }
                response.code in 400..499 -> {
                    // refresh_token 失效或服务端吊销
                    throw AuthLostException("refresh rejected: HTTP ${response.code}")
                }
                else -> {
                    // P1-6: 5xx → IOException(RETRYABLE)，让上层 WorkManager 退避重试
                    // 仅 4xx → AuthLostException（永久失败）
                    throw IOException("refresh failed: HTTP ${response.code}")
                }
            }
        }
    }

    private fun nowSec(): Long = System.currentTimeMillis() / 1000L

    companion object {
        private val JSON = "application/json".toMediaType()
    }
}
