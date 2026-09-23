package com.aozora.app.db

import com.aozora.app.db.entity.SyncMetaEntity
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * T18-10（REQ-72 追加）：**一次性游标重置**守护用例。
 *
 * 背景：服务端 `_serialize_entity` 自 T-M3 起把 `occurred_at` 出网成 ISO 字符串，
 * 客户端 `toTransaction()` 此前只认 `toLongOrNull()` → null → 调用方 `?: continue`
 * 静默丢弃，**游标照常推进** → 数据永久丢失。解析修复（epoch/ISO 双收）让新数据
 * 正常落地，但历史已丢的行不会自己回来。本方法把 cursor 置 0，触发一次从起点开始
 * 的全量 pull，让修复后的路径把漏掉的行拉回来。
 *
 * 契约：
 * 1. 首次调用：cursor 置 0 + 标记写入
 * 2. 二次调用：空操作（标记已存在，cursor 不变）
 * 3. 事务性：标记与 cursor 在同一事务内写入，崩溃安全
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class OneTimeCursorResetTest {

    private lateinit var db: AozoraDatabase
    private lateinit var repo: Repository

    @Before
    fun setUp() {
        // 用 createInMemory 而非 getInstance：后者缓存的 INSTANCE 在 close() 后不会清空，
        // Robolectric 每个用例重建 Application 时 AozoraApp.onCreate 的 runBlocking 会拿到
        // 已关闭的库，Room 2.7 关闭时取消协程作用域 → 查询任务永不执行 → 永久挂起。
        db = AozoraDatabase.createInMemory(RuntimeEnvironment.getApplication())
        repo = Repository(db)
    }

    @After
    fun tearDown() {
        db.close()
    }

    /**
     * 首次调用：把 cursor 置 0 + 写入标记。
     */
    @Test
    fun `首次调用：cursor 置 0 且标记写入`() = runBlocking {
        // 先置一个非零 cursor
        repo.setSyncCursor(541L)
        assertEquals(541L, repo.getSyncCursor())
        assertEquals(null, repo.getMeta(SyncMetaEntity.KEY_PULL_ISO_FIX_APPLIED))

        repo.applyOneTimeCursorResetIfNeeded()

        assertEquals("cursor 应被重置为 0", 0L, repo.getSyncCursor())
        assertEquals("标记应已写入", "1", repo.getMeta(SyncMetaEntity.KEY_PULL_ISO_FIX_APPLIED))
    }

    /**
     * 二次调用：空操作（标记已存在，cursor 不变）。
     *
     * 守护：二次启动不重放全量 pull。
     */
    @Test
    fun `二次调用：空操作，cursor 不变`() = runBlocking {
        // 模拟首次调用后的状态
        repo.setSyncCursor(0L)
        repo.setMeta(SyncMetaEntity.KEY_PULL_ISO_FIX_APPLIED, "1")
        // 假设首次调用后正常 pull 把 cursor 推到了最新
        repo.setSyncCursor(1234L)

        repo.applyOneTimeCursorResetIfNeeded()

        assertEquals("二次调用不应再动 cursor", 1234L, repo.getSyncCursor())
        assertEquals("标记应保持 '1'", "1", repo.getMeta(SyncMetaEntity.KEY_PULL_ISO_FIX_APPLIED))
    }

    /**
     * 事务性：标记与 cursor 同时写入，要么都成功要么都不成功。
     *
     * 注：事务原子性由 Room 保证，本用例守护的是"两件事都在同一个方法里做了"。
     * 崩溃恢复场景的测试需要更复杂的 harness，此处只做基本守护。
     */
    @Test
    fun `事务性：标记与 cursor 都在同一事务内写入`() = runBlocking {
        repo.setSyncCursor(100L)

        repo.applyOneTimeCursorResetIfNeeded()

        // 两者必须同时成立
        val cursorOk = repo.getSyncCursor() == 0L
        val markOk = repo.getMeta(SyncMetaEntity.KEY_PULL_ISO_FIX_APPLIED) == "1"
        assertEquals("cursor 与标记必须同时到位", cursorOk && markOk, true)
    }
}
