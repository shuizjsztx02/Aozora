package com.aozora.app.export

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
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.util.concurrent.atomic.AtomicReference

/**
 * T-M5-04b review P3#7: ExportClient unit tests.
 *
 * Coverage:
 *  - POST/GET request shape: Authorization, X-Request-Id, JSON body fields
 *  - ExportRecord parsing incl. null expires_at / missing file_size
 *  - HTTP status → ExportError subclass mapping (401/404/410/500)
 *  - AuthClient.AuthLostException → ExportError.AuthLost
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])  // Robolectric 4.11.1 caps at SDK 34; project tests pin 33
class ExportClientTest {

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
                .message(if (code in 200..299) "OK" else "Response")
                .body(body.toResponseBody("application/json".toMediaType()))
                .build()
        }
    }

    /** AuthClient subclass that returns a fixed token (or throws AuthLost). */
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

    private companion object {
        const val RECORD_JSON =
            """{"id":"e-1","book_id":"b-1","format":"json","status":"completed",""" +
            """"created_at":"2026-01-01T00:00:00Z","expires_at":null,"file_size":123}"""
    }

    private lateinit var testContext: android.content.Context

    @Before
    fun setUp() {
        testContext = ApplicationProvider.getApplicationContext()
    }

    private fun clientWith(interceptor: FakeInterceptor, auth: AuthClient =
        TestAuthClient(testContext)) = ExportClient(
        baseUrl = "http://localhost",
        httpClient = OkHttpClient.Builder().addInterceptor(interceptor).build(),
        authClient = auth,
    )

    private fun requestBody(request: okhttp3.Request): String {
        val buffer = okio.Buffer()
        request.body!!.writeTo(buffer)
        return buffer.readUtf8()
    }

    // ─── createExport ───

    @Test
    fun `createExport posts book_id and format and parses record`() = runBlocking {
        val interceptor = FakeInterceptor(201, RECORD_JSON)
        val client = clientWith(interceptor)

        val record = client.createExport("b-1", "json")

        assertEquals("e-1", record.id)
        assertEquals("b-1", record.bookId)
        assertEquals("json", record.format)
        assertEquals("completed", record.status)
        assertEquals(123L, record.fileSize)
        assertNull("expires_at null must parse as Kotlin null", record.expiresAt)

        val request = interceptor.lastRequest.get()!!
        assertEquals("POST", request.method)
        assertEquals("http://localhost/exports", request.url.toString())
        val json = org.json.JSONObject(requestBody(request))
        assertEquals("b-1", json.getString("book_id"))
        assertEquals("json", json.getString("format"))
    }

    @Test
    fun `createExport sends Authorization bearer and X-Request-Id UUID`() = runBlocking {
        val interceptor = FakeInterceptor(201, RECORD_JSON)
        val client = clientWith(
            interceptor,
            TestAuthClient(testContext, token = "my-jwt"),
        )

        client.createExport("b-1", "csv")

        val request = interceptor.lastRequest.get()!!
        assertEquals("Bearer my-jwt", request.header("Authorization"))
        val requestId = request.header("X-Request-Id")
        assertNotNull(requestId)
        assertTrue(
            "X-Request-Id should be UUID format, got: $requestId",
            requestId!!.matches(Regex("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")),
        )
    }

    @Test
    fun `createExport parses non-null expires_at and file_size`() = runBlocking {
        val interceptor = FakeInterceptor(
            201,
            """{"id":"e-2","book_id":"b-1","format":"csv","status":"completed",""" +
                """"created_at":"2026-01-01T00:00:00Z","expires_at":"2026-01-08T00:00:00Z",""" +
                """"file_size":456}""",
        )
        val client = clientWith(interceptor)

        val record = client.createExport("b-1", "csv")

        assertEquals("2026-01-08T00:00:00Z", record.expiresAt)
        assertEquals(456L, record.fileSize)
    }

    @Test
    fun `createExport treats absent file_size as null`() = runBlocking {
        val interceptor = FakeInterceptor(
            201,
            """{"id":"e-3","book_id":"b-1","format":"json","status":"pending",""" +
                """"created_at":"2026-01-01T00:00:00Z"}""",
        )
        val client = clientWith(interceptor)

        val record = client.createExport("b-1", "json")

        assertEquals("pending", record.status)
        assertNull(record.fileSize)
        assertNull(record.expiresAt)
    }

    // ─── listExports / getExport ───

    @Test
    fun `listExports parses JSON array of records`() = runBlocking {
        val interceptor = FakeInterceptor(
            200,
            """[$RECORD_JSON,{"id":"e-2","book_id":"b-2","format":"csv","status":"pending",""" +
                """"created_at":"2026-01-02T00:00:00Z","expires_at":null,"file_size":null}]""",
        )
        val client = clientWith(interceptor)

        val records = client.listExports()

        assertEquals(2, records.size)
        assertEquals("e-1", records[0].id)
        assertEquals("e-2", records[1].id)
        assertNull(records[1].fileSize)
        assertEquals("GET", interceptor.lastRequest.get()!!.method)
    }

    @Test
    fun `getExport hits per-id endpoint`() = runBlocking {
        val interceptor = FakeInterceptor(200, RECORD_JSON)
        val client = clientWith(interceptor)

        val record = client.getExport("e-1")

        assertEquals("e-1", record.id)
        assertEquals("http://localhost/exports/e-1", interceptor.lastRequest.get()!!.url.toString())
    }

    // ─── HTTP status → error mapping ───

    @Test
    fun `401 maps to AuthLost`() = runBlocking {
        val client = clientWith(FakeInterceptor(401))
        try {
            client.createExport("b-1", "json")
            fail("should have thrown")
        } catch (e: ExportClient.ExportError.AuthLost) {
            // expected
        }
    }

    @Test
    fun `404 maps to NotFound`() = runBlocking {
        val client = clientWith(FakeInterceptor(404))
        try {
            client.getExport("missing")
            fail("should have thrown")
        } catch (e: ExportClient.ExportError.NotFound) {
            // expected
        }
    }

    @Test
    fun `410 maps to Expired`() = runBlocking {
        val client = clientWith(FakeInterceptor(410))
        try {
            client.getExport("gone")
            fail("should have thrown")
        } catch (e: ExportClient.ExportError.Expired) {
            // expected
        }
    }

    @Test
    fun `500 maps to ServerError with httpCode`() = runBlocking {
        val client = clientWith(FakeInterceptor(500, "internal error"))
        try {
            client.createExport("b-1", "json")
            fail("should have thrown")
        } catch (e: ExportClient.ExportError.ServerError) {
            assertEquals(500, e.httpCode)
        }
    }

    @Test
    fun `AuthClient AuthLostException propagates as ExportError_AuthLost`() = runBlocking {
        val client = clientWith(
            FakeInterceptor(200),
            TestAuthClient(testContext, throwAuthLost = true),
        )
        try {
            client.listExports()
            fail("should have thrown")
        } catch (e: ExportClient.ExportError.AuthLost) {
            // expected — token never reached the wire
        }
    }
}
