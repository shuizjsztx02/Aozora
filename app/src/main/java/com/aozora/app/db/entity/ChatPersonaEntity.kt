package com.aozora.app.db.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * 聊天角色实体（第十一轮 REQ-28 / 决策 #8）— 小贝之外的人设角色。
 *
 * 仅存本地、不上行同步（镜像 SavingsGoalEntity 的理由）。本卡（T11-4）只建
 * 表 + DAO；桥命令 persona.*、种子 ensureSeedPersonas()、当前角色 meta 键
 * active_persona_id 均在 T11-7 引入。
 *
 * - [avatar]：emoji 或 data:image base64（IconPicker 产物，T9-6），可空。
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
