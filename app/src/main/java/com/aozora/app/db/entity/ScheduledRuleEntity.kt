package com.aozora.app.db.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * 定时记账规则实体（第十一轮 REQ-26 / 决策 #4）— 周期性自动记账。
 *
 * 已知限制：该实体**仅存本地、不上行同步**（镜像 SavingsGoalEntity 的理由：
 * 服务端 sync 契约 entity_type 不认 scheduled_rules，入队会整批 422 毒丸）。
 * Repository 方法签名刻意不含 mutationId、不写 pending_mutations；但引擎按
 * 规则**生成的交易**走正常 mutation 上行。多设备/重装（clearAllData →
 * clearAllTables）不保留此表数据。
 *
 * 字段语义：
 * - [type]/[amountCents]/[bookId]/[categoryId]/[accountId]/[includeInBudget]/[note]：
 *   生成交易时的固定参数（includeInBudget 透传给交易，REQ-17 语义）。
 * - [repeat]：'daily' | 'weekly' | 'monthly' | 'yearly'（应用层校验仅此四值）。
 * - [startDate]/[endDate]：'YYYY-MM-DD'；endDate null = 无限制。
 * - [enabled]：规则开关，false 时引擎跳过。
 * - [lastRunDate]：最近一次生成交易的 bizDate（'YYYY-MM-DD'），当日去重锚点。
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
    val createdAt: Long,          // UTC ms
    val updatedAt: Long,          // UTC ms
    val archivedAt: Long?,        // 删除=归档；UTC ms or null
    val version: Int
)
