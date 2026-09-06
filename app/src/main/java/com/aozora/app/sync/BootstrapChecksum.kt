package com.aozora.app.sync

import java.security.MessageDigest

/**
 * T-M3-03: Bootstrap checksum 计算 — 与服务端算法保持一致。
 *
 * 算法说明（详见 server/src/modules/sync/service.py:compute_bootstrap_checksum）：
 * 1. 每个 entity list 按 id 升序排序。
 * 2. 每个 entity 的 JSON 键按字母顺序排序，使用紧凑分隔符（"," 和 ":"）。
 * 3. 所有 entity JSON 按类型顺序拼接：books|categories|accounts|transactions|budgets。
 * 4. 末尾追加 `|cursor:{cursor}`。
 * 5. 整体 UTF-8 编码后计算 SHA-256，返回 64 字符小写十六进制字符串。
 *
 * P0-1 修复：调用方必须传入 snake_case 键的 Map（与服务端 _serialize_entity 输出一致）。
 * 此前调用方传入 camelCase Map，因键名不同（created_at vs createdAt 等）导致永远无法匹配。
 */
object BootstrapChecksum {

    /**
     * 从 bootstrap 响应数据计算 checksum。
     *
     * @param books snake_case 字段 Map 列表（P0-1：必须保留原始 snake_case 键）
     * @param categories snake_case 字段 Map 列表
     * @param accounts snake_case 字段 Map 列表
     * @param transactions snake_case 字段 Map 列表
     * @param budgets snake_case 字段 Map 列表
     * @param cursor 服务端 cursor
     * @return 64 字符小写十六进制 SHA-256
     */
    fun compute(
        books: List<Map<String, String?>>,
        categories: List<Map<String, String?>>,
        accounts: List<Map<String, String?>>,
        transactions: List<Map<String, String?>>,
        budgets: List<Map<String, String?>>,
        cursor: Long
    ): String {
        val parts = mutableListOf<String>()

        fun appendList(items: List<Map<String, String?>>) {
            val sorted = items.sortedBy { it["id"] ?: "" }
            for (item in sorted) {
                parts += mapToJson(item)
            }
        }

        appendList(books)
        appendList(categories)
        appendList(accounts)
        appendList(transactions)
        appendList(budgets)
        parts += "cursor:$cursor"

        val payload = parts.joinToString("|")
        return sha256Hex(payload)
    }

    /** 计算 UTF-8 字符串的 SHA-256，返回 64 字符小写十六进制。 */
    private fun sha256Hex(input: String): String {
        val digest = MessageDigest.getInstance("SHA-256")
        val bytes = digest.digest(input.toByteArray(Charsets.UTF_8))
        val sb = StringBuilder(bytes.size * 2)
        for (b in bytes) {
            val v = b.toInt() and 0xFF
            sb.append(HEX_CHARS[v ushr 4])
            sb.append(HEX_CHARS[v and 0x0F])
        }
        return sb.toString()
    }

    /**
     * 将 Map<String, String?> 序列化为确定性 JSON（键按字母序，紧凑格式）。
     *
     * 与服务端 json.dumps(obj, sort_keys=True, separators=(",", ":")) 等价。
     * null 值序列化为 JSON `null`（不带引号）。
     * 数字字符串（整数或有限小数）序列化为 JSON 数字（不带引号）。
     * 布尔字符串（"true"/"false"）序列化为 JSON 布尔（不带引号）。
     */
    private fun mapToJson(map: Map<String, String?>): String {
        val sb = StringBuilder()
        sb.append('{')
        val sortedKeys = map.keys.sorted()
        sortedKeys.forEachIndexed { index, key ->
            if (index > 0) sb.append(',')
            sb.append('"').append(escapeJsonString(key)).append('"')
            sb.append(':')
            val value = map[key]
            if (value == null) {
                sb.append("null")
            } else when {
                // 布尔值必须在数字之前判定，否则 "true" 不会被误判为数字
                value.equals("true", ignoreCase = true) -> sb.append("true")
                value.equals("false", ignoreCase = true) -> sb.append("false")
                // 判定是否为数字（纯数字字符串保持数字格式，与服务端一致）
                isNumeric(value) -> sb.append(value)
                else -> sb.append('"').append(escapeJsonString(value)).append('"')
            }
        }
        sb.append('}')
        return sb.toString()
    }

    /**
     * 判断字符串是否为 JSON 数字字面量（整数或有限小数）。
     *
     * P3-2 修复：严格正则与 Python json.dumps 的数字输出规则对齐：
     * - 整数：可选负号 + 非零开头或单个 0 + 后续数字
     * - 小数：同上 + 小数点 + 至少一位数字
     * 旧实现用 toDoubleOrNull 接受 "1e5" / "Infinity" 等，客户端与服务端
     * 序列化不一致会导致 checksum 偏差。服务端 _serialize_entity 仅产出整数
     * （amount_cents/version）或字符串，不会出现科学计数法。
     */
    private fun isNumeric(s: String): Boolean {
        if (s.isEmpty()) return false
        return NUMERIC_REGEX.matches(s)
    }

    /** 转义 JSON 字符串中的特殊字符。 */
    private fun escapeJsonString(s: String): String {
        // P3-3 修复：预分配 StringBuilder 容量 + 用查表/字符串拼接替代 String.format
        // （每字符一次 format 调用在大 bootstrap 数据量下是热点）。
        val sb = StringBuilder(s.length)
        for (c in s) {
            when (c) {
                '"' -> sb.append("\\\"")
                '\\' -> sb.append("\\\\")
                '\b' -> sb.append("\\b")
                '\n' -> sb.append("\\n")
                '\r' -> sb.append("\\r")
                '\t' -> sb.append("\\t")
                else -> sb.append(c)
            }
        }
        return sb.toString()
    }

    private val NUMERIC_REGEX = Regex(
        """^-?(?:0|[1-9]\d*)(?:\.\d+)?$"""
    )

    private val HEX_CHARS = "0123456789abcdef".toCharArray()
}
