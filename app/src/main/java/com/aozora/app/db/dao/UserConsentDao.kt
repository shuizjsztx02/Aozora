package com.aozora.app.db.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import com.aozora.app.db.entity.UserConsentEntity

/**
 * T-M5-04a: 隐私同意记录 DAO。
 */
@Dao
interface UserConsentDao {

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(consent: UserConsentEntity)

    @Query("SELECT * FROM user_consent ORDER BY consentedAt DESC LIMIT 1")
    suspend fun getLatest(): UserConsentEntity?

    @Query("SELECT COUNT(*) FROM user_consent")
    suspend fun count(): Int
}
