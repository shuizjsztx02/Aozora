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
    fun keys_whitelistIsExactlySix() {
        assertEquals(
            setOf(
                "default_book_id", "default_account_id",
                "book_order", "account_order",
                // T10-3（决策 #4）：账号展示信息（仅本地）
                "avatar_uri", "display_name",
            ),
            MetaWhitelist.KEYS
        )
        assertFalse(MetaWhitelist.isAllowed("master_book_id"))
        assertFalse(MetaWhitelist.isAllowed("sync_cursor"))
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
}
