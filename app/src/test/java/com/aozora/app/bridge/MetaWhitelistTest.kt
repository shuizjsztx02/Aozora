package com.aozora.app.bridge

import com.aozora.app.db.entity.SyncMetaEntity
import org.junit.Assert.*
import org.junit.Test

/**
 * T9-5a（REQ-11 / 决策 #1）：meta.set 校验纯函数测试（白名单/格式/体积）。
 * 无 Robolectric——MetaWhitelist 不依赖 Android 运行时（org.json 由 Robolectric
 * 之外的 stub 提供不了，故本测试同样跑在 JVM：org.json 已在测试 classpath，
 * 与 BridgePayloadTest 同模式）。
 */
class MetaWhitelistTest {

    private val uuidA = "11111111-1111-4111-8111-111111111111"
    private val uuidB = "22222222-2222-4222-8222-222222222222"

    @Test
    fun keys_whitelistIsExactlyEleven() {
        assertEquals(
            setOf(
                "default_book_id", "default_account_id",
                "book_order", "account_order",
                // T10-3（决策 #4）：账号展示信息（仅本地）
                "avatar_uri", "display_name",
                // T11-1（REQ-23 / 决策 #1）：预算沿用开关 + 幂等 mark
                "budget_carryover", "budget_carryover_mark",
                // T11-2（REQ-24 / 决策 #5）：桌面小组件配置
                "widget_config",
                // T11-6（REQ-27 / 决策 #7）：用户画像
                "user_profile",
                // T11-7（REQ-28 / 决策 #8）：当前聊天角色（personas_seeded 为
                // Repository 自管键，同 master_book_id 不开放）
                "active_persona_id",
            ),
            MetaWhitelist.KEYS
        )
        assertFalse(MetaWhitelist.isAllowed("master_book_id"))
        assertFalse(MetaWhitelist.isAllowed("sync_cursor"))
        assertFalse(MetaWhitelist.isAllowed("personas_seeded"))
        assertTrue(MetaWhitelist.isAllowed(SyncMetaEntity.KEY_BOOK_ORDER))
    }

    @Test
    fun orderKey_acceptsValidUuidArray() {
        MetaWhitelist.validateValue(
            SyncMetaEntity.KEY_BOOK_ORDER, """["$uuidA","$uuidB"]"""
        )
        MetaWhitelist.validateValue(SyncMetaEntity.KEY_BOOK_ORDER, "[]")
    }

    @Test
    fun orderKey_rejectsNonJsonArray() {
        var threw = false
        try {
            MetaWhitelist.validateValue(SyncMetaEntity.KEY_BOOK_ORDER, "not-json")
        } catch (e: IllegalArgumentException) { threw = true }
        assertTrue(threw)
    }

    @Test
    fun orderKey_rejectsNonUuidElements() {
        var threw = false
        try {
            MetaWhitelist.validateValue(SyncMetaEntity.KEY_BOOK_ORDER, """["$uuidA","book_123"]""")
        } catch (e: IllegalArgumentException) { threw = true }
        assertTrue(threw)
    }

    @Test
    fun orderKey_rejectsNonStringElements() {
        var threw = false
        try {
            MetaWhitelist.validateValue(SyncMetaEntity.KEY_BOOK_ORDER, "[123]")
        } catch (e: IllegalArgumentException) { threw = true }
        assertTrue(threw)
    }

    @Test
    fun defaultKey_acceptsUuidOrEmpty() {
        MetaWhitelist.validateValue(SyncMetaEntity.KEY_DEFAULT_BOOK_ID, uuidA)
        MetaWhitelist.validateValue(SyncMetaEntity.KEY_DEFAULT_BOOK_ID, "") // 空串 = 清除默认
    }

    @Test
    fun defaultKey_rejectsNonUuid() {
        var threw = false
        try {
            MetaWhitelist.validateValue(SyncMetaEntity.KEY_DEFAULT_ACCOUNT_ID, "acc_1")
        } catch (e: IllegalArgumentException) { threw = true }
        assertTrue(threw)
    }

    @Test
    fun value_rejectsOver8Kb() {
        val big = "\"" + "a".repeat(9000) + "\""
        var threw = false
        try {
            MetaWhitelist.validateValue(SyncMetaEntity.KEY_BOOK_ORDER, big)
        } catch (e: IllegalArgumentException) { threw = true }
        assertTrue(threw)
    }

    // ─── T10-3（REQ-19 / 决策 #4）：avatar_uri / display_name ───

    @Test
    fun avatarKey_acceptsDataUriUpTo64Kb() {
        MetaWhitelist.validateValue(SyncMetaEntity.KEY_AVATAR_URI, "data:image/png;base64,AAAA")
        MetaWhitelist.validateValue(SyncMetaEntity.KEY_AVATAR_URI, "") // 空串 = 清除头像
        val exactly64K = "data:image/jpeg;base64," + "A".repeat(64 * 1024 - "data:image/jpeg;base64,".length)
        MetaWhitelist.validateValue(SyncMetaEntity.KEY_AVATAR_URI, exactly64K)
    }

    @Test
    fun avatarKey_rejectsNonDataUriAndOversize() {
        var threw = false
        try {
            MetaWhitelist.validateValue(SyncMetaEntity.KEY_AVATAR_URI, "https://example.com/a.png")
        } catch (e: IllegalArgumentException) { threw = true }
        assertTrue("非 data: URI 必须拒绝", threw)
        threw = false
        try {
            MetaWhitelist.validateValue(
                SyncMetaEntity.KEY_AVATAR_URI, "data:image/png;base64," + "A".repeat(64 * 1024)
            )
        } catch (e: IllegalArgumentException) { threw = true }
        assertTrue("超过 64KB 必须拒绝", threw)
    }

    @Test
    fun avatarKey_allowsValueBeyondLegacy8KbCap() {
        // 回归：全局 8KB 上限不得误伤照片头像（16KB data URI 合法）
        MetaWhitelist.validateValue(
            SyncMetaEntity.KEY_AVATAR_URI, "data:image/png;base64," + "A".repeat(16 * 1024)
        )
    }

    @Test
    fun displayNameKey_lengthCappedAt64() {
        MetaWhitelist.validateValue(SyncMetaEntity.KEY_DISPLAY_NAME, "小岛")
        MetaWhitelist.validateValue(SyncMetaEntity.KEY_DISPLAY_NAME, "a".repeat(64))
        var threw = false
        try {
            MetaWhitelist.validateValue(SyncMetaEntity.KEY_DISPLAY_NAME, "a".repeat(65))
        } catch (e: IllegalArgumentException) { threw = true }
        assertTrue(threw)
    }

    // ─── T11-1（REQ-23 / 决策 #1）：budget_carryover / budget_carryover_mark ───

    @Test
    fun carryoverFlag_acceptsOnlyOneOrZero() {
        MetaWhitelist.validateValue(SyncMetaEntity.KEY_BUDGET_CARRYOVER, "1")
        MetaWhitelist.validateValue(SyncMetaEntity.KEY_BUDGET_CARRYOVER, "0")
        for (bad in listOf("", "true", "2", "1 ")) {
            var threw = false
            try {
                MetaWhitelist.validateValue(SyncMetaEntity.KEY_BUDGET_CARRYOVER, bad)
            } catch (e: IllegalArgumentException) { threw = true }
            assertTrue("budget_carryover 只接受 '1'/'0'，got '$bad'", threw)
        }
    }

    @Test
    fun carryoverMark_acceptsBookIdToMonthMap() {
        MetaWhitelist.validateValue(SyncMetaEntity.KEY_BUDGET_CARRYOVER_MARK, "{}")
        MetaWhitelist.validateValue(
            SyncMetaEntity.KEY_BUDGET_CARRYOVER_MARK, """{"$uuidA":"2026-09"}"""
        )
    }

    @Test
    fun carryoverMark_rejectsBadShape() {
        // 非对象（数组/坏 JSON）
        for (bad in listOf("""["$uuidA"]""", "not-json")) {
            var threw = false
            try {
                MetaWhitelist.validateValue(SyncMetaEntity.KEY_BUDGET_CARRYOVER_MARK, bad)
            } catch (e: IllegalArgumentException) { threw = true }
            assertTrue("mark 必须是 JSON 对象，got '$bad'", threw)
        }
        // value 非 YYYY-MM
        var threw = false
        try {
            MetaWhitelist.validateValue(
                SyncMetaEntity.KEY_BUDGET_CARRYOVER_MARK, """{"$uuidA":"2026-9"}"""
            )
        } catch (e: IllegalArgumentException) { threw = true }
        assertTrue(threw)
    }

    // ─── T11-2（REQ-24 / 决策 #5）：widget_config ───

    @Test
    fun widgetKey_acceptsJsonObjectOrEmpty() {
        MetaWhitelist.validateValue(
            SyncMetaEntity.KEY_WIDGET_CONFIG,
            """{"showTodayCount":true,"showMonthExpense":false,"showBudget":true,"tapAction":"entry"}"""
        )
        MetaWhitelist.validateValue(SyncMetaEntity.KEY_WIDGET_CONFIG, "{}")
        MetaWhitelist.validateValue(SyncMetaEntity.KEY_WIDGET_CONFIG, "") // 空串 = 清除（读取端回退全默认）
    }

    @Test
    fun widgetKey_rejectsNonJsonAndOversize() {
        for (bad in listOf("not-json", """["$uuidA"]""")) {
            var threw = false
            try {
                MetaWhitelist.validateValue(SyncMetaEntity.KEY_WIDGET_CONFIG, bad)
            } catch (e: IllegalArgumentException) { threw = true }
            assertTrue("widget_config 必须是 JSON 对象，got '$bad'", threw)
        }
        // 超 4KB
        var threw = false
        try {
            MetaWhitelist.validateValue(
                SyncMetaEntity.KEY_WIDGET_CONFIG, "{\"pad\":\"" + "a".repeat(4096) + "\"}"
            )
        } catch (e: IllegalArgumentException) { threw = true }
        assertTrue("超过 4KB 必须拒绝", threw)
    }

    // ─── T11-6（REQ-27 / 决策 #7）：user_profile ───

    @Test
    fun userProfile_acceptsJsonObjectOrEmpty() {
        MetaWhitelist.validateValue(
            SyncMetaEntity.KEY_USER_PROFILE,
            """{"nickname":"老板","communicationStyle":"简洁直接"}"""
        )
        MetaWhitelist.validateValue(SyncMetaEntity.KEY_USER_PROFILE, "{}")
        MetaWhitelist.validateValue(SyncMetaEntity.KEY_USER_PROFILE, "") // 空串 = 清除画像
    }

    @Test
    fun userProfile_rejectsNonJsonAndOversize() {
        for (bad in listOf("not-json", """["$uuidA"]""")) {
            var threw = false
            try {
                MetaWhitelist.validateValue(SyncMetaEntity.KEY_USER_PROFILE, bad)
            } catch (e: IllegalArgumentException) { threw = true }
            assertTrue("user_profile 必须是 JSON 对象，got '$bad'", threw)
        }
        // 超 8KB（决策 #1：user_profile ≤8KB）
        var threw = false
        try {
            MetaWhitelist.validateValue(
                SyncMetaEntity.KEY_USER_PROFILE, "{\"pad\":\"" + "a".repeat(8192) + "\"}"
            )
        } catch (e: IllegalArgumentException) { threw = true }
        assertTrue("超过 8KB 必须拒绝", threw)
    }

    // ─── T11-7（REQ-28 / 决策 #8）：active_persona_id（else 分支 UUID 校验） ───

    @Test
    fun activePersonaKey_acceptsUuidOrEmpty() {
        MetaWhitelist.validateValue(SyncMetaEntity.KEY_ACTIVE_PERSONA_ID, uuidA)
        MetaWhitelist.validateValue(SyncMetaEntity.KEY_ACTIVE_PERSONA_ID, "") // 空串 = 清除，回退默认角色
    }

    @Test
    fun activePersonaKey_rejectsNonUuid() {
        for (bad in listOf("persona_1", "小贝", "[]")) {
            var threw = false
            try {
                MetaWhitelist.validateValue(SyncMetaEntity.KEY_ACTIVE_PERSONA_ID, bad)
            } catch (e: IllegalArgumentException) { threw = true }
            assertTrue("active_persona_id 只接受 UUID/空串，got '$bad'", threw)
        }
    }
}
