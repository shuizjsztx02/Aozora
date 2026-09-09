package com.aozora.app.db

import com.aozora.app.db.entity.ChatPersonaEntity
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
 * 第十一轮 T11-7（REQ-28 / 决策 #8）：聊天角色 Repository 层 JVM 测试。
 * 核心不变量（镜像 RepositoryScheduledTest）：角色写操作**绝不产生 pending
 * mutation**（服务端不认 chat_personas，入队=422 毒丸）；种子幂等靠
 * personas_seeded 标记——首启播种、删光不重播。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class RepositoryPersonaTest {

    private lateinit var db: AozoraDatabase
    private lateinit var repository: Repository

    @Before
    fun setUp() {
        db = AozoraDatabase.createInMemory(RuntimeEnvironment.getApplication())
        repository = Repository(db)
    }

    @After
    fun tearDown() { db.close() }

    private fun persona(id: String, version: Int = 1, name: String = "测试角色") = ChatPersonaEntity(
        id = id, name = name, avatar = "🙂", persona = "一段人设",
        createdAt = 1000L, updatedAt = 1000L, archivedAt = null, version = version
    )

    // ─── 种子（决策 #8 幂等语义） ───

    @Test
    fun seed_firstRun_insertsFivePersonasAndMarks() = runBlocking {
        repository.ensureSeedPersonas()
        val seeded = repository.listChatPersonas()
        assertEquals(5, seeded.size)
        // 决策 #8 首版角色清单（createdAt 同毫秒，展示顺序不作断言；小贝为回退锚点）
        assertEquals(
            setOf("小贝", "老陈", "饭团", "阿娘", "冷面管家"),
            seeded.map { it.name }.toSet()
        )
        assertTrue(seeded.any { it.name == "小贝" })
        // 人设文本 100–2000 字（决策 #8），头像为 emoji
        for (p in seeded) {
            assertTrue("人设过短: ${p.name}", p.persona.length >= 100)
            assertTrue("人设超上限", p.persona.length <= Repository.MAX_PERSONA_TEXT_LEN)
            assertNotNull(p.avatar)
            assertTrue("id 必须是 UUID（meta 键回链/未来扩展）", java.util.UUID.fromString(p.id).toString() == p.id)
        }
        assertEquals("1", repository.getMeta(SyncMetaEntity.KEY_PERSONAS_SEEDED))
    }

    @Test
    fun seed_secondRun_doesNotDuplicate() = runBlocking {
        repository.ensureSeedPersonas()
        repository.ensureSeedPersonas()
        assertEquals(5, repository.listChatPersonas().size)
    }

    @Test
    fun seed_afterUserDeletedAll_noReplay() = runBlocking {
        // 删光 = 用户意愿，mark 挡住重播（决策 #8）
        repository.ensureSeedPersonas()
        repository.listChatPersonas().forEach { repository.archiveChatPersona(it.id) }
        repository.ensureSeedPersonas()
        assertTrue(repository.listChatPersonas().isEmpty())
    }

    // ─── CRUD 契约（镜像 scheduled） ───

    @Test
    fun writes_never_enqueuePendingMutations() = runBlocking {
        repository.insertChatPersona(persona("p1"))
        repository.updateChatPersona(persona("p1", version = 2))
        repository.archiveChatPersona("p1")
        assertEquals(0, repository.listPendingMutations().size)
    }

    @Test
    fun update_sameVersion_rejected() = runBlocking {
        repository.insertChatPersona(persona("p1"))
        repository.updateChatPersona(persona("p1", version = 2, name = "改名"))
        assertEquals("改名", repository.getChatPersona("p1")!!.name)
        var threw = false
        try { repository.updateChatPersona(persona("p1", version = 2, name = "再改")) }
        catch (e: IllegalArgumentException) { threw = true }
        assertTrue(threw)
        assertEquals("改名", repository.getChatPersona("p1")!!.name)
    }

    @Test
    fun insert_rejectsNameOver16_andPersonaOver2000() = runBlocking {
        var threw = false
        try { repository.insertChatPersona(persona("p1", name = "a".repeat(17))) }
        catch (e: IllegalArgumentException) { threw = true }
        assertTrue("昵称 >16 必须拒绝", threw)
        threw = false
        try {
            repository.insertChatPersona(persona("p1").copy(persona = "x".repeat(2001)))
        } catch (e: IllegalArgumentException) { threw = true }
        assertTrue("人设 >2000 必须拒绝", threw)
        // 边界合法：16 字 / 2000 字
        repository.insertChatPersona(persona("p1", name = "a".repeat(16)))
    }

    @Test
    fun archive_activeThenList_excludes_reGetStillWorks() = runBlocking {
        repository.insertChatPersona(persona("p1"))
        repository.archiveChatPersona("p1")
        assertTrue(repository.listChatPersonas().isEmpty())
        assertNotNull(repository.getChatPersona("p1")) // 软删行保留（镜像 scheduled）
        var threw = false
        try { repository.archiveChatPersona("p1") } catch (e: IllegalArgumentException) { threw = true }
        assertTrue("重复归档拒绝", threw)
    }

    @Test
    fun clearAllData_wipesEntitiesAndSeedMark() = runBlocking {
        repository.ensureSeedPersonas()
        repository.setMeta(SyncMetaEntity.KEY_ACTIVE_PERSONA_ID, "p-x")
        repository.clearAllData()
        assertTrue(repository.listChatPersonas().isEmpty())
        // clearAllData 清 sync_meta → mark 与 active 指针一并归零（重播种子属预期）
        assertNull(repository.getMeta(SyncMetaEntity.KEY_PERSONAS_SEEDED))
        assertNull(repository.getMeta(SyncMetaEntity.KEY_ACTIVE_PERSONA_ID))
    }

    @Test
    fun clearAllEntities_keepsPersonasAndSeedMark() = runBlocking {
        // bootstrap 重建只清同步实体；角色与自管键是设备本地数据（同 savings/scheduled）
        repository.ensureSeedPersonas()
        repository.clearAllEntities()
        assertEquals(5, repository.listChatPersonas().size)
        assertEquals("1", repository.getMeta(SyncMetaEntity.KEY_PERSONAS_SEEDED))
    }
}
