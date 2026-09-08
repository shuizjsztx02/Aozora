package com.aozora.app.crashlytics

import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.runBlocking
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.util.concurrent.atomic.AtomicReference

/**
 * T-M5-05b + T-M5-05ab: CrashReporter-related unit tests (Robolectric / JVM).
 *
 * Coverage:
 *  - CrashSanitizer: redact tokens/passwords/API keys from messages & stacktraces
 *  - CrashSanitizer: length limits (message 2000, stacktrace 16000)
 *  - CrashSanitizer: stable signature for identical exception frames
 *  - HeartbeatClient: skips network I/O when user hasn't consented
 *  - HeartbeatClient: sends request when user has consented (Authorization header)
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])  // Robolectric 4.11.1 caps at SDK 34; project tests pin 33
class CrashReporterTest {

    // ─── Fake HTTP interceptor for capturing requests ───

    private class FakeInterceptor(
        private val code: Int = 200,
        private val responseBody: String = "{}",
    ) : Interceptor {
        val lastRequest = AtomicReference<okhttp3.Request?>()

        override fun intercept(chain: Interceptor.Chain): Response {
            lastRequest.set(chain.request())
            return Response.Builder()
                .request(chain.request())
                .protocol(Protocol.HTTP_1_1)
                .code(code)
                .message("OK")
                .body(responseBody.toResponseBody("application/json".toMediaType()))
                .build()
        }
    }

    // ─── Helpers ───

    private fun newRuntimeException(): RuntimeException = RuntimeException("boom")

    private fun makeMockHeartbeatClient(
        httpClient: OkHttpClient,
        consented: Boolean,
    ): HeartbeatClient {
        val context = ApplicationProvider.getApplicationContext<android.content.Context>()
        val tokenStore = object : com.aozora.app.auth.TokenStore(context) {
            override val deviceId = "test-device-id"
        }
        val authClient = object : com.aozora.app.auth.AuthClient(
            "http://localhost",
            tokenStore,
            httpClient,
        ) {
            override suspend fun ensureValidAccessToken(): String = "mock-token"
        }
        return HeartbeatClient(
            baseUrl = "http://localhost",
            httpClient = httpClient,
            authClient = authClient,
            tokenStore = tokenStore,
        )
    }

    // ═══════════════════════════════════════════════════════════
    // CrashSanitizer tests
    // ═══════════════════════════════════════════════════════════

    @Test
    fun `sanitize strips access_token JSON pair from message`() {
        val msg = """Login failed: {"access_token": "abc123def456", "user": "alice"}"""
        val (cleanMsg, _) = CrashSanitizer.sanitize(msg, "")
        assertFalse(cleanMsg.contains("abc123def456"))
        assertTrue(cleanMsg.contains("\"access_token\": \"[REDACTED]\""))
    }

    @Test
    fun `sanitize strips refresh_token JSON pair from message`() {
        val msg = """Token expired: {"refresh_token": "rt_998877", "expires_in": 3600}"""
        val (cleanMsg, _) = CrashSanitizer.sanitize(msg, "")
        assertFalse(cleanMsg.contains("rt_998877"))
    }

    @Test
    fun `sanitize strips password from message`() {
        val msg = """Auth error: {"password": "hunter2", "username": "admin"}"""
        val (cleanMsg, _) = CrashSanitizer.sanitize(msg, "")
        assertFalse(cleanMsg.contains("hunter2"))
    }

    @Test
    fun `sanitize strips API key from URL query params`() {
        val msg = "Request to https://api.example.com/v1?api_key=sk-live-xxx&limit=10"
        val (cleanMsg, _) = CrashSanitizer.sanitize(msg, "")
        assertFalse(cleanMsg.contains("sk-live-xxx"))
        assertTrue(cleanMsg.contains("api_key=[REDACTED]"))
    }

    @Test
    fun `sanitize strips token from URL query params`() {
        val msg = "Error at https://auth.example.com/oauth?token=TOK_SECRET_999&redirect=/"
        val (cleanMsg, _) = CrashSanitizer.sanitize(msg, "")
        assertFalse(cleanMsg.contains("TOK_SECRET_999"))
    }

    @Test
    fun `sanitize preserves stacktrace while stripping sensitive values`() {
        val msg = "NullPointerException"
        val stack = """java.lang.NullPointerException: secret=shhh123
	at com.aozora.app.MainActivity.onCreate(MainActivity.kt:42)
	at android.app.Activity.performCreate(Activity.java:8000)
""".trimIndent()
        val (_, cleanStack) = CrashSanitizer.sanitize(msg, stack)
        assertTrue(cleanStack.contains("MainActivity.onCreate"))
        assertFalse(cleanStack.contains("shhh123"))
        assertTrue(cleanStack.contains("secret=[REDACTED]"))
    }

    @Test
    fun `sanitize does NOT strip non-sensitive fields`() {
        val msg = """Error: {"user_id": "u123", "amount_cents": 5000, "note": "lunch"}"""
        val (cleanMsg, _) = CrashSanitizer.sanitize(msg, "")
        // "note" is in SENSITIVE_KEYS, so it gets redacted
        assertFalse(cleanMsg.contains("\"note\": \"lunch\""))
        // But user_id and amount_cents are safe
        assertTrue(cleanMsg.contains("user_id"))
        assertTrue(cleanMsg.contains("5000"))
    }

    @Test
    fun `sanitize truncates oversized message to 2000 chars`() {
        val longMsg = "x".repeat(3_000)
        val (cleanMsg, _) = CrashSanitizer.sanitize(longMsg, "")
        assertEquals(2_000, cleanMsg.length)
    }

    @Test
    fun `sanitize truncates oversized stacktrace to 16000 chars`() {
        val longStack = "a\n".repeat(9_000)  // 18_000 chars — above the 16_000 cap
        val (_, cleanStack) = CrashSanitizer.sanitize("", longStack)
        assertEquals(16_000, cleanStack.length)
    }

    @Test
    fun `sanitize respects both limits independently`() {
        val longMsg = "m".repeat(3_000)
        val longStack = "s\n".repeat(9_000)  // 18_000 chars — above the 16_000 cap
        val (cleanMsg, cleanStack) = CrashSanitizer.sanitize(longMsg, longStack)
        assertEquals(2_000, cleanMsg.length)
        assertEquals(16_000, cleanStack.length)
    }

    @Test
    fun `signature is deterministic for same exception type`() {
        // Both exceptions are constructed at the same source line inside the
        // helper, so their top frames are identical — which is exactly what
        // the signature is documented to hash (class + top frame).
        val ex1 = newRuntimeException()
        val ex2 = newRuntimeException()
        assertEquals(CrashSanitizer.signature(ex1), CrashSanitizer.signature(ex2))
    }

    @Test
    fun `sanitize handles empty inputs`() {
        val (cleanMsg, cleanStack) = CrashSanitizer.sanitize("", "")
        assertEquals("", cleanMsg)
        assertEquals("", cleanStack)
    }

    // ═══════════════════════════════════════════════════════════
    // HeartbeatClient consent gate tests
    // ═══════════════════════════════════════════════════════════

    @Test
    fun `HeartbeatClient send skips network when consent is false`() = runBlocking {
        val interceptor = FakeInterceptor()
        val httpClient = OkHttpClient.Builder().addInterceptor(interceptor).build()
        val client = makeMockHeartbeatClient(httpClient, consented = false)

        client.send("1.0.0", hasConsent = false)

        assertNull(
            "No HTTP request should be sent without consent",
            interceptor.lastRequest.get(),
        )
    }

    @Test
    fun `HeartbeatClient send includes Authorization when consent is true`() = runBlocking {
        val interceptor = FakeInterceptor()
        val httpClient = OkHttpClient.Builder().addInterceptor(interceptor).build()
        val client = makeMockHeartbeatClient(httpClient, consented = true)

        client.send("1.0.0", hasConsent = true)

        val req = interceptor.lastRequest.get()
        assertNotNull("HTTP request should be made with consent", req)
        assertEquals("Bearer mock-token", req!!.header("Authorization"))
    }

    @Test
    fun `HeartbeatClient send includes app_version in body`() = runBlocking {
        val interceptor = FakeInterceptor(200, "{}")
        val httpClient = OkHttpClient.Builder().addInterceptor(interceptor).build()
        val client = makeMockHeartbeatClient(httpClient, consented = true)

        client.send("2.1.0", hasConsent = true)

        val req = interceptor.lastRequest.get()!!
        val bodyStr = req.body!!.let { buffer ->
            val b = okio.Buffer()
            buffer.writeTo(b)
            b.readUtf8()
        }
        assertTrue("Body should contain app_version", bodyStr.contains("2.1.0"))
        assertTrue("Body should have app_version key", bodyStr.contains("\"app_version\""))
    }

    @Test
    fun `HeartbeatClient send includes X-Device-Id header`() = runBlocking {
        val interceptor = FakeInterceptor(200, "{}")
        val httpClient = OkHttpClient.Builder().addInterceptor(interceptor).build()
        val client = makeMockHeartbeatClient(httpClient, consented = true)

        client.send("1.0.0", hasConsent = true)

        val req = interceptor.lastRequest.get()!!
        assertEquals("test-device-id", req.header("X-Device-Id"))
    }

    @Test
    fun `HeartbeatClient targets correct endpoint`() = runBlocking {
        val interceptor = FakeInterceptor(200, "{}")
        val httpClient = OkHttpClient.Builder().addInterceptor(interceptor).build()
        val client = makeMockHeartbeatClient(httpClient, consented = true)

        client.send("1.0.0", hasConsent = true)

        val req = interceptor.lastRequest.get()!!
        assertTrue(req.url.toString().contains("/telemetry/heartbeat"))
    }
}
