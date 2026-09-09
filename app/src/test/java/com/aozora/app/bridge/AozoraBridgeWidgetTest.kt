package com.aozora.app.bridge

import android.content.Context
import android.webkit.WebView
import com.aozora.app.db.AozoraDatabase
import com.aozora.app.db.Repository
import com.aozora.app.db.entity.BookEntity
import com.aozora.app.db.entity.CategoryEntity
import com.aozora.app.db.entity.TransactionEntity
import com.aozora.app.widget.AozoraWidgetProvider
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/**
 * T11-2（REQ-24 / 决策 #5）：桌面小组件刷新挂钩测试。
 *
 * 经 AozoraBridge.dispatch（internal，R-M4-21 先例）直走真实 handler 路径，
 * 断言包内 ACTION_UPDATE 广播（仿 AozoraWidgetProviderTest 的 shadow 捕获法）：
 * - widget.refresh 命令 → 广播
 * - transaction.insert / transaction.softDelete 尾部挂钩 → 广播（v0 缺口修复）
 * - 无 context → unavailable 错误、不广播
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class AozoraBridgeWidgetTest {

    private lateinit var context: Context
    private lateinit var db: AozoraDatabase
    private lateinit var repository: Repository

    @Before
    fun setUp() {
        context = RuntimeEnvironment.getApplication()
        db = AozoraDatabase.createInMemory(context)
        repository = Repository(db)
    }

    @After
    fun tearDown() {
        db.close()
    }

    private fun newBridge(syncContext: Context? = context) =
        AozoraBridge(repository, WebView(context), syncContext = syncContext)

    private fun updateBroadcastCount(): Int =
        shadowOf(RuntimeEnvironment.getApplication()).broadcastIntents
            .count { it.action == AozoraWidgetProvider.ACTION_UPDATE }

    @Test
    fun widgetRefresh_sendsUpdateBroadcast(): Unit = runBlocking {
        val resp = newBridge().dispatch("r1", BridgeCommands.WIDGET_REFRESH, JSONObject())
        assertTrue(resp.success)
        assertEquals(1, updateBroadcastCount())
    }

    @Test
    fun widgetRefresh_noContext_isUnavailableAndNoBroadcast(): Unit = runBlocking {
        val resp = newBridge(syncContext = null)
            .dispatch("r2", BridgeCommands.WIDGET_REFRESH, JSONObject())
        assertFalse(resp.success)
        assertEquals("unavailable", resp.errorCode)
        assertEquals(0, updateBroadcastCount())
    }

    @Test
    fun txInsert_hookRefreshesWidget(): Unit = runBlocking {
        seedBookAndCategory()
        val p = JSONObject().apply {
            put("mutationId", "m-tx-1")
            put(
                "tx", JSONObject().apply {
                    put("id", "t1"); put("bookId", "b1"); put("type", "expense")
                    put("amountCents", 1234); put("categoryId", "c1"); put("note", "")
                    put("bizDate", "2026-09-09"); put("occurredAt", System.currentTimeMillis())
                    put("source", "manual")
                }
            )
        }
        val resp = newBridge().dispatch("r3", BridgeCommands.TX_INSERT, p)
        assertTrue(resp.errorMessage ?: "", resp.success)
        assertEquals("挂钩应恰好广播一次", 1, updateBroadcastCount())
    }

    @Test
    fun txSoftDelete_hookRefreshesWidget(): Unit = runBlocking {
        seedBookAndCategory()
        repository.insertTransaction(
            TransactionEntity(
                id = "t1", bookId = "b1", type = "expense", amountCents = 100,
                categoryId = "c1", accountId = null, note = "", bizDate = "2026-09-09",
                occurredAt = System.currentTimeMillis(), source = "manual",
                version = 1, deletedAt = null
            ),
            "m-seed"
        )
        val before = updateBroadcastCount()
        val p = JSONObject().put("id", "t1").put("mutationId", "m-del")
        val resp = newBridge().dispatch("r4", BridgeCommands.TX_SOFT_DELETE, p)
        assertTrue(resp.success)
        assertEquals(1, updateBroadcastCount() - before)
    }

    private suspend fun seedBookAndCategory() {
        repository.insertBook(
            BookEntity(
                id = "b1", ownerId = "owner1", name = "默认账本",
                icon = "default", version = 1, archivedAt = null
            ),
            "m-book"
        )
        repository.insertCategory(
            CategoryEntity(
                id = "c1", ownerId = "owner1", bookId = null, parentId = null,
                kind = "expense", name = "餐饮", archivedAt = null, version = 1
            ),
            "m-cat"
        )
    }
}
