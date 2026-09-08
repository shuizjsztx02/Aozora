package com.aozora.app.db.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * 支付渠道标记实体 — 对应 APP-CONTEXT.md §5.1 Account。
 * 仅标记支付渠道，不维护真实余额。
 */
@Entity(tableName = "accounts")
data class AccountEntity(
    @PrimaryKey val id: String,
    val ownerId: String,
    val name: String,
    val icon: String,
    val accountType: String?,
    val archivedAt: Long?,
    val version: Int
)
