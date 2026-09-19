package com.aozora.app.bridge

import android.content.Context
import android.os.Looper
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
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/**
 * T-M7-02h: `ai.agent` / `ai.cancel` 桥命令测试。
 *
 * 直走 `AozoraBridge.dispatch`（internal，R-M4-21 先例）：命令注册面、参数校验、
 * 立即应答的形状，以及 SSE 帧 → `window.__aozoraAiStream__` 的推送。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class AozoraBridgeAiStreamTest {

    private lateinit var context: Context
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
        context = RuntimeEnvironment.getApplication()
        db = AozoraDatabase.createInMemory(context)
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
            authClient = FakeAuthClient("http://localhost", TokenStore(context), http),
        )
    }

    /** WebView 已 attach：未 attach 的 View 会把 post 的 Runnable 攒在 run queue 里不执行。 */
    private fun attachedWebView(): WebView {
        val activity = Robolectric.buildActivity(ComponentActivity::class.java).setup().get()
        return WebView(activity).also { activity.setContentView(it) }
    }

    private fun newBridge(webView: WebView, client: AiGatewayClient?) = AozoraBridge(
        repository = Repository(db),
        webView = webView,
        // 生产是 Dispatchers.Main（持续运行的 looper）；Robolectric 的 paused looper
        // 需要手动 idle，而 runBlocking 直调 dispatch 会占住主线程——两者互锁。
        // 测试显式隔离 dispatcher，语义不受影响。
        scope = CoroutineScope(Dispatchers.IO),
        aiGatewayClient = client,
    )

    /** 推进主 looper 直到拿到含指定片段的 JS（协程与 webView.post 都排队在主线程）。 */
    private fun awaitJavascript(webView: WebView, needle: String, timeoutMs: Long = 10_000): String {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            shadowOf(Looper.getMainLooper()).idle()
            val js = shadowOf(webView).lastEvaluatedJavascript
            if (js != null && js.contains(needle)) return js
            Thread.sleep(20)
        }
        fail("timed out waiting for JS containing: $needle")
        error("unreachable")
    }

    private fun sse(body: String) = MockResponse()
        .setHeader("Content-Type", "text/event-stream")
        .setBody(body)

    /**
     * 第一帧之后继续慢速发送的响应 —— 连接在测试窗口内走不到 EOF，
     * 于是「流还在跑」这一状态可被稳定地观察。
     */
    private fun slowStream(firstFrame: String) = MockResponse()
        .setHeader("Content-Type", "text/event-stream")
        .setBody(firstFrame + "\n".repeat(6_400))
        .throttleBody(64, 1, java.util.concurrent.TimeUnit.SECONDS)

    @Test
    fun aiAgent_isDispatched_andWithoutClientReturnsUnavailable(): Unit = runBlocking {
        val response = newBridge(attachedWebView(), client = null)
            .dispatch("req-1", BridgeCommands.AI_AGENT, JSONObject().put("input", "hi").put("book_id", "b-1"))

        assertFalse(response.success)
        assertEquals("unavailable", response.errorCode)
    }

    @Test
    fun aiCancel_isDispatched_andWithNoActiveStreamIsIdempotent(): Unit = runBlocking {
        val bridge = newBridge(attachedWebView(), newClient())

        val response = bridge.dispatch("req-1", BridgeCommands.AI_CANCEL, JSONObject())

        assertTrue(response.success)
        assertEquals(
            false,
            JSONObject(response.data!!).getBoolean("cancelled"),
        )
    }

    @Test
    fun aiAgent_missingFieldsReturnInvalidInput(): Unit = runBlocking {
        val bridge = newBridge(attachedWebView(), newClient())

        val noInput = bridge.dispatch(
            "req-1", BridgeCommands.AI_AGENT, JSONObject().put("book_id", "b-1"),
        )
        assertEquals("invalid_input", noInput.errorCode)

        val noBook = bridge.dispatch(
            "req-2", BridgeCommands.AI_AGENT, JSONObject().put("input", "hi"),
        )
        assertEquals("invalid_input", noBook.errorCode)
    }

    @Test
    fun aiAgent_acksImmediately_withStreamIdMatchingTheRequestId(): Unit = runBlocking {
        server.enqueue(sse("event: done\ndata: {\"stop_reason\":\"completed\"}\n\n"))
        val bridge = newBridge(attachedWebView(), newClient())

        val response = bridge.dispatch(
            "req-42",
            BridgeCommands.AI_AGENT,
            JSONObject().put("input", "记一笔 12 块").put("book_id", "b-1"),
        )

        assertTrue(response.success)
        val data = JSONObject(response.data!!)
        assertTrue("命令必须立即应答 pending", data.getBoolean("pending"))
        // 事件里的 streamId 与它相等——前端据此把事件归到本轮
        assertEquals("req-42", data.getString("streamId"))
    }

    @Test
    fun aiAgent_forwardsServerFramesToWebView(): Unit = runBlocking {
        server.enqueue(
            sse(
                "event: tool_call\ndata: {\"tool\":\"budget_list\",\"step\":1,\"message\":\"正在查询预算\"}\n\n" +
                    "event: done\ndata: {\"conversation_id\":\"c-1\",\"stop_reason\":\"completed\"}\n\n"
            )
        )
        val webView = attachedWebView()
        val bridge = newBridge(webView, newClient())

        bridge.dispatch(
            "req-7",
            BridgeCommands.AI_AGENT,
            JSONObject().put("input", "查预算").put("book_id", "b-1"),
        )

        val js = awaitJavascript(webView, "conversation_id")
        // 两处必须指向同一个回调名：typeof 守卫 + 实际调用。写歪任何一处，
        // 浏览器里事件都再也送不到，而 JS 文本仍"看起来"像那么回事——
        // 所以逐处断言，不用 contains("__aozoraAiStream__") 这种宽匹配。
        assertTrue(js.contains("typeof window.__aozoraAiStream__ === 'function'"))
        assertTrue(js.contains("window.__aozoraAiStream__("))
        // 载荷是 {streamId, event, data}，data 为服务端帧原样 JSON
        val payload = extractStreamPayload(js)
        assertEquals("req-7", payload.getString("streamId"))
        assertEquals("done", payload.getString("event"))
        assertEquals("c-1", payload.getJSONObject("data").getString("conversation_id"))
    }

    @Test
    fun aiAgent_emitsLocalErrorWhenTheStreamEndsWithoutTerminalFrame(): Unit = runBlocking {
        // 连接在终态帧之前结束（服务端重启 / 中间层断开）：Kotlin 侧必须补一个
        // error 事件收尾，否则前端会一直等一个不再到来的 done。
        server.enqueue(sse("event: phase\ndata: {\"phase\":\"thinking\",\"step\":1}\n\n"))
        val webView = attachedWebView()
        val bridge = newBridge(webView, newClient())

        bridge.dispatch(
            "req-8",
            BridgeCommands.AI_AGENT,
            JSONObject().put("input", "查预算").put("book_id", "b-1"),
        )

        val payload = extractStreamPayload(awaitJavascript(webView, "stream_ended"))
        assertEquals("req-8", payload.getString("streamId"))
        assertEquals("error", payload.getString("event"))
        assertEquals("stream_ended", payload.getJSONObject("data").getString("code"))
    }

    @Test
    fun aiAgent_doesNotEmitLocalErrorAfterATerminalFrame(): Unit = runBlocking {
        // 反向：正常 done 收尾之后不得补发 error（终态只能有一个）
        server.enqueue(sse("event: done\ndata: {\"stop_reason\":\"completed\"}\n\n"))
        val webView = attachedWebView()
        val bridge = newBridge(webView, newClient())

        bridge.dispatch(
            "req-9",
            BridgeCommands.AI_AGENT,
            JSONObject().put("input", "hi").put("book_id", "b-1"),
        )
        awaitJavascript(webView, "stop_reason")
        Thread.sleep(200)
        shadowOf(Looper.getMainLooper()).idle()

        assertFalse(
            "done 之后不得再补 error 帧",
            shadowOf(webView).lastEvaluatedJavascript.contains("stream_ended"),
        )
    }

    @Test(timeout = 30_000)
    fun aiCancel_terminatesTheRunningStream(): Unit = runBlocking {
        // 第一帧立即可读，第二帧在约 1 s 后随节流到达——取消若没真的关连接，
        // 第二帧会被推到 WebView，下面的断言就会抓住它。
        server.enqueue(
            slowStream(
                "event: phase\ndata: {\"phase\":\"thinking\",\"step\":1}\n\n" +
                    "event: tool_call\ndata: {\"tool\":\"budget_list\",\"step\":1}\n\n"
            )
        )
        val webView = attachedWebView()
        val bridge = newBridge(webView, newClient())

        bridge.dispatch(
            "req-10",
            BridgeCommands.AI_AGENT,
            JSONObject().put("input", "帮我改预算").put("book_id", "b-1"),
        )
        // 第一帧已推到 WebView = 流确实在跑
        awaitJavascript(webView, "phase")

        val cancel = bridge.dispatch("req-11", BridgeCommands.AI_CANCEL, JSONObject())

        assertTrue(cancel.success)
        assertTrue(
            "有活动流时必须报告已取消",
            JSONObject(cancel.data!!).getBoolean("cancelled"),
        )
        // 取消后服务端即便还在发，也不得再有任何一帧到达 WebView
        Thread.sleep(2_500)
        shadowOf(Looper.getMainLooper()).idle()
        assertFalse(
            "取消必须终止流：不得再推送后续帧",
            shadowOf(webView).lastEvaluatedJavascript.contains("budget_list"),
        )
    }

    /** 从 `window.__aozoraAiStream__("<json>")` 的 JS 里取回载荷 JSON。 */
    private fun extractStreamPayload(js: String): JSONObject {
        val marker = "__aozoraAiStream__("
        val start = js.indexOf(marker)
        assertTrue("找不到 __aozoraAiStream__ 调用: $js", start >= 0)
        // payload 是由 JSONObject.quote 包出来的 JSON 字符串字面量
        val quoted = js.substring(start + marker.length).trim().removeSuffix(");")
        return JSONObject(org.json.JSONTokener(quoted).nextValue() as String)
    }
}