package com.aozora.app.permission

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat

/**
 * T-M4-04a: 权限状态机。
 *
 * 管理 RECORD_AUDIO / CAMERA / READ_MEDIA_IMAGES 三种权限的四态生命周期：
 * - NOT_REQUESTED：从未请求过
 * - GRANTED：已授予
 * - DENIED：已请求但被拒绝（仍可再次请求）
 * - PERMANENTLY_DENIED：已请求且被永久拒绝（需引导用户到设置页）
 *
 * 设计约束：
 * - 权限拒绝不阻塞手动记账（isCoreFunctionBlocked 始终返回 false）
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
            android.Manifest.permission.RECORD_AUDIO to getStatus(android.Manifest.permission.RECORD_AUDIO),
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
