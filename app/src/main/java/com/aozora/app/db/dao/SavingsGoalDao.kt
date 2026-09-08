package com.aozora.app.db.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Update
import com.aozora.app.db.entity.SavingsGoalEntity

/**
 * 存钱目标 DAO（第八轮 REQ-7）。仅本地：无同步 upsert/archive 路径。
 */
@Dao
interface SavingsGoalDao {

    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insert(goal: SavingsGoalEntity)

    @Update
    suspend fun update(goal: SavingsGoalEntity)

    @Query("SELECT * FROM savings_goals WHERE id = :id")
    suspend fun getById(id: String): SavingsGoalEntity?

    @Query("SELECT * FROM savings_goals WHERE archivedAt IS NULL ORDER BY createdAt ASC")
    suspend fun listActive(): List<SavingsGoalEntity>

    @Query("UPDATE savings_goals SET archivedAt = :archivedAt, updatedAt = :archivedAt, version = version + 1 WHERE id = :id")
    suspend fun archive(id: String, archivedAt: Long)
}
