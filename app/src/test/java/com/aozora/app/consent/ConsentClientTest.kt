package com.aozora.app.consent

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
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.util.concurrent.atomic.AtomicReference

/**
 * T-M5-04a review P3#4: ConsentClient unit tests.
 *
 * Coverage:
 *  - POST /consent request shape (device_id / policy_version / consented_at)
 *  - device_id comes from TokenStore, not from the (possibly empty) JS param
 *  - policyVersion truncated to 32 chars (server column limit)
 *  - Best-effort semantics: HTTP failures and token errors return false,
 *    never throw (consent upload must not block onboarding).
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])  // Robolectric 4.11.1 caps at SDK 34; project tests pin 33
class ConsentClientTest {

    /** Captures the outgoing Request and returns a canned Response. */
    private class FakeInterceptor(
        private val code: Int,
        private val body: String = "",
    ) : Interceptor {
        val lastRequest = AtomicReference<okhttp3.Request?>()

        override fun intercept(chain: Interceptor.Chain): Response {
            lastRequest.set(chain.request())
            return Response.Builder()
                .request(chain.request())
                .protocol(Protocol.HTTP_1_1)
                .code(code)
                .message(if (code == 200) "OK" else "Response")
                .body(body.toResponseBody("application/json".toMediaType()))
                .build()
        }
    }

    /** TokenStore subclass with a fixed deviceId; optionally throws. */
    private class TestTokenStore(
        context: android.content.Context,
        private val fixedDeviceId: String = "real-device-uuid",
        private val throwOnAccess: Boolean = false,
    ) : TokenStore(context) {
        override val deviceId: String
            get() {
                if (throwOnAccess) throw IllegalStateException("device id unavailable")
                return fixedDeviceId
            }
    }

    private lateinit var testContext: android.content.Context

    @Before
    fun setUp() {
        testContext = ApplicationProvider.getApplicationContext()
    }

    private fun clientWith(interceptor: FakeInterceptor, tokenStore: TokenStore) =
        ConsentClient(
            baseUrl = "http://localhost",
            httpClient = OkHttpClient.Builder().addInterceptor(interceptor).build(),
            tokenStore = tokenStore,
        )

    @Test
    fun `send returns true on 200 and posts to consent endpoint`() = runBlocking {
        val interceptor = FakeInterceptor(200, """{"id":"c-1"}""")
        val client = clientWith(interceptor, TestTokenStore(testContext))

        val ok = client.send("v1", 1_700_000_000_000L)

        assertTrue(ok)
        val request = interceptor.lastRequest.get()
        assertNotNull(request)
        assertEquals("http://localhost/consent", request!!.url.toString())
        assertEquals("POST", request.method)
    }

    @Test
    fun `request body carries TokenStore device_id, not empty string`() = runBlocking {
        val interceptor = FakeInterceptor(200)
        val client = clientWith(
            interceptor,
            TestTokenStore(testContext, fixedDeviceId = "device-abc-123"),
        )

        client.send("v1", 1_700_000_000_000L)

        val bodyStr = interceptor.lastRequest.get()!!.body!!.let {
            val buffer = okio.Buffer()
            it.writeTo(buffer)
            buffer.readUtf8()
        }
        val json = org.json.JSONObject(bodyStr)
        assertEquals("device-abc-123", json.getString("device_id"))
        assertEquals("v1", json.getString("policy_version"))
        assertEquals(1_700_000_000_000L, json.getLong("consented_at"))
    }

    @Test
    fun `policy version is truncated to 32 chars`() = runBlocking {
        val interceptor = FakeInterceptor(200)
        val client = clientWith(interceptor, TestTokenStore(testContext))
        val long = "x".repeat(100)

        client.send(long, 1L)

        val bodyStr = interceptor.lastRequest.get()!!.body!!.let {
            val buffer = okio.Buffer()
            it.writeTo(buffer)
            buffer.readUtf8()
        }
        val sent = org.json.JSONObject(bodyStr).getString("policy_version")
        assertEquals(32, sent.length)
    }

    @Test
    fun `send returns false on client error 422`() = runBlocking {
        val interceptor = FakeInterceptor(422)
        val client = clientWith(interceptor, TestTokenStore(testContext))

        assertFalse(client.send("v1", 1L))
    }

    @Test
    fun `send returns false on server error 500`() = runBlocking {
        val interceptor = FakeInterceptor(500)
        val client = clientWith(interceptor, TestTokenStore(testContext))

        assertFalse(client.send("v1", 1L))
    }

    @Test
    fun `send returns false without HTTP call when deviceId throws`() = runBlocking {
        val interceptor = FakeInterceptor(200)
        val client = clientWith(
            interceptor,
            TestTokenStore(testContext, throwOnAccess = true),
        )

        assertFalse(client.send("v1", 1L))
        // An empty device_id would be rejected server-side; the client must
        // skip the call entirely rather than burn rate-limit budget.
        assertEquals(null, interceptor.lastRequest.get())
    }
}
