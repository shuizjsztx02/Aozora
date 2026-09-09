package com.aozora.app.db.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * T-M3-02: 同步元数据 — 存储 cursor 等少量同步状态。
 * 使用方式：与实体合并/游标推进在同一事务内，避免"游标推进但实体未落盘"
 * 的中间态；反之若游标未推进，重复 pull 是幂等且安全的。
 */
@Entity(tableName = "sync_meta")
data class SyncMetaEntity(
    @PrimaryKey val key: String,   // 现用 key: "sync_cursor" / "master_book_id"
    val value: String              // 字符串化；cursor 为 Long 字面量
) {
    companion object {
        const val KEY_CURSOR = "sync_cursor"
        // T7-1（REQ-5）：总账本 book id，双账本种子写入；未设置时为 null。
        const val KEY_MASTER_BOOK_ID = "master_book_id"
        // T9-5a（REQ-11 / 决策 #1）：默认选择 + 列表顺序。一律存本表、不加实体列、
        // 不上行（push/pull 不感知）；bootstrap 重建实体不清 sync_meta → 跨重建存活。
        const val KEY_DEFAULT_BOOK_ID = "default_book_id"
        const val KEY_DEFAULT_ACCOUNT_ID = "default_account_id"
        const val KEY_BOOK_ORDER = "book_order"        // JSON 字符串数组（UUID id）
        const val KEY_ACCOUNT_ORDER = "account_order"  // JSON 字符串数组（UUID id）
        // 第十轮 T10-3（REQ-19 / 决策 #4）：账号展示信息，同样仅本地（换机不保留）。
        const val KEY_AVATAR_URI = "avatar_uri"        // data: URI（icon.pick 压缩链产物，≤64KB）
        const val KEY_DISPLAY_NAME = "display_name"    // 昵称离线镜像（PATCH /me 成功后写入，≤64 字符）
    }
}
