package com.aozora.app.db

import com.aozora.app.db.entity.AccountEntity
import com.aozora.app.db.entity.BookEntity
import com.aozora.app.db.entity.SyncMetaEntity
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * T9-5a（REQ-11 / 决策 #2/#3）：sync_meta 存储 + 列表出口整形 + 默认优先。
 *
 * 覆盖：
 * - meta get/set 往返
 * - listBooksForUi：book_order 重排生效、未列项垫后、总账本恒首位、坏 orderJson 容错
 * - getEntryBook：default_book_id 优先；默认指向 master 时回退（master 防御）
 * - listAccountsForUi / 默认账户回退链
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class RepositoryMetaOrderTest {

    private lateinit var db: AozoraDatabase
    private lateinit var repository: Repository

    @Before
    fun setUp() {
        db = AozoraDatabase.createInMemory(RuntimeEnvironment.getApplication())
        repository = Repository(db)
    }

    @After
    fun tearDown() { db.close() }

    private fun book(id: String, name: String) = BookEntity(
        id = id, ownerId = "local", name = name, icon = "📒", version = 1, archivedAt = null
    )

    private fun account(id: String, name: String) = AccountEntity(
        id = id, ownerId = "local", name = name, icon = "💳", version = 1, archivedAt = null,
        accountType = null
    )

    @Test
    fun meta_setAndGet_roundTrips(): Unit = runBlocking {
        assertNull(repository.getMeta(SyncMetaEntity.KEY_DEFAULT_BOOK_ID))
        repository.setMeta(SyncMetaEntity.KEY_DEFAULT_BOOK_ID, "abc")
        assertEquals("abc", repository.getMeta(SyncMetaEntity.KEY_DEFAULT_BOOK_ID))
        // 覆写
        repository.setMeta(SyncMetaEntity.KEY_DEFAULT_BOOK_ID, "def")
        assertEquals("def", repository.getMeta(SyncMetaEntity.KEY_DEFAULT_BOOK_ID))
    }

    @Test
    fun bookOrder_reordered_unlistedTailPadded_masterAlwaysFirst(): Unit = runBlocking {
        repository.insertBook(book("b1", "A账本"), "m1")
        repository.insertBook(book("b2", "B账本"), "m2")
        repository.insertBook(book("b3", "C账本"), "m3")
        repository.setMeta(SyncMetaEntity.KEY_MASTER_BOOK_ID, "b3")
        // 顺序：b2 在前；b3(master) 即便写进 order 也恒被顶到首位
        repository.setMeta(SyncMetaEntity.KEY_BOOK_ORDER, """["b3","b2"]""")

        val ordered = repository.listBooksForUi().map { it.id }
        assertEquals(listOf("b3", "b2", "b1"), ordered)

        // 未列项按 name 序（listActive ORDER BY name）垫后
        repository.setMeta(SyncMetaEntity.KEY_BOOK_ORDER, """["b2"]""")
        val again = repository.listBooksForUi().map { it.id }
        assertEquals(listOf("b3", "b2", "b1"), again)
    }

    @Test
    fun bookOrder_malformedJson_fallsBackToNameOrder(): Unit = runBlocking {
        repository.insertBook(book("b1", "A账本"), "m1")
        repository.insertBook(book("b2", "B账本"), "m2")
        repository.setMeta(SyncMetaEntity.KEY_BOOK_ORDER, "not-json")
        assertEquals(listOf("b1", "b2"), repository.listBooksForUi().map { it.id })
    }

    @Test
    fun entryBook_defaultIdWins(): Unit = runBlocking {
        repository.ensureSeedDefaults()
        val masterId = repository.getMasterBookId()!!
        val books = repository.listBooks().filter { it.id != masterId }
        val target = books.last() // 故意选 name 序非第一个
        repository.setMeta(SyncMetaEntity.KEY_DEFAULT_BOOK_ID, target.id)
        assertEquals(target.id, repository.getEntryBook()?.id)
    }

    @Test
    fun entryBook_defaultPointingToMaster_fallsBack(): Unit = runBlocking {
        repository.ensureSeedDefaults()
        val masterId = repository.getMasterBookId()!!
        repository.setMeta(SyncMetaEntity.KEY_DEFAULT_BOOK_ID, masterId)
        // master 防御：默认指向总账本 → 回退第一个非 master
        val entry = repository.getEntryBook()
        assertNotNull(entry)
        assertNotEquals(masterId, entry!!.id)
    }

    @Test
    fun entryBook_defaultMissingBook_fallsBack(): Unit = runBlocking {
        repository.ensureSeedDefaults()
        repository.setMeta(SyncMetaEntity.KEY_DEFAULT_BOOK_ID, "00000000-0000-4000-8000-000000000000")
        val entry = repository.getEntryBook()
        assertNotNull(entry)
        assertNotEquals(repository.getMasterBookId(), entry!!.id)
    }

    @Test
    fun accountList_reordered_andDefaultFallback(): Unit = runBlocking {
        repository.insertAccount(account("a1", "微信"), "m1")
        repository.insertAccount(account("a2", "支付宝"), "m2")
        repository.setMeta(SyncMetaEntity.KEY_ACCOUNT_ORDER, """["a2","a1"]""")
        assertEquals(listOf("a2", "a1"), repository.listAccountsForUi().map { it.id })

        // 默认账户优先；指向不存在 → 回退 listActive().first()（name 序：微信 U+5FAE < 支付宝 U+652F → a1）
        repository.setMeta(SyncMetaEntity.KEY_DEFAULT_ACCOUNT_ID, "a1")
        assertEquals("a1", repository.getDefaultAccount()?.id)
        repository.setMeta(SyncMetaEntity.KEY_DEFAULT_ACCOUNT_ID, "missing")
        assertEquals("a1", repository.getDefaultAccount()?.id)
    }
}
