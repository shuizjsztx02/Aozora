package com.aozora.app.crashlytics

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Update

/**
 * T-M5-05b: 崩溃报告 DAO。
 *
 * 全部 suspend —— 写入路径在 UncaughtExceptionHandler 内，但 Room 的
 * execute() 调用是同步的，由 [CrashReporter] 在 handler 里以非协程方式
 * 通过 runBlocking 调用 [insertBlocking]。
 */
@Dao
interface CrashReportDao {

    /** 全局 handler 中同步写入（崩溃路径不能 suspend）。 */
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    fun insertBlocking(report: CrashReportEntity)

    @Query("SELECT * FROM crash_reports ORDER BY reportedAt ASC")
    suspend fun listPending(): List<CrashReportEntity>

    @Update
    suspend fun update(report: CrashReportEntity)

    @Query("DELETE FROM crash_reports WHERE id = :id")
    suspend fun deleteById(id: String)

    @Query("SELECT COUNT(*) FROM crash_reports")
    suspend fun count(): Int
}
