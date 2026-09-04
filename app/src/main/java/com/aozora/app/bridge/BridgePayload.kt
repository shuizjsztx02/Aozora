package com.aozora.app.bridge

import org.json.JSONObject

/** JSON 数值边界：getLong/getInt 会截断小数或接受字符串，不能用于金额。 */
internal object BridgePayload {
    fun safeLong(input: JSONObject, key: String): Long {
        val raw = input.opt(key)
        require(raw is Number) { "$key must be a number" }
        val value = raw.toDouble()
        require(value.isFinite() && value % 1.0 == 0.0 &&
            value >= -9007199254740991.0 && value <= 9007199254740991.0) {
            "$key must be a safe integer"
        }
        return raw.toLong()
    }

    fun optionalString(input: JSONObject, key: String): String? {
        if (input.isNull(key)) return null
        val value = input.get(key)
        require(value is String && value.isNotBlank()) { "$key must be a non-empty string" }
        return value
    }

    fun version(input: JSONObject): Int {
        if (!input.has("version")) return 1
        val version = safeLong(input, "version")
        require(version in 1..Int.MAX_VALUE.toLong()) { "invalid version" }
        return version.toInt()
    }
}
