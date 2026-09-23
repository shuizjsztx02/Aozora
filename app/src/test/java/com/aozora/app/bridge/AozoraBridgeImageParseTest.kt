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
 * T18-1（REQ-66）: `image.parse` 的 503 **归因分层**。
 *
 * 服务端对 503 会在 body 里带一个 `code`：`ai_unavailable`（provider 暂时故障，
 * 或服务端没配 key —— 服务端对这两者同码）与 `image_parsing_disabled`
 * （运维把图片解析关了）。桥以前**不读 body**、只按状态码映射，把整类 503 压成
 * `no_ai_provider`；前端据此显示「AI 服务未配置：需在服务器 .env 填
 * AI_PROVIDER_API_KEY 并重启」。于是一次 provider 抽风就把用户引去改服务器配置，
 * 而真因与那句话毫无关系（2026-09-23 实测：长驻 uvicorn 进程内 provider 连接层
 * 持续失败，重启即恢复；文本 interpret 同样 503，只是被 agent 路径盖住了）。
 *
 * 这一层钉的是**分流规则**本身：已知子码原样透传、其余 503 归到 `ai_unavailable`、
 * 非 503 归到 `server_error`。用户可见文案不在这里——它由前端再归一到同一句
 * （`features/assistant/ai-error-text.ts`），差异只留给归因与监控。
 *
 * 命令面零变：仍是 `image.parse` 一个入口，不新增桥命令。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class AozoraBridgeImageParseTest {

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

    private fun newBridge(client: AiGatewayClient?) = AozoraBridge(
        repository = Repository(db),
        webView = attachedWebView(),
        scope = CoroutineScope(Dispatchers.IO),
        aiGatewayClient = client,
    )

    private suspend fun parseImage() = newBridge(newClient()).dispatch(
        "r-1",
        BridgeCommands.IMAGE_PARSE,
        JSONObject().put("attachmentId", "att-1").put("bookId", "book-1"),
    )

    @Test
    fun imageParse_503WithImageParsingDisabled_passesSubcodeThrough(): Unit = runBlocking {
        // 运维把图片解析关了（AI_IMAGE_MAX_BYTES=0）——这是**另一个原因**，
        // 不能被译成「没配 key」
        server.enqueue(
            MockResponse().setResponseCode(503)
                .setBody("""{"code":"image_parsing_disabled","message":"Image parsing is disabled (ai_image_max_bytes=0)"}"""),
        )

        val response = parseImage()

        assertFalse(response.success)
        assertEquals("image_parsing_disabled", response.errorCode)
    }

    @Test
    fun imageParse_503WithAiUnavailable_passesSubcodeThrough(): Unit = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(503)
                .setBody("""{"code":"ai_unavailable","message":"AI service is temporarily unavailable"}"""),
        )

        val response = parseImage()

        assertFalse(response.success)
        // 旧口径在这里回的是 no_ai_provider（前端于是显示「去填 .env」）
        assertEquals("ai_unavailable", response.errorCode)
    }

    @Test
    fun imageParse_503WithoutUsableBodyCode_fallsBackToAiUnavailable(): Unit = runBlocking {
        // 防御：body 为空 / 非 JSON / 无 code 时，宁可给笼统的「暂时不可用」，
        // 也不能猜成「没配 key」
        for (body in listOf("", "not json", """{"message":"oops"}""")) {
            server.enqueue(MockResponse().setResponseCode(503).setBody(body))

            val response = parseImage()

            assertFalse(response.success)
            assertEquals("ai_unavailable", response.errorCode)
        }
    }

    @Test
    fun imageParse_503WithUnknownSubcode_isNotForwardedVerbatim(): Unit = runBlocking {
        // 未知子码不透传：前端对未知码会回退**原生英文 message**——
        // 那比笼统的 ai_unavailable 更糟（同 REQ-66 的「不暴露服务商错误」口径）
        server.enqueue(
            MockResponse().setResponseCode(503).setBody("""{"code":"some_future_code"}"""),
        )

        val response = parseImage()

        assertFalse(response.success)
        assertEquals("ai_unavailable", response.errorCode)
    }

    @Test
    fun imageParse_nonJsonErrorBody_stillMapsByStatusCode(): Unit = runBlocking {
        // 非 503 的 Unavailable（服务端 502/504 等）没有子码可分，维持 server_error
        server.enqueue(MockResponse().setResponseCode(502).setBody("<html>bad gateway</html>"))

        val response = parseImage()

        assertFalse(response.success)
        // 502 在 AiGatewayClient 里落到 Network（非 503 的 Unavailable 分支是防御性的），
        // 桥的图片链路把它映射成 network_error
        assertEquals("network_error", response.errorCode)
    }
}