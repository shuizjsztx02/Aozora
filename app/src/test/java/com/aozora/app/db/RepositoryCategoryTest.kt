package com.aozora.app.db

import com.aozora.app.db.entity.CategoryEntity
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
 * 第八轮 REQ-8：分类自定义（新建 / 二级 / 图标 / 更新）Repository 层 JVM 测试。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class RepositoryCategoryTest {

    private lateinit var db: AozoraDatabase
    private lateinit var repository: Repository

    @Before
    fun setUp() {
        db = AozoraDatabase.createInMemory(RuntimeEnvironment.getApplication())
        repository = Repository(db)
    }

    @After
    fun tearDown() { db.close() }

    private fun cat(id: String, kind: String, name: String, parentId: String? = null,
                    icon: String? = null, version: Int = 1) =
        CategoryEntity(
            id = id, ownerId = "local", bookId = null, parentId = parentId,
            kind = kind, name = name, icon = icon, archivedAt = null, version = version
        )

    @Test
    fun insert_customCategory_enqueuesMutation() = runBlocking {
        repository.insertCategory(cat("c1", "expense", "宠物", icon = "🐶"), "m1")
        val c = repository.listCategories().first { it.id == "c1" }
        assertEquals("宠物", c.name)
        assertEquals("🐶", c.icon)
        assertTrue(repository.listPendingMutations().any { it.entityId == "c1" && it.operation == "insert" })
    }

    @Test
    fun update_nameIcon_enqueuesUpdateWithExpectedVersion() = runBlocking {
        repository.insertCategory(cat("c1", "expense", "宠物"), "m1")
        repository.updateCategory(cat("c1", "expense", "萌宠", icon = "data:image/jpeg;base64,AAA", version = 2), "m2")
        val c = repository.listCategories().first { it.id == "c1" }
        assertEquals("萌宠", c.name)
        assertEquals("data:image/jpeg;base64,AAA", c.icon)
        val upd = repository.listPendingMutations().first { it.entityId == "c1" && it.operation == "update" }
        assertEquals(1, upd.expectedVersion)
    }

    @Test
    fun update_requiresVersionIncrease() = runBlocking {
        repository.insertCategory(cat("c1", "expense", "宠物"), "m1")
        var threw = false
        try { repository.updateCategory(cat("c1", "expense", "宠物", version = 1), "m2") }
        catch (e: IllegalArgumentException) { threw = true }
        assertTrue(threw)
    }

    @Test
    fun twoLevel_allowed() = runBlocking {
        repository.insertCategory(cat("p", "expense", "餐饮"), "m1")
        repository.insertCategory(cat("s", "expense", "火锅", parentId = "p"), "m2")
        val s = repository.listCategories().first { it.id == "s" }
        assertEquals("p", s.parentId)
    }

    @Test
    fun threeLevel_rejected() = runBlocking {
        repository.insertCategory(cat("p", "expense", "餐饮"), "m1")
        repository.insertCategory(cat("s", "expense", "火锅", parentId = "p"), "m2")
        // s 已是二级，再挂到 s 下形成三级 → 拒绝
        var threw = false
        try { repository.insertCategory(cat("t", "expense", "羊肉锅", parentId = "s"), "m3") }
        catch (e: IllegalArgumentException) { threw = true }
        assertTrue(threw)
    }

    @Test
    fun parentKindMismatch_rejected() = runBlocking {
        repository.insertCategory(cat("p", "income", "工资"), "m1")
        var threw = false
        try { repository.insertCategory(cat("s", "expense", "误挂", parentId = "p"), "m2") }
        catch (e: IllegalArgumentException) { threw = true }
        assertTrue(threw)
    }
}
