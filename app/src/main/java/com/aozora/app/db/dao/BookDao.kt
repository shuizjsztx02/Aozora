package com.aozora.app.db.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Update
import com.aozora.app.db.entity.BookEntity

@Dao
interface BookDao {

    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insert(book: BookEntity)

    @Update
    suspend fun update(book: BookEntity)

    @Query("SELECT * FROM books WHERE id = :id")
    suspend fun getById(id: String): BookEntity?

    @Query("SELECT * FROM books WHERE archivedAt IS NULL ORDER BY name")
    suspend fun listActive(): List<BookEntity>

    @Query("SELECT * FROM books ORDER BY name")
    suspend fun listAll(): List<BookEntity>

    @Query("UPDATE books SET archivedAt = :archivedAt, version = version + 1 WHERE id = :id")
    suspend fun archive(id: String, archivedAt: Long)

    /** T-M3-02: 同步合并 upsert 路径 — REPLACE 语义。仅由 Repository.applyServerChanges 调用。 */
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertFromSync(book: BookEntity)

    /**
     * T-M3-02: 同步合并归档路径 — 仅更新 archivedAt 与 version。
     * 用于：服务端 pull 的 book delete 变更在客户端映射为归档。
     */
    @Query("UPDATE books SET archivedAt = :archivedAt, version = :version WHERE id = :id")
    suspend fun applyServerArchive(id: String, archivedAt: Long, version: Int)

    /** T-M3-03: 清空所有账本记录（bootstrap 重建前使用）。 */
    @Query("DELETE FROM books")
    suspend fun clearAll()
}
