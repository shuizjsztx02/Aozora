package com.aozora.app.permission

import android.content.Context
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * T-M4-04a: PermissionManager 单元测试。
 *
 * 使用 Robolectric 验证：
 * - 四态检测（NOT_REQUESTED / GRANTED / DENIED / PERMANENTLY_DENIED）
 * - SharedPreferences 持久化"已请求"标记
 * - 权限拒绝不阻塞手动记账
 * - openAppSettings 不抛异常
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class PermissionManagerTest {

    private lateinit var context: Context
    private lateinit var activity: androidx.appcompat.app.AppCompatActivity
    private lateinit var manager: PermissionManager

    @Before
    fun setUp() {
        context = RuntimeEnvironment.getApplication()
        // registerForActivityResult 必须在 activity 到达 STARTED 前调用
        val controller = Robolectric.buildActivity(
            androidx.appcompat.app.AppCompatActivity::class.java
        ).create()
        activity = controller.get()
        manager = PermissionManager(activity)
        manager.registerLaunchers()
        controller.resume()
    }

    @Test
    fun defaultStatus_isNotRequested() {
        val status = manager.getStatus(android.Manifest.permission.RECORD_AUDIO)
        assertEquals(PermissionStatus.NOT_REQUESTED, status)
    }

    @Test
    fun getAllStatuses_returnsFourPermissions_onApi33() {
        val statuses = manager.getAllStatuses()
        assertTrue(statuses.containsKey(android.Manifest.permission.RECORD_AUDIO))
        assertTrue(statuses.containsKey(android.Manifest.permission.CAMERA))
        // NEW-A1 fix: POST_NOTIFICATIONS included on API 33+
        assertTrue(statuses.containsKey(android.Manifest.permission.POST_NOTIFICATIONS))
        assertEquals(4, statuses.size)
    }

    @Test
    fun isCoreFunctionBlocked_alwaysReturnsFalse() {
        assertFalse(manager.isCoreFunctionBlocked())
    }

    @Test
    fun openAppSettings_doesNotThrow() {
        manager.openAppSettings()
    }

    @Test
    fun requestPermission_withoutLauncher_returnsDenied() {
        val unregistered = PermissionManager(activity)
        var result: PermissionStatus? = null
        unregistered.requestPermission(android.Manifest.permission.CAMERA) { result = it }
        assertEquals(PermissionStatus.DENIED, result)
    }

    @Test
    fun getStatus_afterGranted_returnsGranted() {
        grantPermission(android.Manifest.permission.RECORD_AUDIO)
        val status = manager.getStatus(android.Manifest.permission.RECORD_AUDIO)
        assertEquals(PermissionStatus.GRANTED, status)
    }

    @Test
    fun getStatus_afterRequestMarked_returnsDeniedOrPermanent() {
        // 标记已请求（模拟用户曾请求过）
        val prefs = context.getSharedPreferences("aozora_permissions", Context.MODE_PRIVATE)
        prefs.edit().putBoolean("perm_requested_${android.Manifest.permission.CAMERA}", true).apply()

        val status = manager.getStatus(android.Manifest.permission.CAMERA)
        assertTrue(
            status == PermissionStatus.DENIED || status == PermissionStatus.PERMANENTLY_DENIED
        )
    }

    @Test
    fun requestPermission_concurrentRequest_returnsDenied() {
        var firstResult: PermissionStatus? = null
        manager.requestPermission(android.Manifest.permission.RECORD_AUDIO) { firstResult = it }

        var secondResult: PermissionStatus? = null
        manager.requestPermission(android.Manifest.permission.CAMERA) { secondResult = it }
        assertEquals(PermissionStatus.DENIED, secondResult)
    }

    private fun grantPermission(permission: String) {
        val appContext = RuntimeEnvironment.getApplication()
        val shadowContext = org.robolectric.Shadows.shadowOf(appContext)
        shadowContext.grantPermissions(permission)
    }
}
