package com.aozora.app.db.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * 存钱目标实体（第八轮 REQ-7）— 轻量储蓄目标。
 *
 * T-M7-01 起**上云**（云端权威 + 本地副本，`D-PROD-03`）：写入经 Repository 与
 * pending mutation 同事务入队，sync entity_type = `savings_goal`。离线可读；换机/
 * 多设备由 bootstrap 从云端快照恢复，本地存量数据在首次同步时一次性上行
 *（Repository.migrateCloudEntitiesUp）。删除＝归档（archivedAt），服务端 archived_at。
 */
@Entity(tableName = "savings_goals")
data class SavingsGoalEntity(
    @PrimaryKey val id: String,
    val ownerId: String,
    val name: String,
    val targetCents: Long,       // 目标金额（整数分）
    val currentCents: Long,      // 已存金额（整数分）
    val icon: String?,           // emoji 图标，可空
    val periodValue: Int? = null, // 存款期限数值（T9-4/REQ-14），null=无期限
    val periodUnit: String? = null, // 期限单位：'week' | 'month'（应用层校验仅此二值）
    val createdAt: Long,         // UTC ms
    val updatedAt: Long,         // UTC ms
    val archivedAt: Long?,       // 删除=归档；UTC ms or null
    val version: Int
)
