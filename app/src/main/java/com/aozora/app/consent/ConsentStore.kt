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
        prefs.edit().remove(KEY_CONSENTED).apply()
    }

    companion object {
        private const val PREFS_NAME = "aozora_consent"
        private const val KEY_CONSENTED = "consented"
    }
}
