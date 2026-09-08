package com.aozora.app.db.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * 存钱目标实体（第八轮 REQ-7）— 轻量储蓄目标。
 *
 * 已知限制：该实体**仅存本地、不上行同步**。服务端 sync 契约的 entity_type
 * 只认 book/category/account/transaction/budget（server/src/modules/sync/schemas.py），
 * 未知实体会让整批 push 422 → SyncEngine 视为可重试 → 毒丸卡死同步队列。
 * 故 Repository 存钱方法签名刻意不含 mutationId、不写 pending_mutations。
 * 多设备/重装（clearAllData 走 clearAllTables）不保留此表数据。
 */
@Entity(tableName = "savings_goals")
data class SavingsGoalEntity(
    @PrimaryKey val id: String,
    val ownerId: String,
    val name: String,
    val targetCents: Long,       // 目标金额（整数分）
    val currentCents: Long,      // 已存金额（整数分）
    val icon: String?,           // emoji 图标，可空
    val createdAt: Long,         // UTC ms
    val updatedAt: Long,         // UTC ms
    val archivedAt: Long?,       // 删除=归档；UTC ms or null
    val version: Int
)
