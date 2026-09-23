package com.aozora.app.db

import com.aozora.app.db.entity.AccountEntity
import com.aozora.app.db.entity.BookEntity
import com.aozora.app.db.entity.DefaultIcons
import com.aozora.app.sync.BootstrapResponse
import com.aozora.app.sync.ServerChange
import com.aozora.app.sync.SyncChangeOp
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
 * 第九轮 T9-6 / REQ-13 决策 #7：照片版 book/account icon 的同步三件套——
 * push 侧超长（>64）icon 省略键（防服务端 _validate_name 422 毒丸），
 * pull 侧本地 data: icon 不被服务端回传的非 data: 值冲刷。
 *
 * 第十八轮 REQ-68（T18-4）：服务端 icon 列可空（AI 新建账户/账本常常不带 icon），
 * 缺键时的兜底值必须是 emoji —— 旧兜底是裸词 `"default"`，CategoryIcon
 * 「非空即原样文本」会把它画进圆形图标框。pull 与 bootstrap 是两条独立读口，同钉。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class RepositoryIconSyncTest {

    private lateinit var db: AozoraDatabase
    private lateinit var repository: Repository

    /** 200 字符照片 data URI（模拟 icon.pick 产物，>64 服务端上限）。 */
    private val photoIcon = "data:image/png;base64," + "A".repeat(200)

    @Before
    fun setUp() {
        db = AozoraDatabase.createInMemory(RuntimeEnvironment.getApplication())
        repository = Repository(db)
    }

    @After
    fun tearDown() { db.close() }

    private fun book(id: String, icon: String, version: Int = 1) =
        BookEntity(id = id, ownerId = "u1", name = "账本-$id", icon = icon,
            version = version, archivedAt = null)

    private fun account(id: String, icon: String, version: Int = 1) =
        AccountEntity(id = id, ownerId = "u1", name = "账户-$id", icon = icon,
            accountType = null, archivedAt = null, version = version)

    // ─── push 侧：payload 键省略 ───

    @Test
    fun bookInsert_photoIcon_payloadOmitsIconKey() = runBlocking {
        repository.insertBook(book("b1", photoIcon), "m1")
        val payload = JSONObject(
            repository.listPendingMutations().first { it.entityId == "b1" }.payload
        )
        assertFalse("超长照片 icon 不应出现在 push payload", payload.has("icon"))
        assertTrue(payload.has("name")) // 其余字段不受影响
    }

    @Test
    fun bookInsert_emojiIcon_payloadKeepsIcon() = runBlocking {
        repository.insertBook(book("b1", "📗"), "m1")
        val payload = JSONObject(
            repository.listPendingMutations().first { it.entityId == "b1" }.payload
        )
        assertEquals("📗", payload.getString("icon"))
    }

    @Test
    fun accountInsert_photoIcon_payloadOmitsIconKey() = runBlocking {
        repository.insertAccount(account("a1", photoIcon), "m1")
        val payload = JSONObject(
            repository.listPendingMutations().first { it.entityId == "a1" }.payload
        )
        assertFalse(payload.has("icon"))
    }

    // ─── pull 侧：防冲刷 ───

    @Test
    fun bookPull_nonDataIcon_preservesLocalPhoto() = runBlocking {
        repository.insertBook(book("b1", photoIcon), "m1")
        repository.removeMutation("m1") // 模拟 push 成功，解除活跃 mutation 跳过条件

        repository.applyServerChanges(
            listOf(ServerChange(
                entityType = "book", entityId = "b1", operation = SyncChangeOp.UPDATE, version = 2,
                entityPayload = mapOf("id" to "b1", "name" to "改名了", "icon" to "📙")
            )),
            newCursor = 0, ownerId = "u1"
        )
        val b = repository.listBooks().first { it.id == "b1" }
        assertEquals("photo 本地专属：服务端非 data: 值不得冲刷", photoIcon, b.icon)
        assertEquals("其余字段正常合并", "改名了", b.name)
    }

    @Test
    fun bookPull_iconMissing_preservesLocalPhoto() = runBlocking {
        repository.insertBook(book("b1", photoIcon), "m1")
        repository.removeMutation("m1")
        repository.applyServerChanges(
            listOf(ServerChange(
                entityType = "book", entityId = "b1", operation = SyncChangeOp.UPDATE, version = 2,
                entityPayload = mapOf("id" to "b1", "name" to "改名了") // 无 icon → toBook 走默认图标
            )),
            newCursor = 0, ownerId = "u1"
        )
        assertEquals(photoIcon, repository.listBooks().first { it.id == "b1" }.icon)
    }

    @Test
    fun bookPull_localEmojiIcon_stillFollowsServer() = runBlocking {
        repository.insertBook(book("b1", "📗"), "m1")
        repository.removeMutation("m1")
        repository.applyServerChanges(
            listOf(ServerChange(
                entityType = "book", entityId = "b1", operation = SyncChangeOp.UPDATE, version = 2,
                entityPayload = mapOf("id" to "b1", "name" to "账本-b1", "icon" to "📘")
            )),
            newCursor = 0, ownerId = "u1"
        )
        assertEquals("本地非照片时正常接受服务端 emoji", "📘",
            repository.listBooks().first { it.id == "b1" }.icon)
    }

    @Test
    fun accountPull_nonDataIcon_preservesLocalPhoto() = runBlocking {
        repository.insertAccount(account("a1", photoIcon), "m1")
        repository.removeMutation("m1")
        repository.applyServerChanges(
            listOf(ServerChange(
                entityType = "account", entityId = "a1", operation = SyncChangeOp.UPDATE, version = 2,
                entityPayload = mapOf("id" to "a1", "name" to "账户-a1", "icon" to "📙")
            )),
            newCursor = 0, ownerId = "u1"
        )
        assertEquals(photoIcon, repository.listAccounts().first { it.id == "a1" }.icon)
    }

    // ─── REQ-68（T18-4）：缺 icon 的兜底值是 emoji，不是英文裸词 ───

    @Test
    fun bookPull_iconMissing_fallsBackToBookEmoji() = runBlocking {
        repository.applyServerChanges(
            listOf(ServerChange(
                entityType = "book", entityId = "b1", operation = SyncChangeOp.INSERT, version = 1,
                // 服务端 book.icon 可空且无默认值 → 出网即为缺键
                entityPayload = mapOf("id" to "b1", "name" to "云端账本")
            )),
            newCursor = 0, ownerId = "u1"
        )
        assertEquals(DefaultIcons.BOOK, repository.listBooks().first { it.id == "b1" }.icon)
    }

    @Test
    fun accountPull_iconMissing_fallsBackToAccountEmoji() = runBlocking {
        repository.applyServerChanges(
            listOf(ServerChange(
                entityType = "account", entityId = "a1", operation = SyncChangeOp.INSERT, version = 1,
                entityPayload = mapOf("id" to "a1", "name" to "云端账户")
            )),
            newCursor = 0, ownerId = "u1"
        )
        assertEquals(DefaultIcons.ACCOUNT, repository.listAccounts().first { it.id == "a1" }.icon)
    }

    /** bootstrap 是另一条读口（serverChangeMapToBook/Account），同一兜底必须一致。 */
    @Test
    fun bootstrap_iconMissing_fallsBackToEmoji() = runBlocking {
        repository.applyBootstrap(
            BootstrapResponse(
                books = listOf(mapOf("id" to "b1", "name" to "云端账本", "version" to "1")),
                categories = emptyList(),
                accounts = listOf(mapOf("id" to "a1", "name" to "云端账户", "version" to "1")),
                transactions = emptyList(),
                budgets = emptyList(),
                cursor = 1,
                checksum = "ignored"
            ),
            ownerId = "u1"
        )
        assertEquals(DefaultIcons.BOOK, repository.listBooks().first { it.id == "b1" }.icon)
        assertEquals(DefaultIcons.ACCOUNT, repository.listAccounts().first { it.id == "a1" }.icon)
    }
}
