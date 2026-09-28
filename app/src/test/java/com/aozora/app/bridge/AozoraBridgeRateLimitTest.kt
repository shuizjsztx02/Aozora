package com.aozora.app.bridge

import android.content.Context
import android.webkit.WebView
import androidx.activity.ComponentActivity
import com.aozora.app.ai.AiGatewayClient
import com.aozora.app.auth.AuthClient
import com.aozora.app.auth.TokenStore
import com.aozora.app.db.AozoraDatabase
import com.aozora.app.db.Repository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * T20-9（REQ-82）: HTTP 429 的**归因分层**（与 T18-1 的 503 那条同法）。
 *
 * 429 **不是一个语义**：`ai_quota_exceeded`（guest 日配额用尽，明天再来）与
 * 「请求太密，缓一下」对用户是两件事、恢复路径也不同。桥以前**不读 body**、
 * 只按状态码映射，两类一律译成 `429` + 「AI rate limit exceeded」——前端拿到
 * 一个说不清前因的码，只能落「连接中断」兜底：用户看到「网断了」，而实际是
 * 服务端**明确拒绝**了这次请求（会话级双限已在同卡删除）。
 *
 * 这一层钉的是**分流规则**本身：已知子码原样透传、其余回落各 handler 既有的
 * 笼统码（`handleAi*` 是 `429`、图片链路是 `rate_limited`——各保持原样）。
 * 用户可见文案不在这里，它由前端归一（`features/assistant/ai-error-text.ts`）。
 *
 * 命令面零变：仍是既有入口，不新增桥命令（桥计数保持 83）。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class AozoraBridgeRateLimitTest {

    private lateinit var db: AozoraDatabase
    private lateinit var server: MockWebServer

    private class FakeAuthClient(
        baseUrl: String,
        tokenStore: TokenStore,
        httpClient: OkHttpClient,
    ) : AuthClient(baseUrl, tokenStore, httpClient) {
        override suspend fun ensureValidAccessToken(): String = "test-token"
    }

    @Before
    fun setUp() {
        db = AozoraDatabase.createInMemory(RuntimeEnvironment.getApplication())
        server = MockWebServer()
        server.start()
    }

    @After
    fun tearDown() {
        server.shutdown()
        db.close()
    }

    private fun newClient(): AiGatewayClient {
        val http = OkHttpClient()
        return AiGatewayClient(
            baseUrl = server.url("/api/v1").toString().trimEnd('/'),
            httpClient = http,
            authClient = FakeAuthClient("http://localhost", TokenStore(context()), http),
        )
    }

    private fun context(): Context = RuntimeEnvironment.getApplication()

    /** WebView 已 attach（同 AozoraBridgeAiStreamTest：未 attach 的 View 不执行 post）。 */
    private fun attachedWebView(): WebView {
        val activity = Robolectric.buildActivity(ComponentActivity::class.java).setup().get()
        return WebView(activity).also { activity.setContentView(it) }
    }

    private fun newBridge() = AozoraBridge(
        repository = Repository(db),
        webView = attachedWebView(),
        scope = CoroutineScope(Dispatchers.IO),
        aiGatewayClient = newClient(),
    )

    private suspend fun interpret() = newBridge().dispatch(
        "r-1",
        BridgeCommands.AI_INTERPRET,
        JSONObject().put("input", "午饭 12"),
    )

    private suspend fun parseImage() = newBridge().dispatch(
        "r-2",
        BridgeCommands.IMAGE_PARSE,
        JSONObject().put("attachmentId", "att-1").put("bookId", "book-1"),
    )

    private fun enqueue429(body: String) {
        server.enqueue(MockResponse().setResponseCode(429).setBody(body))
    }

    @Test
    fun interpret_429WithQuotaSubcode_passesSubcodeThrough(): Unit = runBlocking {
        // guest 的日配额用尽——这是**另一个原因**，不能被译成「请求太密」
        enqueue429(
            """{"code":"ai_quota_exceeded","message":"Daily AI quota used up, please try again tomorrow"}""",
        )

        val response = interpret()

        assertFalse(response.success)
        // 旧口径在这里回的是 429（前端于是落「连接中断」兜底）
        assertEquals("ai_quota_exceeded", response.errorCode)
    }

    @Test
    fun interpret_429WithoutUsableSubcode_fallsBackTo429(): Unit = runBlocking {
        // 防御：body 为空 / 非 JSON / 无 code / 认不出的 code，一律回落笼统的 429
        // （未知码透传会让前端回退**原生英文 message**，比笼统码更糟）
        for (body in listOf("", "not json", """{"message":"oops"}""", """{"code":"some_future_code"}""")) {
            enqueue429(body)

            val response = interpret()

            assertFalse(response.success)
            assertEquals("429", response.errorCode)
        }
    }

    @Test
    fun interpret_429WithServerRateLimitCode_staysOnTheGeneralCode(): Unit = runBlocking {
        // `ai_rate_limited` 是「请求太密」那一支：桥**不**为它新增码——笼统的 `429`
        // 已经是那个语义，前端对它的文案也已如实（不再说「连接中断」）。
        enqueue429("""{"code":"ai_rate_limited","message":"Too many AI requests, please slow down"}""")

        val response = interpret()

        assertFalse(response.success)
        assertEquals("429", response.errorCode)
    }

    @Test
    fun imageParse_429WithQuotaSubcode_passesSubcodeThrough(): Unit = runBlocking {
        // 图片链路同样吃日配额（`AIService.parse_image` 走同一个 check_daily_quota），
        // 故配额子码同样放行；本 handler 既有的笼统码是 `rate_limited`。
        enqueue429("""{"code":"ai_quota_exceeded","message":"Daily AI quota used up"}""")

        val response = parseImage()

        assertFalse(response.success)
        assertEquals("ai_quota_exceeded", response.errorCode)
    }

    @Test
    fun imageParse_429WithoutUsableSubcode_fallsBackToRateLimited(): Unit = runBlocking {
        enqueue429("{}")

        val response = parseImage()

        assertFalse(response.success)
        assertEquals("rate_limited", response.errorCode)
    }
}