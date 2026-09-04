package com.aozora.app.db.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Update
import com.aozora.app.db.entity.TransactionEntity

@Dao
interface TransactionDao {

    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insert(transaction: TransactionEntity)

    @Update
    suspend fun update(transaction: TransactionEntity)

    @Query("SELECT * FROM transactions WHERE id = :id")
    suspend fun getById(id: String): TransactionEntity?

    @Query("SELECT * FROM transactions WHERE bookId = :bookId AND deletedAt IS NULL ORDER BY bizDate DESC, occurredAt DESC")
    suspend fun listByBook(bookId: String): List<TransactionEntity>

    @Query("SELECT * FROM transactions WHERE bizDate BETWEEN :startDate AND :endDate AND deletedAt IS NULL ORDER BY bizDate DESC, occurredAt DESC")
    suspend fun listByDateRange(startDate: String, endDate: String): List<TransactionEntity>

    @Query("SELECT * FROM transactions WHERE bookId = :bookId AND bizDate BETWEEN :startDate AND :endDate AND deletedAt IS NULL ORDER BY bizDate DESC, occurredAt DESC")
    suspend fun listByBookAndDateRange(bookId: String, startDate: String, endDate: String): List<TransactionEntity>

    /** 软删除：设置 deletedAt 时间戳 */
    @Query("UPDATE transactions SET deletedAt = :deletedAt, version = version + 1 WHERE id = :id")
    suspend fun softDelete(id: String, deletedAt: Long)

    @Query("SELECT COUNT(*) FROM transactions WHERE deletedAt IS NULL")
    suspend fun countActive(): Int
}
