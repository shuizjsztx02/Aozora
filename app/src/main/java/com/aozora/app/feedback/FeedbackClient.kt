package com.aozora.app.feedback

import android.content.Context
import android.os.Build
import android.util.Log
import com.aozora.app.auth.AuthClient
import com.aozora.app.auth.TokenStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import java.security.MessageDigest
import java.util.UUID

/**
 * T-M5-05c: In-app feedback client.
 *
 * Submits user feedback to `POST /telemetry/feedback`. Unlike crash reports,
 * feedback is submitted in real-time from the UI — no local queue. The user
 * sees the result (success / rate-limited / network error) immediately.
 *
 * Rate limit: 5 per user per hour (enforced server-side). This client does
 * not pre-check — the server is the source of truth.
 *
 * PII: `content` and `contact` are user-provided. The feedback form should
 * warn users not to include sensitive data. We do NOT auto-redact feedback
 * text (unlike crash reports) — it's intentionally user-authored.
 */
class FeedbackClient(
    private val context: Context,
    private val baseUrl: String,
    private val httpClient: OkHttpClient,
    private val authClient: AuthClient,
    private val tokenStore: TokenStore,
) {

    sealed class FeedbackError(message: String, cause: Throwable? = null) : Exception(message, cause) {
        class AuthLost(cause: Throwable? = null) : FeedbackError("auth lost", cause)
        class RateLimited : FeedbackError("rate limited")
        class BadRequest(val detail: String) : FeedbackError("bad request: $detail")
        class Network(message: String, cause: Throwable? = null) : FeedbackError(message, cause)
        class ServerError(val httpCode: Int, message: String) : FeedbackError("HTTP $httpCode: $message")
    }

    /**
     * Submit feedback. Returns the server-assigned id on success.
     *
     * @param category one of "bug", "suggestion", "other"
     * @param content free-form text (1–4000 chars)
     * @param contact optional self-supplied contact (email / handle)
     */
    suspend fun submit(
        category: String,
        content: String,
        contact: String? = null,
    ): String = withContext(Dispatchers.IO) {
        val token = try {
            authClient.ensureValidAccessToken()
        } catch (e: AuthClient.AuthLostException) {
            throw FeedbackError.AuthLost(e)
        }

        val deviceIdHash = try {
            val deviceId = tokenStore.deviceId
            val digest = MessageDigest.getInstance("SHA-256").digest(deviceId.toByteArray())
            digest.joinToString("") { "%02x".format(it) }
        } catch (_: Exception) {
            null
        }

        val body = JSONObject()
            .put("category", category)
            .put("content", content)
            .apply { contact?.let { put("contact", it) } }
            .put("app_version", appVersion())
            .put("android_version", Build.VERSION.RELEASE)
            .put("device_model", "${Build.MANUFACTURER} ${Build.MODEL}".trim())
            .apply { deviceIdHash?.let { put("device_id_hash", it) } }
            .toString()

        val request = Request.Builder()
            .url("$baseUrl/telemetry/feedback")
            .post(body.toRequestBody(JSON))
            .header("Authorization", "Bearer $token")
            .header("Content-Type", "application/json")
            .header("X-Request-Id", UUID.randomUUID().toString())
            .build()

        try {
            httpClient.newCall(request).execute().use { resp ->
                when {
                    resp.isSuccessful -> {
                        val respBody = resp.body?.string().orEmpty()
                        JSONObject(respBody).optString("id", "")
                    }
                    resp.code == 401 -> throw FeedbackError.AuthLost()
                    resp.code == 429 -> throw FeedbackError.RateLimited()
                    resp.code == 400 -> {
                        val detail = resp.body?.string().orEmpty()
                        throw FeedbackError.BadRequest(detail)
                    }
                    resp.code in 402..499 -> {
                        // Other 4xx — treat as bad request (client-side issue)
                        throw FeedbackError.AuthLost()
                    }
                    else -> throw FeedbackError.ServerError(
                        resp.code,
                        resp.body?.string().orEmpty()
                    )
                }
            }
        } catch (e: FeedbackError) {
            throw e
        } catch (e: IOException) {
            throw FeedbackError.Network(e.message ?: "network error", e)
        } catch (e: Exception) {
            throw FeedbackError.Network(e.message ?: "request failed", e)
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
        private const val TAG = "FeedbackClient"
        private val JSON = "application/json".toMediaType()
    }
}
