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

    /** R-M4-31: 统计终态失败的 mutation 数量（与 pending 口径一致用于首页 SyncBadge 对齐）。 */
    @Query("SELECT COUNT(*) FROM pending_mutations WHERE status = 'failed'")
    suspend fun countFailed(): Int

    // ─── T-M3-02: sync engine queries ───

    /**
     * 待推送批次：pending 状态按 createdAt 顺序，最多 :limit 条。
     * 失败状态由终态保留，不参与下一轮推送（除非用户手动重试）。
     */
    @Query("SELECT * FROM pending_mutations WHERE status = 'pending' ORDER BY createdAt ASC LIMIT :limit")
    suspend fun listPushable(limit: Int): List<PendingMutationEntity>

    /**
     * 崩溃/进程终止恢复：将上次运行残留的 syncing 状态回退到 pending。
     * mutationId 幂等保证此操作对服务端是安全的（重复 push 返回 duplicate）。
     */
    @Query("UPDATE pending_mutations SET status = 'pending' WHERE status = 'syncing'")
    suspend fun resetSyncingToPending()

    /** 标记失败并记录原因；终态，不再自动重试。 */
    @Query("UPDATE pending_mutations SET status = 'failed', lastError = :lastError, retryCount = retryCount + 1 WHERE mutationId = :mutationId")
    suspend fun markFailedWithError(mutationId: String, lastError: String?)

    /**
     * 合并跳过查询：返回指定实体类型/ID 中仍有活跃（pending/syncing）
     * mutation 的 ID 集合。同步引擎收到服务端推送的 entity 时，
     * 如果本地还有待处理的 mutation，则跳过覆盖（保留本地待处理版本）。
     */
    @Query("SELECT entityId FROM pending_mutations WHERE entityType = :entityType AND entityId IN (:ids) AND status IN ('pending', 'syncing')")
    suspend fun listActiveIds(entityType: String, ids: List<String>): List<String>

    /** 批量移除已推送成功的 mutation。 */
    @Query("DELETE FROM pending_mutations WHERE mutationId IN (:ids)")
    suspend fun removeBatch(ids: List<String>)

    /**
     * 查询终态失败的 mutation（被服务端 rejected 或本地标记失败）。
     * 由 SyncEngine 随 SyncStatusEvent 发布，前端据此展示冲突提示。
     */
    @Query("SELECT * FROM pending_mutations WHERE status = 'failed' ORDER BY createdAt ASC")
    suspend fun listFailed(): List<PendingMutationEntity>
}
