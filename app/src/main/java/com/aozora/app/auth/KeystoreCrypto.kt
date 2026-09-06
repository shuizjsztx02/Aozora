package com.aozora.app.auth

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * AndroidKeyStore-backed AES/GCM 加解密。
 *
 * - 密钥永不离开 Keystore，明文只出现在本进程内存。
 * - GCM 12 字节 nonce 与密文一起前缀拼接存储。
 * - 仅由 [TokenStore] 调用；其它层禁止直接引用 KeystoreCrypto。
 *
 * R-PROD-13: 设备擦除/Keystore 损坏时抛出 [KeystoreUnavailableException]，
 * 上层视为"认证丢失"并引导用户重新进入匿名注册流程。
 */
internal object KeystoreCrypto {

    private const val KEYSTORE_PROVIDER = "AndroidKeyStore"
    private const val KEY_ALIAS = "aozora_token_key"
    private const val TRANSFORMATION = "AES/GCM/NoPadding"
    private const val GCM_TAG_BITS = 128
    private const val GCM_NONCE_BYTES = 12

    class KeystoreUnavailableException(message: String, cause: Throwable? = null) :
        RuntimeException(message, cause)

    fun encrypt(plaintext: ByteArray): ByteArray {
        val cipher = cipherForEncrypt()
        val encrypted = cipher.doFinal(plaintext)
        // IV 前缀拼接：[nonce 12B][ciphertext]
        return cipher.iv + encrypted
    }

    fun decrypt(payload: ByteArray): ByteArray {
        require(payload.size > GCM_NONCE_BYTES) { "payload too short" }
        val iv = payload.copyOfRange(0, GCM_NONCE_BYTES)
        val ciphertext = payload.copyOfRange(GCM_NONCE_BYTES, payload.size)
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.DECRYPT_MODE, loadOrGenerateKey(), GCMParameterSpec(GCM_TAG_BITS, iv))
        return cipher.doFinal(ciphertext)
    }

    private fun cipherForEncrypt(): Cipher {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, loadOrGenerateKey())
        return cipher
    }

    private fun loadOrGenerateKey(): SecretKey {
        val ks = try {
            KeyStore.getInstance(KEYSTORE_PROVIDER).apply { load(null) }
        } catch (e: Exception) {
            throw KeystoreUnavailableException("AndroidKeyStore unavailable", e)
        }
        val existing = ks.getEntry(KEY_ALIAS, null)
        if (existing is KeyStore.SecretKeyEntry) return existing.secretKey
        val generator = try {
            KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE_PROVIDER)
        } catch (e: Exception) {
            throw KeystoreUnavailableException("KeyGenerator init failed", e)
        }
        val spec = KeyGenParameterSpec.Builder(
            KEY_ALIAS,
            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
        )
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            .build()
        generator.init(spec)
        return generator.generateKey()
    }
}
