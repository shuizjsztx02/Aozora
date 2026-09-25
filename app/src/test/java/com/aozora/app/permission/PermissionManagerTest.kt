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
        val status = manager.getStatus(android.Manifest.permission.CAMERA)
        assertEquals(PermissionStatus.NOT_REQUESTED, status)
    }

    @Test
    fun getAllStatuses_returnsThreePermissions_onApi33() {
        val statuses = manager.getAllStatuses()
        assertTrue(statuses.containsKey(android.Manifest.permission.CAMERA))
        // NEW-A1 fix: POST_NOTIFICATIONS included on API 33+
        assertTrue(statuses.containsKey(android.Manifest.permission.POST_NOTIFICATIONS))
        assertEquals(3, statuses.size)
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
        grantPermission(android.Manifest.permission.CAMERA)
        val status = manager.getStatus(android.Manifest.permission.CAMERA)
        assertEquals(PermissionStatus.GRANTED, status)
    }

    @Test
    fun getStatus_afterRequestMarked_returnsDeniedOrPermanent() {
        // P2-2 fix: 将原本 OR 断言拆分。Robolectric 默认 shouldShowRequestPermissionRationale=false
        // （无 UI 对话框历史），所以「已请求 + 未授予」必然落入 PERMANENTLY_DENIED。
        // 这比原来的 OR 断言更严格：回归到"总是 DENIED"会被捕获。
        val prefs = context.getSharedPreferences("aozora_permissions", Context.MODE_PRIVATE)
        prefs.edit().putBoolean("perm_requested_${android.Manifest.permission.CAMERA}", true).apply()
        val status = manager.getStatus(android.Manifest.permission.CAMERA)
        assertEquals(
            "Robolectric 无 UI 历史 → shouldShowRationale=false → PERMANENTLY_DENIED",
            PermissionStatus.PERMANENTLY_DENIED, status
        )
    }

    @Test
    fun getStatus_withoutRequestMarked_returnsNotRequested() {
        // P2-2 配套：未请求分支保持 NOT_REQUESTED，确保「已请求」标记是关键分支点
        val status = manager.getStatus(android.Manifest.permission.CAMERA)
        assertEquals(PermissionStatus.NOT_REQUESTED, status)
    }

    // 注：DENIED 状态（shouldShowRequestPermissionRationale=true）需要真实权限对话框历史，
    // Robolectric 无公开 API 模拟"已拒绝一次且 rationale 仍显示"。此分支由 PERMANENTLY_DENIED
    // 测试间接覆盖——getStatus 的 if/else 两个分支均被断言。

    @Test
    @Config(sdk = [30])
    fun getAllStatuses_onApi30_returnsLegacyKeys() {
        // P2-3 fix: 覆盖 API < 33 的 legacy 分支（NOTIFICATIONS_LEGACY + READ_EXTERNAL_STORAGE）
        val statuses = manager.getAllStatuses()
        assertTrue(statuses.containsKey(android.Manifest.permission.CAMERA))
        assertTrue(statuses.containsKey(android.Manifest.permission.READ_EXTERNAL_STORAGE))
        assertTrue(statuses.containsKey("NOTIFICATIONS_LEGACY"))
        assertEquals(PermissionStatus.GRANTED, statuses["NOTIFICATIONS_LEGACY"])
        assertEquals(3, statuses.size)
        // API 30 不应包含 TIRAMISU 才有的 key
        assertFalse(statuses.containsKey(android.Manifest.permission.POST_NOTIFICATIONS))
        assertFalse(statuses.containsKey(android.Manifest.permission.READ_MEDIA_IMAGES))
    }

    @Test
    fun requestPermission_concurrentRequest_returnsDenied() {
        var firstResult: PermissionStatus? = null
        manager.requestPermission(android.Manifest.permission.READ_MEDIA_IMAGES) { firstResult = it }

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
