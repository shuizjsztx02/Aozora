package com.aozora.app.db

import androidx.room.Room
import androidx.room.testing.MigrationTestHelper
import androidx.sqlite.db.framework.FrameworkSQLiteOpenHelperFactory
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.aozora.app.db.migrations.MIGRATION_1_2
import com.aozora.app.db.entity.*
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * T-M3-02: Room Migration 1 → 2 集成测试。
 *
 * 验证：
 * - sync_meta 表被创建且可读。
 * - pending_mutations 新增 expectedVersion / lastError 列。
 * - 现有数据在迁移后保留。
 */
@RunWith(AndroidJUnit4::class)
class Migration1To2Test {

    @get:Rule
    @Suppress("DEPRECATION")
    val helper = MigrationTestHelper(
        InstrumentationRegistry.getInstrumentation(),
        AozoraDatabase::class.java.canonicalName!!,
        FrameworkSQLiteOpenHelperFactory()
    )

    @Test
    fun migration1to2_createsSyncMetaAndNewColumns() = runBlocking {
        // 1. 创建 v1 schema
        helper.createDatabase(TEST_DB, 1).use { dbV1 ->
            // 写入一条 v1 形态的 pending mutation（仅旧列）
            dbV1.execSQL(
                """
                INSERT INTO pending_mutations
                (mutationId, entityType, entityId, operation, payload, status, retryCount, createdAt)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """.trimIndent(),
                arrayOf<Any>("m-old", "transaction", "t1", "insert", "{}", "pending", 0, 1000L)
            )
        }

        // 2. 打开 v2 — 自动触发 migration
        val db = Room.databaseBuilder(
            InstrumentationRegistry.getInstrumentation().targetContext,
            AozoraDatabase::class.java,
            TEST_DB
        )
            .addMigrations(MIGRATION_1_2)
            .build()

        try {
            // 3. sync_meta 表存在并可写
            val repo = Repository(db)
            repo.setSyncCursor(99L)
            assertEquals(99L, repo.getSyncCursor())

            // 4. 旧 mutation 被保留且新列默认为 null
            val mutations = repo.listPendingMutations()
            assertEquals(1, mutations.size)
            val pm = mutations[0]
            assertEquals("m-old", pm.mutationId)
            assertEquals("transaction", pm.entityType)
            assertNull(pm.expectedVersion)
            assertNull(pm.lastError)
        } finally {
            db.close()
        }
    }

    @Test
    fun freshV2Database_hasSyncMetaTableAndNewColumns() = runBlocking {
        val ctx = InstrumentationRegistry.getInstrumentation().targetContext
        ctx.deleteDatabase(TEST_DB_FRESH)
        val db = Room.inMemoryDatabaseBuilder(ctx, AozoraDatabase::class.java).build()
        try {
            val repo = Repository(db)
            // sync_meta 可读写
            repo.setSyncCursor(123L)
            assertEquals(123L, repo.getSyncCursor())
            // 新列可写
            repo.insertBook(BookEntity("b1", "o", "B", "", 1, null), "m1")
            repo.insertBudget(
                BudgetEntity("bud1", "b1", "monthly", null, "2026-09", 1000L, 1),
                "m2"
            )
            // expectedVersion=1 的 update mutation
            repo.updateBudget(
                BudgetEntity("bud1", "b1", "monthly", null, "2026-09", 2000L, 2),
                "m3"
            )
            val mutations = repo.listPendingMutations()
            val updateMutation = mutations.first { it.mutationId == "m3" }
            assertEquals(1, updateMutation.expectedVersion)
            assertNull(updateMutation.lastError)
        } finally {
            db.close()
        }
    }

    companion object {
        private const val TEST_DB = "test-migration-1-2.db"
        private const val TEST_DB_FRESH = "test-migration-1-2-fresh.db"
    }
}
