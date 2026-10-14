package com.aozora.app.ai

import com.aozora.app.auth.AuthClient
import com.aozora.app.auth.TokenStore
import com.aozora.app.autobook.AiCaptureRemoteParser
import com.aozora.app.autobook.LocalCaptureParser
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
 * T21-6（REQ-83）：`POST /ai/parse-notification` 的 HTTP 客户端测试
 * （真实 OkHttp + MockWebServer 回环）。
 *
 * 这一层要钉住的是**动宾结构**而不是业务：路径、请求体三件（text / source_channel /
 * context）、鉴权头，以及降级契约（429 / 503 → [AiGatewayClient.AiError]，调用侧据此
 * 「留在待解析、下次再试」）。路径写错在服务端只会得一个 404，而客户端会把它当成
 * 一次普通网络故障——正是那种「单测不写就查不出来」的错。
 *
 * 另外钉住 `AiCaptureRemoteParser` 的两件事：**字段映射**与**缺 output 必须抛异常**
 * （不许造一份空草稿——空草稿会被当成"解析成功但缺字段"进待确认箱，掩盖真因）。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class AiGatewayClientCaptureParseTest {

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

    private fun draftBody(
        kind: String = "transaction",
        reason: String = "",
        type: String = "expense",
        amountCents: Long = 3200L,
        category: String = "餐饮",
        note: String = "星巴克",
        confidence: Double = 0.95,
    ) = """{"request_id":"r-1","task":"capture_parse","model":"qwen-max",
        "output":{"kind":"$kind","reason":"$reason","type":"$type","amountCents":$amountCents,
        "category":"$category","note":"$note","bizDate":"2026-10-13","confidence":$confidence},
        "usage":{"prompt_tokens":10,"completion_tokens":20,"total_tokens":30}}"""

    @Test
    fun parseNotification_isPost_withTextChannelAndContext() = runBlocking {
        server.enqueue(MockResponse().setBody(draftBody()))

        client.parseNotification(
            text = "支付成功 ¥32.00 星巴克",
            sourceChannel = "accessibility",
            contextJson = """{"today":"2026-10-13","categories":["餐饮","交通"]}""",
        )

        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/api/v1/ai/parse-notification", request.path)
        assertEquals("Bearer test-token", request.getHeader("Authorization"))
        val body = JSONObject(request.body.readUtf8())
        assertEquals("支付成功 ¥32.00 星巴克", body.getString("text"))
        assertEquals("accessibility", body.getString("source_channel"))
        assertEquals("2026-10-13", body.getJSONObject("context").getString("today"))
        assertEquals(2, body.getJSONObject("context").getJSONArray("categories").length())
    }

    @Test
    fun parseNotification_omitsContextKey_whenAbsent() = runBlocking {
        server.enqueue(MockResponse().setBody(draftBody()))

        client.parseNotification("云闪付 消费人民币9.90元", "notification")

        val body = JSONObject(server.takeRequest().body.readUtf8())
        assertTrue("没有 context 时不得造一个空对象", !body.has("context"))
    }

    @Test
    fun parseNotification_maps429And503ToFallbackErrors() = runBlocking {
        // 降级契约与 /ai/interpret 逐字相同：429 / 503 都表示「回退本地规则解析」，
        // 调用侧不分对错、一律「留在待解析、下次再试」
        server.enqueue(
            MockResponse().setResponseCode(429).setBody("""{"code":"ai_quota_exceeded"}""")
        )
        try {
            client.parseNotification("x", "notification")
            fail("429 必须抛出 RateLimited")
        } catch (e: AiGatewayClient.AiError.RateLimited) {
            assertEquals("ai_quota_exceeded", e.serverCode)
        }

        server.enqueue(
            MockResponse().setResponseCode(503).setBody("""{"code":"ai_unavailable"}""")
        )
        try {
            client.parseNotification("x", "notification")
            fail("503 必须抛出 Unavailable")
        } catch (e: AiGatewayClient.AiError.Unavailable) {
            assertEquals("ai_unavailable", e.serverCode)
        }

        server.enqueue(MockResponse().setResponseCode(401).setBody("{}"))
        try {
            client.parseNotification("x", "notification")
            fail("401 必须抛出 AuthLost")
        } catch (_: AiGatewayClient.AiError.AuthLost) {
        }
    }

    @Test
    fun adapter_mapsServerOutputToTheLocalDraftShape() = runBlocking {
        server.enqueue(MockResponse().setBody(draftBody(amountCents = 3200L, note = "星巴克(北京国贸店)")))

        val draft = AiCaptureRemoteParser(client).parse("支付成功 ¥32.00", "accessibility", null)

        assertEquals(LocalCaptureParser.KIND_TRANSACTION, draft.kind)
        assertEquals(LocalCaptureParser.TYPE_EXPENSE, draft.type)
        assertEquals(3200L, draft.amountCents)
        assertEquals("餐饮", draft.categoryHint)
        assertEquals("星巴克(北京国贸店)", draft.note)
        assertEquals("2026-10-13", draft.bizDate)
        assertEquals(0.95, draft.confidence, 0.0001)
    }

    @Test
    fun adapter_mapsNotTransactionAndKeepsTheReason() = runBlocking {
        server.enqueue(MockResponse().setBody(draftBody(kind = "not_transaction", reason = "refund", amountCents = 0L)))

        val draft = AiCaptureRemoteParser(client).parse("退款成功 32.00元", "notification", null)

        assertEquals(LocalCaptureParser.KIND_NOT_TRANSACTION, draft.kind)
        assertEquals(LocalCaptureParser.REASON_REFUND, draft.reason)
        assertEquals(0L, draft.amountCents)
    }

    @Test
    fun adapter_throwsWhenResponseHasNoOutput() = runBlocking {
        // 畸形响应（缺 output）**不是**"解析成功但缺字段"——它必须抛出去，
        // 让行留在 captured 等下次重试，而不是进待确认箱掩盖真因
        server.enqueue(MockResponse().setBody("""{"request_id":"r-1","task":"capture_parse"}"""))

        try {
            AiCaptureRemoteParser(client).parse("x", "notification", null)
            fail("缺 output 必须抛异常")
        } catch (e: IllegalStateException) {
            assertTrue(e.message!!.contains("output"))
        }
    }
}