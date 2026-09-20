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
 *
 * T-M7-01 修复：入参值类型为 `Any?` 且**按真实类型**序列化。此前入参是 `String?`，
 * 序列化时靠内容猜类型（"2026" 出裸数字、大小写不敏感的 "true" 出布尔），而服务端
 * `json.dumps` 按值的真实类型出网 —— 任何内容形如数字/布尔的**字符串字段**（存钱目标
 * 名「2026」、备注「123」……）都会让两侧哈希不等。类型信息由
 * [SyncPayloadMapper.rawEntityMap] 从 JSON 一路保留到这里，不再猜。
 */
object BootstrapChecksum {

    /**
     * 从 bootstrap 响应数据计算 checksum。
     *
     * @param books snake_case 字段 Map 列表（P0-1：必须保留原始 snake_case 键；
     *   T-M7-01：值必须保留 JSON 原始类型，不得预先 toString）
     * @param categories snake_case 字段 Map 列表
     * @param accounts snake_case 字段 Map 列表
     * @param transactions snake_case 字段 Map 列表
     * @param budgets snake_case 字段 Map 列表
     * @param cursor 服务端 cursor
     * @return 64 字符小写十六进制 SHA-256
     */
    fun compute(
        books: List<Map<String, Any?>>,
        categories: List<Map<String, Any?>>,
        accounts: List<Map<String, Any?>>,
        transactions: List<Map<String, Any?>>,
        budgets: List<Map<String, Any?>>,
        cursor: Long
    ): String = computeOver(
        listOf(books, categories, accounts, transactions, budgets), cursor,
    )

    /**
     * T-M7-01: 八类实体 + cursor 的 checksum，与服务端
     * `compute_bootstrap_checksum_v2` 对应。
     *
     * 前五类的**拼接顺序与逐条序列化完全同 [compute]**，只在其后追加三个上云实体
     * （savings_goals|scheduled_rules|chat_personas）——两个算法共用 [computeOver]，
     * 不会各自漂移。
     *
     * 服务端对旧客户端仍发 legacy 的 `checksum`（五类）；本方法只在响应带
     * `checksum_v2` 时使用。
     */
    fun computeV2(
        books: List<Map<String, Any?>>,
        categories: List<Map<String, Any?>>,
        accounts: List<Map<String, Any?>>,
        transactions: List<Map<String, Any?>>,
        budgets: List<Map<String, Any?>>,
        savingsGoals: List<Map<String, Any?>>,
        scheduledRules: List<Map<String, Any?>>,
        chatPersonas: List<Map<String, Any?>>,
        cursor: Long
    ): String = computeOver(
        listOf(
            books, categories, accounts, transactions, budgets,
            savingsGoals, scheduledRules, chatPersonas,
        ),
        cursor,
    )

    /**
     * T-M7-05j: 九类实体 + cursor 的 checksum，与服务端
     * `compute_bootstrap_checksum_v3` 对应。
     *
     * 前八类的拼接顺序与逐条序列化完全同 [computeV2]，只在其后追加
     * `user_preferences` —— 三个算法共用 [computeOver]，不会各自漂移。
     * 偏好列表为空时**与 [computeV2] 逐字节相同**（[computeOver] 对空列表不产出
     * 分段），故没写过偏好的账号两条车道算出同一个值。
     *
     * 车道选择：服务端同时下发 v2（八类）与 v3（九类）；当前客户端用 v3，读不到
     * v3 时才回退 v2（见 SyncEngine.runBootstrap）。
     */
    fun computeV3(
        books: List<Map<String, Any?>>,
        categories: List<Map<String, Any?>>,
        accounts: List<Map<String, Any?>>,
        transactions: List<Map<String, Any?>>,
        budgets: List<Map<String, Any?>>,
        savingsGoals: List<Map<String, Any?>>,
        scheduledRules: List<Map<String, Any?>>,
        chatPersonas: List<Map<String, Any?>>,
        userPreferences: List<Map<String, Any?>>,
        cursor: Long
    ): String = computeOver(
        listOf(
            books, categories, accounts, transactions, budgets,
            savingsGoals, scheduledRules, chatPersonas, userPreferences,
        ),
        cursor,
    )

    /** 按给定顺序拼接各列表的确定性 JSON，末尾追加 `cursor:{cursor}`，取 SHA-256。 */
    private fun computeOver(
        entityLists: List<List<Map<String, Any?>>>,
        cursor: Long,
    ): String {
        val parts = mutableListOf<String>()
        for (items in entityLists) {
            val sorted = items.sortedBy { it["id"] as? String ?: "" }
            for (item in sorted) {
                parts += mapToJson(item)
            }
        }
        parts += "cursor:$cursor"
        return sha256Hex(parts.joinToString("|"))
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
     * 将 Map<String, Any?> 序列化为确定性 JSON（键按字母序，紧凑格式）。
     *
     * 与服务端 json.dumps(obj, sort_keys=True, separators=(",", ":")) 等价 —— 关键是
     * **按值的真实类型**出字面量，而不是看内容像什么。
     */
    private fun mapToJson(map: Map<String, Any?>): String {
        val sb = StringBuilder()
        sb.append('{')
        val sortedKeys = map.keys.sorted()
        sortedKeys.forEachIndexed { index, key ->
            if (index > 0) sb.append(',')
            sb.append('"').append(escapeJsonString(key)).append('"')
            sb.append(':')
            sb.append(jsonValue(map[key]))
        }
        sb.append('}')
        return sb.toString()
    }

    /**
     * 单个值 → JSON 字面量。类型由 [SyncPayloadMapper.rawEntityMap] 从 JSON 保留下来：
     * Number（服务端的 int）与 Boolean 不加引号，其余（含所有字符串字段）加引号并转义。
     *
     * T-M7-01 修复：此前对字符串值做内容推断，"2026"（存钱目标名）出裸数字 `2026`、
     * "true" 出布尔 `true`，而服务端对 str 恒出带引号形式 → bootstrap checksum 恒不等
     * → SyncEngine 判 RETRYABLE 死循环。改为按类型出字面量后此路径不再依赖内容。
     */
    private fun jsonValue(value: Any?): String = when (value) {
        null -> "null"
        is Boolean -> if (value) "true" else "false"
        is Number -> value.toString()
        else -> '"' + escapeJsonString(value.toString()) + '"'
    }

    /**
     * 转义 JSON 字符串中的特殊字符，**逐字节对齐 Python `json.dumps` 的默认口径**
     * （`ensure_ascii=True`）——服务端 `compute_bootstrap_checksum` 用的就是它。
     *
     * P3-3 修复：预分配 StringBuilder 容量 + 用查表/字符串拼接替代 String.format
     * （每字符一次 format 调用在大 bootstrap 数据量下是热点）。
     *
     * T-M7-01 修复：此前只转义 `" \ \b \n \r \t`，**非 ASCII 与其余控制字符原样透传**，
     * 而 Python 会把它们转成 `\uXXXX`（星平面字符按 UTF-16 代理对逐个转义）。
     * 于是任何含中文的实体（默认账本「默认账本」、分类「餐饮」、备注……）都会让
     * 客户端重算值与服务端声明值不等 → bootstrap 判 RETRYABLE 死循环。纯 ASCII
     * 数据不受影响，故该缺陷长期未被既有测试发现。
     */
    private fun escapeJsonString(s: String): String {
        val sb = StringBuilder(s.length)
        for (c in s) {
            when (c) {
                '"' -> sb.append("\\\"")
                '\\' -> sb.append("\\\\")
                '\b' -> sb.append("\\b")
                '' -> sb.append("\\f")
                '\n' -> sb.append("\\n")
                '\r' -> sb.append("\\r")
                '\t' -> sb.append("\\t")
                else -> {
                    val code = c.code
                    // 0x7F(DEL) 及以上的非 ASCII 与控制字符一律 \uXXXX（小写十六进制）；
                    // Python 对生僻字/emoji 输出的代理对 \udXXX\udXXX 由逐 Char 迭代自然产生。
                    if (code < 0x20 || code >= 0x7F) {
                        sb.append("\\u")
                        sb.append(HEX_CHARS[(code shr 12) and 0x0F])
                        sb.append(HEX_CHARS[(code shr 8) and 0x0F])
                        sb.append(HEX_CHARS[(code shr 4) and 0x0F])
                        sb.append(HEX_CHARS[code and 0x0F])
                    } else {
                        sb.append(c)
                    }
                }
            }
        }
        return sb.toString()
    }

    private val HEX_CHARS = "0123456789abcdef".toCharArray()
}
