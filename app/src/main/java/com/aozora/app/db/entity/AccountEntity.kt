package com.aozora.app.db.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * 账户实体 — 对应 APP-CONTEXT.md §5.1 Account。
 *
 * 第十八轮 REQ-69（T18-5）：账户**有**当前余额与分类，两端各存一列
 * （推翻此前「仅支付渠道标记，不维护真实余额」的口径，见 ADR-013）。
 */
@Entity(tableName = "accounts")
data class AccountEntity(
    @PrimaryKey val id: String,
    val ownerId: String,
    val name: String,
    val icon: String,
    val accountType: String?,
    // REQ-69（决策 #2）：存量余额，单位分，**允许负值**（信用卡欠款是常态）。
    // 服务端 accounts.balance_cents 同语义、同样不加非负 CHECK。
    // 与「由交易现算的净额」不保证相等，差额 = 手动校准量（决策 #3）。
    val balanceCents: Long = 0,
    val archivedAt: Long?,
    val version: Int
)
