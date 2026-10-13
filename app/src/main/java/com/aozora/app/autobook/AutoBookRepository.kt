package com.aozora.app.autobook

import com.aozora.app.db.AozoraDatabase
import com.aozora.app.db.entity.AutoCaptureEntity
import com.aozora.app.db.entity.SyncMetaEntity
import org.json.JSONArray
import org.json.JSONException
import java.util.UUID

/**
 * 自动记账的写入路径（第二十一轮 REQ-83 / T21-3）。
 *
 * **两条通道都走这里**：通知监听（本卡）与无障碍服务（T21-4）只在"事件 → 文本"
 * 这一段不同，落库、去重、状态全共用一条路径——这是双通道方案能保持简单的原因
 * （`ADR-016` 决策 1「通道关系」）。
 *
 * 三条约束：
 * 1. **本表的写入不入队 pending mutation**：它记的是采集侧本地素材，不是要推给
 *    服务端的变更（`docs/architecture/data-model.md` 末段）。`committed` 的落账
 *    仍走 `Repository.enqueueInsert`（决策 #16）。
 * 2. **不做网络、不做解析**（ADR-016 决策 4）：本类只做去重与落库，解析由 T21-6。
 * 3. **幂等键一律确定性生成**（决策 #6/#7）：[DedupeKeys]。`captureId` 用随机 UUID
 *    只是行标识——**它不是幂等键**，两者的区别是本仓付过学费的（T18：LLM 生成
 *    UUID 当幂等键 → 撞键 → 静默丢弃）。
 */
class AutoBookRepository(private val db: AozoraDatabase) {

    /** 一条待落库的采集素材。字段都已由通道侧归一（文本拼接、原文限长见 [recordCapture]）。 */
    data class CaptureDraft(
        val sourceChannel: String,
        val sourcePackage: String,
        val sourceApp: String?,
        val rawText: String,
        val postedAt: Long,
        /** 同一事件内稳定的标识：通知传 `StatusBarNotification.key`，无障碍传窗口/页面标识。 */
        val eventIdentity: String,
        val merchantKey: String? = null,
    )

    sealed interface CaptureOutcome {
        /** 已落一行。 */
        data class Stored(val captureId: String) : CaptureOutcome

        /** L1 命中去重（同通道同事件），**没有写任何行**。 */
        data class DuplicateL1(val existingCaptureId: String?) : CaptureOutcome
    }

    sealed interface MergeOutcome {
        /** 没有跨通道候选（含"本行已被合并"与"候选被 L1 之外的规则挡下"）。 */
        data object NoCandidate : MergeOutcome

        /** L2 合并成立。[keptCaptureId] 是保留行（信息最全的那份）。 */
        data class Merged(val keptCaptureId: String, val keptIsThisRow: Boolean) : MergeOutcome

        /** 有跨通道同笔候选，但**商户名不同** → 属于 L3：不合并，只提示。 */
        data class MerchantConflict(val candidateIds: List<String>) : MergeOutcome
    }

    // ─── 写入路径 ───

    /**
     * 落一条采集行：L1 去重（`dedupeKey` 唯一索引 + 冲突忽略）→ 插入 `captured`。
     *
     * 断网、未登录、未同步都**不影响**这一步——捕获先落本地是 PR-3 与决策 #14 的
     * 要求（REQ-72 的教训：丢弃后游标照常推进＝静默永久丢失）。
     */
    suspend fun recordCapture(draft: CaptureDraft): CaptureOutcome {
        require(draft.sourcePackage.isNotBlank()) { "sourcePackage is required" }
        require(draft.eventIdentity.isNotBlank()) { "eventIdentity is required" }
        require(
            draft.sourceChannel == AutoCaptureEntity.CHANNEL_NOTIFICATION ||
                draft.sourceChannel == AutoCaptureEntity.CHANNEL_ACCESSIBILITY
        ) { "unknown sourceChannel: ${draft.sourceChannel}" }

        val now = System.currentTimeMillis()
        val rawText = draft.rawText.take(AutoCaptureEntity.MAX_RAW_TEXT_LEN)
        val dedupeKey = DedupeKeys.l1Key(
            sourceChannel = draft.sourceChannel,
            sourcePackage = draft.sourcePackage,
            eventIdentity = draft.eventIdentity,
            postedAtMs = draft.postedAt,
            rawText = rawText,
        )
        val row = AutoCaptureEntity(
            captureId = UUID.randomUUID().toString(),
            dedupeKey = dedupeKey,
            mergeKey = null,
            sourceChannel = draft.sourceChannel,
            sourcePackage = draft.sourcePackage,
            sourceApp = draft.sourceApp,
            rawText = rawText,
            postedAt = draft.postedAt,
            merchantKey = draft.merchantKey,
            status = AutoCaptureEntity.STATUS_CAPTURED,
            parsedJson = null,
            confidence = null,
            transactionId = null,
            mergedInto = null,
            error = null,
            createdAt = now,
            updatedAt = now,
        )
        val rowId = db.autoCaptureDao().insertIgnore(row)
        if (rowId != -1L) return CaptureOutcome.Stored(row.captureId)
        // 冲突 = 这一行本来就有（重复投递），回查既有行 id 供上层观测，但不改写它
        return CaptureOutcome.DuplicateL1(db.autoCaptureDao().getByDedupeKey(dedupeKey)?.captureId)
    }

    /**
     * L2：解析完成后判定"这一笔是不是另一条通道已经抓过的那一笔"。
     *
     * 调用方（T21-6）在解析出**权威的**方向与金额后调用：本方法回填 `mergeKey`
     * 并找跨通道同键候选；命中且商户名相容（[DedupeKeys.merchantCompatible]）时
     * 合并为一条，**保留信息最全的一份**（无障碍 > 通知），另一行用 `mergedInto`
     * 留痕、**不删除**。商户名不相容时不合并（L3：同额消费是日常）。
     *
     * ★ 合并只解决"不重复入账"，**不改变"要不要问用户"**——合并后的条目仍按置信度
     * 走快速通道或待确认箱，来源数由 [AutoCaptureDao.countMergedSources] 派生。
     */
    suspend fun mergeIfSameTransaction(
        captureId: String,
        direction: String,
        amountCents: Long,
        merchantKey: String?,
    ): MergeOutcome {
        require(amountCents > 0) { "amountCents must be positive" }
        val row = db.autoCaptureDao().getById(captureId) ?: return MergeOutcome.NoCandidate
        // 已被合并过的行不再作为合并的主体（留痕行不二次改写）
        if (row.mergedInto != null) return MergeOutcome.NoCandidate

        val now = System.currentTimeMillis()
        val mergeKey = DedupeKeys.l2Key(direction, amountCents, row.postedAt)
        // 无论有没有候选都回填：晚到的那一条要能找到先到的这一条
        db.autoCaptureDao().updateMergeKey(captureId, mergeKey, now)

        val candidates = db.autoCaptureDao()
            .listCrossChannelByMergeKey(mergeKey, row.sourceChannel)
            .filter { it.captureId != captureId }
        if (candidates.isEmpty()) return MergeOutcome.NoCandidate

        val compatible = candidates.firstOrNull {
            DedupeKeys.merchantCompatible(merchantKey, it.merchantKey)
        } ?: return MergeOutcome.MerchantConflict(candidates.map { it.captureId })

        val keepThis = AutoCaptureEntity.channelRank(row.sourceChannel) >=
            AutoCaptureEntity.channelRank(compatible.sourceChannel)
        return if (keepThis) {
            db.autoCaptureDao().markMergedInto(compatible.captureId, row.captureId, now)
            MergeOutcome.Merged(keptCaptureId = row.captureId, keptIsThisRow = true)
        } else {
            db.autoCaptureDao().markMergedInto(row.captureId, compatible.captureId, now)
            MergeOutcome.Merged(keptCaptureId = compatible.captureId, keptIsThisRow = false)
        }
    }

    // ─── 开关与来源清单（设备本地自管 meta 键，决策 #9）───

    /** 自动记账总开关。**缺省 false**——默认关闭是"不知情就不该开始"的兑现（ADR-016 决策 9）。 */
    suspend fun isEnabled(): Boolean =
        db.syncMetaDao().getValue(SyncMetaEntity.KEY_AUTOBOOK_ENABLED) == "1"

    suspend fun setEnabled(enabled: Boolean) {
        db.syncMetaDao().upsert(
            SyncMetaEntity(
                SyncMetaEntity.KEY_AUTOBOOK_ENABLED,
                if (enabled) "1" else "0",
            )
        )
    }

    /** 全量来源集合 = 内置三项 ∪ 用户勾选的追加项。 */
    suspend fun sourcePackages(): Set<String> {
        val raw = db.syncMetaDao().getValue(SyncMetaEntity.KEY_AUTOBOOK_SOURCE_PACKAGES)
        return NotificationFilter.DEFAULT_SOURCE_PACKAGES + parsePackages(raw)
    }

    /** 覆盖用户追加项（内置三项不接受改写，永远在集合里）。 */
    suspend fun setUserSourcePackages(packages: Collection<String>) {
        val clean = packages.map { it.trim() }.filter { it.isNotEmpty() }.toSortedSet()
        db.syncMetaDao().upsert(
            SyncMetaEntity(
                SyncMetaEntity.KEY_AUTOBOOK_SOURCE_PACKAGES,
                JSONArray(clean).toString(),
            )
        )
    }

    private fun parsePackages(raw: String?): Set<String> {
        if (raw.isNullOrBlank()) return emptySet()
        return try {
            val arr = JSONArray(raw)
            (0 until arr.length()).mapNotNull { arr.optString(it).takeIf { s -> s.isNotBlank() } }.toSet()
        } catch (_: JSONException) {
            // 键被写坏时按"没有追加项"处理：内置三项仍工作，不影响采集
            emptySet()
        }
    }
}