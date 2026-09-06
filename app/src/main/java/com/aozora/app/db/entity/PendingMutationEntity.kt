package com.aozora.app.db.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * 待同步操作实体 — 离线操作队列。
 * 每次本地写入必须与对应 pending mutation 在同一 Room 事务内完成。
 *
 * status: "pending" → "syncing" → 成功则 remove / 失败则 "failed"
 * operation: "insert" | "update" | "delete"
 *
 * T-M3-02：
 * - expectedVersion：update/delete/archive 入队时捕获的变更**前**版本号，
 *   推送时作为服务端期望版本；insert 为 null。
 * - lastError：失败原因（冲突、不可重试错误等），仅 failed 状态有值。
 * - failed 状态为终态：自动同步不再重试该 mutation，等待用户处理。
 */
@Entity(tableName = "pending_mutations")
data class PendingMutationEntity(
    @PrimaryKey val mutationId: String,
    val entityType: String,     // "transaction" | "book" | "category" | "account" | "budget"
    val entityId: String,
    val operation: String,      // "insert" | "update" | "delete" | "archive"
    val payload: String,        // JSON 序列化的变更内容
    val status: String,         // "pending" | "syncing" | "failed"
    val retryCount: Int,
    val createdAt: Long,        // UTC ms
    val expectedVersion: Int?,  // update/delete/archive 入队时的 current.version；insert 为 null
    val lastError: String?      // failed 状态时的失败原因
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
