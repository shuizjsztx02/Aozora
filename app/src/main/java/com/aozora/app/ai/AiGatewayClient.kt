package com.aozora.app.ai

import com.aozora.app.auth.AuthClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONObject
import java.io.IOException
import java.io.InterruptedIOException
import java.util.UUID
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * T-M4-02c: AI Gateway HTTP client for WebView bridge.
 *
 * Responsible for:
 * - Token injection via [AuthClient]
 * - POST to /ai/ endpoints on the app server
 * - 25 s call timeout (aligned with ADR-003 SLO)
 * - Error mapping to bridge error codes
 *
 * Token never leaves the Kotlin layer — the WebView receives only the
 * final AI response data.
 */
class AiGatewayClient(
    private val baseUrl: String,
    httpClient: OkHttpClient,
    private val authClient: AuthClient
) {

    /**
     * P3#11: enforce the ADR-003 25 s call budget regardless of the
     * caller-provided client config. `newBuilder()` shares the connection
     * pool and dispatcher with the original client, so this is cheap.
     */
    private val httpClient: OkHttpClient = httpClient.newBuilder()
        .callTimeout(CALL_TIMEOUT_SECONDS, TimeUnit.SECONDS)
        .build()

    sealed class AiError(message: String, cause: Throwable? = null) : Exception(message, cause) {
        class Timeout(cause: Throwable? = null) :
            AiError("AI request timed out", cause)
        class Network(message: String, cause: Throwable? = null) :
            AiError(message, cause)
        class RateLimited :
            AiError("AI rate limit exceeded")
        class Unavailable(val httpCode: Int) :
            AiError("AI service unavailable: HTTP $httpCode")
        class AuthLost(cause: Throwable? = null) :
            AiError("Authentication lost", cause)
    }

    /** POST /ai/interpret — 自然语言 → ActionProposal.
     *  T11-6（决策 #7）：contextJson = 桥 payload.context 原样序列化（{persona, profile}）；
     *  null 时 body 不带该键，服务端保持旧行为。 */
    suspend fun interpret(input: String, contextJson: String? = null): String =
        executeAuthorized("$baseUrl/ai/interpret", buildInterpretRequest(input, contextJson))

    /** POST /ai/validate — 验证 ActionProposal. */
    suspend fun validate(proposalJson: String, bookId: String): String =
        executeAuthorized("$baseUrl/ai/validate", buildValidateRequest(proposalJson, bookId))

    /** POST /ai/execute — 执行已验证的 ActionProposal. */
    suspend fun execute(proposalJson: String, bookId: String, confirmationToken: String): String =
        executeAuthorized("$baseUrl/ai/execute", buildExecuteRequest(proposalJson, bookId, confirmationToken))

    /** POST /ai/report — 月度报告/洞察. */
    suspend fun report(bookId: String, period: String): String =
        executeAuthorized("$baseUrl/ai/report", buildReportRequest(bookId, period))

    /**
     * T-M4-04e: POST /ai/parse-image — 小票/账单图片 → ActionProposal.
     *
     * 视觉模型较慢，使用 120s 超时而非默认 25s。
     */
    suspend fun parseImage(attachmentId: String, bookId: String): String =
        executeAuthorized(
            "$baseUrl/ai/parse-image",
            buildParseImageRequest(attachmentId, bookId),
            timeoutSeconds = IMAGE_PARSE_TIMEOUT_SECONDS,
        )

    // ─── internal ────────────────────────────────────────────────────────

    private fun buildInterpretRequest(input: String, contextJson: String?): String {
        val body = JSONObject().put("input", input)
        if (contextJson != null) body.put("context", JSONObject(contextJson))
        return body.toString()
    }

    private fun buildValidateRequest(proposalJson: String, bookId: String): String {
        val proposal = JSONObject(proposalJson)
        return JSONObject()
            .put("proposal", proposal)
            .put("book_id", bookId)
            .toString()
    }

    private fun buildExecuteRequest(proposalJson: String, bookId: String, confirmationToken: String): String {
        val proposal = JSONObject(proposalJson)
        return JSONObject()
            .put("proposal", proposal)
            .put("book_id", bookId)
            .put("confirmation_token", confirmationToken)
            .toString()
    }

    private fun buildReportRequest(bookId: String, period: String): String =
        JSONObject()
            .put("book_id", bookId)
            .put("period", period)
            .toString()

    private fun buildParseImageRequest(attachmentId: String, bookId: String): String =
        JSONObject()
            .put("attachment_id", attachmentId)
            .put("book_id", bookId)
            .toString()

    private suspend fun executeAuthorized(
        url: String,
        bodyJson: String,
        timeoutSeconds: Long = CALL_TIMEOUT_SECONDS,
    ): String {
        val token = withContext(Dispatchers.IO) {
            try {
                authClient.ensureValidAccessToken()
            } catch (e: AuthClient.AuthLostException) {
                throw AiError.AuthLost(e)
            }
        }

        val request = Request.Builder()
            .url(url)
            .post(bodyJson.toRequestBody(JSON))
            .header("Authorization", "Bearer $token")
            .header("Content-Type", "application/json")
            .header("X-Request-Id", UUID.randomUUID().toString())
            .build()

        // Use a per-call client when the caller needs a non-default timeout
        // (e.g. image parse at 120s). newBuilder() shares the connection pool.
        val callClient = if (timeoutSeconds != CALL_TIMEOUT_SECONDS) {
            httpClient.newBuilder().callTimeout(timeoutSeconds, TimeUnit.SECONDS).build()
        } else {
            httpClient
        }

        // P1#2: enqueue + suspendCancellableCoroutine instead of a blocking
        // execute() — the HTTP call is now actually cancelled when the
        // calling coroutine is cancelled (e.g. user leaves the screen).
        return suspendCancellableCoroutine { cont ->
            val call = callClient.newCall(request)
            cont.invokeOnCancellation { call.cancel() }
            call.enqueue(object : Callback {
                override fun onFailure(call: Call, e: IOException) {
                    if (!cont.isActive) return
                    val error = when (e) {
                        // SocketTimeoutException and callTimeout expiry
                        // (InterruptedIOException) both map to Timeout.
                        is InterruptedIOException -> AiError.Timeout(e)
                        else -> AiError.Network(e.message ?: "Network error", e)
                    }
                    cont.resumeWithException(error)
                }

                override fun onResponse(call: Call, response: Response) {
                    response.use { resp ->
                        if (!cont.isActive) return@use
                        try {
                            val body = resp.body?.string().orEmpty()
                            val result = when {
                                resp.isSuccessful -> body
                                resp.code == 429 -> throw AiError.RateLimited()
                                // P2#4: 401 → AuthLost so the bridge can prompt re-login.
                                // 403 is NOT auth — /ai/execute uses it for proposal
                                // rejection, so it falls through to the 4xx branch below.
                                resp.code == 401 -> throw AiError.AuthLost()
                                resp.code == 503 -> throw AiError.Unavailable(resp.code)
                                // P2#4: keep the server's structured error message
                                // (ErrorResponse{code,message} from our own gateway —
                                // never raw provider content) instead of dropping the body.
                                else -> throw AiError.Network(serverMessage(body, resp.code))
                            }
                            cont.resume(result)
                        } catch (e: AiError) {
                            cont.resumeWithException(e)
                        } catch (e: Exception) {
                            cont.resumeWithException(
                                AiError.Network(e.message ?: "Response handling error", e)
                            )
                        }
                    }
                }
            })
        }
    }

    /** Best-effort extraction of the server's ``ErrorResponse.message``. */
    private fun serverMessage(body: String, code: Int): String {
        val msg = try {
            JSONObject(body).optString("message", "")
        } catch (_: Exception) {
            ""
        }
        return if (msg.isNotBlank()) "Server error: HTTP $code: $msg" else "Server error: HTTP $code"
    }

    companion object {
        private val JSON = "application/json".toMediaType()
        private const val CALL_TIMEOUT_SECONDS = 25L
        /** T-M4-04e: 视觉模型较慢，图片解析使用 120s 超时。 */
        private const val IMAGE_PARSE_TIMEOUT_SECONDS = 120L
    }
}
