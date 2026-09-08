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
    }
}
