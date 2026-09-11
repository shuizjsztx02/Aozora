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

/**
 * 第九轮（REQ-14 存款时间）Room schema 迁移。
 *
 * v5 → v6：
 * - savings_goals 新增 periodValue INTEGER / periodUnit TEXT（均可空，
 *   列序与 SavingsGoalEntity 一致：icon 之后、createdAt 之前）。
 */
val MIGRATION_5_6: Migration = object : Migration(5, 6) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("ALTER TABLE `savings_goals` ADD COLUMN `periodValue` INTEGER")
        db.execSQL("ALTER TABLE `savings_goals` ADD COLUMN `periodUnit` TEXT")
    }
}

/**
 * 第十轮（REQ-17 预算计入标记）Room schema 迁移。
 *
 * v6 → v7：
 * - transactions 新增 includeInBudget INTEGER NOT NULL DEFAULT 1
 *   （存量行=计入，预算行为不变；仅 QuickEntrySheet 手动记账可传 0）。
 */
val MIGRATION_6_7: Migration = object : Migration(6, 7) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL(
            "ALTER TABLE `transactions` ADD COLUMN `includeInBudget` INTEGER NOT NULL DEFAULT 1"
        )
    }
}

/**
 * 第十一轮（REQ-26 定时记账 + REQ-28 聊天角色）Room schema 迁移（决策 #1/#4/#8）。
 *
 * v7 → v8：一张迁移建两表（镜像 MIGRATION_4_5 双表先例）。
 * - scheduled_rules：定时记账规则，仅本地不同步。
 * - chat_personas：聊天角色，仅本地不同步（命令与种子在 T11-7）。
 * 列序/类型须与实体一致，否则 Room 打开时 schema 校验失败。
 */
val MIGRATION_7_8: Migration = object : Migration(7, 8) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL(
            "CREATE TABLE IF NOT EXISTS `scheduled_rules` (" +
                "`id` TEXT NOT NULL, " +
                "`ownerId` TEXT NOT NULL, " +
                "`type` TEXT NOT NULL, " +
                "`name` TEXT NOT NULL, " +
                "`amountCents` INTEGER NOT NULL, " +
                "`bookId` TEXT NOT NULL, " +
                "`categoryId` TEXT NOT NULL, " +
                "`accountId` TEXT, " +
                "`includeInBudget` INTEGER NOT NULL, " +
                "`note` TEXT NOT NULL, " +
                "`repeat` TEXT NOT NULL, " +
                "`startDate` TEXT NOT NULL, " +
                "`endDate` TEXT, " +
                "`enabled` INTEGER NOT NULL, " +
                "`lastRunDate` TEXT, " +
                "`createdAt` INTEGER NOT NULL, " +
                "`updatedAt` INTEGER NOT NULL, " +
                "`archivedAt` INTEGER, " +
                "`version` INTEGER NOT NULL, " +
                "PRIMARY KEY(`id`))"
        )
        db.execSQL(
            "CREATE TABLE IF NOT EXISTS `chat_personas` (" +
                "`id` TEXT NOT NULL, " +
                "`name` TEXT NOT NULL, " +
                "`avatar` TEXT, " +
                "`persona` TEXT NOT NULL, " +
                "`createdAt` INTEGER NOT NULL, " +
                "`updatedAt` INTEGER NOT NULL, " +
                "`archivedAt` INTEGER, " +
                "`version` INTEGER NOT NULL, " +
                "PRIMARY KEY(`id`))"
        )
    }
}

/**
 * 第十四轮（REQ-43 定时记账时刻化 / 决策 #1/#4）Room schema 迁移（T14-5，v9 唯一声明者）。
 *
 * v8 → v9：
 * - scheduled_rules 新增 runHour INTEGER NOT NULL DEFAULT 8 /
 *   runMinute INTEGER NOT NULL DEFAULT 0（执行时刻，到点才生成当日账单）。
 *   存量行取默认 = 保持原「每日 08:00 闹钟」语义，零数据搬迁。
 */
val MIGRATION_8_9: Migration = object : Migration(8, 9) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL(
            "ALTER TABLE `scheduled_rules` ADD COLUMN `runHour` INTEGER NOT NULL DEFAULT 8"
        )
        db.execSQL(
            "ALTER TABLE `scheduled_rules` ADD COLUMN `runMinute` INTEGER NOT NULL DEFAULT 0"
        )
    }
}
