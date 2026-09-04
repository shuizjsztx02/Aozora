package com.aozora.app.db.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import com.aozora.app.db.entity.PendingMutationEntity

@Dao
interface PendingMutationDao {

    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insert(mutation: PendingMutationEntity)

    @Query("SELECT * FROM pending_mutations ORDER BY createdAt ASC")
    suspend fun listAll(): List<PendingMutationEntity>

    @Query("SELECT * FROM pending_mutations WHERE status = :status ORDER BY createdAt ASC")
    suspend fun listByStatus(status: String): List<PendingMutationEntity>

    @Query("SELECT * FROM pending_mutations WHERE mutationId = :mutationId")
    suspend fun getById(mutationId: String): PendingMutationEntity?

    @Query("UPDATE pending_mutations SET status = :status WHERE mutationId = :mutationId")
    suspend fun updateStatus(mutationId: String, status: String)

    @Query("UPDATE pending_mutations SET status = 'syncing' WHERE mutationId = :mutationId")
    suspend fun markSyncing(mutationId: String)

    @Query("UPDATE pending_mutations SET status = 'failed', retryCount = retryCount + 1 WHERE mutationId = :mutationId")
    suspend fun markFailed(mutationId: String)

    @Query("DELETE FROM pending_mutations WHERE mutationId = :mutationId")
    suspend fun remove(mutationId: String)

    @Query("SELECT COUNT(*) FROM pending_mutations WHERE status = 'pending'")
    suspend fun countPending(): Int
}
