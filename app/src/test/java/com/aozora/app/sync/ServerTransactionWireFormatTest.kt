package com.aozora.app.sync

import com.aozora.app.db.AozoraDatabase
import com.aozora.app.db.Repository
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * T18-2（REQ-67① 取证第二层）：**服务端下行的 transaction 必须能落进 Room**。
 *
 * 本用例的载荷是**从运行中的服务端逐字节抄下来的**（`_serialize_entity` 的
 * transaction 分支，见 `server/src/modules/sync/service.py`），不是手编的：
 *
 * ```json
 * {"id":"…","book_id":"…","type":"expense","amount_cents":600,"category_id":"…",
 *  "account_id":null,"note":"祖传牛肉面","biz_date":"2026-09-23",
 *  "occurred_at":"2026-09-23T09:33:47.687000+00:00","source":"manual","version":2,
 *  "created_at":"…","updated_at":"…","deleted_at":null}
 * ```
 *
 * 要点：`occurred_at` 出网是 **ISO 8601 字符串**（服务端自 T-M3 起就如此），而客户端
 * Room 的时间列是 **epoch 毫秒**。`SyncPayloadMapper.isoToEpochMillis` 就是为这条
 * 契约写的——存钱目标/定时规则等实体都在用，**唯独 transaction 的两条映射路径漏了**：
 *   - pull：`ServerChange.toTransaction()`（`sync/SyncModels.kt`）
 *   - bootstrap：`Repository.serverChangeMapToTransaction()`（`db/Repository.kt`）
 * 两处都写 `map["occurredAt"]?.toLongOrNull() ?: return null`，ISO 字串解析不出 Long
 * → 返回 null → 调用方 `?: continue` **静默丢弃**。表现为：服务端写了、同步也跑了，
 * 本地一条 transaction 都不落地（真机日志：`bootstrap entity count MISMATCH:
 * transactions: expected=4 actual=0`）。
 *
 * 假绿成因：既有 Kotlin 用例的 fixture 用 epoch 毫秒喂 payload，与服务端真实出网形态
 * 不一致——所以这条契约此前**一处守护都没有**。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ServerTransactionWireFormatTest {

    private lateinit var db: AozoraDatabase
    private lateinit var repo: Repository

    /** 服务端 `_serialize_entity(Transaction)` 的逐字节形态（2026-09-23 实测抓取）。 */
    private val wireTx = """
        {"id":"aaaaaaaa-1111-4111-8111-790156027687",
         "book_id":"7ddd982a-bf78-4f41-bcd9-626b8466f199",
         "type":"expense","amount_cents":600,
         "category_id":"07a79772-3637-4d53-81ac-516a2dba53cb",
         "account_id":null,"note":"祖传牛肉面","biz_date":"2026-09-23",
         "occurred_at":"2026-09-23T09:33:47.687000+00:00","source":"manual","version":2,
         "created_at":"2026-09-23T09:33:49.132290+00:00",
         "updated_at":"2026-09-23T10:12:59.035559+00:00","deleted_at":null}
    """.trimIndent()

    private val ownerId = "4b83c64d-6bf1-4c89-a41b-ef4fb887d7af"

    @Before
    fun setUp() {
        // **不要**用 `AozoraDatabase.getInstance()`：它缓存的 INSTANCE 在 close() 后
        // **不会清空**，而 Robolectric 每个用例都会重建 Application → `AozoraApp.onCreate`
        // 里的 `runBlocking` 会经 `AozoraApplication.database` 拿到这个**已关闭**的库；
        // Room 2.7（声明 2.6.1，被 WorkManager 提升到 2.7.0）关闭时会取消数据库协程
        // 作用域，导致查询任务永不执行、调用方永久挂起——整套 JVM 板随之卡死
        // （实测：`AozoraApp.kt:208` 的 BlockingCoroutine 一直 parking）。
        // 故与仓库内其它 Repository*Test 同款，走 createInMemory。
        db = AozoraDatabase.createInMemory(RuntimeEnvironment.getApplication())
        repo = Repository(db)
    }

    @After
    fun tearDown() {
        db.close()
    }

    /** pull 路径：`ServerChange.toTransaction()`。 */
    @Test
    fun `pull 路径：ISO occurred_at 的 transaction 能映射成实体`() {
        val camel = SyncPayloadMapper.snakeToCamelMap(JSONObject(wireTx))
        val change = ServerChange(
            entityType = "transaction",
            entityId = camel["id"]!!,
            operation = SyncChangeOp.UPDATE,
            version = 2,
            entityPayload = camel,
        )
        val entity = change.toTransaction()
        assertNotNull("ISO occurred_at 的 transaction 被静默丢弃了", entity)
        assertEquals(600L, entity!!.amountCents)
        assertEquals(2, entity.version)
        // 2026-09-23T09:33:47.687Z → epoch 毫秒
        assertEquals(1790156027687L, entity.occurredAt)
    }

    /** bootstrap 路径：`Repository.applyBootstrap` 必须把 transaction 写进 Room。 */
    @Test
    fun `bootstrap 路径：ISO occurred_at 的 transaction 能落库`() = runBlocking {
        val json = """
            {"books":[{"id":"7ddd982a-bf78-4f41-bcd9-626b8466f199","name":"默认账本",
                       "icon":"📒","version":1,"created_at":"2026-09-23T09:33:06.098607+00:00",
                       "updated_at":"2026-09-23T09:33:06.098607+00:00","deleted_at":null}],
             "categories":[{"id":"07a79772-3637-4d53-81ac-516a2dba53cb","name":"交通",
                       "kind":"expense","book_id":null,"parent_id":null,"version":1,
                       "created_at":"2026-09-23T09:33:06.098607+00:00",
                       "updated_at":"2026-09-23T09:33:06.098607+00:00","archived_at":null}],
             "accounts":[],"transactions":[$wireTx],"budgets":[],
             "savings_goals":[],"scheduled_rules":[],"chat_personas":[],"user_preferences":[],
             "cursor":1,"checksum":"","checksum_v2":"","checksum_v3":""}
        """.trimIndent()

        repo.applyBootstrap(SyncPayloadMapper.parseBootstrapResponse(json), ownerId)

        val rows = db.transactionDao().listByBook("7ddd982a-bf78-4f41-bcd9-626b8466f199")
        assertEquals("bootstrap 的 transaction 被静默丢弃了", 1, rows.size)
        assertEquals(600L, rows[0].amountCents)
        assertEquals(1790156027687L, rows[0].occurredAt)
    }
}
