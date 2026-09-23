package com.aozora.app.db

import android.database.sqlite.SQLiteDatabase
import com.aozora.app.db.entity.AccountEntity
import com.aozora.app.db.entity.PendingMutationEntity
import com.aozora.app.sync.BootstrapResponse
import com.aozora.app.sync.ServerChange
import com.aozora.app.sync.SyncPayloadMapper
import com.aozora.app.sync.SyncChangeOp
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * 第十八轮 REQ-69（T18-5）：账户 `accountType` / `balanceCents` 两端化的同步三件套。
 *
 * 覆盖三面：
 * 1. **push**：两条 mutation（insert / update）的 payload 都带上两字段，余额是**绝对值**
 *    （决策 #4：重放不重复累加的前提）。
 * 2. **pull / bootstrap**：服务端的 `account_type` / `balance_cents` 落到 Room；
 *    `account_type` 有本地保护（服务端空值不冲掉本地分类），`balanceCents` 没有
 *    （0 是合法服务端值，服务端权威 —— 决策 #4）。
 * 3. **迁移 10 → 11**：`balanceCents` 列真的加上且存量行取 0。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class RepositoryAccountBalanceSyncTest {

    private lateinit var db: AozoraDatabase
    private lateinit var repository: Repository

    @Before
    fun setUp() {
        db = AozoraDatabase.createInMemory(RuntimeEnvironment.getApplication())
        repository = Repository(db)
    }

    @After
    fun tearDown() { db.close() }

    private fun account(
        id: String = "a1",
        type: String? = null,
        balance: Long = 0L,
        version: Int = 1,
    ) = AccountEntity(
        id = id, ownerId = "u1", name = "账户-$id", icon = "💰",
        accountType = type, balanceCents = balance, archivedAt = null, version = version,
    )

    private suspend fun payloadOf(
        entityId: String,
        operation: String = PendingMutationEntity.OP_INSERT,
    ): JSONObject = JSONObject(
        repository.listPendingMutations()
            .single { it.entityId == entityId && it.operation == operation }
            .payload
    )

    /** 模拟 push 成功：解除「活跃 mutation 跳过合并」条件。 */
    private suspend fun clearMutations() {
        repository.listPendingMutations().forEach { repository.removeMutation(it.mutationId) }
    }

    private fun accountChange(
        id: String, version: Int, extra: Map<String, String?>
    ) = ServerChange(
        entityType = "account", entityId = id, operation = SyncChangeOp.UPDATE, version = version,
        entityPayload = mapOf("id" to id, "name" to "账户-$id") + extra,
    )

    // ─── push 侧：两字段上行，余额为绝对值 ───

    @Test
    fun accountInsert_payloadCarriesTypeAndAbsoluteBalance() = runBlocking {
        repository.insertAccount(account(type = "储蓄卡", balance = 500_000L), "m1")
        val payload = payloadOf("a1")
        assertEquals("储蓄卡", payload.getString("accountType"))
        assertEquals(500_000L, payload.getLong("balanceCents"))
    }

    @Test
    fun accountUpdate_payloadCarriesTypeAndAbsoluteBalance() = runBlocking {
        repository.insertAccount(account(type = "微信", balance = 1_000L), "m1")
        repository.updateAccount(
            account(type = "储蓄卡", balance = 501_000L, version = 2), "m2"
        )
        val payload = payloadOf("a1", PendingMutationEntity.OP_UPDATE)
        assertEquals("储蓄卡", payload.getString("accountType"))
        // 绝对值而非增量：1_000 + 500_000 的结果，不是「+500_000」
        assertEquals(501_000L, payload.getLong("balanceCents"))
    }

    @Test
    fun accountInsert_negativeBalanceAllowed() = runBlocking {
        // 信用卡欠款：负余额必须能上行（服务端同样不加非负 CHECK）
        repository.insertAccount(account(type = "信用卡", balance = -32_500L), "m1")
        assertEquals(-32_500L, payloadOf("a1").getLong("balanceCents"))
    }

    @Test
    fun accountPayload_convertsToWireSnakeCase() = runBlocking {
        // 生产端发 camelCase、线缆要 snake_case：两键都必须落在 SNAKE_TO_CAMEL 的
        // 逆表里（否则服务端 account 分支读不到，字段再次静默丢失）。
        repository.insertAccount(account(type = "储蓄卡", balance = -32_500L), "m1")
        val wire = SyncPayloadMapper.toWirePayload(payloadOf("a1").toString())
        assertEquals("储蓄卡", wire.getString("account_type"))
        assertEquals(-32_500L, wire.getLong("balance_cents"))
    }

    // ─── pull 侧：两字段落库 ───

    @Test
    fun accountPull_mapsTypeAndBalanceFromServer() = runBlocking {
        repository.insertAccount(account(), "m1")
        clearMutations()
        repository.applyServerChanges(
            listOf(accountChange("a1", 2, mapOf("accountType" to "储蓄卡", "balanceCents" to "784500"))),
            newCursor = 0, ownerId = "u1",
        )
        val a = repository.getAccount("a1")!!
        assertEquals("储蓄卡", a.accountType)
        assertEquals(784_500L, a.balanceCents)
        assertEquals("服务端 version 对齐", 2, a.version)
    }

    @Test
    fun accountPull_negativeBalanceFromServer_mapped() = runBlocking {
        repository.insertAccount(account(), "m1")
        clearMutations()
        repository.applyServerChanges(
            listOf(accountChange("a1", 2, mapOf("balanceCents" to "-32500"))),
            newCursor = 0, ownerId = "u1",
        )
        assertEquals(-32_500L, repository.getAccount("a1")!!.balanceCents)
    }

    @Test
    fun accountPull_serverNullType_preservesLocalType() = runBlocking {
        // 存量场景：服务端 account_type 仍是 NULL（从未上行过），本地早已存着用户选的分类。
        repository.insertAccount(account(type = "储蓄卡"), "m1")
        clearMutations()
        repository.applyServerChanges(
            listOf(accountChange("a1", 2, mapOf("accountType" to null, "balanceCents" to "0"))),
            newCursor = 0, ownerId = "u1",
        )
        assertEquals("服务端空值不得冲掉本地分类", "储蓄卡",
            repository.getAccount("a1")!!.accountType)
    }

    @Test
    fun accountPull_serverType_winsOverLocalType() = runBlocking {
        repository.insertAccount(account(type = "微信"), "m1")
        clearMutations()
        repository.applyServerChanges(
            listOf(accountChange("a1", 2, mapOf("accountType" to "储蓄卡"))),
            newCursor = 0, ownerId = "u1",
        )
        assertEquals("服务端非空值仍为权威", "储蓄卡", repository.getAccount("a1")!!.accountType)
    }

    @Test
    fun accountPull_serverZeroBalance_overwritesLocal() = runBlocking {
        // 刻意没有本地保护（决策 #4）：0 是合法的服务端值，保护它就永远吃不下云端清零。
        repository.insertAccount(account(balance = 500_000L), "m1")
        clearMutations()
        repository.applyServerChanges(
            listOf(accountChange("a1", 2, mapOf("balanceCents" to "0"))),
            newCursor = 0, ownerId = "u1",
        )
        assertEquals(0L, repository.getAccount("a1")!!.balanceCents)
    }

    @Test
    fun accountPull_legacyServerWithoutBalanceKey_fallsBackToZero() = runBlocking {
        // 旧服务端不返回 balance_cents → 按 0 回落，不炸也不留脏值
        repository.insertAccount(account(), "m1")
        clearMutations()
        repository.applyServerChanges(
            listOf(accountChange("a1", 2, emptyMap())),
            newCursor = 0, ownerId = "u1",
        )
        assertEquals(0L, repository.getAccount("a1")!!.balanceCents)
    }

    // ─── bootstrap 重建（serverChangeMapToAccount 口） ───

    @Test
    fun bootstrap_rebuildsTypeAndBalance() = runBlocking {
        val response = BootstrapResponse(
            books = emptyList(), categories = emptyList(),
            accounts = listOf(
                mapOf(
                    "id" to "a1", "name" to "交行卡", "icon" to "💰",
                    "accountType" to "储蓄卡", "balanceCents" to "1234567",
                    "version" to "4",
                )
            ),
            transactions = emptyList(), budgets = emptyList(),
            cursor = 7, checksum = "x",
        )
        repository.clearAllEntities()
        repository.applyBootstrap(response, ownerId = "u1")

        val a = repository.getAccount("a1")!!
        assertEquals("储蓄卡", a.accountType)
        assertEquals(1_234_567L, a.balanceCents)
    }

    // ─── Room 10 → 11 迁移 ───

    /**
     * 迁移 10→11：accounts 加 balanceCents 列，存量行取 0，其余数据保留。
     *
     * 手法同 9→10（见 RepositoryUserPreferenceSyncTest）：先用当前版本建库，
     * 再手工退回 v10 形态 —— 删掉新列、把 room_master_table 的 identity_hash
     * 改回 10.json 的值、user_version 置 10。这样重开时 Room 会真实走
     * MIGRATION_10_11，并按 v11 生成代码校验 schema。
     */
    @Test
    fun migration10To11_addsBalanceColumnAndKeepsData() = runBlocking {
        val ctx = RuntimeEnvironment.getApplication()
        val name = "test-migration-10-11.db"
        ctx.deleteDatabase(name)
        val fresh = AozoraDatabase.open(ctx, name)
        try {
            fresh.accountDao().insert(
                AccountEntity(
                    id = "a-old", ownerId = "u1", name = "老账户", icon = "💰",
                    accountType = "储蓄卡", balanceCents = 0L, archivedAt = null, version = 1,
                )
            )
        } finally {
            fresh.close()
        }
        val path = ctx.getDatabasePath(name).path
        SQLiteDatabase.openDatabase(path, null, SQLiteDatabase.OPEN_READWRITE).use { raw ->
            // SQLite 3.35+ 支持 DROP COLUMN；Robolectric 的 SQLite 版本更高，
            // 但为稳妥改走「重建表」——v10 形态即 accounts 无 balanceCents 列。
            raw.execSQL(
                "CREATE TABLE `accounts_v10` (" +
                    "`id` TEXT NOT NULL, `ownerId` TEXT NOT NULL, `name` TEXT NOT NULL, " +
                    "`icon` TEXT NOT NULL, `accountType` TEXT, `archivedAt` INTEGER, " +
                    "`version` INTEGER NOT NULL, PRIMARY KEY(`id`))"
            )
            raw.execSQL(
                "INSERT INTO `accounts_v10` " +
                    "SELECT `id`, `ownerId`, `name`, `icon`, `accountType`, `archivedAt`, `version` " +
                    "FROM `accounts`"
            )
            raw.execSQL("DROP TABLE `accounts`")
            raw.execSQL("ALTER TABLE `accounts_v10` RENAME TO `accounts`")
            raw.execSQL(
                "UPDATE room_master_table SET identity_hash = ?",
                arrayOf(V10_IDENTITY_HASH)
            )
            raw.version = 10
        }

        val migrated = AozoraDatabase.open(ctx, name)
        try {
            // 列真的加上了（否则 DAO 读取会 "no such column"）
            val a = migrated.accountDao().getById("a-old")!!
            assertEquals("存量行取默认 0", 0L, a.balanceCents)
            // 存量字段跨迁移保留（accountType 早在 v4 就存在）
            assertEquals("储蓄卡", a.accountType)
            assertEquals("老账户", a.name)
            // 新写入可存负值
            migrated.accountDao().upsertFromSync(
                a.copy(balanceCents = -1_500L)
            )
            assertEquals(-1_500L, migrated.accountDao().getById("a-old")!!.balanceCents)
        } finally {
            migrated.close()
            ctx.deleteDatabase(name)
        }
    }

    private companion object {
        /** app/schemas/com.aozora.app.db.AozoraDatabase/10.json 的 identityHash。 */
        const val V10_IDENTITY_HASH = "fed669c6e10fe1f8b769987b428add64"
    }
}