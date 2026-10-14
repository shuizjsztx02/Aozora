package com.aozora.app.bridge

import android.content.Context
import android.webkit.WebView
import com.aozora.app.autobook.AutoBookRepository
import com.aozora.app.db.AozoraDatabase
import com.aozora.app.db.Repository
import com.aozora.app.db.entity.AutoCaptureEntity
import com.aozora.app.db.entity.BookEntity
import com.aozora.app.db.entity.CategoryEntity
import com.aozora.app.db.entity.SyncMetaEntity
import kotlinx.coroutines.runBlocking
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * 第二十一轮 REQ-83（T21-5）：自动记账桥通道（`autobook.*` 四条）的 JVM 测试。
 *
 * 经 `AozoraBridge.dispatch`（internal，R-M4-21 先例）直走真实 handler 路径：
 * - `get_status`：开关 / 两类授权态 / 今日捕获数 / 待确认数（留痕行不计）
 * - `set_enabled`：写设备本地自管键，**不进 `MetaWhitelist.KEYS`**
 * - `list_captures`：待确认箱分页，被 L2 合并的留痕行不作为独立卡返回
 * - `resolve_capture`：确认 / 改后确认 / 忽略三分支 + 幂等 + 不可落账的输入被拒
 *
 * ★ **本类不验"授权现场"**：无障碍 / 通知授权的真机授予与投递只能在真机 / 模拟器上
 * 取证（决策 #19）。这里只测确定性逻辑；两条授权态在无系统授权时恒为 false。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class AozoraBridgeAutoBookTest {

    private lateinit var context: Context
    private lateinit var db: AozoraDatabase
    private lateinit var repository: Repository

    @Before
    fun setUp() = runBlocking {
        context = RuntimeEnvironment.getApplication()
        db = AozoraDatabase.createInMemory(context)
        repository = Repository(db)
        seedBookAndCategories()
    }

    @After
    fun tearDown() {
        db.close()
    }

    private fun newBridge(autoBookDb: AozoraDatabase? = db) =
        AozoraBridge(repository, WebView(context), syncContext = context, autoBookDb = autoBookDb)

    private suspend fun seedBookAndCategories() {
        db.bookDao().insert(
            BookEntity(id = "b1", ownerId = "local", name = "默认账本", icon = "📒", version = 1, archivedAt = null)
        )
        db.categoryDao().insert(
            CategoryEntity(
                id = "c-food", ownerId = "local", bookId = null, parentId = null,
                kind = "expense", name = "餐饮", archivedAt = null, version = 1
            )
        )
        db.categoryDao().insert(
            CategoryEntity(
                id = "c-salary", ownerId = "local", bookId = null, parentId = null,
                kind = "income", name = "工资", archivedAt = null, version = 1
            )
        )
        // resolve_capture 组装交易时要默认账本；getDefaultBookId 读本地自管键
        db.syncMetaDao().upsert(SyncMetaEntity(SyncMetaEntity.KEY_DEFAULT_BOOK_ID, "b1"))
    }

    private fun parsedJson(
        type: String = "expense",
        amountCents: Long = 3200,
        category: String = "餐饮",
        note: String = "星巴克",
        bizDate: String = "2026-10-14",
        kind: String = "transaction",
    ): String = JSONObject().apply {
        put("kind", kind); put("reason", ""); put("type", type)
        put("amountCents", amountCents); put("category", category); put("note", note)
        put("bizDate", bizDate); put("confidence", 0.9)
    }.toString()

    private fun captureRow(
        id: String,
        status: String = AutoCaptureEntity.STATUS_PENDING_CONFIRM,
        parsed: String? = parsedJson(),
        channel: String = AutoCaptureEntity.CHANNEL_NOTIFICATION,
        mergedInto: String? = null,
    ) = AutoCaptureEntity(
        captureId = id,
        dedupeKey = "dk-$id",
        mergeKey = null,
        sourceChannel = channel,
        sourcePackage = "com.tencent.mm",
        sourceApp = "微信",
        rawText = "微信支付 已支付¥32.00",
        postedAt = System.currentTimeMillis(),
        merchantKey = "starbucks",
        status = status,
        parsedJson = parsed,
        confidence = 0.9,
        transactionId = null,
        mergedInto = mergedInto,
        error = null,
        createdAt = System.currentTimeMillis(),
        updatedAt = System.currentTimeMillis(),
    )

    private suspend fun insertCapture(row: AutoCaptureEntity) {
        assertTrue("采集行应落库", db.autoCaptureDao().insertIgnore(row) != -1L)
    }

    // ─── get_status ───

    @Test
    fun getStatus_reflectsSwitchCountsAndExcludesMergedRows(): Unit = runBlocking {
        AutoBookRepository(db).setEnabled(true)
        insertCapture(captureRow("cap-a"))
        insertCapture(captureRow("cap-b"))
        // cap-c 是 L2 留痕行（被合并进 cap-a）——不进待确认箱，也不该计入角标
        insertCapture(captureRow("cap-c", mergedInto = "cap-a"))
        insertCapture(captureRow("cap-d", status = AutoCaptureEntity.STATUS_COMMITTED))

        val resp = newBridge().dispatch("r1", BridgeCommands.AUTOBOOK_GET_STATUS, JSONObject())
        assertTrue(resp.errorMessage ?: "", resp.success)
        val data = JSONObject(resp.data!!)
        assertTrue("总开关应反映已开启", data.getBoolean("enabled"))
        // 无系统授权 → 两条恒 false（授权现场归真机取证）
        assertFalse(data.getBoolean("accessibilityGranted"))
        assertFalse(data.getBoolean("notificationGranted"))
        // 今日捕获数 = 今日创建的全部行（4）
        assertEquals(4, data.getInt("todayCaptureCount"))
        // 待确认数 = pending_confirm 且未被合并 = cap-a / cap-b（cap-c 被排除）
        assertEquals(2, data.getInt("pendingConfirmCount"))
    }

    @Test
    fun getStatus_withoutAutoBookDb_returnsUnavailable(): Unit = runBlocking {
        val resp = newBridge(autoBookDb = null)
            .dispatch("r1", BridgeCommands.AUTOBOOK_GET_STATUS, JSONObject())
        assertFalse(resp.success)
        assertEquals("unavailable", resp.errorCode)
    }

    // ─── set_enabled ───

    @Test
    fun setEnabled_persistsToLocalMetaKey_notInMetaWhitelist(): Unit = runBlocking {
        val bridge = newBridge()
        val on = bridge.dispatch("r1", BridgeCommands.AUTOBOOK_SET_ENABLED, JSONObject().put("enabled", true))
        assertTrue(on.success)
        assertTrue(AutoBookRepository(db).isEnabled())

        val off = bridge.dispatch("r2", BridgeCommands.AUTOBOOK_SET_ENABLED, JSONObject().put("enabled", false))
        assertTrue(off.success)
        assertFalse(AutoBookRepository(db).isEnabled())

        // 开关是设备本地自管键，**不经过 meta 白名单**（决策 #9：meta 仍 11 键）
        assertFalse(MetaWhitelist.isAllowed(SyncMetaEntity.KEY_AUTOBOOK_ENABLED))
    }

    @Test
    fun setEnabled_withoutFlag_isInvalidInput(): Unit = runBlocking {
        val resp = newBridge().dispatch("r1", BridgeCommands.AUTOBOOK_SET_ENABLED, JSONObject())
        assertFalse(resp.success)
        assertEquals("invalid_input", resp.errorCode)
    }

    // ─── list_captures ───

    @Test
    fun listCaptures_filtersMergedRowsAndReportsSourceCount(): Unit = runBlocking {
        insertCapture(captureRow("cap-a"))
        insertCapture(captureRow("cap-b", channel = AutoCaptureEntity.CHANNEL_ACCESSIBILITY, mergedInto = "cap-a"))

        val resp = newBridge().dispatch("r1", BridgeCommands.AUTOBOOK_LIST_CAPTURES, JSONObject())
        assertTrue(resp.errorMessage ?: "", resp.success)
        val arr = JSONArray(resp.data!!)
        // 留痕行 cap-b 不作为独立卡片；只剩保留行 cap-a
        assertEquals(1, arr.length())
        val card = arr.getJSONObject(0)
        assertEquals("cap-a", card.getString("captureId"))
        // 来源数 = 本行 + 被合并进来的留痕行
        assertEquals(2, card.getInt("sourceCount"))
        assertTrue(card.getBoolean("multiSource"))
        assertTrue(card.getJSONObject("parsedJson").getString("category") == "餐饮")
    }

    @Test
    fun listCaptures_singleSourceCardIsNotMultiSource(): Unit = runBlocking {
        insertCapture(captureRow("cap-a"))
        val resp = newBridge().dispatch("r1", BridgeCommands.AUTOBOOK_LIST_CAPTURES, JSONObject())
        val card = JSONArray(resp.data!!).getJSONObject(0)
        assertEquals(1, card.getInt("sourceCount"))
        assertFalse(card.getBoolean("multiSource"))
    }

    // ─── resolve_capture：确认 ───

    @Test
    fun resolve_confirm_writesTransactionAndMarksCommitted(): Unit = runBlocking {
        insertCapture(captureRow("cap-a"))
        val p = JSONObject().put("captureId", "cap-a").put("resolution", "confirm")
        val resp = newBridge().dispatch("r1", BridgeCommands.AUTOBOOK_RESOLVE_CAPTURE, p)
        assertTrue(resp.errorMessage ?: "", resp.success)
        val data = JSONObject(resp.data!!)
        val txId = data.getString("transactionId")
        assertEquals(AutoCaptureEntity.STATUS_COMMITTED, data.getString("status"))

        val tx = repository.getTransaction(txId)!!
        assertEquals("expense", tx.type)
        assertEquals(3200L, tx.amountCents)
        assertEquals("c-food", tx.categoryId)
        assertEquals("b1", tx.bookId)
        // 来源必须是服务端 TxSource 并集内的既有值（不得新造枚举）
        assertEquals("nlu_ai", tx.source)
        // 落账与 pending mutation 同事务（决策 #16）——同一次 confirm 只入队一条
        assertEquals(1, db.pendingMutationDao().listAll().count { it.entityId == txId })

        val row = db.autoCaptureDao().getById("cap-a")!!
        assertEquals(AutoCaptureEntity.STATUS_COMMITTED, row.status)
        assertEquals(txId, row.transactionId)
    }

    // ─── resolve_capture：改后确认 ───

    @Test
    fun resolve_confirmModified_honoursOverrides(): Unit = runBlocking {
        insertCapture(captureRow("cap-a"))
        val overrides = JSONObject()
            .put("type", "income")
            .put("amountCents", 500000)
            .put("categoryId", "c-salary")
            .put("note", "十月工资")
        val p = JSONObject().put("captureId", "cap-a")
            .put("resolution", "confirm_modified").put("overrides", overrides)
        val resp = newBridge().dispatch("r1", BridgeCommands.AUTOBOOK_RESOLVE_CAPTURE, p)
        assertTrue(resp.errorMessage ?: "", resp.success)
        val tx = repository.getTransaction(JSONObject(resp.data!!).getString("transactionId"))!!
        assertEquals("income", tx.type)
        assertEquals(500000L, tx.amountCents)
        assertEquals("c-salary", tx.categoryId)
        assertEquals("十月工资", tx.note)
    }

    // ─── resolve_capture：忽略 ───

    @Test
    fun resolve_ignore_marksIgnoredAndWritesNoTransaction(): Unit = runBlocking {
        insertCapture(captureRow("cap-a"))
        val p = JSONObject().put("captureId", "cap-a").put("resolution", "ignore")
        val resp = newBridge().dispatch("r1", BridgeCommands.AUTOBOOK_RESOLVE_CAPTURE, p)
        assertTrue(resp.errorMessage ?: "", resp.success)
        assertEquals(AutoCaptureEntity.STATUS_IGNORED, JSONObject(resp.data!!).getString("status"))
        assertEquals(AutoCaptureEntity.STATUS_IGNORED, db.autoCaptureDao().getById("cap-a")!!.status)
        // 忽略 ≠ 写账目：不留半成品
        assertEquals(0, db.transactionDao().countActive())
    }

    // ─── resolve_capture：幂等与拒绝 ───

    @Test
    fun resolve_confirmTwice_isIdempotent(): Unit = runBlocking {
        insertCapture(captureRow("cap-a"))
        val bridge = newBridge()
        val p = JSONObject().put("captureId", "cap-a").put("resolution", "confirm")
        val first = bridge.dispatch("r1", BridgeCommands.AUTOBOOK_RESOLVE_CAPTURE, p)
        val txId = JSONObject(first.data!!).getString("transactionId")
        val second = bridge.dispatch("r2", BridgeCommands.AUTOBOOK_RESOLVE_CAPTURE, p)
        assertTrue(second.success)
        val data = JSONObject(second.data!!)
        assertTrue(data.getBoolean("alreadyResolved"))
        assertEquals(txId, data.getString("transactionId"))
        // 没有写出第二笔
        assertEquals(1, db.transactionDao().countActive())
    }

    @Test
    fun resolve_unknownCapture_returnsNotFound(): Unit = runBlocking {
        val p = JSONObject().put("captureId", "nope").put("resolution", "confirm")
        val resp = newBridge().dispatch("r1", BridgeCommands.AUTOBOOK_RESOLVE_CAPTURE, p)
        assertFalse(resp.success)
        assertEquals("not_found", resp.errorCode)
    }

    @Test
    fun resolve_confirmWithoutParseResult_isInvalidInput(): Unit = runBlocking {
        insertCapture(captureRow("cap-a", parsed = null))
        val p = JSONObject().put("captureId", "cap-a").put("resolution", "confirm")
        val resp = newBridge().dispatch("r1", BridgeCommands.AUTOBOOK_RESOLVE_CAPTURE, p)
        assertFalse(resp.success)
        assertEquals("invalid_input", resp.errorCode)
        assertEquals(0, db.transactionDao().countActive())
    }

    @Test
    fun resolve_confirmOnNotTransaction_isInvalidInput(): Unit = runBlocking {
        insertCapture(captureRow("cap-a", parsed = parsedJson(kind = "not_transaction")))
        val p = JSONObject().put("captureId", "cap-a").put("resolution", "confirm")
        val resp = newBridge().dispatch("r1", BridgeCommands.AUTOBOOK_RESOLVE_CAPTURE, p)
        assertFalse(resp.success)
        assertEquals("invalid_input", resp.errorCode)
    }

    @Test
    fun resolve_unknownResolution_isInvalidInput(): Unit = runBlocking {
        insertCapture(captureRow("cap-a"))
        val p = JSONObject().put("captureId", "cap-a").put("resolution", "delete")
        val resp = newBridge().dispatch("r1", BridgeCommands.AUTOBOOK_RESOLVE_CAPTURE, p)
        assertFalse(resp.success)
        assertEquals("invalid_input", resp.errorCode)
    }
}