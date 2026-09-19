package com.aozora.app.ai

import com.aozora.app.auth.AuthClient
import com.aozora.app.auth.TokenStore
import kotlinx.coroutines.runBlocking
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

/**
 * T-M7-03d: 会话索引三命令的 HTTP 客户端测试（真实 OkHttp + MockWebServer 回环）。
 *
 * 这一层要钉住的是**动宾结构**而不是业务：HTTP 方法（GET / PATCH / DELETE）、路径、
 * 请求体、鉴权头，以及 404 → [AiGatewayClient.AiError.NotFound] 的映射。方法与路径
 * 写错（例如把 PATCH 写成 POST）在服务端只会得到一个 405，而客户端会把它当成一次
 * 普通网络故障——正是那种「单测不写就查不出来」的错。
 *
 * 令牌走 FakeAuthClient，与 `AiGatewayClientStreamTest` 同法：焦点在链路本身。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class AiGatewayClientConversationTest {

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
        client = AiGatewayClient(
            baseUrl = server.url("/api/v1").toString().trimEnd('/'),
            httpClient = http,
            authClient = FakeAuthClient("http://localhost", TokenStore(RuntimeEnvironment.getApplication()), http),
        )
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun listConversations_isGet_andPassesBodyThroughVerbatim() = runBlocking {
        val payload = """{"conversations":[{"id":"c-1","title":"午饭记账","book_id":null,
            "created_at":"2026-09-19T10:00:00Z","last_active_at":"2026-09-19T11:00:00Z"}]}"""
        server.enqueue(MockResponse().setBody(payload))

        val result = client.listConversations()

        // 响应体**原样**透传（字段语义属服务端，这一层不认识它）
        assertEquals(payload, result)
        val request = server.takeRequest()
        assertEquals("GET", request.method)
        assertEquals("/api/v1/ai/conversations", request.path)
        assertEquals("Bearer test-token", request.getHeader("Authorization"))
        // GET 不能带体（OkHttp 的 method() 契约）
        assertTrue("GET 不得带请求体", request.body.size == 0L)
    }

    @Test
    fun renameConversation_isPatch_withTitleInBody() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"id":"c-1","title":"新标题"}"""))

        client.renameConversation("c-1", "新标题")

        val request = server.takeRequest()
        assertEquals("PATCH", request.method)
        assertEquals("/api/v1/ai/conversations/c-1", request.path)
        assertEquals("新标题", JSONObject(request.body.readUtf8()).getString("title"))
    }

    @Test
    fun deleteConversation_isDelete_withEmptyBody() = runBlocking {
        // 服务端 204：无响应体（这也是桥侧必须自己造 {success:true} 的原因）
        server.enqueue(MockResponse().setResponseCode(204))

        val result = client.deleteConversation("c-1")

        assertEquals("", result)
        val request = server.takeRequest()
        assertEquals("DELETE", request.method)
        assertEquals("/api/v1/ai/conversations/c-1", request.path)
    }

    @Test
    fun conversationEndpoints_map404ToNotFound() = runBlocking {
        // 服务端对「别人的 / 已删除的 / 不存在的 id」一律同一个 404
        // （`routes.py` 的 conversation_not_found）——不能折叠成网络故障
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"code":"conversation_not_found"}"""))
        try {
            client.renameConversation("c-gone", "x")
            fail("404 必须抛出 NotFound")
        } catch (_: AiGatewayClient.AiError.NotFound) {
        }

        server.enqueue(MockResponse().setResponseCode(404).setBody("{}"))
        try {
            client.deleteConversation("c-gone")
            fail("404 必须抛出 NotFound")
        } catch (_: AiGatewayClient.AiError.NotFound) {
        }

        server.enqueue(MockResponse().setResponseCode(404).setBody("{}"))
        try {
            client.listConversations()
            fail("404 必须抛出 NotFound")
        } catch (_: AiGatewayClient.AiError.NotFound) {
        }
    }

    @Test
    fun agentStream_maps404ToNotFound_staleConversationId() = runBlocking {
        // 带一个已失效的 conversation_id：服务端在**开流之前**就回 404（03a 口径）。
        // 它必须与普通链路故障分开——客户端要丢弃这个 id 并开新会话。
        server.enqueue(MockResponse().setResponseCode(404).setBody("{}"))

        try {
            client.agentStream("帮我记账", "book-1", conversationId = "c-gone") { _, _ -> }
            fail("陈旧的 conversation_id 必须抛出 NotFound")
        } catch (_: AiGatewayClient.AiError.NotFound) {
        }
    }
}