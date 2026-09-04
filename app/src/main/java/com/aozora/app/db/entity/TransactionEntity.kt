package com.aozora.app.db.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * 流水实体 — 对应 APP-CONTEXT.md §5.1 Transaction。
 * 金额为正整数分（Long），bizDate 为用户时区 YYYY-MM-DD，occurredAt 为 UTC ms。
 */
@Entity(tableName = "transactions")
data class TransactionEntity(
    @PrimaryKey val id: String,
    val bookId: String,
    val type: String,           // "expense" | "income"
    val amountCents: Long,      // 正整数分
    val categoryId: String,
    val accountId: String?,
    val note: String,
    val bizDate: String,        // YYYY-MM-DD 用户时区
    val occurredAt: Long,       // UTC ms timestamp
    val source: String,         // manual | nlu_local | nlu_ai | import
    val version: Int,
    val deletedAt: Long?        // UTC ms or null（软删除）
)
