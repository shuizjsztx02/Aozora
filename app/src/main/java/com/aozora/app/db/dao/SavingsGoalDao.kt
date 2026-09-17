package com.aozora.app.db.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Update
import com.aozora.app.db.entity.SavingsGoalEntity

/**
 * 存钱目标 DAO（第八轮 REQ-7）。T-M7-01 起接入同步：云端权威 + 本地副本。
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

    /** 上行迁移：读取**全部**行（含归档）用于判定"本地还有哪些未上行"。 */
    @Query("SELECT * FROM savings_goals ORDER BY createdAt ASC")
    suspend fun listAll(): List<SavingsGoalEntity>

    @Query("UPDATE savings_goals SET archivedAt = :archivedAt, updatedAt = :archivedAt, version = version + 1 WHERE id = :id")
    suspend fun archive(id: String, archivedAt: Long)

    /** T-M7-01: 同步合并 upsert 路径 — REPLACE 语义。仅由 Repository.applyServerChanges/applyBootstrap 调用。 */
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertFromSync(goal: SavingsGoalEntity)

    /** T-M7-01: 同步合并归档路径 — 仅更新 archivedAt 与 version。 */
    @Query("UPDATE savings_goals SET archivedAt = :archivedAt, version = :version WHERE id = :id")
    suspend fun applyServerArchive(id: String, archivedAt: Long, version: Int)

    /** T-M7-01: 清空所有存钱目标（bootstrap 重建前使用）。 */
    @Query("DELETE FROM savings_goals")
    suspend fun clearAll()
}
