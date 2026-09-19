package com.aozora.app.db

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import com.aozora.app.db.dao.*
import com.aozora.app.db.entity.*
import com.aozora.app.db.migrations.MIGRATION_1_2
import com.aozora.app.db.migrations.MIGRATION_2_3
import com.aozora.app.db.migrations.MIGRATION_3_4
import com.aozora.app.db.migrations.MIGRATION_4_5
import com.aozora.app.db.migrations.MIGRATION_5_6
import com.aozora.app.db.migrations.MIGRATION_6_7
import com.aozora.app.db.migrations.MIGRATION_7_8
import com.aozora.app.db.migrations.MIGRATION_8_9

/**
 * Aozora Room 数据库。
 *
 * version 说明：
 * - v1：初始 schema（T-M1-03）
 * - v2：新增 sync_meta、pending_mutations 增加 expectedVersion/lastError
 *        （T-M3-02 同步引擎）
 * - v4：accounts 新增 accountType；双账本种子（REQ-5/REQ-6，T7-1）
 * - v5：categories 新增 icon；新增 savings_goals 表（第八轮 REQ-8/REQ-7）
 * - v6：savings_goals 新增 periodValue/periodUnit（第九轮 REQ-14）
 * - v7：transactions 新增 includeInBudget（第十轮 REQ-17，默认计入）
 * - v8：新增 scheduled_rules / chat_personas 表（第十一轮 REQ-26/REQ-28，
 *        一张迁移建两表）。两表当时均仅本地不同步；T-M7-01 起与
 *        savings_goals 一并上云（D-PROD-03），Room 结构未变故无需新迁移。
 * - v9：scheduled_rules 新增 runHour/runMinute 执行时刻列（第十四轮 REQ-43，
 *        T14-5 决策 #1 唯一声明者；存量行默认 8/0=保持 08:00 语义）
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
        UserConsentEntity::class,
        SavingsGoalEntity::class,
        ScheduledRuleEntity::class,
        ChatPersonaEntity::class,
    ],
    version = 9,
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
    abstract fun userConsentDao(): UserConsentDao
    abstract fun savingsGoalDao(): SavingsGoalDao
    abstract fun scheduledRuleDao(): ScheduledRuleDao
    abstract fun chatPersonaDao(): ChatPersonaDao

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
                .addMigrations(MIGRATION_1_2, MIGRATION_2_3, MIGRATION_3_4, MIGRATION_4_5, MIGRATION_5_6, MIGRATION_6_7, MIGRATION_7_8, MIGRATION_8_9)
                .build()

        /** 仅用于测试：创建内存数据库，迁移同样应用 */
        fun createInMemory(context: Context): AozoraDatabase {
            return Room.inMemoryDatabaseBuilder(
                context.applicationContext,
                AozoraDatabase::class.java
            ).addMigrations(MIGRATION_1_2, MIGRATION_2_3, MIGRATION_3_4, MIGRATION_4_5, MIGRATION_5_6, MIGRATION_6_7, MIGRATION_7_8, MIGRATION_8_9).build()
        }
    }
}
