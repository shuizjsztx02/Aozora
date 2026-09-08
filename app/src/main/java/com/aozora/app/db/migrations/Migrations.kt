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

/**
 * T-M5-04a Room schema 迁移。
 *
 * v2 → v3：
 * - 新增 user_consent 表（隐私政策同意记录）。
 */
val MIGRATION_2_3: Migration = object : Migration(2, 3) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL(
            "CREATE TABLE IF NOT EXISTS `user_consent` (" +
                "`id` TEXT NOT NULL, " +
                "`deviceId` TEXT NOT NULL, " +
                "`policyVersion` TEXT NOT NULL, " +
                "`consentedAt` INTEGER NOT NULL, " +
                "PRIMARY KEY(`id`))"
        )
    }
}

/**
 * T7-1（REQ-6）Room schema 迁移。
 *
 * v3 → v4：
 * - accounts 新增 accountType TEXT（nullable，支付渠道子类型）。
 */
val MIGRATION_3_4: Migration = object : Migration(3, 4) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL(
            "ALTER TABLE `accounts` ADD COLUMN `accountType` TEXT"
        )
    }
}

/**
 * 第八轮（REQ-8 分类自定义 + REQ-7 存钱管理）Room schema 迁移。
 *
 * v4 → v5：
 * - categories 新增 icon TEXT（自定义图标：emoji 或 data:image base64；可空）。
 * - 新增 savings_goals 表（存钱目标，仅本地不同步）。列序/类型须与
 *   SavingsGoalEntity 一致，否则 Room 打开时 schema 校验失败。
 */
val MIGRATION_4_5: Migration = object : Migration(4, 5) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("ALTER TABLE `categories` ADD COLUMN `icon` TEXT")
        db.execSQL(
            "CREATE TABLE IF NOT EXISTS `savings_goals` (" +
                "`id` TEXT NOT NULL, " +
                "`ownerId` TEXT NOT NULL, " +
                "`name` TEXT NOT NULL, " +
                "`targetCents` INTEGER NOT NULL, " +
                "`currentCents` INTEGER NOT NULL, " +
                "`icon` TEXT, " +
                "`createdAt` INTEGER NOT NULL, " +
                "`updatedAt` INTEGER NOT NULL, " +
                "`archivedAt` INTEGER, " +
                "`version` INTEGER NOT NULL, " +
                "PRIMARY KEY(`id`))"
        )
    }
}
