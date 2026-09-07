package com.aozora.app.crashlytics

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

/**
 * T-M5-05b: 独立于业务库的崩溃报告数据库。
 *
 * 与 [com.aozora.app.db.AozoraDatabase] 分离的原因：
 * - 崩溃路径不能依赖业务库处于一致状态（可能正是业务库的 bug 导致了崩溃）。
 * - 崩溃数据生命周期与业务数据不同（上报后删除，无需同步/备份）。
 * - 简化 schema —— 单表、无迁移。
 */
@Database(
    entities = [CrashReportEntity::class],
    version = 1,
    exportSchema = false,
)
abstract class CrashReportDatabase : RoomDatabase() {

    abstract fun crashReportDao(): CrashReportDao

    companion object {
        const val DATABASE_NAME = "aozora-crashes.db"

        @Volatile
        private var INSTANCE: CrashReportDatabase? = null

        fun getInstance(context: Context): CrashReportDatabase {
            return INSTANCE ?: synchronized(this) {
                INSTANCE ?: Room.databaseBuilder(
                    context.applicationContext,
                    CrashReportDatabase::class.java,
                    DATABASE_NAME
                ).build().also { INSTANCE = it }
            }
        }
    }
}
