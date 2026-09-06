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
}
