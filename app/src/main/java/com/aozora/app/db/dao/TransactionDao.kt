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

    /**
     * 第十二轮 T12-7（REQ-36）：乐观锁全字段更新 —— 仅当 id + 变更**前**版本
     * （:expectedVersion）双条件命中才写入并置新 version；返回受影响行数
     * （0 = 版本冲突，调用方中止事务）。
     */
    @Query(
        "UPDATE transactions SET bookId = :bookId, type = :type, amountCents = :amountCents, " +
            "categoryId = :categoryId, accountId = :accountId, note = :note, bizDate = :bizDate, " +
            "occurredAt = :occurredAt, source = :source, includeInBudget = :includeInBudget, " +
            "version = :newVersion " +
            "WHERE id = :id AND version = :expectedVersion"
    )
    suspend fun updateWithVersion(
        id: String,
        bookId: String,
        type: String,
        amountCents: Long,
        categoryId: String,
        accountId: String?,
        note: String,
        bizDate: String,
        occurredAt: Long,
        source: String,
        includeInBudget: Boolean,
        newVersion: Int,
        expectedVersion: Int,
    ): Int

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

    /**
     * T-M3-02: 同步合并 upsert 路径 — REPLACE 语义。
     * 仅由 Repository.applyServerChanges 调用，业务写入仍使用 ABORT 插入。
     * 用于：服务端 pull/bootstrap 推送的流水实体覆盖本地副本。
     */
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertFromSync(transaction: TransactionEntity)

    /**
     * T-M3-02: 同步合并软删路径 — 仅更新 deletedAt 与 version，不触碰其他列。
     * 用于：服务端 pull 的 delete 变更应用到本地。
     */
    @Query("UPDATE transactions SET deletedAt = :deletedAt, version = :version WHERE id = :id")
    suspend fun applyServerDelete(id: String, deletedAt: Long, version: Int)

    /** T-M3-03: 清空所有流水记录（bootstrap 重建前使用）。 */
    @Query("DELETE FROM transactions")
    suspend fun clearAll()
}
