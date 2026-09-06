package com.aozora.app.db.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Update
import com.aozora.app.db.entity.AccountEntity

@Dao
interface AccountDao {

    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insert(account: AccountEntity)

    @Update
    suspend fun update(account: AccountEntity)

    @Query("SELECT * FROM accounts WHERE id = :id")
    suspend fun getById(id: String): AccountEntity?

    @Query("SELECT * FROM accounts WHERE archivedAt IS NULL ORDER BY name")
    suspend fun listActive(): List<AccountEntity>

    @Query("UPDATE accounts SET archivedAt = :archivedAt, version = version + 1 WHERE id = :id")
    suspend fun archive(id: String, archivedAt: Long)

    /** T-M3-02: 同步合并 upsert 路径 — REPLACE 语义。仅由 Repository.applyServerChanges 调用。 */
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertFromSync(account: AccountEntity)

    /** T-M3-02: 同步合并归档路径 — 仅更新 archivedAt 与 version。 */
    @Query("UPDATE accounts SET archivedAt = :archivedAt, version = :version WHERE id = :id")
    suspend fun applyServerArchive(id: String, archivedAt: Long, version: Int)

    /** T-M3-03: 清空所有账户记录（bootstrap 重建前使用）。 */
    @Query("DELETE FROM accounts")
    suspend fun clearAll()
}
