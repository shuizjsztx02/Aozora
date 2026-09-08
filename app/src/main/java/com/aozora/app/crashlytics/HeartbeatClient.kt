package com.aozora.app.crashlytics

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

/**
 * T-M5-05ab fix (S1-3): lightweight client that calls `POST /telemetry/heartbeat`
 * once per app start. The server uses the signal to populate the
 * `active_devices_total` Gauge (unique devices seen in the last 24h).
 *
 * Design:
 * - Sends `X-Device-Id: <raw UUID>`; server hashes server-side to full SHA-256.
 * - No-op when no access token is available (user not logged in).
 * - Failures are swallowed (best-effort; never blocks app startup).
 * - Does NOT enforce its own rate-limit — server's per-IP + per-device rate
 *   limit handles abusive clients. One call per startup is well under budget.
 */
class HeartbeatClient(
    private val baseUrl: String,
    private val httpClient: OkHttpClient,
    private val authClient: AuthClient,
    private val tokenStore: TokenStore,
) {
    /**
     * Fire-and-forget heartbeat. Safe to call on any dispatcher; internally
     * hops to [Dispatchers.IO].
     *
     * @param appVersion current app version string.
     * @param hasConsent whether the user has accepted the privacy policy.
     *   When false the heartbeat is skipped (defense-in-depth).
     */
    suspend fun send(appVersion: String, hasConsent: Boolean = false) {
        // F-M5-04a-F1: skip heartbeat when user hasn't consented.
        // This is defense-in-depth; the server also checks consent for
        // sync pushes but telemetry signals should not be sent without consent.
        if (!hasConsent) return

        val token = try {
            authClient.ensureValidAccessToken()
        } catch (_: AuthClient.AuthLostException) {
            // Not logged in / token unrecoverable — skip silently.
            return
        } catch (_: Exception) {
            Log.w(TAG, "heartbeat: token fetch failed", null)
            return
        }

        // Device ID header; server hashes it to full SHA-256.
        val deviceId = try {
            tokenStore.deviceId
        } catch (_: Exception) {
            // TokenStore is unlikely to throw here, but defend anyway.
            null
        }

        val body = JSONObject().put("app_version", appVersion).toString()
        val request = Request.Builder()
            .url("$baseUrl/telemetry/heartbeat")
            .post(body.toRequestBody(JSON))
            .header("Authorization", "Bearer $token")
            .header("Content-Type", "application/json")
            .apply { deviceId?.let { header("X-Device-Id", it) } }
            .build()

        try {
            withContext(Dispatchers.IO) {
                httpClient.newCall(request).execute().use { resp ->
                    if (!resp.isSuccessful) {
                        Log.w(TAG, "heartbeat: HTTP ${resp.code}")
                    }
                }
            }
        } catch (_: IOException) {
            // Network error — best-effort, don't log every transient failure.
        } catch (_: Exception) {
            Log.w(TAG, "heartbeat: unexpected failure", null)
        }
    }

    companion object {
        private const val TAG = "Heartbeat"
        private val JSON = "application/json".toMediaType()
    }
}
