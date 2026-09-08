package com.aozora.app.crashlytics

import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * T-M5-05b: CrashSanitizer 单元测试 — 脱敏规则回归守卫。
 *
 * 覆盖：JSON_PAIR 脱敏、URL_PARAM 脱敏、长度截断、signature 稳定性。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])  // Robolectric 4.11.1 caps at SDK 34; project tests pin 33
class CrashSanitizerTest {

    // ─── JSON_PAIR 脱敏 ────────────────────────────────────────────────

    @Test
    fun `sanitize redacts token in JSON pair`() {
        val msg = """{"token": "abc123", "user": "alice"}"""
        val (cleanMsg, _) = CrashSanitizer.sanitize(msg, "")
        assertTrue(cleanMsg.contains("\"token\": \"[REDACTED]\""))
        assertFalse(cleanMsg.contains("abc123"))
    }

    @Test
    fun `sanitize redacts API key in JSON pair`() {
        val msg = """{"api_key": "sk-live-xxx"}"""
        val (cleanMsg, _) = CrashSanitizer.sanitize(msg, "")
        assertTrue(cleanMsg.contains("\"api_key\": \"[REDACTED]\""))
        assertFalse(cleanMsg.contains("sk-live-xxx"))
    }

    @Test
    fun `sanitize redacts password case-insensitively`() {
        val msg = """{"PASSWORD": "hunter2"}"""
        val (cleanMsg, _) = CrashSanitizer.sanitize(msg, "")
        assertTrue(cleanMsg.contains("[REDACTED]"))
        assertFalse(cleanMsg.contains("hunter2"))
    }

    @Test
    fun `sanitize redacts nested sensitive fields in message`() {
        val msg = """Login error: {"access_token": "at_123", "refresh_token": "rt_456", "note": "manual fix"}"""
        val (cleanMsg, _) = CrashSanitizer.sanitize(msg, "")
        assertFalse(cleanMsg.contains("at_123"))
        assertFalse(cleanMsg.contains("rt_456"))
        assertFalse(cleanMsg.contains("manual fix"))
    }

    // ─── URL_PARAM 脱敏 ────────────────────────────────────────────────

    @Test
    fun `sanitize redacts api_key in URL params`() {
        val msg = "GET /api?api_key=sk-secret&format=json"
        val (cleanMsg, _) = CrashSanitizer.sanitize(msg, "")
        assertTrue(cleanMsg.contains("api_key=[REDACTED]"))
        assertTrue(cleanMsg.contains("&format=json")) // non-sensitive params preserved
        assertFalse(cleanMsg.contains("sk-secret"))
    }

    @Test
    fun `sanitize redacts token in URL query string`() {
        val msg = "POST /oauth/authorize?auth_token=TOK_999&client_id=c123"
        val (cleanMsg, _) = CrashSanitizer.sanitize(msg, "")
        assertTrue(cleanMsg.contains("auth_token=[REDACTED]"))
        assertFalse(cleanMsg.contains("TOK_999"))
    }

    @Test
    fun `sanitize redacts cookie in URL params`() {
        val msg = "Request with ?cookie=session%3Dxyz&other=val"
        val (cleanMsg, _) = CrashSanitizer.sanitize(msg, "")
        assertTrue(cleanMsg.contains("cookie=[REDACTED]"))
        assertFalse(cleanMsg.contains("session%3Dxyz"))
    }

    // ─── 非敏感字段不被脱敏 ─────────────────────────────────────────────

    @Test
    fun `sanitize preserves non-sensitive fields`() {
        val msg = """{"request_id": "req-123", "amount_cents": 5000, "user_name": "bob"}"""
        val (cleanMsg, _) = CrashSanitizer.sanitize(msg, "")
        assertEquals(
            "request_id should be untouched",
            """{"request_id": "req-123", "amount_cents": 5000, "user_name": "bob"}""",
            cleanMsg,
        )
    }

    @Test
    fun `sanitize preserves safe URL parameters`() {
        val msg = "https://example.com/api?limit=10&page=2&sort=desc"
        val (cleanMsg, _) = CrashSanitizer.sanitize(msg, "")
        assertEquals(msg, cleanMsg)
    }

    // ─── 长度截断 ──────────────────────────────────────────────────────

    @Test
    fun `sanitized message truncated to MAX_MESSAGE_LENGTH`() {
        val longMsg = "x".repeat(5_000)
        val (cleanMsg, _) = CrashSanitizer.sanitize(longMsg, "")
        assertEquals(0, CrashSanitizer.sanitize("", "").first.length) // baseline
        assertEquals(2_000, cleanMsg.length)
    }

    @Test
    fun `sanitized stacktrace truncated to MAX_STACKTRACE_LENGTH`() {
        val longStack = ("java.lang.StackOverflowError\n\tat a.b.c(Dog.kt:1)\n").repeat(2_000)
        val (_, cleanStack) = CrashSanitizer.sanitize("", longStack)
        assertEquals(16_000, cleanStack.length)
    }

    // ─── Signature 稳定性 ──────────────────────────────────────────────

    @Test
    fun `signature produces stable output for same exception class`() {
        val ex = IllegalStateException("config missing")
        // Run multiple times; should always produce identical hash
        val sig1 = CrashSanitizer.signature(ex)
        val sig2 = CrashSanitizer.signature(ex)
        val sig3 = CrashSanitizer.signature(ex)
        assertEquals(sig1, sig2)
        assertEquals(sig2, sig3)
        // Should be an 8-char hex string
        assertEquals(8, sig1.length)
        assertTrue(sig1.matches(Regex("[0-9a-f]+")))
    }

    // ─── Edge Cases ────────────────────────────────────────────────────

    @Test
    fun `sanitize handles inputs with only newlines`() {
        val msg = "\n\n\n"
        val (cleanMsg, cleanStack) = CrashSanitizer.sanitize(msg, "\n\n")
        assertEquals("\n\n\n", cleanMsg)
        assertEquals("\n\n", cleanStack)
    }

    @Test
    fun `sanitize does not alter empty strings`() {
        val (cleanMsg, cleanStack) = CrashSanitizer.sanitize("", "")
        assertEquals("", cleanMsg)
        assertEquals("", cleanStack)
    }

    @Test
    fun `sanitize does not touch text without sensitive keys`() {
        val msg = """Error in MainActivity.onCreate: database connection lost"""
        val stack = """java.sql.SQLException: Connection lost
	at com.aozora.app.db.ConnectionPool.open(ConnectionPool.kt:42)
""".trimIndent()
        val (cleanMsg, cleanStack) = CrashSanitizer.sanitize(msg, stack)
        assertEquals(msg, cleanMsg)
        assertEquals(stack, cleanStack)
    }

    @Test
    fun `sanitize handles escaped characters in sensitive values`() {
        val msg = """{"token": "ab\\"c\\d", "password": "pass\\nword"}"""
        val (cleanMsg, _) = CrashSanitizer.sanitize(msg, "")
        assertFalse(cleanMsg.contains("ab") || cleanMsg.contains("cd")) // value redacted
        assertTrue(cleanMsg.contains("[REDACTED]"))
    }
}
