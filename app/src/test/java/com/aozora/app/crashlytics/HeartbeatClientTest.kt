package com.aozora.app.crashlytics

import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.runBlocking
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.util.concurrent.atomic.AtomicReference

/**
 * T-M5-05ab fix (S1-3): HeartbeatClient unit tests.
 *
 * Coverage:
 *  - Consent guard: skips when hasConsent = false
 *  - Consent satisfied: sends heartbeat when hasConsent = true
 *  - No token: silently returns (no crash)
 *  - Auth lost: silently returns
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])  // Robolectric 4.11.1 caps at SDK 34; project tests pin 33
class HeartbeatClientTest {

    private class CaptureInterceptor : Interceptor {
        val capturedRequests = mutableListOf<okhttp3.Request>()

        override fun intercept(chain: Interceptor.Chain): Response {
            chain.request().also { capturedRequests.add(it) }
            return Response.Builder()
                .request(chain.request())
                .protocol(Protocol.HTTP_1_1)
                .code(200)
                .message("OK")
                .body("{}".toResponseBody("application/json".toMediaType()))
                .build()
        }
    }

    /** TokenStore that always provides a fixed deviceId. */
    private class TestTokenStore(context: android.content.Context) :
        com.aozora.app.auth.TokenStore(context) {
        override val deviceId: String get() = "test-device-uuid"
    }

    /** AuthClient that returns a fixed token or throws. */
    private class TestAuthClient(
        context: android.content.Context,
        private val token: String? = "bearer-token",
        private val throwAuthLost: Boolean = false,
    ) : com.aozora.app.auth.AuthClient(
        "http://localhost",
        object : com.aozora.app.auth.TokenStore(context) {
            override val deviceId = "unused"
        },
        OkHttpClient(),
    ) {
        override suspend fun ensureValidAccessToken(): String {
            if (throwAuthLost) throw com.aozora.app.auth.AuthClient.AuthLostException("lost")
            return token ?: throw IllegalStateException("no token available")
        }
    }

    private lateinit var testContext: android.content.Context

    @Before
    fun setUp() {
        testContext = ApplicationProvider.getApplicationContext()
    }

    // ─── Consent guard ───

    @Test
    fun `send skips when hasConsent is false`() = runBlocking {
        val interceptor = CaptureInterceptor()
        val client = HeartbeatClient(
            baseUrl = "http://localhost",
            httpClient = OkHttpClient.Builder().addInterceptor(interceptor).build(),
            authClient = TestAuthClient(testContext),
            tokenStore = TestTokenStore(testContext),
        )

        client.send("1.0.0", hasConsent = false)

        assertTrue("should not send without consent", interceptor.capturedRequests.isEmpty())
    }

    @Test
    fun `send proceeds when hasConsent is true`() = runBlocking {
        val interceptor = CaptureInterceptor()
        val client = HeartbeatClient(
            baseUrl = "http://localhost",
            httpClient = OkHttpClient.Builder().addInterceptor(interceptor).build(),
            authClient = TestAuthClient(testContext),
            tokenStore = TestTokenStore(testContext),
        )

        client.send("1.0.0", hasConsent = true)

        assertEquals(1, interceptor.capturedRequests.size)
        val req = interceptor.capturedRequests[0]
        assertEquals("http://localhost/telemetry/heartbeat", req.url.toString())
        assertEquals("Bearer bearer-token", req.header("Authorization"))
    }

    // ─── Default parameter backward compat ───

    @Test
    fun `send with default hasConsent skips (backward compat)`() = runBlocking {
        val interceptor = CaptureInterceptor()
        val client = HeartbeatClient(
            baseUrl = "http://localhost",
            httpClient = OkHttpClient.Builder().addInterceptor(interceptor).build(),
            authClient = TestAuthClient(testContext),
            tokenStore = TestTokenStore(testContext),
        )

        // Old call style — should NOT crash, just skip.
        client.send("2.0.0")

        assertTrue("default hasConsent=false should skip", interceptor.capturedRequests.isEmpty())
    }

    // ─── Token / auth edge cases ───

    @Test
    fun `send silently returns when no access token`() = runBlocking {
        val interceptor = CaptureInterceptor()
        val client = HeartbeatClient(
            baseUrl = "http://localhost",
            httpClient = OkHttpClient.Builder().addInterceptor(interceptor).build(),
            authClient = TestAuthClient(testContext, token = null),
            tokenStore = TestTokenStore(testContext),
        )

        client.send("1.0.0", hasConsent = true)

        assertTrue("silent return on missing token", interceptor.capturedRequests.isEmpty())
    }

    @Test
    fun `send silently returns on auth lost exception`() = runBlocking {
        val interceptor = CaptureInterceptor()
        val client = HeartbeatClient(
            baseUrl = "http://localhost",
            httpClient = OkHttpClient.Builder().addInterceptor(interceptor).build(),
            authClient = TestAuthClient(testContext, throwAuthLost = true),
            tokenStore = TestTokenStore(testContext),
        )

        client.send("1.0.0", hasConsent = true)

        assertTrue("silent return on auth lost", interceptor.capturedRequests.isEmpty())
    }

    // ─── Request structure ───

    @Test
    fun `sends X-Device-Id header and JSON body with app_version`() = runBlocking {
        val interceptor = CaptureInterceptor()
        val client = HeartbeatClient(
            baseUrl = "http://localhost",
            httpClient = OkHttpClient.Builder().addInterceptor(interceptor).build(),
            authClient = TestAuthClient(testContext),
            tokenStore = TestTokenStore(testContext),
        )

        client.send("v2.1.0", hasConsent = true)

        val req = interceptor.capturedRequests[0]
        assertEquals("test-device-uuid", req.header("X-Device-Id"))
        val body = req.body!!.let {
            val buffer = okio.Buffer()
            it.writeTo(buffer)
            buffer.readUtf8()
        }
        assertTrue("body should contain app_version", body.contains("\"app_version\""))
        assertTrue("body should have v2.1.0", body.contains("\"v2.1.0\""))
    }
}
