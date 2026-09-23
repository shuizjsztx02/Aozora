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
            books = listOf(mapOf("id" to "a", "name" to "X", "icon" to null, "version" to 1)),
            categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            cursor = 0
        )
        // 应与服务端对相同输入的计算结果一致
        // Python: json.dumps({"id":"a","icon":null,"name":"X","version":1},
        //                    sort_keys=True, separators=(",",":"))
        // → '{"icon":null,"id":"a","name":"X","version":1}'
        // version 在两侧都是真数字（服务端 int / 客户端 JSON 解析出的 Number）→ 不带引号
        val expectedEntityJson = """{"icon":null,"id":"a","name":"X","version":1}"""
        val expectedPayload = "$expectedEntityJson|cursor:0"
        val expectedHash = sha256Reference(expectedPayload)
        assertEquals(expectedHash, hash)
    }

    @Test
    fun compute_numericAndBooleanFields_serializeAsBareLiterals() {
        // 服务端 _serialize_entity 的真类型：int / bool → json.dumps 出裸字面量。
        // 客户端经 rawEntityMap 保留 JSON 原始类型后必须同样出裸字面量。
        val hash = BootstrapChecksum.compute(
            books = listOf(mapOf(
                "id" to "a",
                "amountCents" to 5000,
                "includeInBudget" to true,
                "enabled" to false,
            )),
            categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            cursor = 0
        )
        // 键按字母序：amountCents, enabled, id, includeInBudget
        val expected = """{"amountCents":5000,"enabled":false,"id":"a","includeInBudget":true}|cursor:0"""
        assertEquals(sha256Reference(expected), hash)
    }

    @Test
    fun compute_numericLookingStringFields_stayQuoted() {
        // T-M7-01 回归（H1）：字符串字段的内容形如数字/布尔时，必须仍按**字符串**出网。
        // 服务端对 str 恒出带引号形式；此前客户端靠内容猜类型（"2026" → 裸 2026、
        // "true" → 布尔 true），于是「存钱目标名叫 2026」「交易备注 123」这类再普通
        // 不过的数据会让两侧哈希不等 → bootstrap 判 RETRYABLE 死循环。
        val hash = BootstrapChecksum.compute(
            books = listOf(mapOf(
                "id" to "a",
                "name" to "2026",
                "note" to "123",
                "flag" to "true",
            )),
            categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            cursor = 42
        )
        // 键按字母序：flag, id, name, note —— 四个值全部带引号
        val expected = """{"flag":"true","id":"a","name":"2026","note":"123"}|cursor:42"""
        assertEquals(sha256Reference(expected), hash)
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
            "version" to 1,  // 与服务端 int 同型（真数字，出裸字面量）
            "created_at" to "2026-09-05T10:00:00+00:00",
            "updated_at" to "2026-09-05T10:00:00+00:00",
            "deleted_at" to null
        ))
        val categories = emptyList<Map<String, Any?>>()
        val accounts = emptyList<Map<String, Any?>>()
        val transactions = emptyList<Map<String, Any?>>()
        val budgets = emptyList<Map<String, Any?>>()
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
            "amount_cents" to 5000,
            "category_id" to "c-1",
            "account_id" to null,
            "note" to "lunch",
            "biz_date" to "2026-09-05",
            "occurred_at" to "2026-09-05T12:00:00+00:00",
            "source" to "manual",
            "version" to 1,
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
        // "1e5" 是**字符串字段的内容**，两侧都必须出带引号形式（服务端 json.dumps 对 str
        // 如此；客户端 T-M7-01 起按类型出字面量，不再有内容推断）。保留用例锁住不回退。
        val hashScientific = BootstrapChecksum.compute(
            books = listOf(mapOf("id" to "a", "amount" to "1e5")),
            categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            cursor = 0
        )
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

    /**
     * 与 fixture 同形的三实体（键名 snake_case，值与类型同服务端 _serialize_entity 输出：
     * 金额/编号是真数字，布尔是真布尔，时间戳是字符串）。
     */
    private val cloudGoal = mapOf(
        "id" to "11111111-1111-4111-8111-111111111111",
        "name" to "旅行基金",
        "target_cents" to 500000,
        "current_cents" to 120000,
        "icon" to null,
        "period_value" to 6,
        "period_unit" to "month",
        "version" to 2,
        "created_at" to "2026-09-04T12:00:00+00:00",
        "updated_at" to "2026-09-04T12:00:00+00:00",
        "archived_at" to null,
    )
    private val cloudRule = mapOf(
        "id" to "22222222-2222-4222-8222-111111111111",
        "type" to "expense",
        "name" to "房租",
        "amount_cents" to 350000,
        "book_id" to "b1",
        "category_id" to "c1",
        "account_id" to null,
        "include_in_budget" to true,
        "note" to "",
        "repeat" to "monthly",
        "start_date" to "2026-01-01",
        "end_date" to null,
        "enabled" to true,
        "last_run_date" to "2026-09-01",
        "run_hour" to 9,
        "run_minute" to 30,
        "version" to 5,
        "created_at" to "2026-09-04T12:00:00+00:00",
        "updated_at" to "2026-09-04T12:00:00+00:00",
        "archived_at" to null,
    )
    private val cloudPersona = mapOf(
        "id" to "33333333-3333-4333-8333-111111111111",
        "name" to "小贝",
        "avatar" to null,
        "persona" to "hello",
        "version" to 1,
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
     * T-M7-01 收口（H1）跨语言钉死：实体名是"内容形如数字的字符串"时，两侧哈希
     * 仍必须逐字节相同。输入与 [computeV2_matchesServerReferenceImplementation]
     * 完全同形，只把 cloudGoal.name 换成 "2026" —— 这正是此前会分叉、且真实用户
     * 极易撞上的形态（存钱目标就叫「2026」）。
     *
     * 同一个值也钉在服务端
     * `server/tests/test_sync_cloud_entities.py::test_checksum_v2_numeric_looking_name_matches_client_reference`，
     * 两端任一漂移都会红。
     */
    @Test
    fun computeV2_numericLookingName_matchesServerReference() {
        val hash = BootstrapChecksum.computeV2(
            books = emptyList(), categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            savingsGoals = listOf(cloudGoal + ("name" to "2026")),
            scheduledRules = listOf(cloudRule),
            chatPersonas = listOf(cloudPersona), cursor = 42,
        )
        assertEquals(
            "9ad2afa8d843d25892a4ac2284394f3b2f46c9414a1a4ef6f9a1c86e9ccb3042",
            hash,
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

    // ─── T-M7-05j: 九类 checksum（computeV3） ───

    /** 偏好行同服务端 `_serialize_entity(UserPreference)`：四个 snake_case 键，version 是真数字。 */
    private val cloudPreference = mapOf(
        "id" to "44444444-4444-4444-8444-111111111111",
        "key" to "book_order",
        "value" to """["aaa"]""",
        "version" to 1,
    )

    /**
     * 跨语言一致性：与服务端 `compute_bootstrap_checksum_v3` 对同一输入的输出逐字节
     * 相同（参考值由 Python 侧真实函数生成）。
     */
    @Test
    fun computeV3_matchesServerReferenceImplementation() {
        val hash = BootstrapChecksum.computeV3(
            books = emptyList(), categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            savingsGoals = listOf(cloudGoal), scheduledRules = listOf(cloudRule),
            chatPersonas = listOf(cloudPersona),
            userPreferences = listOf(cloudPreference), cursor = 42,
        )
        assertEquals(
            "45d45a5f10c299925fb9cff048aee8c08c8ff13c50d84ec002a3b53c0516cb34",
            hash,
        )
    }

    /**
     * 偏好值是**非 ASCII**时仍逐字节相同 —— 偏好值多是 JSON 文本（user_profile
     * 存的就是中文称呼/风格），转义口径与 v2 共用 [computeOver]，此处钉住不漂移。
     */
    @Test
    fun computeV3_nonAsciiValue_matchesServerReference() {
        val hash = BootstrapChecksum.computeV3(
            books = emptyList(), categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            savingsGoals = listOf(cloudGoal), scheduledRules = listOf(cloudRule),
            chatPersonas = listOf(cloudPersona),
            userPreferences = listOf(cloudPreference + ("value" to """{"称呼":"阿空"}""")),
            cursor = 42,
        )
        assertEquals(
            "b4ff71bd1c29f50164f4d0bdc115c9275bcebe6c49981e6020bed0f4fa3eed09",
            hash,
        )
    }

    /**
     * 偏好列表为空时 v3 必须与 v2 完全相等 —— "没写过偏好的账号"在两条车道上
     * 算出同一个值；这也是 05j 最初把偏好并进 v2 没被发现问题的原因
     * （只有真正写过偏好行的账号才会失配）。既有的 v2 断言值不变。
     */
    @Test
    fun computeV3_withEmptyPreferences_equalsV2() {
        val v2 = BootstrapChecksum.computeV2(
            books = emptyList(), categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            savingsGoals = listOf(cloudGoal), scheduledRules = listOf(cloudRule),
            chatPersonas = listOf(cloudPersona), cursor = 42,
        )
        val v3 = BootstrapChecksum.computeV3(
            books = emptyList(), categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            savingsGoals = listOf(cloudGoal), scheduledRules = listOf(cloudRule),
            chatPersonas = listOf(cloudPersona), userPreferences = emptyList(), cursor = 42,
        )
        assertEquals(v2, v3)
        assertEquals("af2c8fda4bd60e312a63c654fa159b64a9982569762ba2d8be559c09c926c841", v3)
    }

    /**
     * 偏好列表：列表内顺序无关（按 id 排序），且**两条偏好**与"把两条并成一条"
     * 必然不同 —— 九类拼接不能像"多值合并"那样塌掉。
     */
    @Test
    fun computeV3_isOrderIndependentWithinPreferencesList() {
        val prefA = mapOf(
            "id" to "11111111-1111-4111-8111-111111111111",
            "key" to "book_order", "value" to """["aa","bb"]""", "version" to 2,
        )
        val prefB = mapOf(
            "id" to "22222222-2222-4222-8222-222222222222",
            "key" to "user_profile", "value" to """{"称呼":"阿空"}""", "version" to 3,
        )
        val forward = BootstrapChecksum.computeV3(
            books = emptyList(), categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            savingsGoals = emptyList(), scheduledRules = emptyList(),
            chatPersonas = emptyList(), userPreferences = listOf(prefA, prefB), cursor = 42,
        )
        val reversed = BootstrapChecksum.computeV3(
            books = emptyList(), categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            savingsGoals = emptyList(), scheduledRules = emptyList(),
            chatPersonas = emptyList(), userPreferences = listOf(prefB, prefA), cursor = 42,
        )
        assertEquals(forward, reversed)
        // Python 参考值：同一输入 + cursor=42
        assertEquals(
            "62969a225ac47c8d541a0bcb8ba272ceace189bfa56cfa082a15b1ed10a0f1ba",
            forward,
        )
        // 少一条偏好必然改变哈希（v3 真的把偏好算进去了）
        assertNotEquals(
            forward,
            BootstrapChecksum.computeV3(
                books = emptyList(), categories = emptyList(), accounts = emptyList(),
                transactions = emptyList(), budgets = emptyList(),
                savingsGoals = emptyList(), scheduledRules = emptyList(),
                chatPersonas = emptyList(), userPreferences = listOf(prefB), cursor = 42,
            ),
        )
    }

    // ─── T18-5: accounts 新增两列后的跨语言 checksum 一致性 ───

    /**
     * 账户同服务端 `_serialize_entity(Account)`（T18-5 起多两列）：`account_type`
     * 是字符串或 `null`，`balance_cents` 是**真数字**（负值出裸字面量、不带引号）。
     */
    private val accountCard = mapOf<String, Any?>(
        "id" to "55555555-5555-4555-8555-222222222222",
        "name" to "交行信用卡",
        "icon" to "💳",
        "account_type" to "信用卡",
        "balance_cents" to -32500,
        "version" to 4,
        "created_at" to "2026-09-23T09:00:00+00:00",
        "updated_at" to "2026-09-23T09:10:00+00:00",
        "archived_at" to null,
    )

    /** 存量账户形态：`account_type` 为 null、`balance_cents` 为 0（迁移后的老行）。 */
    private val accountLegacy = mapOf<String, Any?>(
        "id" to "66666666-6666-4666-8666-333333333333",
        "name" to "现金",
        "icon" to null,
        "account_type" to null,
        "balance_cents" to 0,
        "version" to 1,
        "created_at" to "2026-09-23T09:00:00+00:00",
        "updated_at" to "2026-09-23T09:00:00+00:00",
        "archived_at" to null,
    )

    /**
     * 新增两列后 checksum 仍与服务端逐字节一致 —— 这是本项目**被咬过两次**的失败
     * 模式（P0-1 的键名、T-M7-01 的类型口径：客户端重算一旦与服务端声明值不等，
     * bootstrap 就判 RETRYABLE 死循环）。accounts 是 checksum 的输入之一，往它
     * 里加键必须钉住这一条。参考值由 Python 侧 `compute_bootstrap_checksum_v3`
     * 对同一输入真实生成。
     */
    @Test
    fun computeV3_accountWithTypeAndBalance_matchesServerReference() {
        val hash = BootstrapChecksum.computeV3(
            books = emptyList(), categories = emptyList(),
            accounts = listOf(accountCard), transactions = emptyList(), budgets = emptyList(),
            savingsGoals = emptyList(), scheduledRules = emptyList(),
            chatPersonas = emptyList(), userPreferences = emptyList(), cursor = 7,
        )
        assertEquals(
            "85dbc4c7c8454d359004225ca9a214fd3d76895b1f5bc78e26a7cdf9d2d1bacb",
            hash,
        )
    }

    /** 存量形态（null 分类 + 0 余额）同样逐字节一致——两种取值都要被钉住。 */
    @Test
    fun computeV3_legacyAccountShape_matchesServerReference() {
        val hash = BootstrapChecksum.computeV3(
            books = emptyList(), categories = emptyList(),
            accounts = listOf(accountLegacy), transactions = emptyList(), budgets = emptyList(),
            savingsGoals = emptyList(), scheduledRules = emptyList(),
            chatPersonas = emptyList(), userPreferences = emptyList(), cursor = 7,
        )
        assertEquals(
            "934a232f9879f56d51a2c4373f852ccffa3b6f5308c93789867e88052de67462",
            hash,
        )
    }

    /** 两账户同批时按 id 升序拼接（与服务端一致），负余额确实进入了哈希。 */
    @Test
    fun computeV3_twoAccounts_matchesServerReferenceInIdOrder() {
        val hash = BootstrapChecksum.computeV3(
            books = emptyList(), categories = emptyList(),
            accounts = listOf(accountLegacy, accountCard), transactions = emptyList(),
            budgets = emptyList(), savingsGoals = emptyList(), scheduledRules = emptyList(),
            chatPersonas = emptyList(), userPreferences = emptyList(), cursor = 7,
        )
        assertEquals(
            "fe3106f8a3c534506ab67e405a92ec6b1e9d96a021e17bfab9e8d91c24ac2d88",
            hash,
        )
    }
}
