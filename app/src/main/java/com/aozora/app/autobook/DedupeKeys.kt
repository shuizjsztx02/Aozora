package com.aozora.app.autobook

import java.security.MessageDigest

/**
 * 自动记账去重键（第二十一轮 REQ-83 / T21-3，ADR-016 决策 5 / 6）。
 *
 * **纯函数、确定性 hash、无 LLM 参与**——这不是风格偏好：本仓已有惨痛先例
 * （T18 收口实证：让模型给这笔账编 id → 照抄训练示例值 → 撞键 → 静默丢弃）。
 * 幂等键一律由通道、包名、时间、金额与归一化文本确定性算出。
 *
 * 三级去重的分工（判据见 `docs/architecture/data-model.md` 的 `auto_captures` 节）：
 * - **L1 [l1Key]**：同通道同事件 → `dedupeKey` 唯一索引，冲突即自动丢弃。
 * - **L2 [l2Key]**：跨通道同笔 → `mergeKey` 命中**且** [merchantCompatible] → 合并。
 * - **L3**：同额但不可判定 → 不合并，只提示（本对象不参与；由调用侧判）。
 */
object DedupeKeys {

    /** 时间桶宽度：1 分钟（"分钟级时间桶"，L2 判据）。 */
    const val BUCKET_MS = 60_000L

    /**
     * 文本归一化：空白折叠（含换行）+ 去首尾 + 小写。
     *
     * 目的是让"同一条通知被重复投递"与"同一页面抖出多份快照"落到同一个键上。
     * **只做形态归一，不做语义归一**——不改数字、不删标点：语义归一属于解析层
     * （T21-6），放在幂等键里会让两笔真实同额消费撞成一行。
     */
    fun normalizeText(raw: String): String =
        raw.replace(WHITESPACE, " ").trim().lowercase()

    /** 时间桶序号：`floorDiv` 而非整除——负数时间戳不参与，但别留下取整歧义。 */
    fun minuteBucket(atMs: Long): Long = Math.floorDiv(atMs, BUCKET_MS)

    /** 时间桶起点（L3「同时间窗」的边界由调用侧给出）。 */
    fun bucketStart(atMs: Long): Long = minuteBucket(atMs) * BUCKET_MS

    /**
     * L1 键：`hash(sourceChannel + sourcePackage + 事件 key/postTime + 归一化文本)`。
     *
     * ★ **时间只进到分钟桶，不进毫秒**：通知重复回调的 `postTime` 本来就是同一个值，
     * 但无障碍的 `TYPE_WINDOW_CONTENT_CHANGED` 每次回调的 `eventTime` 都不同——
     * 用毫秒当输入等于没去重。桶化之后，"同一页面的高频抖动"才真的落到同一行。
     * 代价是跨分钟边界的一次重复会漏过 L1（残差见 T21-3 交付记录，由 T21-4 侧
     * 的页面签名防抖兜底）。
     *
     * [eventIdentity] 由调用侧给出，必须**在同一事件内稳定**：通知通道传
     * `StatusBarNotification.key`，无障碍通道传窗口 / 页面标识。
     */
    fun l1Key(
        sourceChannel: String,
        sourcePackage: String,
        eventIdentity: String,
        postedAtMs: Long,
        rawText: String,
    ): String = sha256Hex(
        listOf(
            "L1", sourceChannel, sourcePackage, eventIdentity,
            minuteBucket(postedAtMs).toString(), normalizeText(rawText),
        ).joinToString(SEP)
    )

    /**
     * L2 键：`hash(方向 + 金额分 + 分钟级时间桶)`——**跨通道同笔**的判据。
     *
     * 采集层拿不到方向与金额（ADR-016 决策 4：采集层不做解析），故本键在**解析之后**
     * 由 T21-6 用权威值算出并回填 `mergeKey` 列（见 AutoBookRepository）。
     */
    fun l2Key(direction: String, amountCents: Long, postedAtMs: Long): String = sha256Hex(
        listOf("L2", direction, amountCents.toString(), minuteBucket(postedAtMs).toString())
            .joinToString(SEP)
    )

    /**
     * L2 的商户附加判据："两边都无商户名 **或** 商户名相同"。商户名不同则**不合并**
     * ——那属于 L3（同额消费是日常，自动合并会吃掉真实账目）。
     */
    fun merchantCompatible(a: String?, b: String?): Boolean =
        a.isNullOrBlank() || b.isNullOrBlank() || a == b

    /** SHA-256 十六进制小写。全仓唯一的幂等键摘要实现（不再另造）。 */
    fun sha256Hex(input: String): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(input.toByteArray(Charsets.UTF_8))
        return buildString(digest.size * 2) {
            for (b in digest) {
                val v = b.toInt() and 0xFF
                append(HEX[v ushr 4])
                append(HEX[v and 0x0F])
            }
        }
    }

    /** 单元分隔符：各段之间用不可见控制符，避免拼接歧义（"ab"+"c" vs "a"+"bc"）。 */
    private const val SEP = "\u0000"
    private const val HEX = "0123456789abcdef"
    private val WHITESPACE = Regex("\\s+")
}