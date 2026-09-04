package com.aozora.app.db.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * 账本实体 — 对应 APP-CONTEXT.md §5.1 Book。
 */
@Entity(tableName = "books")
data class BookEntity(
    @PrimaryKey val id: String,
    val ownerId: String,
    val name: String,
    val icon: String,
    val version: Int,
    val archivedAt: Long?       // UTC ms or null
)
