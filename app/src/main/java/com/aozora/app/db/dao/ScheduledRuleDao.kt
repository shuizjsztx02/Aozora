package com.aozora.app.db.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Update
import com.aozora.app.db.entity.ScheduledRuleEntity

/**
 * 定时记账规则 DAO（第十一轮 REQ-26）。仅本地：无同步 upsert/archive 路径。
 */
@Dao
interface ScheduledRuleDao {

    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insert(rule: ScheduledRuleEntity)

    @Update
    suspend fun update(rule: ScheduledRuleEntity)

    @Query("SELECT * FROM scheduled_rules WHERE id = :id")
    suspend fun getById(id: String): ScheduledRuleEntity?

    @Query("SELECT * FROM scheduled_rules WHERE archivedAt IS NULL ORDER BY createdAt ASC")
    suspend fun listActive(): List<ScheduledRuleEntity>

    @Query("UPDATE scheduled_rules SET archivedAt = :archivedAt, updatedAt = :archivedAt, version = version + 1 WHERE id = :id")
    suspend fun archive(id: String, archivedAt: Long)
}
