package com.aozora.app.ai

import com.aozora.app.auth.AuthClient
import com.aozora.app.auth.TokenStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import java.util.concurrent.TimeUnit

/**
 * T-M7-02h: agent SSE 客户端测试（真实 OkHttp + MockWebServer 回环）。
 *
 * 覆盖卡面的「SSE 事件送达」与「取消能终止流」两项在**客户端侧**能验证的部分：
 * 帧按序透传、`done` 之后的收尾、HTTP 失败映射、取消即断连。经 nginx 的真实链路
 * 属部署侧验证（R-PROD-22 残余项）。
 *
 * 令牌走 [FakeAuthClient]（`AuthClient.ensureValidAccessToken` 是 open，androidTest
 * 已有此先例），因此焦点全在 SSE 通道本身。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class AiGatewayClientStreamTest {

    private lateinit var server: MockWebServer
    private lateinit var client: AiGatewayClient

    private class FakeAuthClient(
        baseUrl: String,
        tokenStore: TokenStore,
        httpClient: OkHttpClient,
    ) : AuthClient(baseUrl, tokenStore, httpClient) {
        override suspend fun ensureValidAccessToken(): String = "test-token"
    }

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        val http = OkHttpClient()
        val auth = FakeAuthClient(
            baseUrl = "http://localhost",
            tokenStore = TokenStore(RuntimeEnvironment.getApplication()),
            httpClient = http,
        )
        client = AiGatewayClient(
            baseUrl = server.url("/api/v1").toString().trimEnd('/'),
            httpClient = http,
            authClient = auth,
        )
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun sse(body: String): MockResponse =
        MockResponse()
            .setHeader("Content-Type", "text/event-stream")
            .setBody(body)

    @Test
    fun agentStream_deliversFramesInOrder_andReturnsOnClose() = runBlocking {
        server.enqueue(
            sse(
                // 心跳是注释帧：客户端必须忽略它（它只是一帧给 nginx 的流量）
                ": ping\n\n" +
                    "event: phase\ndata: {\"phase\":\"thinking\",\"step\":1}\n\n" +
                    "event: tool_call\ndata: {\"tool\":\"budget_list\",\"step\":1,\"message\":\"正在查询预算\"}\n\n" +
                    "event: delta\ndata: {\"text\":\"已改成 3000\"}\n\n" +
                    "event: done\ndata: {\"conversation_id\":\"c-1\",\"stop_reason\":\"completed\"}\n\n"
            )
        )

        val events = mutableListOf<Pair<String, String>>()
        client.agentStream("帮我改预算", "book-1") { event, data -> events += event to data }

        assertEquals(
            listOf("phase", "tool_call", "delta", "done"),
            events.map { it.first },
        )
        assertEquals(
            "budget_list",
            JSONObject(events[1].second).getString("tool"),
        )
        assertEquals("c-1", JSONObject(events[3].second).getString("conversation_id"))

        // 请求面：POST /ai/agent + Bearer token（token 不出 Kotlin 层的既有约定）
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/api/v1/ai/agent", request.path)
        assertEquals("Bearer test-token", request.getHeader("Authorization"))
        assertEquals("text/event-stream", request.getHeader("Accept"))
        val body = JSONObject(request.body.readUtf8())
        assertEquals("帮我改预算", body.getString("input"))
        assertEquals("book-1", body.getString("book_id"))
        // 首轮不带会话 id —— 服务端建会话，id 由 done 帧回传
        assertTrue("首轮不得带 conversation_id", !body.has("conversation_id"))
    }

    @Test
    fun agentStream_sendsConversationIdOnLaterTurns() = runBlocking {
        server.enqueue(sse("event: done\ndata: {\"stop_reason\":\"completed\"}\n\n"))

        client.agentStream("改成 3000", "book-1", conversationId = "c-9") { _, _ -> }

        val body = JSONObject(server.takeRequest().body.readUtf8())
        assertEquals("c-9", body.getString("conversation_id"))
    }

    @Test
    fun agentStream_preflightFailures_mapToAiErrors() = runBlocking {
        // 端点前置失败是普通 HTTP 状态码（02g：开关关闭 503 / 配额 429 / 认证 401）
        server.enqueue(MockResponse().setResponseCode(503).setBody("{\"code\":\"agent_disabled\"}"))
        try {
            client.agentStream("hi", "book-1") { _, _ -> }
            fail("503 必须抛出")
        } catch (e: AiGatewayClient.AiError.Unavailable) {
            assertEquals(503, e.httpCode)
        }

        server.enqueue(MockResponse().setResponseCode(429).setBody("{}"))
        try {
            client.agentStream("hi", "book-1") { _, _ -> }
            fail("429 必须抛出")
        } catch (_: AiGatewayClient.AiError.RateLimited) {
        }

        server.enqueue(MockResponse().setResponseCode(401).setBody("{}"))
        try {
            client.agentStream("hi", "book-1") { _, _ -> }
            fail("401 必须抛出")
        } catch (_: AiGatewayClient.AiError.AuthLost) {
        }
    }

    @Test
    fun agentStream_cancel_terminatesTheStream() = runBlocking {
        // 服务端发完第一帧后继续慢速发送（连接在整个测试窗口内不会走到 EOF），
        // 模拟「loop 还在跑」时用户点了「停止」。
        server.enqueue(
            MockResponse()
                .setHeader("Content-Type", "text/event-stream")
                .setBody(
                    "event: phase\ndata: {\"phase\":\"thinking\",\"step\":1}\n\n" +
                        "\n".repeat(SLOW_PADDING_BYTES)
                )
                .throttleBody(64, 1, TimeUnit.SECONDS)
        )

        val events = mutableListOf<String>()
        val job = launch(Dispatchers.IO) {
            client.agentStream("hi", "book-1") { event, _ -> synchronized(events) { events += event } }
        }

        withTimeout(10_000) {
            while (synchronized(events) { events.isEmpty() }) delay(20)
        }
        job.cancel()
        job.join()

        assertTrue("取消必须终止流（服务端把断连当取消）", job.isCancelled)
        assertEquals(listOf("phase"), synchronized(events) { events.toList() })
    }

    private companion object {
        /** 配合 64 B/s 的节流，让响应体在测试窗口内读不完。 */
        const val SLOW_PADDING_BYTES = 6_400
    }
}