package com.aozora.app.consent

import android.util.Log
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
 * T-M5-05c fix (S1): lightweight client that uploads privacy consent to
 * `POST /consent` immediately after the user accepts onboarding.
 *
 * Design:
 * - Sends [deviceId], [policyVersion], [consentedAt] with the local clock time.
 * - No authentication required — consent is pre-auth (guest phase).
 *   The server rate-limits per IP.
 * - Failures are swallowed (best-effort; never blocks app startup or
 *   onboarding completion). Consent was already recorded locally.
 * - Does NOT enforce its own rate-limit — server's per-IP rate limit
 *   handles abusive clients. One call per onboarding session is well
 *   under budget.
 */
class ConsentClient(
    private val baseUrl: String,
    private val httpClient: OkHttpClient,
    private val tokenStore: TokenStore,
) {

    /**
     * Upload consent to the server. Best-effort; returns true on HTTP success.
     *
     * @param policyVersion e.g. "v1"
     * @param consentedAt epoch millis when the user accepted.
     * @return true if the server accepted the upload (HTTP 2xx), false otherwise.
     */
    suspend fun send(policyVersion: String, consentedAt: Long): Boolean {
        val deviceId = try {
            tokenStore.deviceId
        } catch (_: Exception) {
            // TokenStore rarely throws here, but defend anyway.
            return false // empty deviceId would be rejected server-side; skip silently.
        }

        val body = JSONObject()
            .put("device_id", deviceId)
            .put("policy_version", policyVersion.take(32))
            .put("consented_at", consentedAt)
            .toString()

        val request = Request.Builder()
            .url("$baseUrl/consent")
            .post(body.toRequestBody(JSON))
            .header("Content-Type", "application/json")
            .build()

        return try {
            withContext(Dispatchers.IO) {
                httpClient.newCall(request).execute().use { resp ->
                    if (!resp.isSuccessful) {
                        Log.w(TAG, "consent: HTTP ${resp.code}")
                    }
                    resp.isSuccessful
                }
            }
        } catch (_: IOException) {
            // Network error — best-effort, don't log every transient failure.
            false
        } catch (_: Exception) {
            Log.w(TAG, "consent: unexpected failure", null)
            false
        }
    }

    companion object {
        private const val TAG = "ConsentClient"
        private val JSON = "application/json".toMediaType()
    }
}
