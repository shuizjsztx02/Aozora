package com.aozora.app.db.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * 聊天角色实体（第十一轮 REQ-28 / 决策 #8）— 小贝之外的人设角色。
 *
 * T-M7-01 起**上云**（云端权威 + 本地副本，`D-PROD-03`）：写入经 Repository 入队
 * pending mutation，sync entity_type = `chat_persona`。本地表无 ownerId 列——归属由
 * 服务端按 token 判定，pull/bootstrap 回填不带该字段。多设备/重装时由 bootstrap
 * 从云端快照恢复（清空前 Repository.migrateCloudEntitiesUp 先把本地存量入队上行）。
 *
 * - [avatar]：emoji 或 data:image base64（IconPicker 产物，T9-6），可空。
 *   照片版超服务端 64 字符上限，push 时省略该键、保持设备本地专属（镜像 book/account）。
 * - [persona]：人设文本（应用层校验 ≤2000 字），注入 AI system 消息（T11-6 管线）。
 */
@Entity(tableName = "chat_personas")
data class ChatPersonaEntity(
    @PrimaryKey val id: String,
    val name: String,             // 昵称（应用层校验 ≤16 字）
    val avatar: String?,          // emoji 或 data URI，可空
    val persona: String,          // 人设文本 ≤2000 字
    val createdAt: Long,          // UTC ms
    val updatedAt: Long,          // UTC ms
    val archivedAt: Long?,        // 删除=归档；UTC ms or null
    val version: Int
)
