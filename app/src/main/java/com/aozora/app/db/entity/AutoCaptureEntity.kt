package com.aozora.app.db.entity

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

/**
 * 自动记账采集行（第二十一轮 REQ-83 / T21-3，ADR-016 决策 5）。
 *
 * 承载「一条被捕获、尚未解析 / 尚未确认的**原始事件**」。**只存在于客户端 Room**
 * ——采集发生在设备上，上行的是解析结果与既有交易实体，不是本表。
 *
 * 为什么不复用 `pending_mutations`：后者装的是**已定型的写操作**，本表装的是
 * **原始素材**，有独立生命周期（captured → parsing → pending_confirm →
 * committed / ignored / failed）、需要去重键唯一索引、需要原文留档，且**两条通道
 * 写同一张表**（靠 [sourceChannel] 区分）。
 *
 * 字段与口径以 `docs/architecture/data-model.md` 的 `auto_captures` 节为准：
 * - [dedupeKey]：L1 同通道同事件，**唯一索引**；重复投递靠它自动丢弃。
 * - [mergeKey]：L2 跨通道同笔，**采集时留空**——方向与金额是解析才有的量
 *   （ADR-016 决策 4：采集层不做解析），解析完成后由 AutoBookRepository 回填。
 * - [mergedInto]：被 L2 合并时指向保留行（**留痕，不静默删除**）。
 * - [rawText]：采集原文，属**敏感数据**——只允许进本表与服务端解析端点，
 *   **禁止进业务日志**（`docs/architecture/security.md`）。
 *
 * 本表的写入是采集侧的本地记录，**不入队 pending mutation**；`committed` 的落账
 * 仍一律走 `Repository.enqueueInsert`（与对应 mutation 同事务，决策 #16）。
 */
@Entity(
    tableName = "auto_captures",
    indices = [
        Index(value = ["dedupeKey"], unique = true),
        Index(value = ["mergeKey"]),
    ]
)
data class AutoCaptureEntity(
    @PrimaryKey val captureId: String,
    val dedupeKey: String,
    val mergeKey: String?,          // L2 键；解析前为 null
    val sourceChannel: String,      // CHANNEL_NOTIFICATION | CHANNEL_ACCESSIBILITY
    val sourcePackage: String,
    val sourceApp: String?,         // 应用名（取不到时 null）
    val rawText: String,            // 限长，见 MAX_RAW_TEXT_LEN
    val postedAt: Long,             // 事件时间（通知 postTime / 页面事件时间），UTC ms
    val merchantKey: String?,       // 商户归一化键（商户记忆回查；解析前为 null）
    val status: String,             // STATUS_*
    val parsedJson: String?,        // 本地规则或服务端解析结果
    val confidence: Double?,        // 解析置信度 [0,1]
    val transactionId: String?,     // 入账后指向生成的交易
    val mergedInto: String?,        // 被合并时指向保留行
    val error: String?,             // 失败原因
    val createdAt: Long,
    val updatedAt: Long,
) {
    companion object {
        const val CHANNEL_NOTIFICATION = "notification"
        const val CHANNEL_ACCESSIBILITY = "accessibility"

        const val STATUS_CAPTURED = "captured"
        const val STATUS_PARSING = "parsing"
        const val STATUS_PENDING_CONFIRM = "pending_confirm"
        const val STATUS_COMMITTED = "committed"
        const val STATUS_IGNORED = "ignored"
        const val STATUS_FAILED = "failed"

        /** [rawText] 限长（字符）。通知正文与页面文本都远小于此，纯防御。 */
        const val MAX_RAW_TEXT_LEN = 4000

        /**
         * 通道优先级（L2 合并时"保留信息最全的一份"的判据）：无障碍 > 通知。
         * 无障碍能拿到商户名与订单号，通知常常只有金额（ADR-016 决策 1）。
         */
        fun channelRank(channel: String): Int = when (channel) {
            CHANNEL_ACCESSIBILITY -> 2
            CHANNEL_NOTIFICATION -> 1
            else -> 0
        }
    }
}