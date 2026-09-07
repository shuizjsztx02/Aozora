package com.aozora.app.deletion

import com.aozora.app.auth.TokenStore
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException

/**
 * DeletionClient — T-M5-04c 账号删除服务端调用。
 *
 * 流程：
 *   1. [requestDeletionToken]：POST /me/deletion-request → 5 分钟有效的 confirm_token
 *   2. [deleteAccount]：DELETE /me?confirm_token=xxx → 服务端将用户置为 frozen
 *
 * 错误模型：
 *   - [DeletionError.AuthLost]：refresh token 已失效，需要用户重新登录
 *   - [DeletionError.InvalidToken]：confirm_token 无效/过期/已用
 *   - [DeletionError.DeletionPending]：用户已在 frozen 状态
 *   - [DeletionError.ServerError] / [DeletionError.Network]：瞬态失败
 *
 * 不直接做本地数据清理；成功后的本地状态清空由调用方
 * （AozoraBridge 的 `account.delete` handler）负责，以便与 TokenStore /
 * ConsentStore / Room 的清空逻辑集中在同一处。
 */
class DeletionClient(
    private val baseUrl: String,
    private val tokenStore: TokenStore,
    private val httpClient: OkHttpClient
) {
    sealed class DeletionError(message: String) : IOException(message) {
        class AuthLost(message: String) : DeletionError(message)
        class InvalidToken(message: String) : DeletionError(message)
        class DeletionPending(message: String) : DeletionError(message)
        class ServerError(val httpCode: Int, message: String) : DeletionError(message)
        class Network(message: String) : DeletionError(message)
    }

    data class DeletionTokenResult(
        val confirmToken: String,
        val expiresAt: String
    )

    data class DeletionResult(
        val status: String,
        val frozenAt: String,
        val message: String
    )

    /**
     * POST /me/deletion-request — 获取一次性 confirm_token。
     *
     * 需要有效的 access_token；若 401 则调用方应触发重新登录流程。
     */
    fun requestDeletionToken(): DeletionTokenResult {
        val accessToken = tokenStore.getAccessToken()
            ?: throw DeletionError.AuthLost("no access token available")
        val request = Request.Builder()
            .url("$baseUrl/me/deletion-request")
            .post("{}".toRequestBody(JSON_MEDIA_TYPE))
            .header("Authorization", "Bearer $accessToken")
            .header("Content-Type", "application/json")
            .build()
        return httpClient.newCall(request).execute().use { response ->
            when {
                response.isSuccessful -> {
                    val body = response.body?.string()
                        ?: throw DeletionError.Network("empty response body")
                    val json = JSONObject(body)
                    DeletionTokenResult(
                        confirmToken = json.optString("confirm_token", "").ifEmpty {
                            throw DeletionError.Network("missing confirm_token in response")
                        },
                        expiresAt = json.optString("expires_at", "").ifEmpty {
                            throw DeletionError.Network("missing expires_at in response")
                        }
                    )
                }
                response.code == 401 -> throw DeletionError.AuthLost("authentication required")
                response.code == 409 -> throw DeletionError.DeletionPending(
                    "account deletion already in progress"
                )
                response.code in 400..499 -> {
                    val errBody = response.body?.string().orEmpty()
                    throw DeletionError.InvalidToken(
                        "deletion request rejected: HTTP ${response.code} $errBody"
                    )
                }
                else -> throw DeletionError.ServerError(
                    response.code,
                    "deletion request failed: HTTP ${response.code}"
                )
            }
        }
    }

    /**
     * DELETE /me?confirm_token=xxx — 消费 token 并将账号置为 frozen。
     *
     * 服务端成功返回 200 后，调用方应清空本地数据（Room / TokenStore /
     * ConsentStore / localStorage）并导航回首启页。
     */
    fun deleteAccount(confirmToken: String): DeletionResult {
        val accessToken = tokenStore.getAccessToken()
            ?: throw DeletionError.AuthLost("no access token available")
        val encodedToken = java.net.URLEncoder.encode(confirmToken, "UTF-8")
        val request = Request.Builder()
            .url("$baseUrl/me?confirm_token=$encodedToken")
            .delete()
            .header("Authorization", "Bearer $accessToken")
            .build()
        return httpClient.newCall(request).execute().use { response ->
            when {
                response.isSuccessful -> {
                    val body = response.body?.string()
                        ?: throw DeletionError.Network("empty response body")
                    val json = JSONObject(body)
                    DeletionResult(
                        status = json.optString("status", "frozen"),
                        frozenAt = json.optString("frozen_at", ""),
                        message = json.optString("message", "")
                    )
                }
                response.code == 401 -> throw DeletionError.AuthLost("authentication required")
                response.code == 409 -> throw DeletionError.DeletionPending(
                    "account deletion already in progress"
                )
                response.code == 400 -> {
                    val errBody = response.body?.string().orEmpty()
                    throw DeletionError.InvalidToken(
                        "confirm_token rejected: $errBody"
                    )
                }
                else -> throw DeletionError.ServerError(
                    response.code,
                    "account deletion failed: HTTP ${response.code}"
                )
            }
        }
    }

    companion object {
        private val JSON_MEDIA_TYPE = "application/json".toMediaType()
    }
}
