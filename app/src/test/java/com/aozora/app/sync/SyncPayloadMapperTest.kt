package com.aozora.app.sync

import com.aozora.app.db.entity.PendingMutationEntity
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

/**
 * T-M3-02: SyncPayloadMapper 纯函数单元测试（JVM 可测，无 Android 依赖）。
 */
class SyncPayloadMapperTest {

    @Test
    fun toMutationWire_insert_hasNoExpectedVersion() {
        val pm = PendingMutationEntity(
            mutationId = "m1",
            entityType = "transaction",
            entityId = "t1",
            operation = PendingMutationEntity.OP_INSERT,
            payload = """{"id":"t1","bookId":"b1","type":"expense","amountCents":100,"categoryId":"c1","bizDate":"2026-09-05","occurredAt":1,"source":"manual","version":1}""",
            status = PendingMutationEntity.STATUS_PENDING,
            retryCount = 0,
            createdAt = 1000L,
            expectedVersion = null,
            lastError = null
        )
        val wire = SyncPayloadMapper.toMutationWire(pm)
        assertEquals("m1", wire.mutationId)
        assertEquals("transaction", wire.entityType)
        assertEquals("t1", wire.entityId)
        assertEquals("insert", wire.operation)
        assertNull(wire.expectedVersion)
        assertEquals(pm.payload, wire.payload)
    }

    @Test
    fun toMutationWire_update_carriesExpectedVersion() {
        val pm = PendingMutationEntity(
            mutationId = "m2",
            entityType = "budget",
            entityId = "bud1",
            operation = PendingMutationEntity.OP_UPDATE,
            payload = """{"id":"bud1","bookId":"b1","budgetType":"monthly","categoryId":null,"period":"2026-09","amountCents":200,"version":2}""",
            status = PendingMutationEntity.STATUS_PENDING,
            retryCount = 0,
            createdAt = 2000L,
            expectedVersion = 1,
            lastError = null
        )
        val wire = SyncPayloadMapper.toMutationWire(pm)
        assertEquals("update", wire.operation)
        assertEquals(1, wire.expectedVersion)
    }

    @Test
    fun toMutationWire_archiveMappedToDeleteOnWire() {
        // NEW-1: 本地 OP_ARCHIVE 在 wire 层必须映射为 "delete"，
        // 因为服务端 _apply_mutation 仅识别 insert/update/delete；
        // 对 account/category，_apply_delete 会设置 archived_at（归档语义）。
        val pm = PendingMutationEntity(
            mutationId = "m3",
            entityType = "account",
            entityId = "a1",
            operation = PendingMutationEntity.OP_ARCHIVE,
            payload = "{}",
            status = PendingMutationEntity.STATUS_PENDING,
            retryCount = 0,
            createdAt = 3000L,
            expectedVersion = 5,
            lastError = null
        )
        val wire = SyncPayloadMapper.toMutationWire(pm)
        assertEquals("delete", wire.operation)
        assertEquals(5, wire.expectedVersion)
    }

    @Test
    fun snakeToCamelMap_convertsBasicFields() {
        val snake = JSONObject()
            .put("id", "x1")
            .put("book_id", "b1")
            .put("owner_id", "u1")
            .put("amount_cents", 100L)
            .put("note", "hello")
        val out = SyncPayloadMapper.snakeToCamelMap(snake)
        assertEquals("x1", out["id"])
        assertEquals("b1", out["bookId"])
        assertEquals("u1", out["ownerId"])
        assertEquals("100", out["amountCents"])
        assertEquals("hello", out["note"])
    }

    @Test
    fun snakeToCamelMap_handlesNullAndUnknownKeys() {
        val snake = JSONObject()
            .put("id", "x1")
            .put("category_id", JSONObject.NULL)
            .put("unknown_field", "value")
        val out = SyncPayloadMapper.snakeToCamelMap(snake)
        assertEquals("x1", out["id"])
        assertNull(out["categoryId"])
        // 未知字段透传（snake_case 原键）
        assertEquals("value", out["unknown_field"])
    }

    @Test
    fun parsePullResponse_parsesChangesAndCursor() {
        val json = """
            {
              "changes": [
                {
                  "entity_type": "transaction",
                  "entity_id": "t1",
                  "operation": "insert",
                  "version": 1,
                  "entity": {
                    "id": "t1",
                    "book_id": "b1",
                    "type": "expense",
                    "amount_cents": 500,
                    "category_id": "c1",
                    "biz_date": "2026-09-05",
                    "occurred_at": 1234567890,
                    "note": "lunch",
                    "source": "manual",
                    "version": 1
                  }
                },
                {
                  "entity_type": "budget",
                  "entity_id": "bud1",
                  "operation": "delete",
                  "version": 3,
                  "entity": null
                }
              ],
              "cursor": 42,
              "has_more": false
            }
        """.trimIndent()
        val response = SyncPayloadMapper.parsePullResponse(json)
        assertEquals(42L, response.nextCursor)
        assertEquals(2, response.changes.size)

        val tx = response.changes[0]
        assertEquals("transaction", tx.entityType)
        assertEquals("t1", tx.entityId)
        assertEquals(SyncChangeOp.INSERT, tx.operation)
        assertEquals(1, tx.version)
        assertEquals("500", tx.entityPayload["amountCents"])
        assertEquals("2026-09-05", tx.entityPayload["bizDate"])

        val budget = response.changes[1]
        assertEquals("budget", budget.entityType)
        assertEquals(SyncChangeOp.DELETE, budget.operation)
        assertEquals(3, budget.version)
        assertTrue(budget.entityPayload.isEmpty())
    }

    @Test
    fun parsePullResponse_skipsUnknownEntityTypes() {
        val json = """
            {
              "changes": [
                {"entity_type": "widget", "entity_id": "w1", "operation": "insert", "version": 1, "entity": {"id":"w1"}},
                {"entity_type": "book", "entity_id": "b1", "operation": "insert", "version": 1, "entity": {"id":"b1","name":"Main","icon":"default"}}
              ],
              "next_cursor": 1
            }
        """.trimIndent()
        val response = SyncPayloadMapper.parsePullResponse(json)
        assertEquals(1, response.changes.size)
        assertEquals("book", response.changes[0].entityType)
    }

    @Test
    fun parsePushResponse_parsesAcceptedRejectedAndCursor() {
        val json = """
            {
              "accepted": ["m1", "m2"],
              "rejected": [
                {"mutation_id": "m3", "reason": "expected_version_mismatch"},
                {"mutation_id": "m4", "reason": "not_found"}
              ],
              "next_cursor": 100
            }
        """.trimIndent()
        val response = SyncPayloadMapper.parsePushResponse(json)
        assertEquals(listOf("m1", "m2"), response.acceptedMutationIds)
        assertEquals(2, response.rejected.size)
        assertEquals("m3", response.rejected[0].mutationId)
        assertEquals("expected_version_mismatch", response.rejected[0].reason)
        assertEquals("m4", response.rejected[1].mutationId)
        assertEquals(100L, response.nextCursor)
    }

    @Test
    fun buildPushRequestJson_omitsNullExpectedVersion() {
        val req = PushRequest(
            cursor = 5,
            mutations = listOf(
                MutationWire(
                    mutationId = "m1",
                    entityType = "transaction",
                    entityId = "t1",
                    operation = "insert",
                    expectedVersion = null,
                    payload = """{"id":"t1","bookId":"b1"}"""
                ),
                MutationWire(
                    mutationId = "m2",
                    entityType = "budget",
                    entityId = "bud1",
                    operation = "update",
                    expectedVersion = 3,
                    payload = """{"id":"bud1","amountCents":100}"""
                )
            )
        )
        val out = SyncPayloadMapper.buildPushRequestJson(req)
        val root = JSONObject(out)
        assertEquals(5L, root.getLong("cursor"))
        val arr = root.getJSONArray("mutations")
        assertEquals(2, arr.length())
        val m1 = arr.getJSONObject(0)
        assertFalse(m1.has("expected_version"))
        val m2 = arr.getJSONObject(1)
        assertEquals(3, m2.getInt("expected_version"))
    }

    @Test
    fun buildPullRequestJson_containsCursorAndLimit() {
        val req = PullRequest(cursor = 7, limit = 50)
        val out = SyncPayloadMapper.buildPullRequestJson(req)
        val root = JSONObject(out)
        assertEquals(7L, root.getLong("cursor"))
        assertEquals(50, root.getInt("limit"))
    }

    // ─── P0-1 跨语言一致性测试 ───

    /**
     * P0-1 修复验证：parseBootstrapResponse 必须从 snake_case 原始数据计算 checksum，
     * 使得 computedChecksum 与服务端 compute_bootstrap_checksum 输出完全一致。
     *
     * 此测试构造与 Python 服务端 _serialize_entity + FastAPI 序列化完全一致的 JSON 输入
     * （snake_case 键 + ISO datetime + int 数字），验证客户端 computedChecksum 等于
     * Python 参考实现已知输出。
     *
     * 这是此前审查漏报的关键回归测试：旧实现对 camelCase Map 重算，
     * 因键名差异（created_at vs createdAt 等）永远无法匹配。
     */
    @Test
    fun parseBootstrapResponse_computedChecksumMatchesPythonReference() {
        // 与 Python 服务端 _serialize_entity(Book) 输出完全一致的 JSON 片段
        val json = """
            {
              "books": [
                {
                  "id": "book-uuid-1",
                  "name": "Alice Book",
                  "icon": null,
                  "version": 1,
                  "created_at": "2026-09-05T10:00:00+00:00",
                  "updated_at": "2026-09-05T10:00:00+00:00",
                  "deleted_at": null
                }
              ],
              "categories": [],
              "accounts": [],
              "transactions": [],
              "budgets": [],
              "cursor": 7,
              "checksum": "placeholder_server_checksum"
            }
        """.trimIndent()

        val response = SyncPayloadMapper.parseBootstrapResponse(json)

        // camelCase 映射正确（Room upsert 使用）
        assertEquals(1, response.books.size)
        val book = response.books[0]
        assertEquals("book-uuid-1", book["id"])
        assertEquals("2026-09-05T10:00:00+00:00", book["createdAt"])
        assertEquals("2026-09-05T10:00:00+00:00", book["updatedAt"])
        assertNull(book["deletedAt"])
        assertEquals("1", book["version"])

        // 关键断言：computedChecksum 必须等于 Python 参考实现的已知输出
        // （实际运行 Python compute_bootstrap_checksum 得到的值）
        val expectedPythonHash = "a0834b8383acd061f4ed09c24214708a9803c03326849b603d0134f6056c05d4"
        assertEquals(expectedPythonHash, response.computedChecksum)

        // server checksum 字段原样透传
        assertEquals("placeholder_server_checksum", response.checksum)
        assertEquals(7L, response.cursor)
    }

    /**
     * P0-1 修复验证：transaction 含多个 snake_case 字段（book_id, amount_cents, category_id 等），
     * parseBootstrapResponse 必须正确计算 checksum。
     */
    @Test
    fun parseBootstrapResponse_computedChecksumMatchesPythonReference_transaction() {
        val json = """
            {
              "books": [],
              "categories": [],
              "accounts": [],
              "transactions": [
                {
                  "id": "tx-1",
                  "book_id": "b-1",
                  "type": "expense",
                  "amount_cents": 5000,
                  "category_id": "c-1",
                  "account_id": null,
                  "note": "lunch",
                  "biz_date": "2026-09-05",
                  "occurred_at": "2026-09-05T12:00:00+00:00",
                  "source": "manual",
                  "version": 1,
                  "created_at": "2026-09-05T12:00:00+00:00",
                  "updated_at": "2026-09-05T12:00:00+00:00",
                  "deleted_at": null
                }
              ],
              "budgets": [],
              "cursor": 42,
              "checksum": "server_tx_checksum"
            }
        """.trimIndent()

        val response = SyncPayloadMapper.parseBootstrapResponse(json)

        // transaction camelCase 映射正确
        val tx = response.transactions[0]
        assertEquals("tx-1", tx["id"])
        assertEquals("b-1", tx["bookId"])
        assertEquals("5000", tx["amountCents"])
        assertEquals("c-1", tx["categoryId"])
        assertNull(tx["accountId"])
        assertEquals("2026-09-05", tx["bizDate"])
        assertEquals("manual", tx["source"])

        // 关键断言：computedChecksum 必须等于 Python 参考实现的已知输出
        val expectedPythonHash = "fa79baf3e9688ae97d58aadbff2a271430dd10e6337e15a11baca0ca5a12e021"
        assertEquals(expectedPythonHash, response.computedChecksum)
    }

    /**
     * P0-2 修复验证：updated_at 必须映射为 updatedAt（此前漏配）。
     */
    @Test
    fun parseBootstrapResponse_updatedAtMappedToCamelCase() {
        val json = """
            {
              "books": [
                {
                  "id": "b1",
                  "name": "Main",
                  "icon": null,
                  "version": 1,
                  "created_at": "2026-09-05T10:00:00+00:00",
                  "updated_at": "2026-09-05T11:00:00+00:00",
                  "deleted_at": null
                }
              ],
              "categories": [], "accounts": [], "transactions": [], "budgets": [],
              "cursor": 1,
              "checksum": "x"
            }
        """.trimIndent()

        val response = SyncPayloadMapper.parseBootstrapResponse(json)
        val book = response.books[0]
        // P0-2 修复后：updated_at 必须映射为 updatedAt
        assertEquals("2026-09-05T11:00:00+00:00", book["updatedAt"])
        // 不应再出现 snake_case 形式的 updated_at
        assertFalse("updated_at should be mapped to updatedAt", book.containsKey("updated_at"))
    }

    /**
     * rawEntityMap 必须保留原始 snake_case 键（P0-1 修复核心）。
     */
    @Test
    fun rawEntityMap_preservesSnakeCaseKeys() {
        val obj = JSONObject()
            .put("id", "b1")
            .put("book_id", "b1")
            .put("created_at", "2026-09-05T10:00:00+00:00")
            .put("updated_at", "2026-09-05T10:00:00+00:00")
            .put("amount_cents", 5000)
            .put("deleted_at", JSONObject.NULL)

        val raw = SyncPayloadMapper.rawEntityMap(obj)
        assertEquals("b1", raw["id"])
        assertEquals("b1", raw["book_id"])
        assertEquals("2026-09-05T10:00:00+00:00", raw["created_at"])
        assertEquals("2026-09-05T10:00:00+00:00", raw["updated_at"])
        assertEquals("5000", raw["amount_cents"])
        assertNull(raw["deleted_at"])
        // 不应出现 camelCase 键
        assertFalse(raw.containsKey("bookId"))
        assertFalse(raw.containsKey("createdAt"))
    }
}
