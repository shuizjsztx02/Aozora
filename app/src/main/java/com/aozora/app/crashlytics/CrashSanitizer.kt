package com.aozora.app.crashlytics

import org.json.JSONObject

/**
 * T-M5-05b: 崩溃数据脱敏。
 *
 * 客户端必须在写入本地 SQLite 前调用 [sanitize]，确保不会将 token、
 * payload、note 等敏感字段原文上传到服务端。
 *
 * 规则：
 * - 遍历异常 message 与堆栈中所有 key-like 片段，命中 [SENSITIVE_KEYS]
 *   的值替换为 "[REDACTED]"。
 * - 堆栈本身保留（崩溃分析核心）。
 * - 长度上限：message 2000 字符、stacktrace 16000 字符（与服务端一致）。
 */
object CrashSanitizer {

    private val SENSITIVE_KEYS = setOf(
        "token", "access_token", "refresh_token", "authorization",
        "password", "secret", "cookie",
        "payload", "note", "memo", "content",
        "api_key", "apikey",
    )

    /**
     * 匹配 JSON 样式的 `"key": "value"` 对（message 中可能嵌入）。
     * key 用通用捕获 + [isSensitiveKey] 判断：精确匹配会让 "auth_token"
     * 这类复合参数名整段漏掉。
     */
    private val JSON_PAIR = Regex(
        """"([A-Za-z][A-Za-z0-9_.-]*)"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"""
    )

    /** 匹配 `name=value`：URL 查询参数与堆栈/日志行中的裸键值对。 */
    private val KEY_VALUE = Regex(
        """([?&])?([A-Za-z][A-Za-z0-9_.-]*)=([^&\s]*)"""
    )

    private const val REDACTED = "[REDACTED]"
    private const val MAX_MESSAGE_LENGTH = 2_000
    private const val MAX_STACKTRACE_LENGTH = 16_000

    /** 键名是否为敏感键：精确命中或带 `_`/`-` 后缀限定（auth_token、set-cookie 等）。 */
    private fun isSensitiveKey(name: String): Boolean {
        val n = name.lowercase()
        return SENSITIVE_KEYS.any { it == n || n.endsWith("_$it") || n.endsWith("-$it") }
    }

    /** 脱敏一条崩溃的 message 与 stacktrace。 */
    fun sanitize(message: String, stacktrace: String): Pair<String, String> {
        val cleanMessage = redact(message).take(MAX_MESSAGE_LENGTH)
        val cleanStacktrace = redact(stacktrace).take(MAX_STACKTRACE_LENGTH)
        return cleanMessage to cleanStacktrace
    }

    private fun redact(text: String): String {
        var out = text
        out = JSON_PAIR.replace(out) { match ->
            val key = match.groupValues[1]
            if (isSensitiveKey(key)) """"$key": "$REDACTED"""" else match.value
        }
        out = KEY_VALUE.replace(out) { match ->
            val (sep, key) = match.destructured
            if (isSensitiveKey(key)) "$sep$key=$REDACTED" else match.value
        }
        return out
    }

    /** 生成稳定的 crash signature（exception class + 顶帧方法名）。 */
    fun signature(throwable: Throwable): String {
        val topFrame = throwable.stackTrace.firstOrNull()?.toString() ?: "no-frame"
        val raw = "${throwable.javaClass.name}@$topFrame"
        return raw.hashCode().toUInt().toString(16).padStart(8, '0')
    }
}
