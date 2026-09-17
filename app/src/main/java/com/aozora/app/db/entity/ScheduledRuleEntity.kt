package com.aozora.app.db.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * 定时记账规则实体（第十一轮 REQ-26 / 决策 #4）— 周期性自动记账。
 *
 * T-M7-01 起**上云**（云端权威 + 本地副本，`D-PROD-03`）：写入经 Repository 与
 * pending mutation 同事务入队，sync entity_type = `scheduled_rule`。引擎按规则
 * **生成的交易**走 transactions 表（source='scheduled'），规则本身的上行不与之冲突。
 * [lastRunDate] 上云后多设备共享——设备 A 生成后上行，设备 B pull 到即不再重复
 * 生成（跨设备去重），这是期望语义。
 *
 * 字段语义：
 * - [type]/[amountCents]/[bookId]/[categoryId]/[accountId]/[includeInBudget]/[note]：
 *   生成交易时的固定参数（includeInBudget 透传给交易，REQ-17 语义）。
 * - [repeat]：'daily' | 'weekly' | 'monthly' | 'yearly'（应用层校验仅此四值）。
 * - [startDate]/[endDate]：'YYYY-MM-DD'；endDate null = 无限制。
 * - [enabled]：规则开关，false 时引擎跳过。
 * - [lastRunDate]：最近一次生成交易的 bizDate（'YYYY-MM-DD'），当日去重锚点。
 * - [runHour]/[runMinute]：每日执行时刻（第十四轮 REQ-43 / 决策 #4）——引擎到
 *   该墙钟时刻才生成当日账单；缺省 8:00（v9 迁移后存量规则保持原 08:00 语义）。
 */
@Entity(tableName = "scheduled_rules")
data class ScheduledRuleEntity(
    @PrimaryKey val id: String,
    val ownerId: String,
    val type: String,             // "expense" | "income"
    val name: String,             // 任务名称（生成交易 note 的主体）
    val amountCents: Long,        // 正整数分
    val bookId: String,
    val categoryId: String,
    val accountId: String?,
    val includeInBudget: Boolean = true,
    val note: String,             // 备注，可空串；生成交易 note = 名称｜备注
    val repeat: String,           // 'daily' | 'weekly' | 'monthly' | 'yearly'
    val startDate: String,        // 'YYYY-MM-DD'
    val endDate: String?,         // 'YYYY-MM-DD' or null=无限制
    val enabled: Boolean = true,
    val lastRunDate: String?,     // 'YYYY-MM-DD' or null=从未执行
    val runHour: Int = 8,         // 执行时刻-时 0..23（REQ-43）
    val runMinute: Int = 0,       // 执行时刻-分 0..59（REQ-43）
    val createdAt: Long,          // UTC ms
    val updatedAt: Long,          // UTC ms
    val archivedAt: Long?,        // 删除=归档；UTC ms or null
    val version: Int
)
