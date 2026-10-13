package com.aozora.app.db.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * T-M3-02: 同步元数据 — 存储 cursor 等少量同步状态。
 * 使用方式：与实体合并/游标推进在同一事务内，避免"游标推进但实体未落盘"
 * 的中间态；反之若游标未推进，重复 pull 是幂等且安全的。
 */
@Entity(tableName = "sync_meta")
data class SyncMetaEntity(
    @PrimaryKey val key: String,   // 现用 key: "sync_cursor" / "master_book_id"
    val value: String              // 字符串化；cursor 为 Long 字面量
) {
    companion object {
        const val KEY_CURSOR = "sync_cursor"
        // T7-1（REQ-5）：总账本 book id，双账本种子写入；未设置时为 null。
        const val KEY_MASTER_BOOK_ID = "master_book_id"
        // T9-5a（REQ-11 / 决策 #1）：默认选择 + 列表顺序。一律存本表、不加实体列
        // （防 bootstrap 冲刷）；bootstrap 重建实体不清 sync_meta → 跨重建存活。
        // T-M7-05j 起这 4 个键**会上云**（见 SYNCABLE_PREFERENCE_KEYS）。
        const val KEY_DEFAULT_BOOK_ID = "default_book_id"
        const val KEY_DEFAULT_ACCOUNT_ID = "default_account_id"
        const val KEY_BOOK_ORDER = "book_order"        // JSON 字符串数组（UUID id）
        const val KEY_ACCOUNT_ORDER = "account_order"  // JSON 字符串数组（UUID id）
        // 第十轮 T10-3（REQ-19 / 决策 #4）：账号展示信息，同样仅本地（换机不保留）。
        const val KEY_AVATAR_URI = "avatar_uri"        // data: URI（icon.pick 压缩链产物，≤64KB）
        const val KEY_DISPLAY_NAME = "display_name"    // 昵称离线镜像（PATCH /me 成功后写入，≤64 字符）
        // 第十一轮 T11-2（REQ-24 / 决策 #5）：桌面小组件全局配置（所有实例共享一份）。
        // T-M7-05j 起上云（换机/多设备共享同一份配置）。
        const val KEY_WIDGET_CONFIG = "widget_config"  // JSON {showTodayCount,showMonthExpense,showBudget,tapAction}（≤4KB）
        // 第十一轮 T11-1（REQ-23 / 决策 #1）：预算沿用开关 + 幂等 mark（{bookId:"YYYY-MM"}）。
        const val KEY_BUDGET_CARRYOVER = "budget_carryover"            // '1' | '0'
        const val KEY_BUDGET_CARRYOVER_MARK = "budget_carryover_mark"  // JSON map ≤8KB，仅 Repository 自管写入
        // 第十一轮 T11-6（REQ-27 / 决策 #7）：用户画像（AI context 注入源）。
        // T-M7-05j 起上云（AI 可经 meta_set 改画像，见 server proposal_ops）。
        const val KEY_USER_PROFILE = "user_profile"    // JSON 对象 ≤8KB（称呼/职业/节奏/风格等，字段见 UserSettingsPage）
        // 第十一轮 T11-7（REQ-28 / 决策 #8）：当前聊天角色。active 仅本地；
        // seeded 标记由 ensureSeedPersonas 自管（镜像 master_book_id 不开放前端写）。
        const val KEY_ACTIVE_PERSONA_ID = "active_persona_id"  // UUID；空/失效 → 回退"小贝"→第一条
        const val KEY_PERSONAS_SEEDED = "personas_seeded"      // '1' = 已播过内置 5 角色（删光不重播）
        // T-M7-01（三实体上云）：存量本地数据一次性上行的完成标记。'1' = 已入队。
        // 仅 Repository.migrateCloudEntitiesUp 自管写入（镜像 personas_seeded 模式），
        // 随 clearAllTables 一并清空（登出/删号后按新账号重新判定）。
        const val KEY_CLOUD_ENTITIES_UPLOADED = "cloud_entities_uploaded_v1"
        // REQ-79（T20-5）：分类种子版本。'2' = 28 支出 / 15 收入 + 二级清单。
        // 冷启动时若键缺失或落后，按「kind + name」diff 把缺失的默认分类补进本地
        // （**只增不改不删**：用户已有分类一行不动，新旧名并存是刻意接受的）。
        // 与 master_book_id / personas_seeded 同款「Repository 自管键」，不进
        // SYNCABLE_PREFERENCE_KEYS（meta 白名单仍 11 键）。
        const val KEY_CATEGORY_SEED_VERSION = "category_seed_version"
        // 第二十一轮 REQ-83（T21-3）：自动记账的开关与来源清单 —— **设备本地自管键**。
        // 两类授权（无障碍 / 通知使用权）本就是按设备在系统设置页授予的，故开关也
        // 按设备独立：**不上云、不进 SYNCABLE_PREFERENCE_KEYS**（决策 #9，meta 白名单
        // 仍 11 键）。镜像 master_book_id / personas_seeded / category_seed_version 的
        // 既有做法，只由 AutoBookRepository 读写。
        const val KEY_AUTOBOOK_ENABLED = "autobook_enabled"              // '1' | '0'；缺省 = '0'（默认关闭）
        // 用户从已安装列表勾选的额外来源包（JSON 字符串数组）。内置三项是代码常量
        // （AutoBookSources.DEFAULT_PACKAGES），此键只装用户追加项。
        const val KEY_AUTOBOOK_SOURCE_PACKAGES = "autobook_source_packages"

        /**
         * T-M7-05j（偏好项上云）：可上行到服务端的偏好键白名单。
         *
         * 与服务端 `SYNCABLE_KEYS`（server/src/models/user_preference.py）逐字对应 ——
         * 客户端这份是"钉住清单"的唯一来源，白名单外一律不上行（守护用例见
         * RepositoryUserPreferenceSyncTest）。本表里其余键都是设备自管量：
         * sync_cursor / master_book_id / personas_seeded / budget_carryover(_mark) /
         * cloud_entities_uploaded_v1 / active_persona_id / avatar_uri / display_name /
         * category_seed_version。
         *
         * 顺序即上行入队顺序，固定可复现。
         */
        val SYNCABLE_PREFERENCE_KEYS = setOf(
            KEY_DEFAULT_BOOK_ID, KEY_DEFAULT_ACCOUNT_ID, KEY_BOOK_ORDER,
            KEY_ACCOUNT_ORDER, KEY_WIDGET_CONFIG, KEY_USER_PROFILE,
        )

        // T-M7-05j：存量本地偏好值一次性上行的完成标记。'1' = 已入队。
        // 仅 Repository.migratePreferencesUp 自管写入，语义与
        // KEY_CLOUD_ENTITIES_UPLOADED 一致（同事务写标记 + mutation）。
        const val KEY_PREFERENCES_UPLOADED = "preferences_uploaded_v1"
        // T18-10（REQ-72）：一次性游标重置的完成标记。'1' = 已执行。
        // 服务端 _serialize_entity 发 ISO 字符串（自 T-M3 起），客户端 toTransaction
        // 之前只认 Long → 所有服务端下行的 transaction 被静默丢弃且游标照常推进。
        // 解析修复（epoch/ISO 双收）已让新数据正常落地，但历史已丢的数据不会自己
        // 回来——把 cursor 置 0 触发一次全量重放，让解析修复后的路径把漏掉的行拉回来。
        // 只做一次：标记与 cursor 重置在同一事务内，幂等且崩溃安全。
        const val KEY_PULL_ISO_FIX_APPLIED = "pull_iso_fix_applied_v1"
    }
}
