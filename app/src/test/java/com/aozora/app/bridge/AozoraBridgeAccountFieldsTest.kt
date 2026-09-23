package com.aozora.app.bridge

import android.content.Context
import android.webkit.WebView
import com.aozora.app.db.AozoraDatabase
import com.aozora.app.db.Repository
import com.aozora.app.db.entity.AccountEntity
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
 * 第十八轮 REQ-69（T18-5）：account.insert / account.update / account.list 的
 * `balanceCents` 载荷契约。
 *
 * 本组钉住的核心是**局部合并不得清零**：`handleAccountUpdate` 是逐字段构造
 * （不是 `current.copy`），漏接一个字段的默认值就是 0 —— 一次只改名字的
 * 编辑就会把余额静默抹掉。accountType 当年就是同一形态（键缺省才保留）。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class AozoraBridgeAccountFieldsTest {

    private lateinit var db: AozoraDatabase
    private lateinit var repository: Repository

    @Before
    fun setUp() {
        val context: Context = RuntimeEnvironment.getApplication()
        db = AozoraDatabase.createInMemory(context)
        repository = Repository(db)
    }

    @After
    fun tearDown() { db.close() }

    private fun bridge() =
        AozoraBridge(repository, WebView(RuntimeEnvironment.getApplication()),
            syncContext = RuntimeEnvironment.getApplication())

    private fun dispatch(command: String, params: JSONObject) = runBlocking {
        bridge().dispatch("r", command, params)
    }

    private suspend fun seed(
        id: String = "a1", type: String? = "储蓄卡", balance: Long = 500_000L,
    ) {
        repository.insertAccount(
            AccountEntity(
                id = id, ownerId = "local", name = "交行卡", icon = "💰",
                accountType = type, balanceCents = balance, archivedAt = null, version = 1,
            ),
            "m-seed-$id",
        )
    }

    private fun listItem(id: String): JSONObject {
        val resp = dispatch(BridgeCommands.ACCOUNT_LIST, JSONObject())
        assertTrue("account.list dispatch must succeed", resp.success)
        val arr = JSONArray(resp.data ?: "[]")
        return (0 until arr.length())
            .map { arr.getJSONObject(it) }
            .first { it.getString("id") == id }
    }

    private fun updatePayload(id: String, version: Int, extra: JSONObject.() -> Unit) =
        JSONObject().apply {
            put("mutationId", "m-upd-$id-$version")
            put("account", JSONObject().apply {
                put("id", id); put("version", version); extra()
            })
        }

    @Test
    fun accountList_exposesBalanceCents() = runBlocking {
        seed()
        val item = listItem("a1")
        assertEquals("账户当前余额须透出", 500_000L, item.getLong("balanceCents"))
        assertEquals("储蓄卡", item.getString("accountType"))
    }

    @Test
    fun accountList_negativeBalance_isExposed() = runBlocking {
        seed(id = "a-cc", type = "信用卡", balance = -32_500L)
        assertEquals(-32_500L, listItem("a-cc").getLong("balanceCents"))
    }

    @Test
    fun accountInsert_withoutBalanceKey_defaultsToZero() = runBlocking {
        val resp = dispatch(BridgeCommands.ACCOUNT_INSERT, JSONObject().apply {
            put("mutationId", "m-ins")
            put("account", JSONObject().apply {
                put("id", "a-new"); put("ownerId", "local"); put("name", "现金"); put("version", 1)
            })
        })
        assertTrue(resp.success)
        assertEquals(0L, repository.getAccount("a-new")!!.balanceCents)
    }

    @Test
    fun accountInsert_negativeBalance_isAccepted() = runBlocking {
        val resp = dispatch(BridgeCommands.ACCOUNT_INSERT, JSONObject().apply {
            put("mutationId", "m-ins-cc")
            put("account", JSONObject().apply {
                put("id", "a-cc"); put("ownerId", "local"); put("name", "交行信用卡")
                put("balanceCents", -32_500L); put("version", 1)
            })
        })
        assertTrue(resp.success)
        assertEquals(-32_500L, repository.getAccount("a-cc")!!.balanceCents)
    }

    @Test
    fun accountUpdate_withoutBalanceKey_preservesIt() = runBlocking {
        seed()
        val resp = dispatch(
            BridgeCommands.ACCOUNT_UPDATE, updatePayload("a1", 2) { put("name", "交行卡改名") }
        )
        assertTrue(resp.success)
        val a = repository.getAccount("a1")!!
        assertEquals("交行卡改名", a.name)
        // 关键断言：只改名字不得把余额清零
        assertEquals(500_000L, a.balanceCents)
        assertEquals("储蓄卡", a.accountType)
    }

    @Test
    fun accountUpdate_withBalanceKey_setsAbsoluteValue() = runBlocking {
        seed()
        val resp = dispatch(
            BridgeCommands.ACCOUNT_UPDATE, updatePayload("a1", 2) { put("balanceCents", 0L) }
        )
        assertTrue(resp.success)
        // 0 是数值（不是"未设置"）：键在场就必须写进去
        assertEquals(0L, repository.getAccount("a1")!!.balanceCents)
    }

    @Test
    fun accountUpdate_nonIntegerBalance_rejected() = runBlocking {
        seed()
        // dispatch 层抛 IllegalArgumentException（`call()` 把它译成 wire 码
        // invalid_input，见 AozoraBridge.call 的 catch）——校验发生在写库之前。
        val result = runCatching {
            dispatch(
                BridgeCommands.ACCOUNT_UPDATE,
                updatePayload("a1", 2) { put("balanceCents", 12.5) }
            )
        }
        assertTrue("小数余额必须被拒", result.exceptionOrNull() is IllegalArgumentException)
        assertEquals("被拒后余额不变", 500_000L, repository.getAccount("a1")!!.balanceCents)
    }
}