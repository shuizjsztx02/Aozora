package com.aozora.app.db

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import com.aozora.app.db.dao.*
import com.aozora.app.db.entity.*
import com.aozora.app.db.migrations.MIGRATION_1_2

/**
 * Aozora Room 数据库。
 *
 * version 说明：
 * - v1：初始 schema（T-M1-03）
 * - v2：新增 sync_meta、pending_mutations 增加 expectedVersion/lastError
 *        （T-M3-02 同步引擎）
 * 后续 migration 使用 MIGRATION_* 追加，不做破坏性 reset；缺失迁移时
 * Room 会拒绝打开并保留数据（fallbackToDestructiveMigration 未开启）。
 */
@Database(
    entities = [
        TransactionEntity::class,
        BookEntity::class,
        CategoryEntity::class,
        AccountEntity::class,
        BudgetEntity::class,
        PendingMutationEntity::class,
        SyncMetaEntity::class,
    ],
    version = 2,
    exportSchema = true
)
abstract class AozoraDatabase : RoomDatabase() {

    abstract fun transactionDao(): TransactionDao
    abstract fun bookDao(): BookDao
    abstract fun categoryDao(): CategoryDao
    abstract fun accountDao(): AccountDao
    abstract fun budgetDao(): BudgetDao
    abstract fun pendingMutationDao(): PendingMutationDao
    abstract fun syncMetaDao(): SyncMetaDao

    companion object {
        const val DATABASE_NAME = "aozora.db"

        @Volatile
        private var INSTANCE: AozoraDatabase? = null

        fun getInstance(context: Context): AozoraDatabase {
            return INSTANCE ?: synchronized(this) {
                INSTANCE ?: open(context)
                    .also { INSTANCE = it }
            }
        }

        internal fun open(context: Context, name: String = DATABASE_NAME): AozoraDatabase =
            Room.databaseBuilder(context.applicationContext, AozoraDatabase::class.java, name)
                .addMigrations(MIGRATION_1_2)
                .build()

        /** 仅用于测试：创建内存数据库，迁移同样应用 */
        fun createInMemory(context: Context): AozoraDatabase {
            return Room.inMemoryDatabaseBuilder(
                context.applicationContext,
                AozoraDatabase::class.java
            ).addMigrations(MIGRATION_1_2).build()
        }
    }
}
