package com.aozora.app.db.entity

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

/**
 * T-M7-05j（偏好项上云）：用户级偏好行 —— 云端权威 + 本地副本。
 *
 * 与既有上云实体的三点差异（服务端契约见 server/src/modules/sync/service.py）：
 * - 身份是 (user_id, key) 而非单纯 id，故本地加 key 唯一索引：一个键一行。
 *   多设备撞同键时服务端幂等返回既有行 id，回程 pull 的 REPLACE upsert 会按 key
 *   覆盖本地自铸 id —— 唯一索引是这条自愈路径成立的前提。
 * - 无 ownerId 列（归属由服务端按 token 判定，pull/bootstrap 不回传）。
 * - 无 created_at/updated_at/archived_at/deleted_at —— 清除 = 物理删，本地表
 *   也就只有 (id, key, value, version) 四列。
 *
 * **偏好值的真身在 sync_meta**：UI 读的始终是 sync_meta 的同名键
 * （[SyncMetaEntity.SYNCABLE_PREFERENCE_KEYS] 那 6 个）。本表只是云端镜像，
 * 作用是记住该键在服务端的行 id 与 version —— 上行 update 要带 expectedVersion，
 * 没有镜像行就只能发 insert（幂等＝永不覆盖），改不动云端已有的值。
 */
@Entity(
    tableName = "user_preferences",
    indices = [Index(value = ["key"], unique = true)]
)
data class UserPreferenceEntity(
    @PrimaryKey val id: String,   // 服务端行 id；本地首次写入时自铸 UUID（服务端 insert 直接采用）
    val key: String,              // sync_meta 存储键名（⊆ SyncMetaEntity.SYNCABLE_PREFERENCE_KEYS）
    val value: String,            // 字符串；order/widget/profile 三类是序列化后的 JSON 文本
    val version: Int
)