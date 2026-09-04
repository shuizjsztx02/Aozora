package com.aozora.app.db.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * 预算实体 — 对应 APP-CONTEXT.md §5.1 Budget。
 * budgetType: "monthly" | "annual"
 * period: "2026-09" (monthly) 或 "2026" (annual)
 * categoryId 为 null 时代表整体预算。
 */
@Entity(tableName = "budgets")
data class BudgetEntity(
    @PrimaryKey val id: String,
    val bookId: String,
    val budgetType: String,     // "monthly" | "annual"
    val categoryId: String?,    // null = 整体预算
    val period: String,         // "2026-09" or "2026"
    val amountCents: Long,      // 正整数分
    val version: Int
)
