package com.aozora.app.permission

import android.accessibilityservice.AccessibilityServiceInfo
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.Settings
import android.view.accessibility.AccessibilityManager
import androidx.activity.ComponentActivity
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.app.NotificationManagerCompat

/**
 * T-M4-04a + 第二十一轮 T21-7（REQ-83）：权限状态机。
 *
 * 管理两类授权：
 * 1. **运行时权限**（CAMERA / READ_MEDIA_IMAGES / POST_NOTIFICATIONS）：四态生命周期
 *    NOT_REQUESTED / GRANTED / DENIED / PERMANENTLY_DENIED，通过 `registerForActivityResult` 走
 *    系统弹窗，拒绝后引导用户到应用设置页。
 * 2. **「设置页授权」**（第二十一轮新增，决策 #10）：无障碍服务 / 通知使用权，两者**都不是
 *    运行时权限**——`checkSelfPermission` 查不到、`requestPermission` 弹窗也走不到，只能
 *    靠系统 API 查询当前是否启用，并引导用户跳到**系统级设置页**手动授予。
 *    三态：UNAUTHORIZED（未授权）/ ENABLED（已启用）/ ENABLED_BUT_DISABLED（已授权但服务
 *    被系统或用户停用）。「已授权但停用」的判定依据 = 本地 SharedPreferences 里的
 *    「曾经启用」标记：系统显示未启用 + 本地标记为 true ⇒ 第三态。
 *
 * 设计约束（沿用）：
 * - 权限拒绝不阻塞手动记账（`isCoreFunctionBlocked` 始终返回 false）
 * - Activity 重建后通过 SharedPreferences 恢复"已请求"标记
 * - requestPermission 异步返回结果（通过回调）
 *
 * 线程约束：所有方法必须在主线程调用（ActivityResultLauncher 回调、
 * SharedPreferences 访问、WebView 推送均绑定主线程）。
 */
class PermissionManager(private val activity: ComponentActivity) {

    private val prefs by lazy {
        activity.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    private var permissionLauncher: ActivityResultLauncher<String>? = null
    // P2-4 fix: 以下两个字段仅在主线程读写（launcher 回调在主线程、requestPermission 由 UI 触发），
    // 不需 @Volatile；跨线程访问违反类级线程约定。
    private var pendingCallback: ((PermissionStatus) -> Unit)? = null
    private var pendingPermission: String? = null

    /**
     * 注册 ActivityResultLauncher。
     *
     * 必须在 Activity 的 onCreate 中调用（否则 registerForActivityResult 会抛异常）。
     */
    fun registerLaunchers() {
        permissionLauncher = activity.registerForActivityResult(
            ActivityResultContracts.RequestPermission()
        ) { granted ->
            val permission = pendingPermission
            val callback = pendingCallback
            pendingPermission = null
            pendingCallback = null

            val status = when {
                granted -> PermissionStatus.GRANTED
                permission != null && !activity.shouldShowRequestPermissionRationale(permission) ->
                    PermissionStatus.PERMANENTLY_DENIED
                else -> PermissionStatus.DENIED
            }
            callback?.invoke(status)
        }
    }

    /**
     * 获取单个权限状态。
     */
    fun getStatus(permission: String): PermissionStatus {
        val granted = ContextCompat.checkSelfPermission(
            activity, permission
        ) == PackageManager.PERMISSION_GRANTED

        if (granted) return PermissionStatus.GRANTED

        val hasRequested = prefs.getBoolean(keyRequested(permission), false)
        if (!hasRequested) return PermissionStatus.NOT_REQUESTED

        // 已请求过但未授予
        return if (activity.shouldShowRequestPermissionRationale(permission)) {
            PermissionStatus.DENIED
        } else {
            PermissionStatus.PERMANENTLY_DENIED
        }
    }

    /**
     * 获取 T-M4-04 相关的所有权限状态。
     *
     * NEW-A1 fix: 补 POST_NOTIFICATIONS，与 TS 类型 Record<PermissionType, PermissionStatus> 对齐。
     * API 33+ 检查真实运行时状态；低版本不需要运行时通知权限，直接返回 GRANTED。
     */
    fun getAllStatuses(): Map<String, PermissionStatus> {
        val result = mutableMapOf(
            android.Manifest.permission.CAMERA to getStatus(android.Manifest.permission.CAMERA),
            imagePermission() to getStatus(imagePermission())
        )
        // POST_NOTIFICATIONS: API 33+ 有真实运行时权限；低版本 checkSelfPermission
        // 对未定义的权限返回 GRANTED，但 shouldShowRequestPermissionRationale 返回 false，
        // 若 prefs 中无「已请求」记录则 getStatus 返回 NOT_REQUESTED — 语义不正确。
        // 因此低版本直接返回 GRANTED。
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU) {
            result[android.Manifest.permission.POST_NOTIFICATIONS] = getStatus(android.Manifest.permission.POST_NOTIFICATIONS)
        } else {
            result["NOTIFICATIONS_LEGACY"] = PermissionStatus.GRANTED
        }
        return result
    }

    /**
     * 请求单个权限。
     *
     * 结果通过 [callback] 异步返回。如果 launcher 未注册或正在处理另一个请求，
     * 立即返回 DENIED。
     */
    fun requestPermission(permission: String, callback: (PermissionStatus) -> Unit) {
        val launcher = permissionLauncher
        if (launcher == null) {
            callback(PermissionStatus.DENIED)
            return
        }

        // 先检查是否已授予（在 pending 检查之前，避免已授予的权限被错误 DENIED）
        val currentStatus = getStatus(permission)
        if (currentStatus == PermissionStatus.GRANTED) {
            callback(PermissionStatus.GRANTED)
            return
        }

        if (pendingCallback != null) {
            // 已有请求在处理中
            callback(PermissionStatus.DENIED)
            return
        }

        pendingPermission = permission
        pendingCallback = callback
        // P2-2: launch 时即写「已请求」标记，防止 Activity 重建导致回调丢失、状态机停在 NOT_REQUESTED
        prefs.edit().putBoolean(keyRequested(permission), true).apply()
        launcher.launch(permission)
    }

    /**
     * 打开应用设置页（用于永久拒绝后引导用户手动开启）。
     */
    fun openAppSettings() {
        val intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
            data = Uri.fromParts("package", activity.packageName, null)
            flags = Intent.FLAG_ACTIVITY_NEW_TASK
        }
        activity.startActivity(intent)
    }

    // ─── 第二十一轮 T21-7（REQ-83）：「设置页授权」形态（决策 #10） ───
    //
    // 无障碍服务 / 通知使用权都不是运行时权限，`checkSelfPermission` 查不到、
    // `requestPermission` 弹窗也走不到。两者只能：①用系统 API 查是否启用，
    // ②跳系统级设置页让用户手动授予。

    /**
     * 查询「设置页授权」的三态（决策 #10）。
     *
     * 三态：
     * - [SettingsAuthStatus.UNAUTHORIZED]：系统未启用 + 本地无「曾经启用」记录。
     * - [SettingsAuthStatus.ENABLED]：系统当前显示启用。调用时同步把本地「曾经启用」
     *   标记置 true，避免下次启用 → 停用 → 再查时回落到 UNAUTHORIZED 而非第三态。
     * - [SettingsAuthStatus.ENABLED_BUT_DISABLED]：系统当前显示未启用、但本地有
     *   「曾经启用」记录 —— 用户启用了又被系统/用户停用。UI 据此给出「重新启用」
     *   的引导而非「首次授权」。
     */
    fun getSettingsAuthStatus(target: SettingsAuthTarget): SettingsAuthStatus {
        val systemEnabled = querySystemEnabled(target)
        val everKey = keyEverEnabled(target)
        if (systemEnabled) {
            // 系统当前启用 ⇒ 落本地「曾经启用」标记，下次即便被停用也能落到第三态
            prefs.edit().putBoolean(everKey, true).apply()
            return SettingsAuthStatus.ENABLED
        }
        val everEnabled = prefs.getBoolean(everKey, false)
        return if (everEnabled) SettingsAuthStatus.ENABLED_BUT_DISABLED
        else SettingsAuthStatus.UNAUTHORIZED
    }

    /**
     * 跳系统级设置页授权对应目标。两者都走 `FLAG_ACTIVITY_NEW_TASK`、与既有
     * `openAppSettings` 一致，无需 ActivityResult 回调——用户返回应用时前端会重新拉
     * `getSettingsAuthStatus` 刷新态。
     */
    fun openSettingsAuthTarget(target: SettingsAuthTarget) {
        val action = when (target) {
            SettingsAuthTarget.ACCESSIBILITY -> Settings.ACTION_ACCESSIBILITY_SETTINGS
            SettingsAuthTarget.NOTIFICATION_LISTENER ->
                "android.settings.ACTION_NOTIFICATION_LISTENER_SETTINGS"
        }
        val intent = Intent(action).apply { flags = Intent.FLAG_ACTIVITY_NEW_TASK }
        // ACTION_NOTIFICATION_LISTENER_SETTINGS 在少数 ROM 上无 Activity 处理，用 resolveActivity
        // 兜底到应用详情页（至少让用户能看到应用条目，再手动找通知使用权入口）。
        if (intent.resolveActivity(activity.packageManager) == null) {
            intent.action = Settings.ACTION_APPLICATION_DETAILS_SETTINGS
            intent.data = Uri.fromParts("package", activity.packageName, null)
        }
        activity.startActivity(intent)
    }

    /**
     * 系统级查询：对应目标当前是否被系统启用。纯系统 API，不读本地标记。
     */
    private fun querySystemEnabled(target: SettingsAuthTarget): Boolean {
        return when (target) {
            SettingsAuthTarget.ACCESSIBILITY -> {
                val am = activity.getSystemService(Context.ACCESSIBILITY_SERVICE)
                    as? AccessibilityManager ?: return false
                val expected = ComponentName(
                    activity.packageName,
                    "com.aozora.app.autobook.AutoBookAccessibilityService"
                )
                // ★ 使用 FEEDBACK_ALL_MASK（与既有 AozoraBridge 判定保持一致）
                am.getEnabledAccessibilityServiceList(AccessibilityServiceInfo.FEEDBACK_ALL_MASK)
                    .any { info ->
                        val si = info.resolveInfo?.serviceInfo ?: return@any false
                        ComponentName(si.packageName, si.name) == expected
                    }
            }
            SettingsAuthTarget.NOTIFICATION_LISTENER -> {
                NotificationManagerCompat.getEnabledListenerPackages(activity)
                    .contains(activity.packageName)
            }
        }
    }

    private fun keyEverEnabled(target: SettingsAuthTarget): String = when (target) {
        SettingsAuthTarget.ACCESSIBILITY -> "autobook_accessibility_ever_enabled"
        SettingsAuthTarget.NOTIFICATION_LISTENER -> "autobook_notification_ever_enabled"
    }

    /**
     * 权限拒绝是否影响核心功能。
     *
     * 按 T-M4-04a 验收标准：权限拒绝不阻塞手动记账。
     */
    fun isCoreFunctionBlocked(): Boolean = false

    /**
     * API 33+ 使用 READ_MEDIA_IMAGES，低版本使用 READ_EXTERNAL_STORAGE。
     */
    private fun imagePermission(): String {
        return if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU) {
            android.Manifest.permission.READ_MEDIA_IMAGES
        } else {
            android.Manifest.permission.READ_EXTERNAL_STORAGE
        }
    }

    private fun keyRequested(permission: String): String = "perm_requested_$permission"

    companion object {
        private const val PREFS_NAME = "aozora_permissions"
    }
}

/**
 * T-M4-04a: 权限四态。
 */
enum class PermissionStatus {
    NOT_REQUESTED,
    GRANTED,
    DENIED,
    PERMANENTLY_DENIED
}

/**
 * 第二十一轮 T21-7（REQ-83，决策 #10）：「设置页授权」目标。
 *
 * 这两类授权都**不是运行时权限**——`checkSelfPermission` 查不到、`requestPermission`
 * 弹窗也走不到，只能由用户在系统设置页手动授予。前端通过 `permissions.openSettings`
 * 的 `target` 参数跳对应系统页，由 `PermissionManager.getSettingsAuthStatus` 查当前态。
 */
enum class SettingsAuthTarget {
    ACCESSIBILITY,
    NOTIFICATION_LISTENER
}

/**
 * 第二十一轮 T21-7（REQ-83，决策 #10）：「设置页授权」三态。
 *
 * UI 给每类授权三种人话文案：
 * - [UNAUTHORIZED]：「未授权 · 点击前往系统设置启用」
 * - [ENABLED]：「已启用」
 * - [ENABLED_BUT_DISABLED]：「已授权但当前停用 · 点击前往系统设置重新启用」
 */
enum class SettingsAuthStatus {
    UNAUTHORIZED,
    ENABLED,
    ENABLED_BUT_DISABLED
}
