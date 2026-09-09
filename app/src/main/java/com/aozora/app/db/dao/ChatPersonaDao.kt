package com.aozora.app.db.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Update
import com.aozora.app.db.entity.ChatPersonaEntity

/**
 * 聊天角色 DAO（第十一轮 REQ-28 / T11-4 建表先行）。仅本地：无同步路径。
 * persona.* 桥命令与种子在 T11-7 接入，本卡只提供数据访问。
 */
@Dao
interface ChatPersonaDao {

    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insert(persona: ChatPersonaEntity)

    @Update
    suspend fun update(persona: ChatPersonaEntity)

    @Query("SELECT * FROM chat_personas WHERE id = :id")
    suspend fun getById(id: String): ChatPersonaEntity?

    @Query("SELECT * FROM chat_personas WHERE archivedAt IS NULL ORDER BY createdAt ASC")
    suspend fun listActive(): List<ChatPersonaEntity>

    @Query("UPDATE chat_personas SET archivedAt = :archivedAt, updatedAt = :archivedAt, version = version + 1 WHERE id = :id")
    suspend fun archive(id: String, archivedAt: Long)
}
