package com.aozora.app.feedback

import com.aozora.app.auth.AuthClient
import com.aozora.app.auth.TokenStore
import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.runBlocking
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.assertNotNull
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.util.concurrent.atomic.AtomicReference

/**
 * T-M5-05c: FeedbackClient unit tests.
 *
 * Coverage:
 *  - HTTP status code → FeedbackError subclass mapping
 *  - Successful submission returns server-assigned id
 *  - AuthClient.AuthLostException → FeedbackError.AuthLost
 *  - Request body structure: device_id_hash SHA-256, X-Request-Id
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])  // Robolectric 4.11.1 caps at SDK 34; project tests pin 33
class FeedbackClientTest {

    /** Captures the outgoing Request and returns a canned Response. */
    private class FakeInterceptor(
        private val code: Int,
        private val body: String = "",
    ) : Interceptor {
        val lastRequest = AtomicReference<okhttp3.Request?>()
        @Volatile var responseCode: Int = code

        override fun intercept(chain: Interceptor.Chain): Response {
            lastRequest.set(chain.request())
            return Response.Builder()
                .request(chain.request())
                .protocol(Protocol.HTTP_1_1)
                .code(responseCode)
                .message(
                    when (responseCode) {
                        200 -> "OK"
                        400 -> "Bad Request"
                        401 -> "Unauthorized"
                        429 -> "Too Many Requests"
                        500 -> "Internal Server Error"
                        else -> "Response"
                    }
                )
                .body(body.toResponseBody("application/json".toMediaType()))
                .build()
        }
    }

    /** TokenStore subclass that returns a fixed deviceId without SharedPreferences. */
    private class TestTokenStore(context: android.content.Context, private val fixedDeviceId: String) :
        TokenStore(context) {
        override val deviceId: String get() = fixedDeviceId
    }

    /** AuthClient subclass that returns a fixed token (or throws). */
    private class TestAuthClient(
        context: android.content.Context,
        private val token: String? = "test-token",
        private val throwAuthLost: Boolean = false,
    ) : AuthClient(
        "http://localhost",
        object : TokenStore(context) { override val deviceId = "unused" },
        OkHttpClient(),
    ) {
        override suspend fun ensureValidAccessToken(): String {
            if (throwAuthLost) throw AuthLostException("auth lost")
            return token ?: throw AuthLostException("no token")
        }
    }

    private lateinit var testContext: android.content.Context

    @Before
    fun setUp() {
        testContext = ApplicationProvider.getApplicationContext()
    }

    // ─── HTTP status code mapping ───

    @Test
    fun `submit returns server-assigned id on 200`() = runBlocking {
        val interceptor = FakeInterceptor(200, """{"id":"fb-uuid-123"}""")
        val client = FeedbackClient(
            context = testContext,
            baseUrl = "http://localhost",
            httpClient = OkHttpClient.Builder().addInterceptor(interceptor).build(),
            authClient = TestAuthClient(testContext),
            tokenStore = TestTokenStore(testContext, "device-1"),
        )

        val id = client.submit("bug", "测试反馈内容")
        assertEquals("fb-uuid-123", id)
    }

    @Test
    fun `submit throws AuthLost on 401`() = runBlocking {
        val interceptor = FakeInterceptor(401)
        val client = FeedbackClient(
            context = testContext,
            baseUrl = "http://localhost",
            httpClient = OkHttpClient.Builder().addInterceptor(interceptor).build(),
            authClient = TestAuthClient(testContext),
            tokenStore = TestTokenStore(testContext, "device-1"),
        )

        try {
            client.submit("bug", "内容")
            throw AssertionError("should have thrown")
        } catch (e: FeedbackClient.FeedbackError.AuthLost) {
            // expected
        }
    }

    @Test
    fun `submit throws RateLimited on 429`() = runBlocking {
        val interceptor = FakeInterceptor(429)
        val client = FeedbackClient(
            context = testContext,
            baseUrl = "http://localhost",
            httpClient = OkHttpClient.Builder().addInterceptor(interceptor).build(),
            authClient = TestAuthClient(testContext),
            tokenStore = TestTokenStore(testContext, "device-1"),
        )

        try {
            client.submit("bug", "内容")
            throw AssertionError("should have thrown")
        } catch (e: FeedbackClient.FeedbackError.RateLimited) {
            // expected
        }
    }

    @Test
    fun `submit throws BadRequest on 400 with detail`() = runBlocking {
        val interceptor = FakeInterceptor(400, """{"detail":"invalid category"}""")
        val client = FeedbackClient(
            context = testContext,
            baseUrl = "http://localhost",
            httpClient = OkHttpClient.Builder().addInterceptor(interceptor).build(),
            authClient = TestAuthClient(testContext),
            tokenStore = TestTokenStore(testContext, "device-1"),
        )

        try {
            client.submit("bug", "内容")
            throw AssertionError("should have thrown")
        } catch (e: FeedbackClient.FeedbackError.BadRequest) {
            assertTrue(e.detail.contains("invalid category"))
        }
    }

    @Test
    fun `submit throws ServerError on 500`() = runBlocking {
        val interceptor = FakeInterceptor(500, "internal error")
        val client = FeedbackClient(
            context = testContext,
            baseUrl = "http://localhost",
            httpClient = OkHttpClient.Builder().addInterceptor(interceptor).build(),
            authClient = TestAuthClient(testContext),
            tokenStore = TestTokenStore(testContext, "device-1"),
        )

        try {
            client.submit("bug", "内容")
            throw AssertionError("should have thrown")
        } catch (e: FeedbackClient.FeedbackError.ServerError) {
            assertEquals(500, e.httpCode)
        }
    }

    @Test
    fun `submit throws AuthLost when AuthClient throws AuthLostException`() = runBlocking {
        val interceptor = FakeInterceptor(200)
        val client = FeedbackClient(
            context = testContext,
            baseUrl = "http://localhost",
            httpClient = OkHttpClient.Builder().addInterceptor(interceptor).build(),
            authClient = TestAuthClient(testContext, throwAuthLost = true),
            tokenStore = TestTokenStore(testContext, "device-1"),
        )

        try {
            client.submit("bug", "内容")
            throw AssertionError("should have thrown")
        } catch (e: FeedbackClient.FeedbackError.AuthLost) {
            // expected
        }
    }

    // ─── Request structure ───

    @Test
    fun `submit sends SHA-256 hashed device_id in body`() = runBlocking {
        // SHA-256("test-device-id-123") = c82553f0...
        val interceptor = FakeInterceptor(200, """{"id":"fb-1"}""")
        val client = FeedbackClient(
            context = testContext,
            baseUrl = "http://localhost",
            httpClient = OkHttpClient.Builder().addInterceptor(interceptor).build(),
            authClient = TestAuthClient(testContext),
            tokenStore = TestTokenStore(testContext, "test-device-id-123"),
        )

        client.submit("bug", "内容")

        val request = interceptor.lastRequest.get()
        assertNotNull("Request should have been captured", request)
        val bodyStr = request!!.body!!.let {
            val buffer = okio.Buffer()
            it.writeTo(buffer)
            buffer.readUtf8()
        }
        assertTrue(
            "body should contain hashed device_id",
            bodyStr.contains("c82553f0455d2d3df6dc90b13f9b63f9b990971c8c0eddf822e7c37bd354fc85")
        )
    }

    @Test
    fun `submit sends X-Request-Id UUID header`() = runBlocking {
        val interceptor = FakeInterceptor(200, """{"id":"fb-1"}""")
        val client = FeedbackClient(
            context = testContext,
            baseUrl = "http://localhost",
            httpClient = OkHttpClient.Builder().addInterceptor(interceptor).build(),
            authClient = TestAuthClient(testContext),
            tokenStore = TestTokenStore(testContext, "device-1"),
        )

        client.submit("bug", "内容")

        val request = interceptor.lastRequest.get()
        assertNotNull(request)
        val requestId = request!!.header("X-Request-Id")
        assertNotNull("X-Request-Id header should be present", requestId)
        // UUID v4 format: 8-4-4-4-12 hex chars
        assertTrue(
            "X-Request-Id should be UUID format, got: $requestId",
            requestId!!.matches(Regex("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"))
        )
    }

    @Test
    fun `submit sends Authorization Bearer token`() = runBlocking {
        val interceptor = FakeInterceptor(200, """{"id":"fb-1"}""")
        val client = FeedbackClient(
            context = testContext,
            baseUrl = "http://localhost",
            httpClient = OkHttpClient.Builder().addInterceptor(interceptor).build(),
            authClient = TestAuthClient(testContext, token = "my-jwt-token"),
            tokenStore = TestTokenStore(testContext, "device-1"),
        )

        client.submit("bug", "内容")

        val request = interceptor.lastRequest.get()
        assertNotNull(request)
        assertEquals("Bearer my-jwt-token", request!!.header("Authorization"))
    }
}
