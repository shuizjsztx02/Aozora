package com.aozora.app.sync

import android.content.Context
import com.aozora.app.auth.AuthClient
import com.aozora.app.auth.TokenStore
import kotlinx.coroutines.runBlocking
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import java.util.concurrent.atomic.AtomicInteger

/**
 * T12 后修复（图片记账 403 身份漂移案）：HttpSyncApi 状态码语义测试。
 *
 * 旧口径 401..403 一律抛 AuthLostException → SyncEngine.clearAuth()。服务端
 * sync 端点的 403 是业务态（consent_required / account_frozen），不是凭证失效；
 * guest 场景下清凭证会直接触发"重注册新 guest → device_id 变更 → 永远无同意
 * 记录 → push 再 403"的死循环（/ai/execute 换到新空账号后报
 * "Proposal validation failed: rejected_hallucination"）。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class HttpSyncApiTest {

    private class FakeInterceptor(private val code: Int) : Interceptor {
        val calls = AtomicInteger()

        override fun intercept(chain: Interceptor.Chain): Response {
            calls.incrementAndGet()
            return Response.Builder()
                .request(chain.request())
                .protocol(Protocol.HTTP_1_1)
                .code(code)
                .message("fake")
                .body("""{"detail":"fake"}""".toResponseBody("application/json".toMediaType()))
                .build()
        }
    }

    /** 绕过真实 token 获取，只提供固定 access_token。 */
    private class StubAuth(context: Context) : AuthClient(
        baseUrl = "http://sync.test",
        tokenStore = TokenStore(context),
        httpClient = OkHttpClient()
    ) {
        override suspend fun ensureValidAccessToken(): String = "fake-token"
        override fun currentUserId(): String? = "u1"
    }

    private lateinit var ctx: Context

    @Before
    fun setUp() { ctx = RuntimeEnvironment.getApplication() }

    private fun apiWith(code: Int): HttpSyncApi {
        val client = OkHttpClient.Builder().addInterceptor(FakeInterceptor(code)).build()
        return HttpSyncApi("http://sync.test", client, StubAuth(ctx))
    }

    @Test
    fun http401_throwsAuthLost() = runBlocking {
        try {
            apiWith(401).bootstrap()
            fail("expected AuthLostException for 401")
        } catch (e: AuthClient.AuthLostException) {
            // 预期：仅 401 = 认证丢失
        }
    }

    @Test
    fun http403_throwsSyncHttpException_notAuthLost() = runBlocking {
        try {
            apiWith(403).bootstrap()
            fail("expected SyncHttpException for 403 (consent_required / frozen 是业务态)")
        } catch (e: AuthClient.AuthLostException) {
            fail("403 must NOT be treated as auth lost — it wipes valid credentials")
        } catch (e: SyncHttpException) {
            assertEquals(403, e.httpCode)
        }
    }

    @Test
    fun http409_throwsSyncHttpException() = runBlocking {
        try {
            apiWith(409).bootstrap()
            fail("expected SyncHttpException for 409")
        } catch (e: SyncHttpException) {
            assertEquals(409, e.httpCode)
        }
    }
}
