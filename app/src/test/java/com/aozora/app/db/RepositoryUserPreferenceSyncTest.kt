package com.aozora.app.db

import android.database.sqlite.SQLiteDatabase
import com.aozora.app.db.entity.PendingMutationEntity
import com.aozora.app.db.entity.SyncMetaEntity
import com.aozora.app.db.entity.UserPreferenceEntity
import com.aozora.app.sync.BootstrapResponse
import com.aozora.app.sync.ServerChange
import com.aozora.app.sync.SyncChangeOp
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * T-M7-05j（偏好项上云）：user_preference 的本地侧全链路 —— 云端→本地合并
 * （pull/bootstrap）、本地→云端上行（写入口 + 一次性迁移）、以及 Room 9→10 迁移。
 *
 * 关键不变式：
 * - 偏好值的**真身在 sync_meta**（UI 读的是它），user_preferences 表只是云端镜像。
 * - 上行只认白名单 6 键（[SyncMetaEntity.SYNCABLE_PREFERENCE_KEYS]）；设备自管量
 *   （avatar_uri / display_name / active_persona_id / 各类标记）一个都不上行。
 * - 服务端 delete = 物理删（无 archived_at / deleted_at）。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class RepositoryUserPreferenceSyncTest {

    private lateinit var db: AozoraDatabase
    private lateinit var repository: Repository

    @Before
    fun setUp() {
        db = AozoraDatabase.createInMemory(RuntimeEnvironment.getApplication())
        repository = Repository(db)
    }

    @After
    fun tearDown() { db.close() }

    private fun change(
        id: String, op: SyncChangeOp, version: Int,
        payload: Map<String, String?> = emptyMap()
    ) = ServerChange(
        entityType = "user_preference", entityId = id, operation = op,
        version = version, entityPayload = payload
    )

    /** 与 SyncPayloadMapper.snakeToCamelMap 输出同形（camelCase，值统一为字符串）。 */
    private fun linkPayload(
        id: String, key: String, value: String, version: Int
    ): Map<String, String?> = mapOf(
        "id" to id, "key" to key, "value" to value, "version" to version.toString()
    )

    // ─── pull：合并 ───

    @Test
    fun pull_userPreference_upsertsMirrorRowAndWritesMeta() = runBlocking {
        repository.applyServerChanges(
            listOf(change("p1", SyncChangeOp.INSERT, 1,
                linkPayload("p1", SyncMetaEntity.KEY_DEFAULT_BOOK_ID, "book-9", 1))),
            newCursor = 10, ownerId = "u1"
        )
        val row = db.userPreferenceDao().getById("p1")!!
        assertEquals(SyncMetaEntity.KEY_DEFAULT_BOOK_ID, row.key)
        assertEquals("book-9", row.value)
        assertEquals(1, row.version)
        // 值回灌 sync_meta —— UI 与 AI 改默认账本都读这里
        assertEquals("book-9", repository.getMeta(SyncMetaEntity.KEY_DEFAULT_BOOK_ID))
        assertEquals(10L, repository.getSyncCursor())
    }

    @Test
    fun pull_userPreference_updateOverwritesMeta() = runBlocking {
        repository.applyServerChanges(
            listOf(change("p1", SyncChangeOp.UPDATE, 2,
                linkPayload("p1", SyncMetaEntity.KEY_BOOK_ORDER, """["b1"]""", 2))),
            newCursor = 11, ownerId = "u1"
        )
        assertEquals("""["b1"]""", repository.getMeta(SyncMetaEntity.KEY_BOOK_ORDER))
        assertEquals(2, db.userPreferenceDao().getById("p1")!!.version)
    }

    @Test
    fun pull_userPreference_physicalDelete_alsoDropsMetaKey() = runBlocking {
        repository.applyServerChanges(
            listOf(change("p1", SyncChangeOp.INSERT, 1,
                linkPayload("p1", SyncMetaEntity.KEY_DEFAULT_ACCOUNT_ID, "acc-1", 1))),
            newCursor = 10, ownerId = "u1"
        )
        assertNotNull(repository.getMeta(SyncMetaEntity.KEY_DEFAULT_ACCOUNT_ID))

        // 云端删行 → 本地物删（非归档），sync_meta 同名键一并清掉
        repository.applyServerChanges(
            listOf(change("p1", SyncChangeOp.DELETE, 2)),
            newCursor = 12, ownerId = "u1"
        )
        assertNull(db.userPreferenceDao().getById("p1"))
        assertNull(repository.getMeta(SyncMetaEntity.KEY_DEFAULT_ACCOUNT_ID))
        assertEquals(12L, repository.getSyncCursor())
    }

    @Test
    fun pull_userPreference_deleteUnknownId_doesNotCrash() = runBlocking {
        repository.applyServerChanges(
            listOf(change("ghost", SyncChangeOp.DELETE, 2)),
            newCursor = 5, ownerId = "u1"
        )
        assertEquals(0, db.userPreferenceDao().listAll().size)
    }

    /**
     * 白名单外键（设备自管量）即使从云端下来也**不覆盖本地 sync_meta** ——
     * 镜像行照落（它是行级事实），但"值回灌"这一步只认 6 键。
     * 服务端不会发这类键，此处钉的是客户端侧的防御口径。
     */
    @Test
    fun pull_nonWhitelistedKey_neverOverwritesMeta() = runBlocking {
        repository.setMeta(SyncMetaEntity.KEY_AVATAR_URI, "data:image/png;base64,local")
        repository.applyServerChanges(
            listOf(change("p9", SyncChangeOp.INSERT, 1,
                linkPayload("p9", SyncMetaEntity.KEY_AVATAR_URI, "data:image/png;base64,cloud", 1))),
            newCursor = 9, ownerId = "u1"
        )
        assertEquals(
            "data:image/png;base64,local",
            repository.getMeta(SyncMetaEntity.KEY_AVATAR_URI),
        )
    }

    // ─── bootstrap ───

    @Test
    fun applyBootstrap_restoresPreferencesAndMeta() = runBlocking {
        val response = BootstrapResponse(
            books = emptyList(), categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            cursor = 42L, checksum = "", computedChecksum = "",
            userPreferences = listOf(
                linkPayload("p1", SyncMetaEntity.KEY_DEFAULT_BOOK_ID, "b7", 3),
                linkPayload("p2", SyncMetaEntity.KEY_WIDGET_CONFIG, """{"showBudget":true}""", 1),
            )
        )
        repository.applyBootstrap(response, ownerId = "u1")

        assertEquals(2, db.userPreferenceDao().listAll().size)
        assertEquals("b7", repository.getMeta(SyncMetaEntity.KEY_DEFAULT_BOOK_ID))
        assertEquals("""{"showBudget":true}""", repository.getMeta(SyncMetaEntity.KEY_WIDGET_CONFIG))
        assertEquals(42L, repository.getSyncCursor())
    }

    @Test
    fun applyBootstrap_skipsMalformedPreferenceRow() = runBlocking {
        // 缺 key → serverChangeMapToUserPreference 返回 null（bootstrap 计数校验会捕获）
        val response = BootstrapResponse(
            books = emptyList(), categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(),
            cursor = 1L, checksum = "", computedChecksum = "",
            userPreferences = listOf(mapOf("id" to "p1", "value" to "x", "version" to "1"))
        )
        repository.applyBootstrap(response, ownerId = "u1")
        assertEquals(0, db.userPreferenceDao().listAll().size)
    }

    // ─── 本地写入 → 上行 ───

    @Test
    fun setMetaWithPreferenceUpload_firstWriteEnqueuesInsert() = runBlocking {
        repository.setMetaWithPreferenceUpload(SyncMetaEntity.KEY_DEFAULT_BOOK_ID, "b1")

        // 本地值照写
        assertEquals("b1", repository.getMeta(SyncMetaEntity.KEY_DEFAULT_BOOK_ID))
        // 镜像行自铸 UUID，version=1
        val row = db.userPreferenceDao().getByKey(SyncMetaEntity.KEY_DEFAULT_BOOK_ID)!!
        assertEquals("b1", row.value)
        assertEquals(1, row.version)

        val mutations = repository.listPendingMutations()
        assertEquals(1, mutations.size)
        assertEquals("user_preference", mutations[0].entityType)
        assertEquals(row.id, mutations[0].entityId)          // entity_id = 镜像行 id = 服务端行 id
        assertEquals(PendingMutationEntity.OP_INSERT, mutations[0].operation)
        assertNull(mutations[0].expectedVersion)             // insert 无期望版本
        // payload 只有 key/value（服务端 insert 直接下标读这两键）
        val payload = org.json.JSONObject(mutations[0].payload)
        assertEquals(SyncMetaEntity.KEY_DEFAULT_BOOK_ID, payload.getString("key"))
        assertEquals("b1", payload.getString("value"))
    }

    @Test
    fun setMetaWithPreferenceUpload_secondWriteEnqueuesUpdateWithExpectedVersion() = runBlocking {
        repository.setMetaWithPreferenceUpload(SyncMetaEntity.KEY_BOOK_ORDER, """["b1"]""")
        db.pendingMutationDao().removeBatch(repository.listPendingMutations().map { it.mutationId })

        repository.setMetaWithPreferenceUpload(SyncMetaEntity.KEY_BOOK_ORDER, """["b2","b1"]""")

        val row = db.userPreferenceDao().getByKey(SyncMetaEntity.KEY_BOOK_ORDER)!!
        assertEquals(2, row.version)
        val mutations = repository.listPendingMutations()
        assertEquals(1, mutations.size)
        assertEquals(PendingMutationEntity.OP_UPDATE, mutations[0].operation)
        assertEquals(row.id, mutations[0].entityId)
        assertEquals(1, mutations[0].expectedVersion)        // = 变更前版本
    }

    /**
     * 守护用例：白名单外的键（设备自管量）**永不上行** —— 钉住 6 键清单，
     * 未来谁把 avatar_uri / display_name 之类塞进白名单都会在这里红。
     */
    @Test
    fun setMetaWithPreferenceUpload_deviceOnlyKeys_neverEnqueue() = runBlocking {
        val deviceOnly = listOf(
            SyncMetaEntity.KEY_CURSOR,
            SyncMetaEntity.KEY_MASTER_BOOK_ID,
            SyncMetaEntity.KEY_AVATAR_URI,
            SyncMetaEntity.KEY_DISPLAY_NAME,
            SyncMetaEntity.KEY_BUDGET_CARRYOVER,
            SyncMetaEntity.KEY_BUDGET_CARRYOVER_MARK,
            SyncMetaEntity.KEY_ACTIVE_PERSONA_ID,
            SyncMetaEntity.KEY_PERSONAS_SEEDED,
            SyncMetaEntity.KEY_CLOUD_ENTITIES_UPLOADED,
            SyncMetaEntity.KEY_PREFERENCES_UPLOADED,
        )
        for (key in deviceOnly) {
            repository.setMetaWithPreferenceUpload(key, "v-$key")
        }
        assertEquals(0, repository.listPendingMutations().size)
        assertEquals(0, db.userPreferenceDao().listAll().size)
        // 本地值照写（纯本地量不受影响）
        assertEquals("v-${SyncMetaEntity.KEY_AVATAR_URI}",
            repository.getMeta(SyncMetaEntity.KEY_AVATAR_URI))
    }

    @Test
    fun syncablePreferenceKeys_areExactlyTheServerWhitelist() {
        // 与服务端 SYNCABLE_KEYS 逐字对应的 6 键；与 MetaWhitelist.KEYS 是子集关系
        assertEquals(
            setOf(
                "default_book_id", "default_account_id", "book_order",
                "account_order", "widget_config", "user_profile",
            ),
            SyncMetaEntity.SYNCABLE_PREFERENCE_KEYS,
        )
        assertTrue(com.aozora.app.bridge.MetaWhitelist.KEYS.containsAll(SyncMetaEntity.SYNCABLE_PREFERENCE_KEYS))
    }

    // ─── 一次性上行迁移 ───

    @Test
    fun migratePreferencesUp_uploadsOnlyExistingKeys_andIsIdempotent() = runBlocking {
        // 旧版本留下的现状：只有两个键有值，其余四个从未写过
        repository.setMeta(SyncMetaEntity.KEY_DEFAULT_BOOK_ID, "b1")
        repository.setMeta(SyncMetaEntity.KEY_BOOK_ORDER, """["b1"]""")
        // 设备自管量：绝不能出现在上行批次里
        repository.setMeta(SyncMetaEntity.KEY_DISPLAY_NAME, "阿空")

        repository.migratePreferencesUp()

        val mutations = repository.listPendingMutations()
        assertEquals(2, mutations.size)
        assertEquals(
            setOf(SyncMetaEntity.KEY_DEFAULT_BOOK_ID, SyncMetaEntity.KEY_BOOK_ORDER),
            mutations.map { keyOf(it.payload) }.toSet(),
        )
        assertTrue(mutations.all { it.entityType == "user_preference" })
        assertTrue(mutations.all { it.operation == PendingMutationEntity.OP_INSERT })
        assertEquals("1", repository.getMeta(SyncMetaEntity.KEY_PREFERENCES_UPLOADED))

        // 幂等：标记已写 → 二次调用不再入队
        repository.migratePreferencesUp()
        assertEquals(2, repository.listPendingMutations().size)
    }

    @Test
    fun migratePreferencesUp_neverUploadsDeviceOnlyKeys() = runBlocking {
        // 白名单 6 键之外的键全部写满，迁移仍然一条都不入队
        for (key in listOf(
            SyncMetaEntity.KEY_AVATAR_URI, SyncMetaEntity.KEY_DISPLAY_NAME,
            SyncMetaEntity.KEY_ACTIVE_PERSONA_ID, SyncMetaEntity.KEY_BUDGET_CARRYOVER,
            SyncMetaEntity.KEY_MASTER_BOOK_ID, SyncMetaEntity.KEY_CURSOR,
        )) {
            repository.setMeta(key, "v")
        }
        repository.migratePreferencesUp()
        assertEquals(0, repository.listPendingMutations().size)
        assertEquals("1", repository.getMeta(SyncMetaEntity.KEY_PREFERENCES_UPLOADED))
    }

    /** 从 mutation payload 取 key（payload 只有 key/value 两键）。 */
    private fun keyOf(payload: String): String = org.json.JSONObject(payload).getString("key")

    // ─── Room 9 → 10 迁移 ───

    /**
     * 迁移 9→10：建 user_preferences 表 + key 唯一索引，存量数据保留。
     *
     * 手法：先用当前版本建库（v10 schema），再手工退回 v9 形态 —— 删掉
     * user_preferences 表、把 room_master_table 的 identity_hash 改回 9.json 的值、
     * user_version 置 9。这样重开时 Room 会真实走 MIGRATION_9_10，并按 v10 生成代码
     * 校验 schema（DDL 与实体不一致这里就会失败）。
     */
    @Test
    fun migration9To10_createsPreferenceTableAndKeepsData() = runBlocking {
        val ctx = RuntimeEnvironment.getApplication()
        val name = "test-migration-9-10.db"
        ctx.deleteDatabase(name)
        val fresh = AozoraDatabase.open(ctx, name)
        try {
            fresh.syncMetaDao().upsert(SyncMetaEntity(SyncMetaEntity.KEY_CURSOR, "7"))
        } finally {
            fresh.close()
        }
        val path = ctx.getDatabasePath(name).path
        SQLiteDatabase.openDatabase(path, null, SQLiteDatabase.OPEN_READWRITE).use { raw ->
            raw.execSQL("DROP TABLE IF EXISTS `user_preferences`")
            raw.execSQL(
                "UPDATE room_master_table SET identity_hash = ?",
                arrayOf(V9_IDENTITY_HASH)
            )
            raw.version = 9
        }

        val migrated = AozoraDatabase.open(ctx, name)
        try {
            // 表真的被建出来了（否则 DAO 调用会 "no such table"）
            migrated.userPreferenceDao().upsert(
                UserPreferenceEntity("p1", SyncMetaEntity.KEY_DEFAULT_BOOK_ID, "b1", 1)
            )
            assertEquals("b1", migrated.userPreferenceDao().getById("p1")!!.value)
            // 存量数据跨迁移保留
            assertEquals("7", migrated.syncMetaDao().getValue(SyncMetaEntity.KEY_CURSOR))
            // 唯一索引在：同 key 不同 id 的第二行会替换掉第一行（REPLACE + 唯一索引）
            migrated.userPreferenceDao().upsert(
                UserPreferenceEntity("p2", SyncMetaEntity.KEY_DEFAULT_BOOK_ID, "b2", 1)
            )
            assertEquals(1, migrated.userPreferenceDao().listAll().size)
            assertEquals("p2", migrated.userPreferenceDao().listAll()[0].id)
        } finally {
            migrated.close()
            ctx.deleteDatabase(name)
        }
    }

    private companion object {
        /** app/schemas/com.aozora.app.db.AozoraDatabase/9.json 的 identityHash。 */
        const val V9_IDENTITY_HASH = "530bd68db95e3c543fba4dd6e3d68bcc"
    }
}