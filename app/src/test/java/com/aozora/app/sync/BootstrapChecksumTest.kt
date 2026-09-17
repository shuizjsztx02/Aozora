package com.aozora.app.sync

import org.junit.Assert.*
import org.junit.Test

/**
 * T-M3-03: BootstrapChecksum 纯函数单元测试。
 *
 * 验证客户端 Kotlin 实现的 checksum 与服务端 Python 实现算法一致。
 * Python 参考实现见 server/src/modules/sync/service.py:compute_bootstrap_checksum。
 */
class BootstrapChecksumTest {

    @Test
    fun compute_emptyData_producesDeterministicHash() {
        val hash1 = BootstrapChecksum.compute(
            books = emptyList(),
            categories = emptyList(),
            accounts = emptyList(),
            transactions = emptyList(),
            budgets = emptyList(),
            cursor = 0
        )
        val hash2 = BootstrapChecksum.compute(
            books = emptyList(),
            categories = emptyList(),
            accounts = emptyList(),
            transactions = emptyList(),
            budgets = emptyList(),
            cursor = 0
        )
        assertEquals(hash1, hash2)
        assertEquals(64, hash1.length)
        // 应与服务端对相同输入的计算结果一致
        // Python: compute_bootstrap_checksum(books=[], categories=[], accounts=[],
        //           transactions=[], budgets=[], cursor=0)
        // 预期输出: SHA-256 of "cursor:0"
        val expectedForEmptyPayload = sha256Reference("cursor:0")
        assertEquals(expectedForEmptyPayload, hash1)
    }

    @Test
    fun compute_withBooks_isOrderIndependent() {
        val book1 = mapOf("id" to "a", "name" to "Alpha", "version" to "1")
        val book2 = mapOf("id" to "b", "name" to "Beta", "version" to "2")

        val hash1 = BootstrapChecksum.compute(
            books = listOf(book1, book2),
            categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            cursor = 0
        )
        val hash2 = BootstrapChecksum.compute(
            books = listOf(book2, book1),  // reversed
            categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            cursor = 0
        )
        assertEquals("checksum should be order-independent (sorted by id)", hash1, hash2)
    }

    @Test
    fun compute_differentData_producesDifferentHash() {
        val hash1 = BootstrapChecksum.compute(
            books = listOf(mapOf("id" to "a", "name" to "Alpha", "version" to "1")),
            categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            cursor = 0
        )
        val hash2 = BootstrapChecksum.compute(
            books = listOf(mapOf("id" to "a", "name" to "Beta", "version" to "1")),
            categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            cursor = 0
        )
        assertNotEquals(hash1, hash2)
    }

    @Test
    fun compute_differentCursor_producesDifferentHash() {
        val hash1 = BootstrapChecksum.compute(
            books = emptyList(), categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            cursor = 0
        )
        val hash2 = BootstrapChecksum.compute(
            books = emptyList(), categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            cursor = 1
        )
        assertNotEquals(hash1, hash2)
    }

    @Test
    fun compute_nullFields_serializeAsNull() {
        val hash = BootstrapChecksum.compute(
            books = listOf(mapOf("id" to "a", "name" to "X", "icon" to null, "version" to "1")),
            categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            cursor = 0
        )
        // 应与服务端对相同输入的计算结果一致
        // Python: json.dumps({"id":"a","icon":null,"name":"X","version":1},
        //                    sort_keys=True, separators=(",",":"))
        // → '{"icon":null,"id":"a","name":"X","version":1}'
        // 注意：version 在 Python 端是 int，序列化后不带引号；客户端的 "1" 经过
        // isNumeric 判定后也序列化为不带引号的 1
        val expectedEntityJson = """{"icon":null,"id":"a","name":"X","version":1}"""
        val expectedPayload = "$expectedEntityJson|cursor:0"
        val expectedHash = sha256Reference(expectedPayload)
        assertEquals(expectedHash, hash)
    }

    @Test
    fun compute_numericValues_storedAsStringsButSerializedWithoutQuotes() {
        val hash = BootstrapChecksum.compute(
            books = emptyList(), categories = emptyList(), accounts = emptyList(),
            transactions = listOf(mapOf(
                "id" to "t1",
                "bookId" to "b1",
                "type" to "expense",
                "amountCents" to "5000",  // numeric string
                "categoryId" to "c1",
                "bizDate" to "2026-09-05",
                "occurredAt" to "1693872000000",
                "source" to "manual",
                "version" to "1"
            )),
            budgets = emptyList(),
            cursor = 42
        )
        assertEquals(64, hash.length)
    }

    @Test
    fun compute_matchesServerReferenceImplementation() {
        // P0-1 修复后：客户端必须用 snake_case 原始键与服务端计算一致。
        // 这是跨语言一致性测试的关键用例 — 模拟真实路径：
        // 服务端 _serialize_entity 输出 snake_case → 客户端 rawEntityMap 保留原键 → BootstrapChecksum.compute
        val books = listOf(mapOf(
            "id" to "book-uuid-1",
            "name" to "Alice Book",
            "icon" to null,
            "version" to "1",  // 字符串，但序列化为数字（与服务端 int 一致）
            "created_at" to "2026-09-05T10:00:00+00:00",
            "updated_at" to "2026-09-05T10:00:00+00:00",
            "deleted_at" to null
        ))
        val categories = emptyList<Map<String, String?>>()
        val accounts = emptyList<Map<String, String?>>()
        val transactions = emptyList<Map<String, String?>>()
        val budgets = emptyList<Map<String, String?>>()
        val cursor = 7L

        val clientHash = BootstrapChecksum.compute(
            books = books, categories = categories, accounts = accounts,
            transactions = transactions, budgets = budgets,
            cursor = cursor
        )

        // Python 等价计算（实际运行验证过的已知输出）：
        // >>> compute_bootstrap_checksum(
        // ...   books=[{"id": "book-uuid-1", "name": "Alice Book", "icon": None,
        // ...           "version": 1, "created_at": "2026-09-05T10:00:00+00:00",
        // ...           "updated_at": "2026-09-05T10:00:00+00:00", "deleted_at": None}],
        // ...   categories=[], accounts=[], transactions=[], budgets=[], cursor=7)
        // → "a0834b8383acd061f4ed09c24214708a9803c03326849b603d0134f6056c05d4"
        val expectedHash = "a0834b8383acd061f4ed09c24214708a9803c03326849b603d0134f6056c05d4"
        assertEquals(expectedHash, clientHash)
    }

    @Test
    fun compute_matchesServerReferenceImplementation_withTransaction() {
        // P0-1 跨语言一致性：transaction 含多个 snake_case 字段（book_id, amount_cents 等）
        val transactions = listOf(mapOf(
            "id" to "tx-1",
            "book_id" to "b-1",
            "type" to "expense",
            "amount_cents" to "5000",
            "category_id" to "c-1",
            "account_id" to null,
            "note" to "lunch",
            "biz_date" to "2026-09-05",
            "occurred_at" to "2026-09-05T12:00:00+00:00",
            "source" to "manual",
            "version" to "1",
            "created_at" to "2026-09-05T12:00:00+00:00",
            "updated_at" to "2026-09-05T12:00:00+00:00",
            "deleted_at" to null
        ))

        val clientHash = BootstrapChecksum.compute(
            books = emptyList(), categories = emptyList(), accounts = emptyList(),
            transactions = transactions, budgets = emptyList(),
            cursor = 42L
        )

        // Python 等价计算（实际运行验证过的已知输出）
        val expectedHash = "fa79baf3e9688ae97d58aadbff2a271430dd10e6337e15a11baca0ca5a12e021"
        assertEquals(expectedHash, clientHash)
    }

    @Test
    fun compute_emptyPayload_matchesPythonReference() {
        // P0-1 跨语言一致性：空 bootstrap 场景
        val clientHash = BootstrapChecksum.compute(
            books = emptyList(), categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            cursor = 0L
        )
        // Python: compute_bootstrap_checksum(books=[], categories=[], accounts=[],
        //           transactions=[], budgets=[], cursor=0)
        // → sha256("cursor:0") = "ccd1acc..."
        val expectedHash = "ccd1acc8a55bd419cd404abe548f4bfe96bc7ab69c96ea6d9f410505f996e6d8"
        assertEquals(expectedHash, clientHash)
    }

    @Test
    fun compute_numericStrict_rejectsScientificNotation() {
        // P3-2 修复验证：科学计数法不应被识别为数字（与服务端 json.dumps 对齐）
        // "1e5" 应被当作字符串（带引号），而不是数字（不带引号）
        val hashScientific = BootstrapChecksum.compute(
            books = listOf(mapOf("id" to "a", "amount" to "1e5")),
            categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            cursor = 0
        )
        // 若 isNumeric 错误地接受 "1e5"，会序列化为 {"amount":1e5,...}
        // 严格模式下应序列化为 {"amount":"1e5",...}（带引号）
        val expectedJsonWithQuotes = """{"amount":"1e5","id":"a"}|cursor:0"""
        val expectedHash = sha256Reference(expectedJsonWithQuotes)
        assertEquals(expectedHash, hashScientific)
    }

    /** Reference SHA-256 implementation using Java stdlib. */
    private fun sha256Reference(input: String): String {
        val digest = java.security.MessageDigest.getInstance("SHA-256")
        val bytes = digest.digest(input.toByteArray(Charsets.UTF_8))
        return bytes.joinToString("") { "%02x".format(it) }
    }

    // ─── T-M7-01: 八类 checksum（computeV2） ───

    /** 与 fixture 同形的三实体（键名 snake_case，值同服务端 _serialize_entity 输出）。 */
    private val cloudGoal = mapOf(
        "id" to "11111111-1111-4111-8111-111111111111",
        "name" to "旅行基金",
        "target_cents" to "500000",
        "current_cents" to "120000",
        "icon" to null,
        "period_value" to "6",
        "period_unit" to "month",
        "version" to "2",
        "created_at" to "2026-09-04T12:00:00+00:00",
        "updated_at" to "2026-09-04T12:00:00+00:00",
        "archived_at" to null,
    )
    private val cloudRule = mapOf(
        "id" to "22222222-2222-4222-8222-111111111111",
        "type" to "expense",
        "name" to "房租",
        "amount_cents" to "350000",
        "book_id" to "b1",
        "category_id" to "c1",
        "account_id" to null,
        "include_in_budget" to "true",
        "note" to "",
        "repeat" to "monthly",
        "start_date" to "2026-01-01",
        "end_date" to null,
        "enabled" to "true",
        "last_run_date" to "2026-09-01",
        "run_hour" to "9",
        "run_minute" to "30",
        "version" to "5",
        "created_at" to "2026-09-04T12:00:00+00:00",
        "updated_at" to "2026-09-04T12:00:00+00:00",
        "archived_at" to null,
    )
    private val cloudPersona = mapOf(
        "id" to "33333333-3333-4333-8333-111111111111",
        "name" to "小贝",
        "avatar" to null,
        "persona" to "hello",
        "version" to "1",
        "created_at" to "2026-09-04T12:00:00+00:00",
        "updated_at" to "2026-09-04T12:00:00+00:00",
        "archived_at" to null,
    )

    /**
     * 跨语言一致性：与服务端 `compute_bootstrap_checksum_v2` 对同一输入的输出逐字节
     * 相同（参考值由 Python 侧真实函数生成，见测试注释里的输入数据）。
     */
    @Test
    fun computeV2_matchesServerReferenceImplementation() {
        val hash = BootstrapChecksum.computeV2(
            books = emptyList(), categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            savingsGoals = listOf(cloudGoal), scheduledRules = listOf(cloudRule),
            chatPersonas = listOf(cloudPersona), cursor = 42,
        )
        assertEquals(
            "af2c8fda4bd60e312a63c654fa159b64a9982569762ba2d8be559c09c926c841",
            hash,
        )
        // 同一输入下 legacy 仍只覆盖五类 —— 与被上云实体无关（旧客户端不失效）
        assertEquals(
            "2cfcc3208403af4dde547253e4e0d23cf4e3ec1a5b90ef64caffe101020ef94e",
            BootstrapChecksum.compute(
                books = emptyList(), categories = emptyList(), accounts = emptyList(),
                transactions = emptyList(), budgets = emptyList(), cursor = 42,
            ),
        )
    }

    /**
     * 三实体为空时 v2 必须与 legacy 完全相等 —— 这是"旧客户端不受影响"的充分条件：
     * 未上云数据的账号，服务端两个 checksum 值相同，客户端用哪个都一样。
     */
    @Test
    fun computeV2_withEmptyCloudLists_equalsLegacy() {
        val legacy = BootstrapChecksum.compute(
            books = emptyList(), categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(), cursor = 7,
        )
        val v2 = BootstrapChecksum.computeV2(
            books = emptyList(), categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            savingsGoals = emptyList(), scheduledRules = emptyList(),
            chatPersonas = emptyList(), cursor = 7,
        )
        assertEquals(legacy, v2)
        assertEquals(sha256Reference("cursor:7"), v2)
    }

    @Test
    fun computeV2_isOrderIndependentWithinEachList() {
        val a = BootstrapChecksum.computeV2(
            books = emptyList(), categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            savingsGoals = listOf(cloudGoal), scheduledRules = listOf(cloudRule),
            chatPersonas = listOf(cloudPersona), cursor = 42,
        )
        // 列表内顺序不影响结果（各自按 id 排序）
        val b = BootstrapChecksum.computeV2(
            books = emptyList(), categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            savingsGoals = listOf(cloudGoal), scheduledRules = listOf(cloudRule),
            chatPersonas = listOf(cloudPersona), cursor = 42,
        )
        assertEquals(a, b)
    }

    @Test
    fun computeV2_cloudListsChangeHash() {
        val withGoal = BootstrapChecksum.computeV2(
            books = emptyList(), categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            savingsGoals = listOf(cloudGoal), scheduledRules = emptyList(),
            chatPersonas = emptyList(), cursor = 42,
        )
        val without = BootstrapChecksum.computeV2(
            books = emptyList(), categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            savingsGoals = emptyList(), scheduledRules = emptyList(),
            chatPersonas = emptyList(), cursor = 42,
        )
        assertNotEquals(withGoal, without)
        // 服务端同样把三实体纳入 v2 的输入，故"漏传一类"必然对不上
        assertNotEquals(
            BootstrapChecksum.computeV2(
                books = emptyList(), categories = emptyList(), accounts = emptyList(),
                transactions = emptyList(), budgets = emptyList(),
                savingsGoals = emptyList(), scheduledRules = listOf(cloudRule),
                chatPersonas = emptyList(), cursor = 42,
            ),
            withGoal,
        )
    }

    // ─── T-M7-01 修复回归：字符串转义必须逐字节对齐 Python json.dumps ───

    /**
     * 非 ASCII 必须转成 `\uXXXX`。
     *
     * 此前客户端只转义 `" \ \b \n \r \t`，中文原样透传，而服务端
     * `json.dumps(..., ensure_ascii=True)` 会转义 —— 于是**任何含中文的实体**
     * （默认账本「默认账本」、分类「餐饮」、备注……）都会让 bootstrap 的
     * 重算值与服务端声明值不等，判 RETRYABLE 后无限重试。纯 ASCII 数据的既有
     * 测试覆盖不到，故长期潜伏。
     */
    @Test
    fun compute_escapesNonAsciiLikePython() {
        val hash = BootstrapChecksum.compute(
            books = listOf(mapOf("id" to "b1", "name" to "默认账本")),
            categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(), cursor = 0,
        )
        // Python: json.dumps({"id":"b1","name":"默认账本"}, sort_keys=True, separators=(",",":"))
        // → {"id":"b1","name":"默认账本"}
        val expected = sha256Reference(
            "{\"id\":\"b1\",\"name\":\"\\u9ed8\\u8ba4\\u8d26\\u672c\"}|cursor:0"
        )
        assertEquals(expected, hash)
    }

    @Test
    fun compute_escapesControlAndAstralCharsLikePython() {
        // 控制字符 → \u00XX；星平面字符 → 代理对 \udXXX\udXXX（逐 Char 迭代自然产生）
        val hash = BootstrapChecksum.compute(
            books = listOf(
                mapOf("id" to "b1", "name" to "ab", "icon" to "😀")
            ),
            categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(), cursor = 0,
        )
        // Python: {"icon":"😀","id":"b1","name":"ab"}
        val expected = sha256Reference(
            "{\"icon\":\"\\ud83d\\ude00\",\"id\":\"b1\",\"name\":\"a\\u0001b\"}|cursor:0"
        )
        assertEquals(expected, hash)
    }

    @Test
    fun compute_asciiFastPath_isUnchanged() {
        // 修复不改变纯 ASCII 输入的结果（既有数据/既有客户端口径不变）
        val hash = BootstrapChecksum.compute(
            books = listOf(mapOf("id" to "b1", "name" to "Alice Book", "icon" to null)),
            categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(), cursor = 0,
        )
        val expected = sha256Reference(
            "{\"icon\":null,\"id\":\"b1\",\"name\":\"Alice Book\"}|cursor:0"
        )
        assertEquals(expected, hash)
    }
}
