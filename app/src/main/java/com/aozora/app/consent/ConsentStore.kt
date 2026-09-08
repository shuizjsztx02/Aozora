package com.aozora.app.consent

import android.content.Context
import android.content.SharedPreferences

/**
 * T-M5-04a: 隐私同意快速检查。
 *
 * 使用 SharedPreferences 存储同意状态，避免每次检查都查询 Room。
 * [hasConsented] 供 SyncScheduler / SyncEngine 在同步前快速判断。
 */
class ConsentStore(context: Context) {

    private val prefs: SharedPreferences =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    /** 是否已同意隐私政策。 */
    fun hasConsented(): Boolean = prefs.getBoolean(KEY_CONSENTED, false)

    /** 标记已同意。 */
    fun markConsented() {
        prefs.edit().putBoolean(KEY_CONSENTED, true).apply()
    }

    /** 清除同意状态（删除账号时使用）。 */
    fun clear() {
        prefs.edit()
            .remove(KEY_CONSENTED)
            .remove(KEY_CONSENT_UPLOADED)
            .apply()
    }

    // ─── T-M5-05c fix (S1): 服务端上传状态 ───

    /**
     * 是否已成功将同意记录上传到服务端 POST /consent。
     *
     * 用于 AozoraApp.onCreate 的重试逻辑 —— 当用户首次同意时网络
     * 不可达导致上传失败，下次冷启动时补发一次。
     */
    fun hasUploadedConsent(): Boolean = prefs.getBoolean(KEY_CONSENT_UPLOADED, false)

    /** 标记服务端上传成功。 */
    fun markConsentUploaded() {
        prefs.edit().putBoolean(KEY_CONSENT_UPLOADED, true).apply()
    }

    companion object {
        private const val PREFS_NAME = "aozora_consent"
        private const val KEY_CONSENTED = "consented"
        private const val KEY_CONSENT_UPLOADED = "consent_uploaded"
    }
}
