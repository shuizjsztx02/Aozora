package com.aozora.app.db.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Update
import com.aozora.app.db.entity.CategoryEntity

@Dao
interface CategoryDao {

    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insert(category: CategoryEntity)

    @Update
    suspend fun update(category: CategoryEntity)

    @Query("SELECT * FROM categories WHERE id = :id")
    suspend fun getById(id: String): CategoryEntity?

    @Query("SELECT * FROM categories WHERE archivedAt IS NULL ORDER BY kind, name")
    suspend fun listActive(): List<CategoryEntity>

    @Query("SELECT * FROM categories WHERE kind = :kind AND archivedAt IS NULL ORDER BY name")
    suspend fun listByKind(kind: String): List<CategoryEntity>

    @Query("UPDATE categories SET archivedAt = :archivedAt, version = version + 1 WHERE id = :id")
    suspend fun archive(id: String, archivedAt: Long)

    /** T-M3-02: 同步合并 upsert 路径 — REPLACE 语义。仅由 Repository.applyServerChanges 调用。 */
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertFromSync(category: CategoryEntity)

    /** T-M3-02: 同步合并归档路径 — 仅更新 archivedAt 与 version。 */
    @Query("UPDATE categories SET archivedAt = :archivedAt, version = :version WHERE id = :id")
    suspend fun applyServerArchive(id: String, archivedAt: Long, version: Int)

    /** T-M3-03: 清空所有分类记录（bootstrap 重建前使用）。 */
    @Query("DELETE FROM categories")
    suspend fun clearAll()
}
