package com.aozora.app.db.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * 待同步操作实体 — 离线操作队列。
 * 每次本地写入必须与对应 pending mutation 在同一 Room 事务内完成。
 *
 * status: "pending" → "syncing" → 成功则 remove / 失败则 "failed"
 * operation: "insert" | "update" | "delete"
 */
@Entity(tableName = "pending_mutations")
data class PendingMutationEntity(
    @PrimaryKey val mutationId: String,
    val entityType: String,     // "transaction" | "book" | "category" | "account" | "budget"
    val entityId: String,
    val operation: String,      // "insert" | "update" | "delete"
    val payload: String,        // JSON 序列化的变更内容
    val status: String,         // "pending" | "syncing" | "failed"
    val retryCount: Int,
    val createdAt: Long         // UTC ms
) {
    companion object {
        const val STATUS_PENDING = "pending"
        const val STATUS_SYNCING = "syncing"
        const val STATUS_FAILED = "failed"

        const val OP_INSERT = "insert"
        const val OP_UPDATE = "update"
        const val OP_DELETE = "delete"
        const val OP_ARCHIVE = "archive"
    }
}
