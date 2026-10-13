package com.aozora.app.db.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import com.aozora.app.db.entity.AutoCaptureEntity

/**
 * 自动记账采集行 DAO（第二十一轮 REQ-83 / T21-3）。
 *
 * `db/` 簇归 T21-3 独占（决策 #17），因此下游卡（T21-5 桥 / T21-6 编排 /
 * T21-8 待确认箱）需要的读写入口都在这里一次给齐，各自消费、不再加查询。
 */
@Dao
interface AutoCaptureDao {

    /**
     * L1 落库：`dedupeKey` 唯一索引 + `IGNORE`。
     *
     * 返回 `-1` 即"这一行本来就有"＝重复投递，调用方据此丢弃。
     * **用冲突忽略而不是先查后写**：`NotificationListenerService.onNotificationPosted`
     * 与无障碍的 `TYPE_WINDOW_CONTENT_CHANGED` 都会重复回调，先查后写存在竞争窗口。
     */
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insertIgnore(row: AutoCaptureEntity): Long

    @Query("SELECT * FROM auto_captures WHERE captureId = :captureId")
    suspend fun getById(captureId: String): AutoCaptureEntity?

    @Query("SELECT * FROM auto_captures WHERE dedupeKey = :dedupeKey")
    suspend fun getByDedupeKey(dedupeKey: String): AutoCaptureEntity?

    // ─── L2：跨通道同笔 ───

    /**
     * L2 候选：同一 `mergeKey` 且**另一条通道**、且**尚未被合并**的行。
     * 本通道内的同额事件不属于 L2（ADR-016 决策 5："跨通道同笔"）。
     */
    @Query(
        "SELECT * FROM auto_captures WHERE mergeKey = :mergeKey " +
            "AND sourceChannel != :channel AND mergedInto IS NULL"
    )
    suspend fun listCrossChannelByMergeKey(
        mergeKey: String,
        channel: String,
    ): List<AutoCaptureEntity>

    /** 解析完成后回填 L2 键（采集层拿不到方向与金额，ADR-016 决策 4）。 */
    @Query("UPDATE auto_captures SET mergeKey = :mergeKey, updatedAt = :now WHERE captureId = :captureId")
    suspend fun updateMergeKey(captureId: String, mergeKey: String, now: Long)

    /**
     * 合并留痕：被合并的行指向保留行，**不删除**（`mergedInto`）。
     *
     * ★ 条件更新（`mergedInto IS NULL`）+ 返回受影响行数：**并发下不允许互相合并**。
     * 两行同时判定"对方是候选"会各自把对方标成被合并行，结果是两行互相指向、
     * 谁也没真的合并掉——而"两条通道同时抓到同一笔"恰恰是本表最核心的场景。
     * 返回 0 = 这一行已被别人先合并了，调用方让路。
     */
    @Query(
        "UPDATE auto_captures SET mergedInto = :keptId, updatedAt = :now " +
            "WHERE captureId = :mergedId AND mergedInto IS NULL"
    )
    suspend fun markMergedInto(mergedId: String, keptId: String, now: Long): Int

    /** 来源数（"标 multi_source" 的派生量：指向本行的行数 > 0 即为多来源）。 */
    @Query("SELECT COUNT(*) FROM auto_captures WHERE mergedInto = :keptId")
    suspend fun countMergedSources(keptId: String): Int

    // ─── 状态推进（T21-6 编排消费）───

    /**
     * 状态机推进 + 解析结果落盘。[merchantKey] / [parsedJson] / [confidence] /
     * [transactionId] / [error] 按阶段给值（用不到的传 null）。
     */
    @Query(
        "UPDATE auto_captures SET status = :status, parsedJson = :parsedJson, " +
            "confidence = :confidence, merchantKey = :merchantKey, " +
            "transactionId = :transactionId, error = :error, " +
            "updatedAt = :now WHERE captureId = :captureId"
    )
    suspend fun applyResolution(
        captureId: String,
        status: String,
        parsedJson: String?,
        confidence: Double?,
        merchantKey: String?,
        transactionId: String?,
        error: String?,
        now: Long,
    )

    /** 商户记忆（决策 #10/#11，第一期不开新表）：同 merchantKey 最近一条已入账的分类来源。 */
    @Query(
        "SELECT * FROM auto_captures WHERE merchantKey = :merchantKey AND status = 'committed' " +
            "AND mergedInto IS NULL ORDER BY updatedAt DESC LIMIT 1"
    )
    suspend fun lastCommittedByMerchant(merchantKey: String): AutoCaptureEntity?

    // ─── 读取面（T21-5 待确认箱分页 / 今日捕获数；T21-8 L3 提示）───

    @Query("SELECT * FROM auto_captures WHERE status = :status ORDER BY postedAt DESC, captureId DESC LIMIT :limit OFFSET :offset")
    suspend fun listByStatus(status: String, limit: Int, offset: Int): List<AutoCaptureEntity>

    @Query("SELECT COUNT(*) FROM auto_captures WHERE status = :status")
    suspend fun countByStatus(status: String): Int

    @Query("SELECT COUNT(*) FROM auto_captures WHERE createdAt >= :sinceMs")
    suspend fun countCapturedSince(sinceMs: Long): Int

    /**
     * L3 疑似重复（决策 #5：**只提示、不合并**）：同时间窗、另一条通道、尚未合并的行。
     * 是否真的同笔由调用侧用金额与商户名判定——同额消费是日常（两杯 18 元咖啡），
     * 自动合并会吃掉真实账目。
     */
    @Query(
        "SELECT * FROM auto_captures WHERE sourceChannel != :channel AND mergedInto IS NULL " +
            "AND status != 'ignored' AND postedAt BETWEEN :fromMs AND :toMs"
    )
    suspend fun listSameWindowOtherChannel(channel: String, fromMs: Long, toMs: Long): List<AutoCaptureEntity>
}