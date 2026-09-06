package com.aozora.app.db.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import com.aozora.app.db.entity.SyncMetaEntity

@Dao
interface SyncMetaDao {

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(entry: SyncMetaEntity)

    @Query("SELECT value FROM sync_meta WHERE `key` = :key")
    suspend fun getValue(key: String): String?
}
