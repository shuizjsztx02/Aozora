package com.aozora.app.db

import com.aozora.app.db.entity.AccountEntity
import com.aozora.app.db.entity.BookEntity
import com.aozora.app.db.entity.SyncMetaEntity
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
 * T7-1：双账本种子（REQ-5）、记账目标选择（决策 #2）与 updateAccount（REQ-6）的
 * Repository 层 JVM 测试。Robolectric + Room 内存数据库（同 WidgetDataHelperTest）。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class RepositorySeedTest {

    private lateinit var db: AozoraDatabase
    private lateinit var repository: Repository

    @Before
    fun setUp() {
        db = AozoraDatabase.createInMemory(RuntimeEnvironment.getApplication())
        repository = Repository(db)
    }

    @After
    fun tearDown() {
        db.close()
    }

    // ─── 决策 #1：三段幂等种子 ───

    @Test
    fun ensureSeedDefaults_firstLaunch_seedsBothBooksAndMasterKey(): Unit = runBlocking {
        repository.ensureSeedDefaults()

        val books = repository.listBooks()
        assertEquals(2, books.size)
        assertTrue(books.any { it.name == "默认账本" })
        val master = books.find { it.name == "总账本" }
        assertNotNull(master)
        assertEquals("📊", master!!.icon)

        // 键已写入且指向总账本
        assertEquals(master.id, repository.getMasterBookId())

        // 分类同批种子
        assertEquals(
            Repository.DEFAULT_EXPENSE_CATEGORIES.size + Repository.DEFAULT_INCOME_CATEGORIES.size,
            repository.listCategories().size
        )

        // 双账本各自入队 insert mutation（联网后 push 给服务端）
        val bookMutations = repository.listPendingMutations().filter { it.entityType == "book" }
        assertEquals(2, bookMutations.size)
    }

    @Test
    fun ensureSeedDefaults_isIdempotent(): Unit = runBlocking {
        repository.ensureSeedDefaults()
        val booksAfterFirst = repository.listBooks().size
        val masterKeyAfterFirst = repository.getMasterBookId()

        repository.ensureSeedDefaults()

        assertEquals(booksAfterFirst, repository.listBooks().size)
        assertEquals(masterKeyAfterFirst, repository.getMasterBookId())
    }

    @Test
    fun ensureSeedDefaults_upgrade_backfillsMasterBook(): Unit = runBlocking {
        // 模拟旧版本升级：只有"默认账本"，没有 master_book_id 键
        repository.insertBook(
            BookEntity(
                id = "b-default", ownerId = "local", name = "默认账本",
                icon = "📒", version = 1, archivedAt = null
            ),
            "m-b-default"
        )

        repository.ensureSeedDefaults()

        val books = repository.listBooks()
        assertEquals(2, books.size)
        val master = books.find { it.name == "总账本" }
        assertNotNull("升级后应补种总账本", master)
        assertEquals(master!!.id, repository.getMasterBookId())
        // 默认账本未被重复创建
        assertEquals(1, books.count { it.name == "默认账本" })
    }

    @Test
    fun ensureSeedDefaults_masterKeyPointsToArchivedBook_reseedsAndRewritesKey(): Unit = runBlocking {
        repository.ensureSeedDefaults()
        val oldMasterId = repository.getMasterBookId()!!

        // 直接经 DAO 归档总账本（模拟键指向失效 book，不经过 mutation 队列）
        db.bookDao().archive(oldMasterId, System.currentTimeMillis())

        repository.ensureSeedDefaults()

        val newMasterId = repository.getMasterBookId()
        assertNotNull(newMasterId)
        assertNotEquals("键应改写为新总账本 id", oldMasterId, newMasterId)
        val newMaster = repository.getBook(newMasterId!!)
        assertNotNull(newMaster)
        assertEquals("总账本", newMaster!!.name)
        assertNull(newMaster.archivedAt)
    }

    @Test
    fun ensureSeedDefaults_masterKeyPointsToMissingBook_reseeds(): Unit = runBlocking {
        repository.ensureSeedDefaults()
        val oldMasterId = repository.getMasterBookId()!!

        // 物理删除总账本行（模拟键指向缺失 book）
        db.bookDao().clearAll()
        repository.insertBook(
            BookEntity(
                id = "b-user", ownerId = "local", name = "我的账本",
                icon = "📒", version = 1, archivedAt = null
            ),
            "m-b-user"
        )

        repository.ensureSeedDefaults()

        val newMasterId = repository.getMasterBookId()
        assertNotNull(newMasterId)
        assertNotEquals(oldMasterId, newMasterId)
        assertEquals("总账本", repository.getBook(newMasterId!!)?.name)
        // 用户已有账本不受影响，且不重复补种默认账本
        assertEquals(2, repository.listBooks().size)
    }

    // ─── 决策 #2：记账目标账本 ───

    @Test
    fun getEntryBook_excludesMasterBook(): Unit = runBlocking {
        repository.ensureSeedDefaults()

        val entry = repository.getEntryBook()
        assertNotNull(entry)
        assertNotEquals(repository.getMasterBookId(), entry!!.id)
        assertEquals("默认账本", entry.name)
    }

    @Test
    fun getEntryBook_allBooksAreMaster_fallsBackToFirst(): Unit = runBlocking {
        repository.insertBook(
            BookEntity(
                id = "b1", ownerId = "local", name = "唯一账本",
                icon = "📒", version = 1, archivedAt = null
            ),
            "m-b1"
        )
        db.syncMetaDao().upsert(SyncMetaEntity(SyncMetaEntity.KEY_MASTER_BOOK_ID, "b1"))

        assertEquals("b1", repository.getEntryBook()?.id)
    }

    @Test
    fun getEntryBook_noBooks_returnsNull(): Unit = runBlocking {
        assertNull(repository.getEntryBook())
        assertNull(repository.getMasterBookId())
    }

    // ─── REQ-6：updateAccount ───

    @Test
    fun updateAccount_versionNotIncreased_throws(): Unit = runBlocking {
        seedAccount("a1", version = 2)

        try {
            repository.updateAccount(account("a1", version = 2), "m-eq")
            fail("equal version accepted")
        } catch (_: IllegalArgumentException) { }

        try {
            repository.updateAccount(account("a1", version = 1), "m-lt")
            fail("lower version accepted")
        } catch (_: IllegalArgumentException) { }

        // 抛错路径不落盘、不入队（仅剩 seed 的 1 条 insert mutation）
        assertEquals(2, repository.getAccount("a1")!!.version)
        assertEquals(1, repository.listPendingMutations().size)
    }

    @Test
    fun updateAccount_notFound_throws(): Unit = runBlocking {
        try {
            repository.updateAccount(account("missing", version = 2), "m-x")
            fail("missing account accepted")
        } catch (_: IllegalArgumentException) { }
    }

    @Test
    fun updateAccount_archived_throws(): Unit = runBlocking {
        seedAccount("a-arch", version = 1)
        repository.archiveAccount("a-arch", "m-arch")

        try {
            repository.updateAccount(account("a-arch", version = 2), "m-x")
            fail("archived account accepted")
        } catch (_: IllegalArgumentException) { }
    }

    @Test
    fun updateAccount_success_enqueuesUpdateMutationWithExpectedVersion(): Unit = runBlocking {
        seedAccount("a1", version = 1, accountType = null)

        repository.updateAccount(account("a1", version = 2, accountType = "wechat"), "m-upd")

        val updated = repository.getAccount("a1")!!
        assertEquals(2, updated.version)
        assertEquals("wechat", updated.accountType)

        // 种子/插入的 mutation 已存在，取本条 update
        val mutation = repository.listPendingMutations().single { it.operation == "update" }
        assertEquals("account", mutation.entityType)
        assertEquals("a1", mutation.entityId)
        assertEquals(1, mutation.expectedVersion)

        val payload = JSONObject(mutation.payload)
        assertEquals("wechat", payload.getString("accountType"))
        assertEquals(2, payload.getInt("version"))
    }

    @Test
    fun accountToJson_nullAccountType_serializedAsJsonNull(): Unit = runBlocking {
        seedAccount("a-null", version = 1)

        repository.updateAccount(account("a-null", version = 2, name = "新名称"), "m-upd")

        val payload = JSONObject(
            repository.listPendingMutations().single { it.operation == "update" }.payload
        )
        assertTrue(payload.has("accountType"))
        assertTrue(payload.isNull("accountType"))
    }

    // ─── 测试数据 helper ───

    private fun account(
        id: String,
        version: Int,
        name: String = "现金",
        accountType: String? = null,
    ) = AccountEntity(
        id = id, ownerId = "owner1", name = name, icon = "cash",
        accountType = accountType, archivedAt = null, version = version
    )

    private suspend fun seedAccount(id: String, version: Int, accountType: String? = null) {
        repository.insertAccount(
            account(id, version = version, accountType = accountType), "m-seed-$id"
        )
    }
}
