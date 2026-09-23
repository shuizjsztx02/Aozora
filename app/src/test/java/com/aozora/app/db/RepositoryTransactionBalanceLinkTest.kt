package com.aozora.app.db

import com.aozora.app.db.entity.AccountEntity
import com.aozora.app.db.entity.PendingMutationEntity
import com.aozora.app.db.entity.TransactionEntity
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
 * 第十八轮 REQ-70（T18-7）：记账联动账户余额。
 *
 * 断言的是 REQ-70 的**五条真实不变量**（不是实现细节）：
 * 1. 记一笔后余额变化**恰为 ±该笔金额**（收入为正、支出为负）；
 * 2. 编辑金额后变化 = 新旧差额；
 * 3. 换账户后**旧账户回补、新账户加计**；
 * 4. 软删后回补；
 * 5. **mutation 重放不重复累加** —— 入队的是账户余额**绝对值**，且 expectedVersion
 *    逐条递增（否则 push 会被服务端的乐观锁挡下）。
 *
 * 另加两条路径覆盖：
 * - **聊天卡替换写**（softDelete 旧 + insert 新）净效果为零 —— 它走的就是本文件的
 *   两个方法，是 REQ-70 里「最容易漏」的那条路径；
 * - 联动只在**同一事务**内发生：主操作失败时不得留下半截余额改动。
 *
 * 符号约定是跨端契约（服务端 `transactions/service.py::balance_delta` 同式），
 * 两端各有用例钉住。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class RepositoryTransactionBalanceLinkTest {

    private lateinit var db: AozoraDatabase
    private lateinit var repository: Repository

    private lateinit var bookId: String
    private lateinit var categoryId: String

    @Before
    fun setUp() {
        db = AozoraDatabase.createInMemory(RuntimeEnvironment.getApplication())
        repository = Repository(db)
        runBlocking {
            repository.ensureSeedDefaults()
            val masterId = repository.getMasterBookId()
            bookId = repository.listBooks().first { it.id != masterId }.id
            categoryId = repository.listCategories().first { it.kind == "expense" }.id
        }
    }

    @After
    fun tearDown() { db.close() }

    // ─── 脚手架 ───

    private suspend fun seedAccount(id: String, balance: Long = 0L) {
        repository.insertAccount(
            AccountEntity(
                id = id, ownerId = "u1", name = "账户-$id", icon = "💰",
                accountType = null, balanceCents = balance, archivedAt = null, version = 1,
            ),
            "m-seed-$id",
        )
    }

    private fun tx(
        id: String,
        version: Int = 1,
        type: String = "expense",
        amountCents: Long = 2_500L,
        accountId: String? = null,
    ) = TransactionEntity(
        id = id, bookId = bookId, type = type, amountCents = amountCents,
        categoryId = categoryId, accountId = accountId, note = "午饭",
        bizDate = "2026-09-23", occurredAt = 1788845613031L, source = "manual",
        includeInBudget = true, version = version, deletedAt = null,
    )

    private suspend fun balanceOf(id: String): Long = repository.getAccount(id)!!.balanceCents

    /** 记账联动**入队**的 account mutation（按入队顺序）。建账户时那条 INSERT 不算。 */
    private suspend fun accountMutations(): List<PendingMutationEntity> =
        repository.listPendingMutations()
            .filter { it.entityType == "account" && it.operation == PendingMutationEntity.OP_UPDATE }

    /** 读某条 mutation 的 payload —— 联动写的是**绝对值**，这里正是要盯住的东西。 */
    private fun balanceIn(mutation: PendingMutationEntity): Long =
        JSONObject(mutation.payload).getLong("balanceCents")

    // ─── 不变量 1：记一笔后余额变化恰为 ±该笔金额 ───

    @Test
    fun expenseInsert_decreasesBalanceByExactlyAmount() = runBlocking {
        seedAccount("a1", balance = 100_000L)
        repository.insertTransaction(tx("t1", amountCents = 2_500L, accountId = "a1"), "m1")
        assertEquals(97_500L, balanceOf("a1"))
    }

    @Test
    fun incomeInsert_increasesBalanceByExactlyAmount() = runBlocking {
        seedAccount("a1", balance = 100_000L)
        repository.insertTransaction(
            tx("t1", type = "income", amountCents = 2_500L, accountId = "a1"), "m1",
        )
        assertEquals(102_500L, balanceOf("a1"))
    }

    @Test
    fun insertWithoutAccount_leavesEveryAccountUntouched() = runBlocking {
        seedAccount("a1", balance = 100_000L)
        repository.insertTransaction(tx("t1", accountId = null), "m1")
        assertEquals(100_000L, balanceOf("a1"))
        assertTrue(accountMutations().isEmpty())
    }

    /** 负余额是常态（信用卡欠款）：余额可以跌破 0，不设下限。 */
    @Test
    fun expenseInsert_mayDriveBalanceNegative() = runBlocking {
        seedAccount("a1", balance = 0L)
        repository.insertTransaction(tx("t1", amountCents = 2_500L, accountId = "a1"), "m1")
        assertEquals(-2_500L, balanceOf("a1"))
    }

    // ─── 不变量 2：编辑后变化 = 新旧差额 ───

    @Test
    fun updateAmount_adjustsByNewMinusOld() = runBlocking {
        seedAccount("a1", balance = 100_000L)
        repository.insertTransaction(tx("t1", amountCents = 2_500L, accountId = "a1"), "m1")
        repository.updateTransaction(
            tx("t1", version = 2, amountCents = 4_000L, accountId = "a1"), "m2",
        )
        assertEquals(96_000L, balanceOf("a1"))
    }

    /** 支出改收入：差额是**两倍**金额（旧值回补 + 新值加计都发生在同一账户上）。 */
    @Test
    fun updateType_expenseToIncome_flipsInOneStep() = runBlocking {
        seedAccount("a1", balance = 100_000L)
        repository.insertTransaction(tx("t1", amountCents = 2_500L, accountId = "a1"), "m1")
        repository.updateTransaction(
            tx("t1", version = 2, type = "income", amountCents = 2_500L, accountId = "a1"), "m2",
        )
        assertEquals(102_500L, balanceOf("a1"))
    }

    /** 只改备注（金额 / 类型 / 账户都没动）→ 余额零变化。 */
    @Test
    fun updateNonAmountFields_leavesBalanceUnchanged() = runBlocking {
        seedAccount("a1", balance = 100_000L)
        repository.insertTransaction(tx("t1", amountCents = 2_500L, accountId = "a1"), "m1")
        repository.updateTransaction(
            tx("t1", version = 2, amountCents = 2_500L, accountId = "a1").copy(note = "晚饭"),
            "m2",
        )
        assertEquals(97_500L, balanceOf("a1"))
    }

    // ─── 不变量 3：换账户后旧回补、新加计 ───

    @Test
    fun updateSwitchingAccount_refundsOldAndChargesNew() = runBlocking {
        seedAccount("a1", balance = 100_000L)
        seedAccount("a2", balance = 50_000L)
        repository.insertTransaction(tx("t1", amountCents = 2_500L, accountId = "a1"), "m1")
        assertEquals(97_500L, balanceOf("a1"))

        repository.updateTransaction(
            tx("t1", version = 2, amountCents = 2_500L, accountId = "a2"), "m2",
        )
        assertEquals(100_000L, balanceOf("a1"))
        assertEquals(47_500L, balanceOf("a2"))
    }

    /** 从「无账户」改到「有账户」→ 只加计新账户，不碰任何旧账户。 */
    @Test
    fun updateFromNoAccountToAccount_chargesOnlyNewOne() = runBlocking {
        seedAccount("a1", balance = 100_000L)
        repository.insertTransaction(tx("t1", amountCents = 2_500L, accountId = null), "m1")
        assertEquals(100_000L, balanceOf("a1"))

        repository.updateTransaction(
            tx("t1", version = 2, amountCents = 2_500L, accountId = "a1"), "m2",
        )
        assertEquals(97_500L, balanceOf("a1"))
    }

    // ─── 不变量 4：软删后回补 ───

    @Test
    fun softDelete_refundsAccount() = runBlocking {
        seedAccount("a1", balance = 100_000L)
        repository.insertTransaction(tx("t1", amountCents = 2_500L, accountId = "a1"), "m1")
        assertEquals(97_500L, balanceOf("a1")) // 先钉住方向：软删用例自身是自反的，不钉中间态就测不出符号错
        repository.softDeleteTransaction("t1", "m2")
        assertEquals(100_000L, balanceOf("a1"))
    }

    @Test
    fun softDeleteIncome_subtractsBack() = runBlocking {
        seedAccount("a1", balance = 100_000L)
        repository.insertTransaction(
            tx("t1", type = "income", amountCents = 2_500L, accountId = "a1"), "m1",
        )
        assertEquals(102_500L, balanceOf("a1"))
        repository.softDeleteTransaction("t1", "m2")
        assertEquals(100_000L, balanceOf("a1"))
    }

    /** 软删一笔编辑过的流水 → 回补的是**当前**金额（不是原始金额）。 */
    @Test
    fun softDeleteAfterEdit_refundsCurrentAmount() = runBlocking {
        seedAccount("a1", balance = 100_000L)
        repository.insertTransaction(tx("t1", amountCents = 2_500L, accountId = "a1"), "m1")
        repository.updateTransaction(
            tx("t1", version = 2, amountCents = 4_000L, accountId = "a1"), "m2",
        )
        assertEquals(96_000L, balanceOf("a1"))
        repository.softDeleteTransaction("t1", "m3")
        assertEquals(100_000L, balanceOf("a1"))
    }

    // ─── 不变量 5：mutation 重放不重复累加 ───

    /**
     * 入队的是**绝对值**：两条 mutation 的 payload 分别是 97_500 / 96_500，
     * 而不是 -2_500 / -1_000。这是重放安全的全部依据 —— 服务端把同一个值再写一次
     * 不产生任何累积。
     */
    @Test
    fun queuedAccountMutations_carryAbsoluteBalanceNotDelta() = runBlocking {
        seedAccount("a1", balance = 100_000L)
        repository.insertTransaction(tx("t1", amountCents = 2_500L, accountId = "a1"), "m1")
        repository.insertTransaction(tx("t2", amountCents = 1_000L, accountId = "a1"), "m2")

        val mutations = accountMutations()
        assertEquals(listOf(97_500L, 96_500L), mutations.map { balanceIn(it) })
        assertEquals(96_500L, balanceOf("a1"))
    }

    /** 每条 mutation 都是 OP_UPDATE，且 expectedVersion 逐条递增（推送乐观锁的前提）。 */
    @Test
    fun queuedAccountMutations_chainExpectedVersions() = runBlocking {
        seedAccount("a1", balance = 100_000L) // version = 1
        repository.insertTransaction(tx("t1", amountCents = 2_500L, accountId = "a1"), "m1")
        repository.insertTransaction(tx("t2", amountCents = 1_000L, accountId = "a1"), "m2")

        val mutations = accountMutations()
        assertEquals(
            listOf(PendingMutationEntity.OP_UPDATE, PendingMutationEntity.OP_UPDATE),
            mutations.map { it.operation },
        )
        assertEquals(listOf(1, 2), mutations.map { it.expectedVersion })
        // 入队 payload 里的 version 与本地行一致：push 通过后两端停在同一版本
        assertEquals(
            repository.getAccount("a1")!!.version,
            JSONObject(mutations.last().payload).getInt("version"),
        )
    }

    /** 换账户时两条 mutation 指向两个不同账户，各自带自己的绝对值。 */
    @Test
    fun switchingAccount_queuesOneMutationPerAccount() = runBlocking {
        seedAccount("a1", balance = 100_000L)
        seedAccount("a2", balance = 50_000L)
        repository.insertTransaction(tx("t1", amountCents = 2_500L, accountId = "a1"), "m1")
        repository.updateTransaction(
            tx("t1", version = 2, amountCents = 2_500L, accountId = "a2"), "m2",
        )

        val byAccount = accountMutations().groupBy { it.entityId }
        assertEquals(100_000L, balanceIn(byAccount.getValue("a1").last()))
        assertEquals(47_500L, balanceIn(byAccount.getValue("a2").last()))
    }

    // ─── 路径覆盖：聊天卡替换写（softDelete 旧 + insert 新） ───

    /**
     * REQ-70 点名「最容易漏」的那条路径：聊天卡编辑 = softDelete 旧 + insert 新。
     * 它走的就是前面两个方法，因此**净效果必须为零**（金额不变时）。
     */
    @Test
    fun replaceWritePattern_isBalanceNeutralWhenAmountUnchanged() = runBlocking {
        seedAccount("a1", balance = 100_000L)
        repository.insertTransaction(tx("t1", amountCents = 2_500L, accountId = "a1"), "m1")
        assertEquals(97_500L, balanceOf("a1"))

        repository.softDeleteTransaction("t1", "m2")
        repository.insertTransaction(tx("t2", amountCents = 2_500L, accountId = "a1"), "m3")
        assertEquals(97_500L, balanceOf("a1"))
    }

    /** 替换写顺带改金额 / 换账户时，同样只留新版那一份的效果。 */
    @Test
    fun replaceWritePattern_appliesOnlyTheNewVersion() = runBlocking {
        seedAccount("a1", balance = 100_000L)
        seedAccount("a2", balance = 50_000L)
        repository.insertTransaction(tx("t1", amountCents = 2_500L, accountId = "a1"), "m1")

        repository.softDeleteTransaction("t1", "m2")
        repository.insertTransaction(tx("t2", amountCents = 4_000L, accountId = "a2"), "m3")

        assertEquals(100_000L, balanceOf("a1"))
        assertEquals(46_000L, balanceOf("a2"))
    }

    // ─── 事务边界：主操作失败不留半截 ───

    /** 乐观锁挡住（`version` 必须严格递增，传当前版本即被拒）→ 行不动、余额不动、不入队。 */
    @Test
    fun versionConflict_leavesBalanceAndQueueUntouched() = runBlocking {
        seedAccount("a1", balance = 100_000L)
        repository.insertTransaction(tx("t1", amountCents = 2_500L, accountId = "a1"), "m1")
        val before = accountMutations().size

        val failed = runCatching {
            // t1 当前版本是 1，传 1 不满足「必须严格递增」
            repository.updateTransaction(
                tx("t1", version = 1, amountCents = 4_000L, accountId = "a1"), "m-bad",
            )
        }.isFailure

        assertTrue(failed)
        assertEquals(97_500L, balanceOf("a1"))
        assertEquals(before, accountMutations().size)
    }

    /** 交易不存在时软删失败 → 余额不动。 */
    @Test
    fun softDeleteMissingTransaction_leavesBalanceUntouched() = runBlocking {
        seedAccount("a1", balance = 100_000L)
        val failed = runCatching {
            repository.softDeleteTransaction("nope", "m1")
        }.isFailure
        assertTrue(failed)
        assertEquals(100_000L, balanceOf("a1"))
    }

    /**
     * 事务边界：联动发生在交易落库**之后**，所以只有「联动已经发生、之后才失败」的
     * 场景才真正拷问它是否在同一事务内 —— 这里用一条预置的同 mutationId 记录触发
     * pending_mutations 主键冲突（mutationId 是该表主键），让主操作的最后一步失败。
     */
    @Test
    fun failureAfterBalanceLink_rollsBackBalanceToo() = runBlocking {
        seedAccount("a1", balance = 100_000L)
        db.pendingMutationDao().insert(
            PendingMutationEntity(
                mutationId = "dup", entityType = "account", entityId = "a1",
                operation = PendingMutationEntity.OP_UPDATE, payload = "{}",
                status = PendingMutationEntity.STATUS_PENDING, retryCount = 0,
                createdAt = 0L, expectedVersion = null, lastError = null,
            )
        )

        val failed = runCatching {
            repository.insertTransaction(tx("t1", amountCents = 2_500L, accountId = "a1"), "dup")
        }.isFailure

        assertTrue(failed)
        assertNull(repository.getTransaction("t1"))
        assertEquals(100_000L, balanceOf("a1"))
    }
}