package com.aozora.app.db

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import com.aozora.app.db.dao.*
import com.aozora.app.db.entity.*

/**
 * Aozora Room 数据库。
 *
 * version 说明：
 * - v1：初始 schema（T-M1-03）
 * 后续 migration 使用 RoomMigration 追加，不做破坏性 reset。
 */
@Database(
    entities = [
        TransactionEntity::class,
        BookEntity::class,
        CategoryEntity::class,
        AccountEntity::class,
        BudgetEntity::class,
        PendingMutationEntity::class,
    ],
    version = 1,
    exportSchema = true
)
abstract class AozoraDatabase : RoomDatabase() {

    abstract fun transactionDao(): TransactionDao
    abstract fun bookDao(): BookDao
    abstract fun categoryDao(): CategoryDao
    abstract fun accountDao(): AccountDao
    abstract fun budgetDao(): BudgetDao
    abstract fun pendingMutationDao(): PendingMutationDao

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
            Room.databaseBuilder(context.applicationContext, AozoraDatabase::class.java, name).build()

        /** 仅用于测试：创建内存数据库 */
        fun createInMemory(context: Context): AozoraDatabase {
            return Room.inMemoryDatabaseBuilder(
                context.applicationContext,
                AozoraDatabase::class.java
            ).build()
        }
    }
}
