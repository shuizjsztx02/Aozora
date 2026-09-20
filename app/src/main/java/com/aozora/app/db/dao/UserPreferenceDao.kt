package com.aozora.app.db.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import com.aozora.app.db.entity.UserPreferenceEntity

/**
 * 用户级偏好 DAO（T-M7-05j）。
 *
 * 本表是云端镜像：pull/bootstrap 走 [upsert]（REPLACE）覆盖；本地写入（键在白名单内）
 * 由 Repository 铸行/涨版本后入队 mutation。无归档语义 —— 服务端 delete 即物理删。
 */
@Dao
interface UserPreferenceDao {

    @Query("SELECT * FROM user_preferences WHERE id = :id")
    suspend fun getById(id: String): UserPreferenceEntity?

    /** 按存储键名定位镜像行（上行取服务端行 id 与 expectedVersion）。 */
    @Query("SELECT * FROM user_preferences WHERE `key` = :key")
    suspend fun getByKey(key: String): UserPreferenceEntity?

    /** 全量（本表无归档/软删概念）；bootstrap 计数校验与一次性上行用它。 */
    @Query("SELECT * FROM user_preferences ORDER BY `key` ASC")
    suspend fun listAll(): List<UserPreferenceEntity>

    /** 合并 upsert — REPLACE 语义（key 唯一索引命中时以新行整体替换旧行）。 */
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(pref: UserPreferenceEntity)

    /** 服务端 delete → 物理删。 */
    @Query("DELETE FROM user_preferences WHERE id = :id")
    suspend fun deleteById(id: String)

    /** bootstrap 重建前清空（调用方须先做一次性上行，见 Repository.migratePreferencesUp）。 */
    @Query("DELETE FROM user_preferences")
    suspend fun clearAll()
}