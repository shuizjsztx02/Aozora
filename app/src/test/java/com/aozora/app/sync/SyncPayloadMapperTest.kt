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
        // N-M3-7 修复：未知字段启发式转为 camelCase（foo_bar → fooBar），
        // 而非保留 snake_case。这确保未来服务端新增字段时，客户端 Room entity
        // 新增对应列后键名可直接匹配。
        assertEquals("value", out["unknownField"])
    }

    @Test
    fun snakeToCamelMap_unknownFieldMultipleUnderscores() {
        // 多下划线场景：new_metadata_value → newMetadataValue
        val snake = JSONObject().put("new_metadata_value", "v1")
        val out = SyncPayloadMapper.snakeToCamelMap(snake)
        assertEquals("v1", out["newMetadataValue"])
        assertFalse("old snake key should not appear", out.containsKey("new_metadata_value"))
    }

    /** P0-2 修复验证：updated_at 必须映射为 updatedAt */
    @Test
    fun snakeToCamelMap_updatedAtMappedCorrectly() {
        val snake = JSONObject().put("updated_at", "2026-09-05T11:00:00+00:00")
        val out = SyncPayloadMapper.snakeToCamelMap(snake)
        assertEquals("2026-09-05T11:00:00+00:00", out["updatedAt"])
        assertFalse(out.containsKey("updated_at"))
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
    fun parsePushResponse_mapsResultsStatusToAcceptedRejected() {
        // 服务端真实契约：{"results":[{mutation_id,status,...}],"cursor":N}
        // applied/duplicate → accepted；conflict → rejected。
        val json = """
            {
              "results": [
                {"mutation_id": "m1", "status": "applied", "cursor": 10, "version": 1, "current_version": null},
                {"mutation_id": "m2", "status": "duplicate", "cursor": 8, "version": 1, "current_version": null},
                {"mutation_id": "m3", "status": "conflict", "cursor": null, "version": null, "current_version": 7}
              ],
              "cursor": 10
            }
        """.trimIndent()
        val response = SyncPayloadMapper.parsePushResponse(json)
        // applied + duplicate 都要移除（duplicate 代表已在上行，不移除则队列永不收敛）
        assertEquals(listOf("m1", "m2"), response.acceptedMutationIds)
        assertEquals(1, response.rejected.size)
        assertEquals("m3", response.rejected[0].mutationId)
        assertTrue(response.rejected[0].reason.contains("version_conflict"))
        assertEquals(10L, response.nextCursor)
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
        // T12-7 前置修复：wire 键为 version（server MutationItem 契约，旧 expected_version 被忽略→恒 422）
        assertFalse(m1.has("version"))
        val m2 = arr.getJSONObject(1)
        assertEquals(3, m2.getInt("version"))
    }

    @Test
    fun buildPushRequestJson_transformsPayloadToSnakeAndIso() {
        val req = PushRequest(
            cursor = 14,
            mutations = listOf(
                MutationWire(
                    mutationId = "m1",
                    entityType = "transaction",
                    entityId = "t1",
                    operation = "insert",
                    expectedVersion = null,
                    // 客户端 Room 存 camelCase + epoch 毫秒
                    payload = """{"id":"t1","bookId":"b1","amountCents":2500,""" +
                        """"categoryId":"c1","bizDate":"2026-09-08","occurredAt":1788845613031}"""
                )
            )
        )
        val m1 = JSONObject(SyncPayloadMapper.buildPushRequestJson(req))
            .getJSONArray("mutations").getJSONObject(0).getJSONObject("payload")
        // wire 必须是 snake_case
        assertEquals("b1", m1.getString("book_id"))
        assertEquals(2500, m1.getInt("amount_cents"))
        assertEquals("c1", m1.getString("category_id"))
        assertEquals("2026-09-08", m1.getString("biz_date"))
        assertFalse(m1.has("bookId"))
        assertFalse(m1.has("occurredAt"))
        // occurred_at 必须是 ISO（服务端 fromisoformat），可回到同一 epoch
        val epoch = java.time.OffsetDateTime.parse(m1.getString("occurred_at")).toInstant().toEpochMilli()
        assertEquals(1788845613031L, epoch)
    }

    @Test
    fun buildPullRequestJson_containsCursorAndLimit() {
        val req = PullRequest(cursor = 7, limit = 50)
        val out = SyncPayloadMapper.buildPullRequestJson(req)
        val root = JSONObject(out)
        assertEquals(7L, root.getLong("cursor"))
        assertEquals(50, root.getInt("limit"))
    }

    @Test
    fun buildPushRequestJson_includesPendingCount_whenNonNull() {
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
                )
            ),
            pendingCount = 3
        )
        val out = SyncPayloadMapper.buildPushRequestJson(req)
        val root = JSONObject(out)
        assertEquals(3, root.getInt("pending_count"))
    }

    @Test
    fun buildPushRequestJson_omitsPendingCount_whenNull() {
        val req = PushRequest(
            cursor = 5,
            mutations = emptyList(),
            pendingCount = null
        )
        val out = SyncPayloadMapper.buildPushRequestJson(req)
        val root = JSONObject(out)
        assertFalse(root.has("pending_count"))
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

    // ─── push wire：camel→snake + epoch→ISO（服务端 _apply_insert 读 snake/ISO） ───

    @Test
    fun toWirePayload_renamesTransactionToSnake() {
        val camel = """{"id":"t1","bookId":"b1","type":"expense","amountCents":2500,""" +
            """"categoryId":"c1","accountId":null,"note":"x","bizDate":"2026-09-08",""" +
            """"occurredAt":1788845613031,"source":"manual","version":1}"""
        val wire = SyncPayloadMapper.toWirePayload(camel)
        // snake 键存在
        assertEquals("b1", wire.getString("book_id"))
        assertEquals(2500, wire.getInt("amount_cents"))
        assertEquals("c1", wire.getString("category_id"))
        assertEquals("2026-09-08", wire.getString("biz_date"))
        assertTrue(wire.isNull("account_id"))
        // camel 键不再出现
        assertFalse(wire.has("bookId"))
        assertFalse(wire.has("amountCents"))
        assertFalse(wire.has("occurredAt"))
    }

    @Test
    fun toWirePayload_convertsOccurredAtEpochToIsoRoundTrips() {
        val camel = """{"occurredAt":1788845613031,"type":"expense"}"""
        val wire = SyncPayloadMapper.toWirePayload(camel)
        val iso = wire.getString("occurred_at")
        // ISO 可被 java.time 解析，且回到同一 epoch 毫秒
        val parsed = java.time.OffsetDateTime.parse(iso).toInstant().toEpochMilli()
        assertEquals(1788845613031L, parsed)
    }

    @Test
    fun toWirePayload_nonDatetimeNumberUntouched() {
        // amount_cents 是数字但非 datetime，必须保持数字（不被当时间戳转换）
        val camel = """{"amountCents":300,"balanceCents":9000}"""
        val wire = SyncPayloadMapper.toWirePayload(camel)
        assertEquals(300, wire.getInt("amount_cents"))
        assertEquals(9000, wire.getInt("balance_cents"))
        // T10-1（决策 #1）：布尔未知字段走同一启发式 includeInBudget→include_in_budget，
        // 值保持布尔不炸（服务端 _apply_insert 白名单不读 → 静默忽略）
        val wire2 = SyncPayloadMapper.toWirePayload(
            """{"amountCents":300,"includeInBudget":false}"""
        )
        assertFalse(wire2.getBoolean("include_in_budget"))
        assertFalse(wire2.has("includeInBudget"))
    }
}
