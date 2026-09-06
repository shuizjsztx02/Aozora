package com.aozora.app.db.migrations

import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

/**
 * T-M3-02 Room schema 迁移。
 *
 * v1 → v2：
 * - 新增 sync_meta 表（同步元数据键值，cursor 持久化）。
 * - pending_mutations 新增 expectedVersion INTEGER（update/delete/archive
 *   入队时捕获的变更**前**版本号，推送时作为服务端期望版本）和
 *   lastError TEXT（失败原因）。
 */
val MIGRATION_1_2: Migration = object : Migration(1, 2) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL(
            "CREATE TABLE IF NOT EXISTS `sync_meta` (" +
                "`key` TEXT NOT NULL, " +
                "`value` TEXT NOT NULL, " +
                "PRIMARY KEY(`key`))"
        )
        db.execSQL(
            "ALTER TABLE `pending_mutations` ADD COLUMN `expectedVersion` INTEGER"
        )
        db.execSQL(
            "ALTER TABLE `pending_mutations` ADD COLUMN `lastError` TEXT"
        )
    }
}
