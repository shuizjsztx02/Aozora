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
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
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
 * - 25 s call timeout (aligned with ADR-003 SLO); the SSE agent channel
 *   opts out of it — a multi-step turn cannot fit in 25 s (T-M7-02h)
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

    /**
     * T-M7-02h: SSE 专用 client —— **长连接不能背 25 s 的 `callTimeout`**。
     *
     * 那个预算是快路径单发（ADR-003 SLO），而一轮 agent loop 由多次模型调用
     * 串行构成，必然超出（架构 §8.3）。这里覆盖两点：
     *
     * - `callTimeout = 0`：**整轮没有总时长上限**，由服务端的步骤边界 + 用户的
     *   取消兜底（server 侧单步 20 s，见 02g）。
     * - `readTimeout = 25 s`：它是「多久没有数据就判链路已死」，表达的是 §8.3
     *   那条**单步静默 25 s** 的口径（与快路径的用户可感 SLO 同值）。服务端空闲
     *   心跳是 15 s（`ai_agent_heartbeat_seconds`），故正常情况下远不会触发。
     *
     * `newBuilder()` 仍共享连接池与调度器。
     */
    private val streamClient: OkHttpClient = httpClient.newBuilder()
        .callTimeout(0, TimeUnit.SECONDS)
        .readTimeout(STREAM_READ_TIMEOUT_SECONDS, TimeUnit.SECONDS)
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
        /**
         * T-M7-03d: HTTP 404。
         *
         * 单独成类而不是并进 [Network]，因为服务端对它有一层**确定的语义**：会话
         * 端点对「别人的 / 已删除的 / 不存在的 id」一律同一个 404
         * `conversation_not_found`（`routes.py`），即「这个 id 不是我的会话了」。
         * 客户端据此幂等处理（删除重试、丢弃陈旧 `conversation_id` 开新会话），
         * 而不是当成一次网络故障让用户重试。
         */
        class NotFound(cause: Throwable? = null) :
            AiError("Resource not found (HTTP 404)", cause)
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

    /**
     * T-M7-03d: `GET /ai/conversations` —— 本人的会话索引（按活跃时间倒序）。
     *
     * 返回体原样透传（`{conversations:[{id,title,book_id,created_at,last_active_at}]}`），
     * 与其它 `/ai/` 调用同口径：这一层不认识业务字段。
     */
    suspend fun listConversations(): String =
        executeAuthorized("$baseUrl/ai/conversations", method = "GET")

    /**
     * T-M7-03d: `PATCH /ai/conversations/{id}` —— 重命名。
     *
     * 404（别人的 / 已删除的 / 不存在的 id）→ [AiError.NotFound]，调用方按
     * 「这个 id 不是我的会话了」处理。
     */
    suspend fun renameConversation(conversationId: String, title: String): String =
        executeAuthorized(
            "$baseUrl/ai/conversations/$conversationId",
            buildRenameConversationRequest(title),
            method = "PATCH",
        )

    /**
     * T-M7-03d: `DELETE /ai/conversations/{id}` —— 删除（服务端 204，无响应体）。
     *
     * **重试安全**：删除成功后旧 id 与别人的 id 拿到同一个 404，[AiError.NotFound]
     * 在调用方等价于成功（「已经不是我的会话了」）。
     */
    suspend fun deleteConversation(conversationId: String): String =
        executeAuthorized("$baseUrl/ai/conversations/$conversationId", method = "DELETE")

    /**
     * T-M7-02h: POST /ai/agent —— 一轮 agent 回合的 SSE 流。
     *
     * 挂起直到流结束（服务端发出 `done` / `error` 帧后关闭连接）、被取消
     * （用户点了停止 → 协程取消 → 底层连接关闭，服务端把断连当取消），或
     * 链路失败（抛 [AiError]）。
     *
     * [onEvent] 在 OkHttp 的读线程上按帧回调 `(event, data)` 并**原样透传**：
     * 事件语义属服务端（`server/src/modules/ai/stream.py`），这一层不认识任何
     * 事件名。心跳注释帧（`: ping`）由 okhttp-sse 自行忽略，不会到达这里。
     */
    suspend fun agentStream(
        input: String,
        bookId: String,
        conversationId: String? = null,
        onEvent: (event: String, data: String) -> Unit,
    ) {
        val token = authorize()
        val request = Request.Builder()
            .url("$baseUrl/ai/agent")
            .post(buildAgentRequest(input, bookId, conversationId).toRequestBody(JSON))
            .header("Authorization", "Bearer $token")
            .header("Content-Type", "application/json")
            .header("Accept", "text/event-stream")
            .header("X-Request-Id", UUID.randomUUID().toString())
            .build()
        streamEvents(request, onEvent)
    }

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

    /** 首轮不带 `conversation_id`：服务端建会话，`done` 帧回传其 id 供下一轮复用。 */
    private fun buildAgentRequest(input: String, bookId: String, conversationId: String?): String {
        val body = JSONObject().put("input", input).put("book_id", bookId)
        if (conversationId != null) body.put("conversation_id", conversationId)
        return body.toString()
    }

    /** T-M7-03d: PATCH 的请求体（服务端 `AIConversationRenameRequest`：1–120 字，先 strip）。 */
    private fun buildRenameConversationRequest(title: String): String =
        JSONObject().put("title", title).toString()

    /** 取一个有效 access token；认证失效统一翻成 [AiError.AuthLost]。 */
    private suspend fun authorize(): String = withContext(Dispatchers.IO) {
        try {
            authClient.ensureValidAccessToken()
        } catch (e: AuthClient.AuthLostException) {
            throw AiError.AuthLost(e)
        }
    }

    /**
     * 带鉴权的请求；`method` 与 `bodyJson` 决定请求形态。
     *
     * T-M7-03d 起支持 GET / PATCH / DELETE（会话索引端点），故 `bodyJson` 可为 null
     * ——OkHttp 的 `method(name, body)` 要求 GET 必须不带体。既有 POST 调用点一律
     * 走默认值，签名与行为不变。
     */
    private suspend fun executeAuthorized(
        url: String,
        bodyJson: String? = null,
        timeoutSeconds: Long = CALL_TIMEOUT_SECONDS,
        method: String = "POST",
    ): String {
        val token = authorize()

        val request = Request.Builder()
            .url(url)
            .method(method, bodyJson?.toRequestBody(JSON))
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
                                // T-M7-03d: 404 有自己的语义（会话端点：别人的 / 已删的 /
                                // 不存在的 id 全是它），不能被折叠成一次网络故障——客户端
                                // 要据此幂等收敛（删除重试、丢弃陈旧的 conversation_id）。
                                resp.code == 404 -> throw AiError.NotFound()
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

    /**
     * 打开 SSE 长连接并按帧回调；挂起直到结束 / 取消 / 失败。
     *
     * internal：JVM 单测直连 MockWebServer 验证帧透传与取消，不必绕经 token
     * （与 `AozoraBridge.dispatch` 的 R-M4-21 先例同法）。
     */
    internal suspend fun streamEvents(
        request: Request,
        onEvent: (event: String, data: String) -> Unit,
    ) {
        suspendCancellableCoroutine { cont ->
            val listener = object : EventSourceListener() {
                override fun onEvent(
                    eventSource: EventSource,
                    id: String?,
                    type: String?,
                    data: String,
                ) {
                    if (!cont.isActive) return
                    // 事件回调**不许杀掉流**：异常抛出去会中断 EventSource 的读循环，
                    // 把一次帧解析失误升级成整轮失败。
                    try {
                        onEvent(type.orEmpty(), data)
                    } catch (_: Exception) {
                    }
                }

                override fun onClosed(eventSource: EventSource) {
                    if (cont.isActive) cont.resume(Unit)
                }

                override fun onFailure(eventSource: EventSource, t: Throwable?, response: Response?) {
                    if (!cont.isActive) return
                    cont.resumeWithException(mapStreamFailure(t, response))
                }
            }
            val source = EventSources.createFactory(streamClient).newEventSource(request, listener)
            cont.invokeOnCancellation { source.cancel() }
        }
    }

    /**
     * SSE 链路的失败映射。**HTTP 状态优先**——okhttp-sse 在非 2xx 时带 response
     * 回调 `onFailure`（`processResponse` 里那次），而流中途断开时 response 是
     * 已经开始的 200，那时才看异常类型。口径与服务端的 `error` 帧一致：只给
     * 状态码与类型，**不回显正文**。
     */
    private fun mapStreamFailure(t: Throwable?, response: Response?): AiError {
        val code = response?.code ?: 0
        if (code != 0 && code != 200) {
            return when (code) {
                401 -> AiError.AuthLost()
                429 -> AiError.RateLimited()
                503 -> AiError.Unavailable(code)
                // T-M7-03d: `POST /ai/agent` 带一个已失效的 conversation_id 时，服务端在
                // **开流之前**就回 404（`conversation.py:load_owned`）。它必须与普通链路
                // 故障分开——客户端要丢弃这个 id 并开新会话，而不是提示用户重试。
                404 -> AiError.NotFound()
                else -> AiError.Network("Server error: HTTP $code")
            }
        }
        return when (t) {
            // 读超时（心跳静默超过 streamClient 的 readTimeout）与调用超时
            // 都是 InterruptedIOException，与同步路径同一口径。
            is InterruptedIOException -> AiError.Timeout(t)
            else -> AiError.Network(t?.message ?: "Stream failed", t)
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
        /** T-M7-02h: SSE 静默上限 = 架构 §8.3 的「单步静默 25 s」口径。 */
        private const val STREAM_READ_TIMEOUT_SECONDS = 25L
    }
}
