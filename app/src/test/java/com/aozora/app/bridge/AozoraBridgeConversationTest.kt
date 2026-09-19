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
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * T-M7-03d: `conversation.list` / `.rename` / `.delete` 桥命令测试。
 *
 * 直走 `AozoraBridge.dispatch`（internal，R-M4-21 先例）：命令注册面、参数校验、
 * 响应形状，以及**服务端 204 空响应体 → 桥必须自己造一个合法 JSON** 这条容易漏的
 * 边界（空串会让信封序列化直接抛，前端也会 JSON.parse 失败、调用悬挂到超时）。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class AozoraBridgeConversationTest {

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

    @Test
    fun conversationCommands_withoutClientReturnUnavailable(): Unit = runBlocking {
        val bridge = newBridge(client = null)

        assertFalse(
            bridge.dispatch("r-1", BridgeCommands.CONVERSATION_LIST, JSONObject()).success
        )
        assertEquals(
            "unavailable",
            bridge.dispatch("r-2", BridgeCommands.CONVERSATION_LIST, JSONObject()).errorCode,
        )
        assertEquals(
            "unavailable",
            bridge.dispatch("r-3", BridgeCommands.CONVERSATION_RENAME, JSONObject()).errorCode,
        )
        assertEquals(
            "unavailable",
            bridge.dispatch("r-4", BridgeCommands.CONVERSATION_DELETE, JSONObject()).errorCode,
        )
        assertEquals(
            "unavailable",
            bridge.dispatch("r-5", BridgeCommands.CONVERSATION_EVENTS, JSONObject()).errorCode,
        )
    }

    @Test
    fun conversationRename_missingFieldsReturnInvalidInput(): Unit = runBlocking {
        val bridge = newBridge(newClient())

        assertEquals(
            "invalid_input",
            bridge.dispatch("r-1", BridgeCommands.CONVERSATION_RENAME, JSONObject()).errorCode,
        )
        assertEquals(
            "invalid_input",
            bridge.dispatch(
                "r-2", BridgeCommands.CONVERSATION_RENAME, JSONObject().put("id", "c-1"),
            ).errorCode,
        )
        assertEquals(
            "invalid_input",
            bridge.dispatch("r-3", BridgeCommands.CONVERSATION_DELETE, JSONObject()).errorCode,
        )
        assertEquals(
            "invalid_input",
            bridge.dispatch("r-4", BridgeCommands.CONVERSATION_EVENTS, JSONObject()).errorCode,
        )
    }

    @Test
    fun conversationList_passesServerJsonThrough(): Unit = runBlocking {
        server.enqueue(MockResponse().setBody("""{"conversations":[{"id":"c-1","title":"午饭"}]}"""))

        val response = newBridge(newClient())
            .dispatch("r-1", BridgeCommands.CONVERSATION_LIST, JSONObject())

        assertTrue(response.success)
        val data = JSONObject(response.data!!)
        assertEquals("c-1", data.getJSONArray("conversations").getJSONObject(0).getString("id"))
        assertEquals("/api/v1/ai/conversations", server.takeRequest().path)
    }

    @Test
    fun conversationRename_sendsTitle_andPassesUpdatedSummaryThrough(): Unit = runBlocking {
        server.enqueue(MockResponse().setBody("""{"id":"c-1","title":"新标题"}"""))

        val response = newBridge(newClient()).dispatch(
            "r-1",
            BridgeCommands.CONVERSATION_RENAME,
            JSONObject().put("id", "c-1").put("title", "新标题"),
        )

        assertTrue(response.success)
        assertEquals("新标题", JSONObject(response.data!!).getString("title"))
        val request = server.takeRequest()
        assertEquals("PATCH", request.method)
        assertEquals("新标题", JSONObject(request.body.readUtf8()).getString("title"))
    }

    @Test
    fun conversationDelete_returnsSuccessShape_evenWhenServerSendsNoBody(): Unit = runBlocking {
        // 服务端 204：data 若原样透传空串，信封会得到 "data": 而 JSONTokener 直接抛，
        // 前端也会 JSON.parse 失败——所以桥必须自己造一个确定的成功形状。
        server.enqueue(MockResponse().setResponseCode(204))

        val response = newBridge(newClient()).dispatch(
            "r-1", BridgeCommands.CONVERSATION_DELETE, JSONObject().put("id", "c-1"),
        )

        assertTrue(response.success)
        assertTrue(JSONObject(response.data!!).getBoolean("success"))
        // 信封本身必须是合法 JSON（toJson 会走 JSONTokener）
        assertTrue(JSONObject(response.toJson()).getBoolean("success"))
        assertEquals("DELETE", server.takeRequest().method)
    }

    @Test
    fun conversationRename_maps404ToNotFound(): Unit = runBlocking {
        // 「别人的 / 已删除的 / 不存在的 id」一律 404——客户端据此幂等处理，
        // 而不是提示用户网络错误
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"code":"conversation_not_found"}"""))

        val response = newBridge(newClient()).dispatch(
            "r-1",
            BridgeCommands.CONVERSATION_RENAME,
            JSONObject().put("id", "c-gone").put("title", "x"),
        )

        assertFalse(response.success)
        assertEquals("not_found", response.errorCode)
    }

    @Test
    fun conversationDelete_maps404ToNotFound(): Unit = runBlocking {
        server.enqueue(MockResponse().setResponseCode(404).setBody("{}"))

        val response = newBridge(newClient()).dispatch(
            "r-1", BridgeCommands.CONVERSATION_DELETE, JSONObject().put("id", "c-gone"),
        )

        assertFalse(response.success)
        assertEquals("not_found", response.errorCode)
    }

    // ─── T-M7-04b: 会话事件增量读取（断连重连的续读入口）───

    @Test
    fun conversationEvents_passesServerJsonThrough_withoutQueryWhenNoCursor(): Unit = runBlocking {
        // 没有游标（首轮断连 / 老服务端不发 done.seq）→ 不带 after_seq，服务端从头给。
        server.enqueue(MockResponse().setBody("""{"conversation_id":"c-1","events":[]}"""))

        val response = newBridge(newClient()).dispatch(
            "r-1", BridgeCommands.CONVERSATION_EVENTS, JSONObject().put("id", "c-1"),
        )

        assertTrue(response.success)
        assertEquals("c-1", JSONObject(response.data!!).getString("conversation_id"))
        val path = server.takeRequest().path!!
        assertEquals("/api/v1/ai/conversations/c-1/events", path)
        assertFalse(path.contains("after_seq"))
    }

    @Test
    fun conversationEvents_carriesAfterSeq_andPassesEventsThrough(): Unit = runBlocking {
        server.enqueue(MockResponse().setBody(
            """{"conversation_id":"c-1","events":[{"seq":9,"turn":2,"type":"assistant"}]}"""
        ))

        val response = newBridge(newClient()).dispatch(
            "r-1",
            BridgeCommands.CONVERSATION_EVENTS,
            JSONObject().put("id", "c-1").put("after_seq", 8),
        )

        assertTrue(response.success)
        val events = JSONObject(response.data!!).getJSONArray("events")
        assertEquals(9, events.getJSONObject(0).getInt("seq"))
        assertEquals("/api/v1/ai/conversations/c-1/events?after_seq=8", server.takeRequest().path)
    }

    @Test
    fun conversationEvents_zeroAfterSeqMeansNoCursor(): Unit = runBlocking {
        // 0 与缺省同义（seq 从 1 起）：前端「还没有游标」时传下来的就是 0，
        // 带上它会让服务端多一层无意义的判空 —— 统一在这里归一。
        server.enqueue(MockResponse().setBody("""{"conversation_id":"c-1","events":[]}"""))

        newBridge(newClient()).dispatch(
            "r-1",
            BridgeCommands.CONVERSATION_EVENTS,
            JSONObject().put("id", "c-1").put("after_seq", 0),
        )

        assertFalse(server.takeRequest().path!!.contains("after_seq"))
    }

    @Test
    fun conversationEvents_maps404ToNotFound(): Unit = runBlocking {
        server.enqueue(MockResponse().setResponseCode(404).setBody("{}"))

        val response = newBridge(newClient()).dispatch(
            "r-1", BridgeCommands.CONVERSATION_EVENTS, JSONObject().put("id", "c-gone"),
        )

        assertFalse(response.success)
        assertEquals("not_found", response.errorCode)
    }
}