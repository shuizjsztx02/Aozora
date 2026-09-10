package com.aozora.app.auth

import android.content.Context
import android.content.SharedPreferences
import android.util.Base64
import androidx.core.content.edit

/**
 * TokenStore — 本地令牌持久化（AES/GCM 加密，Keystore 托管）。
 *
 * 存储字段：
 * - access_token：JWT，调用 /auth/... 时作为 Bearer 携带。
 * - refresh_token：长期令牌，用于 /auth/refresh 轮换。
 * - expires_at_epoch_sec：access_token 过期时间（epoch 秒）。
 * - user_id：服务端返回的 UUID。
 * - device_id：本地生成的设备标识（/auth/guest 与 /auth/refresh 共用）。
 * - registered：是否已经成功调用过 /auth/guest；未注册时首次同步前需先注册。
 *
 * 线程安全：SharedPreferences.edit(commit=false) 是同步落盘；本类不做跨进程同步，
 * 进程内由调用方（WorkManager / ViewModel）协程调度保证串行。
 */
open class TokenStore(context: Context) {

    private val prefs: SharedPreferences =
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** 本地设备 ID；首次读取时生成并固化。 */
    open val deviceId: String
        get() {
            // P2-6: 同步块防止 TOCTOU 竞态（两次首读各生成不同 UUID）
            synchronized(this) {
                prefs.getString(KEY_DEVICE_ID, null)?.let { return it }
                val generated = java.util.UUID.randomUUID().toString()
                prefs.edit { putString(KEY_DEVICE_ID, generated) }
                return generated
            }
        }

    /** 是否已经完成过匿名注册（/auth/guest）。 */
    open val isRegistered: Boolean
        get() = prefs.getBoolean(KEY_REGISTERED, false)

    /** 当前 access_token；未注册或已清除时返回 null。 */
    open fun getAccessToken(): String? = readSecret(KEY_ACCESS_TOKEN)

    /** 当前 refresh_token；未注册或已清除时返回 null。 */
    open fun getRefreshToken(): String? = readSecret(KEY_REFRESH_TOKEN)

    /** access_token 过期时间（epoch 秒）；未注册时返回 0。 */
    open fun getExpiresAt(): Long = prefs.getLong(KEY_EXPIRES_AT, 0L)

    /** 当前 user_id；未注册时返回 null。 */
    open fun getUserId(): String? = prefs.getString(KEY_USER_ID, null)

    /**
     * 持久化令牌集合（来自 /auth/guest 或 /auth/refresh）。
     *
     * - [accessToken] / [refreshToken]：原样以 UTF-8 编码后 AES/GCM 加密，
     *   结果 Base64 存储，避免 SharedPreferences 对特殊字符的二次处理。
     * - [expiresAtSec]：access_token 的过期时间戳（epoch 秒）。
     * - [userId]：仅首次注册时写入，refresh 时保持原值（传 null 跳过）。
     * - [markRegistered]：是否将 registered 标记为 true。
     */
    fun saveTokens(
        accessToken: String,
        refreshToken: String,
        expiresAtSec: Long,
        userId: String? = null,
        markRegistered: Boolean = false
    ) {
        prefs.edit {
            putString(KEY_ACCESS_TOKEN, encryptB64(accessToken))
            putString(KEY_REFRESH_TOKEN, encryptB64(refreshToken))
            putLong(KEY_EXPIRES_AT, expiresAtSec)
            if (userId != null) putString(KEY_USER_ID, userId)
            if (markRegistered) putBoolean(KEY_REGISTERED, true)
        }
    }

    /**
     * 清除所有令牌状态（登出 / 认证丢失时调用）。
     *
     * **device_id 跨清除保留**：它不是凭证，而是设备标识（/auth/guest 与
     * /auth/refresh 共用，且服务端设备级隐私同意按它关联）。若一并清除，
     * guest 重注册会生成新 device_id → 服务端 consent 判定查无此设备 →
     * sync/push 恒 403，形成"清凭证即换身份"的漂移循环（T12 后修图片记账 403 案）。
     */
    fun clear() {
        val deviceIdToKeep = prefs.getString(KEY_DEVICE_ID, null)
        prefs.edit {
            clear()
            if (deviceIdToKeep != null) putString(KEY_DEVICE_ID, deviceIdToKeep)
        }
    }

    /**
     * 在 access_token 过期前 [skewSec] 秒就开始刷新，避免边界窗口。
     * 默认 30 秒。
     */
    fun isAccessTokenExpired(nowEpochSec: Long, skewSec: Long = 30L): Boolean =
        nowEpochSec + skewSec >= getExpiresAt()

    // ─── internal helpers ───

    private fun encryptB64(value: String): String {
        val encrypted = KeystoreCrypto.encrypt(value.toByteArray(Charsets.UTF_8))
        return Base64.encodeToString(encrypted, Base64.NO_WRAP)
    }

    private fun readSecret(key: String): String? {
        val raw = prefs.getString(key, null) ?: return null
        return try {
            val decoded = Base64.decode(raw, Base64.NO_WRAP)
            String(KeystoreCrypto.decrypt(decoded), Charsets.UTF_8)
        } catch (e: KeystoreCrypto.KeystoreUnavailableException) {
            // Keystore 损坏：视为无令牌，触发上层重新注册。
            null
        } catch (e: java.security.GeneralSecurityException) {
            // P1-3: 密文损坏（AEADBadTagException 等）→ 视为无令牌，触发重新注册
            // 而非崩溃
            null
        }
    }

    companion object {
        private const val PREFS = "aozora_tokens"
        private const val KEY_ACCESS_TOKEN = "access_token_enc"
        private const val KEY_REFRESH_TOKEN = "refresh_token_enc"
        private const val KEY_EXPIRES_AT = "expires_at"
        private const val KEY_USER_ID = "user_id"
        private const val KEY_DEVICE_ID = "device_id"
        private const val KEY_REGISTERED = "registered"
    }
}
