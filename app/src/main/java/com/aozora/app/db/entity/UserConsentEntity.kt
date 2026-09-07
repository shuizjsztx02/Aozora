package com.aozora.app.db.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * T-M5-04a: 隐私政策同意记录。
 *
 * 记录用户同意隐私政策的时间、版本号与设备 ID。
 * 通常只有一行记录；若政策版本升级，会追加新记录。
 */
@Entity(tableName = "user_consent")
data class UserConsentEntity(
    @PrimaryKey val id: String,
    val deviceId: String,
    val policyVersion: String,
    val consentedAt: Long   // epoch millis
)
