package com.aozora.app.db.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * 分类实体 — 对应 APP-CONTEXT.md §5.1 Category。
 * 支持系统分类和二级自定义分类；删除为归档。
 */
@Entity(tableName = "categories")
data class CategoryEntity(
    @PrimaryKey val id: String,
    val ownerId: String,
    val bookId: String?,        // null = 系统分类
    val parentId: String?,      // 二级分类的父 ID
    val kind: String,           // "expense" | "income"
    val name: String,
    val archivedAt: Long?,      // UTC ms or null
    val version: Int
)
