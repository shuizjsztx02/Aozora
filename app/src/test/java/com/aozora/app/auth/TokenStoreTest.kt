package com.aozora.app.auth

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * T12 后修复（图片记账 403 身份漂移案）：TokenStore.clear() 必须保留 device_id。
 *
 * device_id 不是凭证而是设备标识：guest 重注册携带同一 device_id，服务端才能把
 * 该设备已有的隐私同意记录（user_consents，按 device_id 关联）套用到新 guest 上。
 * 旧实现连 device_id 一起抹掉 → 重注册即"新设备" → sync/push 恒 403
 * consent_required → 再被误判为认证丢失 → 死循环换身份。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class TokenStoreTest {

    private fun store(): TokenStore = TokenStore(RuntimeEnvironment.getApplication())

    @Test
    fun clear_preservesDeviceId_andWipesSessionState() {
        val s = store()
        val deviceId = s.deviceId

        s.clear()

        assertEquals("device_id must survive clear()", deviceId, s.deviceId)
        assertFalse(s.isRegistered)
        assertNull(s.getUserId())
    }

    @Test
    fun saveTokensThenClear_dropsTokens_butKeepsDeviceId() {
        val s = store()
        val deviceId = s.deviceId
        try {
            s.saveTokens(
                accessToken = "access-abc",
                refreshToken = "refresh-xyz",
                expiresAtSec = System.currentTimeMillis() / 1000 + 3600,
                userId = "u-1",
                markRegistered = true
            )
        } catch (e: Exception) {
            // Robolectric 环境无 AndroidKeyStore provider：加密路径不可用，跳过。
            assumeTrue("AndroidKeyStore unavailable under Robolectric", false)
        }
        assertEquals("access-abc", s.getAccessToken())

        s.clear()

        assertNull(s.getAccessToken())
        assertFalse(s.isRegistered)
        assertEquals(deviceId, s.deviceId)
    }
}
