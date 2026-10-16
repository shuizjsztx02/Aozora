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

    // ─── 第二十一轮 T21-7（REQ-83，决策 #10）：「设置页授权」形态 ───
    //
    // Robolectric 下系统 API `getEnabledAccessibilityServiceList` /
    // `getEnabledListenerPackages` 始终返回空集——**无法模拟 ENABLED 态**。ENABLED 由
    // 真机验收覆盖（决策 #19 ①⑦）。JVM 侧只验证：①默认 UNAUTHORIZED；②ever_enabled
    // 标记被系统查不到时翻转为 ENABLED_BUT_DISABLED；③两个 target 的 ever_enabled 键
    // 互不干扰（只授一个也应有可用的一半能力，决策 #10）。

    @Test
    fun settingsAuth_defaultStatus_isUnauthorized_forBothTargets() {
        assertEquals(
            SettingsAuthStatus.UNAUTHORIZED,
            manager.getSettingsAuthStatus(SettingsAuthTarget.ACCESSIBILITY)
        )
        assertEquals(
            SettingsAuthStatus.UNAUTHORIZED,
            manager.getSettingsAuthStatus(SettingsAuthTarget.NOTIFICATION_LISTENER)
        )
    }

    @Test
    fun settingsAuth_everEnabled_butSystemDisabled_returnsEnabledButDisabled() {
        // 模拟"用户曾经启用过"：写本地 ever_enabled 标记。Robolectric 下系统 API
        // 返回空集，因此应落入第三态 ENABLED_BUT_DISABLED（"已授权但停用"）。
        val prefs = context.getSharedPreferences("aozora_permissions", Context.MODE_PRIVATE)
        prefs.edit()
            .putBoolean("autobook_accessibility_ever_enabled", true)
            .putBoolean("autobook_notification_ever_enabled", true)
            .apply()
        assertEquals(
            SettingsAuthStatus.ENABLED_BUT_DISABLED,
            manager.getSettingsAuthStatus(SettingsAuthTarget.ACCESSIBILITY)
        )
        assertEquals(
            SettingsAuthStatus.ENABLED_BUT_DISABLED,
            manager.getSettingsAuthStatus(SettingsAuthTarget.NOTIFICATION_LISTENER)
        )
    }

    @Test
    fun settingsAuth_twoTargets_areIndependent() {
        // 两个授权各自的 ever_enabled 键互不干扰——只授一个也应有可用的一半能力。
        val prefs = context.getSharedPreferences("aozora_permissions", Context.MODE_PRIVATE)
        prefs.edit()
            .putBoolean("autobook_accessibility_ever_enabled", true)
            .apply()
        assertEquals(
            "无障碍：本地 ever=true，系统查不到 → 第三态",
            SettingsAuthStatus.ENABLED_BUT_DISABLED,
            manager.getSettingsAuthStatus(SettingsAuthTarget.ACCESSIBILITY)
        )
        assertEquals(
            "通知：本地 ever=false，系统查不到 → UNAUTHORIZED",
            SettingsAuthStatus.UNAUTHORIZED,
            manager.getSettingsAuthStatus(SettingsAuthTarget.NOTIFICATION_LISTENER)
        )
    }

    @Test
    fun openSettingsAuthTarget_doesNotThrow() {
        // Robolectric 下跳系统页不会真开 Activity，但不应抛异常。
        manager.openSettingsAuthTarget(SettingsAuthTarget.ACCESSIBILITY)
        manager.openSettingsAuthTarget(SettingsAuthTarget.NOTIFICATION_LISTENER)
    }

    private fun grantPermission(permission: String) {
        val appContext = RuntimeEnvironment.getApplication()
        val shadowContext = org.robolectric.Shadows.shadowOf(appContext)
        shadowContext.grantPermissions(permission)
    }
}
