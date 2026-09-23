package com.aozora.app.bridge

import android.content.Context
import android.webkit.WebView
import com.aozora.app.db.AozoraDatabase
import com.aozora.app.db.Repository
import com.aozora.app.db.entity.DefaultIcons
import kotlinx.coroutines.runBlocking
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
 * 第十八轮 REQ-68（T18-4）：`book.insert` / `account.insert` 缺 icon 键时的兜底值。
 *
 * 旧兜底是裸词 `"book"` / `"account"`（`optString` 的默认值）——JS 不传 icon 即落库成
 * 英文单词，经 CategoryIcon「非空即原样文本」直接画进圆形图标框。与 pull/bootstrap
 * 的 `RepositoryIconSyncTest` 是两条独立入口，同钉。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class AozoraBridgeDefaultIconTest {

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

    private fun dispatch(command: String, params: JSONObject) = runBlocking {
        AozoraBridge(
            repository,
            WebView(RuntimeEnvironment.getApplication()),
            syncContext = RuntimeEnvironment.getApplication()
        ).dispatch("r", command, params)
    }

    @Test
    fun bookInsert_noIconKey_landsBookEmoji() = runBlocking {
        val resp = dispatch(BridgeCommands.BOOK_INSERT, JSONObject().apply {
            put("mutationId", "m-book")
            put("book", JSONObject().apply {
                put("id", "b1"); put("ownerId", "local"); put("name", "旅行"); put("version", 1)
            })
        })
        assertTrue(resp.success)
        assertEquals(DefaultIcons.BOOK, repository.listBooks().first { it.id == "b1" }.icon)
    }

    @Test
    fun accountInsert_noIconKey_landsAccountEmoji() = runBlocking {
        val resp = dispatch(BridgeCommands.ACCOUNT_INSERT, JSONObject().apply {
            put("mutationId", "m-account")
            put("account", JSONObject().apply {
                put("id", "a1"); put("ownerId", "local"); put("name", "交行卡"); put("version", 1)
            })
        })
        assertTrue(resp.success)
        assertEquals(DefaultIcons.ACCOUNT, repository.listAccounts().first { it.id == "a1" }.icon)
    }
}