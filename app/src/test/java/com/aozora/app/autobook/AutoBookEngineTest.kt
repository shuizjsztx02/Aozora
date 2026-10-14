package com.aozora.app.autobook

import com.aozora.app.db.AozoraDatabase
import com.aozora.app.db.Repository
import com.aozora.app.db.entity.AutoCaptureEntity
import com.aozora.app.db.entity.CategoryEntity
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * 第二十一轮 REQ-83（T21-6）：解析与编排引擎的 JVM 测试。
 *
 * 载具：内存 Room（`createInMemory`）+ 一个 **fake 上行通道**（T21-6 卡面：
 * 「断网→联网全链路（载体可用 fake API）」）。★ **本类不验真实模型输出**——
 * 服务端的判据先行与形状消毒归 T21-2，两条通道的现场投递归真机/模拟器（决策 #19）。
 * 这里钉的是编排的**分支与状态机**：谁入账、谁进待确认箱、谁留待解析、谁被合并。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class AutoBookEngineTest {

    private lateinit var db: AozoraDatabase
    private lateinit var repository: Repository
    private lateinit var captures: AutoBookRepository

    /** 同一个分钟桶内的时刻：L2 判据是分钟级时间桶，同桶才算"同笔"。 */
    private val bucket = DedupeKeys.bucketStart(1_700_000_040_000L)

    @Before
    fun setUp() {
        db = AozoraDatabase.createInMemory(RuntimeEnvironment.getApplication())
        repository = Repository(db)
        captures = AutoBookRepository(db)
        runBlocking {
            repository.ensureSeedDefaults()
            captures.setEnabled(true)
        }
    }

    @After
    fun tearDown() {
        db.close()
    }

    // ─── 载具 ───

    private fun engine(remote: AutoBookEngine.CaptureRemoteParser? = null) =
        AutoBookEngine(db = db, repository = repository, remote = remote)

    /** 按需应答的 fake 上行通道；[fail] = true 时一律抛异常（模拟断网 / 服务端故障）。 */
    private class FakeRemote(
        private val fail: Boolean = false,
        private val answer: (String) -> LocalCaptureParser.Draft,
    ) : AutoBookEngine.CaptureRemoteParser {
        var calls = 0
            private set

        override suspend fun parse(
            text: String,
            sourceChannel: String,
            contextJson: String?,
        ): LocalCaptureParser.Draft {
            calls++
            if (fail) throw IllegalStateException("network down")
            return answer(text)
        }
    }

    private fun remoteDraft(
        type: String = LocalCaptureParser.TYPE_EXPENSE,
        amountCents: Long = 3200L,
        category: String = "餐饮",
        note: String = "",
        confidence: Double = 0.95,
        kind: String = LocalCaptureParser.KIND_TRANSACTION,
        reason: String = LocalCaptureParser.REASON_NONE,
    ) = LocalCaptureParser.Draft(
        kind = kind,
        reason = reason,
        type = if (kind == LocalCaptureParser.KIND_TRANSACTION) type else "",
        amountCents = if (kind == LocalCaptureParser.KIND_TRANSACTION) amountCents else 0L,
        categoryHint = if (kind == LocalCaptureParser.KIND_TRANSACTION) category else "",
        note = note,
        confidence = confidence,
    )

    private suspend fun capture(
        text: String,
        channel: String = AutoCaptureEntity.CHANNEL_NOTIFICATION,
        pkg: String = "com.tencent.mm",
        event: String = "e-1",
        at: Long = bucket + 1_000L,
    ): String {
        val outcome = captures.recordCapture(
            AutoBookRepository.CaptureDraft(
                sourceChannel = channel,
                sourcePackage = pkg,
                sourceApp = null,
                rawText = text,
                postedAt = at,
                eventIdentity = event,
            )
        )
        assertTrue("expected Stored, got $outcome", outcome is AutoBookRepository.CaptureOutcome.Stored)
        return (outcome as AutoBookRepository.CaptureOutcome.Stored).captureId
    }

    private suspend fun row(id: String) = db.autoCaptureDao().getById(id)!!

    /** 条目数：`countActive` 只数未软删的流水，正是"写了几笔账"的判据。 */
    private suspend fun txCount() = db.transactionDao().countActive()

    private fun categoryOf(txId: String): CategoryEntity = runBlocking {
        val tx = db.transactionDao().getById(txId)!!
        db.categoryDao().getById(tx.categoryId)!!
    }

    // ─── 本地规则命中：离线也能入账 ───

    @Test
    fun localRuleHit_commitsOffline_withoutAnyNetwork() = runBlocking {
        // 引擎**没有**上行通道（等价于断网）；本地规则认得出的那一笔照样落账——
        // 这是 PR-3「离线核心记账可用」与 T21-6 卡面「本地规则命中的一笔离线也能入账」的兑现
        val id = capture("支付成功\n¥32.00\n星巴克(北京国贸店)\n零钱")
        val outcome = engine(remote = null).processCapture(id, allowRemote = false)

        assertTrue("expected Committed, got $outcome", outcome is AutoBookEngine.Outcome.Committed)
        val stored = row(id)
        assertEquals(AutoCaptureEntity.STATUS_COMMITTED, stored.status)
        assertEquals((outcome as AutoBookEngine.Outcome.Committed).transactionId, stored.transactionId)
        assertEquals("星巴克(北京国贸店)", stored.merchantKey)

        val tx = db.transactionDao().getById(stored.transactionId!!)!!
        assertEquals("expense", tx.type)
        assertEquals(3200L, tx.amountCents)
        assertEquals("nlu_local", tx.source)
        assertEquals("星巴克(北京国贸店)", tx.note)
        assertEquals("餐饮", categoryOf(tx.id).name)
        assertEquals("业务日期用捕获时刻折算，不是解析时刻", "2023-11-15", tx.bizDate)

        // ★ 写库契约：自动入账也必须与 pending_mutations 同事务（决策 #16）
        assertEquals(1, db.pendingMutationDao().listAll().count { it.entityType == "transaction" })
    }

    @Test
    fun localRuleMiss_staysCapturedOffline_andIsNeverLost() = runBlocking {
        val id = capture("云闪付\n消费人民币32.00元")   // 本地规则认得金额与方向，但没有分类提示
        val outcome = engine(remote = null).processCapture(id, allowRemote = false)

        assertEquals(AutoBookEngine.Outcome.Deferred, outcome)
        assertEquals("未定论的行留在 captured，等在线档", AutoCaptureEntity.STATUS_CAPTURED, row(id).status)
        assertTrue(txCount() == 0)
    }

    // ─── 上行：服务端定的高置信 / 低置信 ───

    @Test
    fun onlineHighConfidence_commitsThroughFastLane() = runBlocking {
        val id = capture("云闪付\n消费人民币32.00元")
        val remote = FakeRemote { remoteDraft(note = "星巴克") }

        val outcome = engine(remote).processCapture(id, allowRemote = true)

        assertTrue("expected Committed, got $outcome", outcome is AutoBookEngine.Outcome.Committed)
        assertEquals(1, remote.calls)
        val tx = db.transactionDao().getById(row(id).transactionId!!)!!
        assertEquals("nlu_ai", tx.source)
        assertEquals("星巴克", tx.note)
    }

    @Test
    fun onlineLowConfidence_goesToPendingBox_withoutWritingAnything() = runBlocking {
        val id = capture("云闪付\n消费人民币32.00元")
        val remote = FakeRemote { remoteDraft(confidence = 0.5, note = "") }

        val outcome = engine(remote).processCapture(id, allowRemote = true)

        assertEquals(AutoBookEngine.Outcome.PendingConfirm, outcome)
        val stored = row(id)
        assertEquals(AutoCaptureEntity.STATUS_PENDING_CONFIRM, stored.status)
        assertNull(stored.transactionId)
        assertTrue("低置信绝不落账（PR-2：永不产生用户不知情的账目）", txCount() == 0)
        assertEquals(1, db.autoCaptureDao().countByStatus(AutoCaptureEntity.STATUS_PENDING_CONFIRM))
    }

    @Test
    fun onlineCategoryNotInLocalTree_goesToPendingBox() = runBlocking {
        // 分类名必须对得上本地真实分类；对不上就不许自动入账（宁低置信，不猜字段）
        val id = capture("云闪付\n消费人民币32.00元")
        val remote = FakeRemote { remoteDraft(category = "星际旅行") }

        assertEquals(AutoBookEngine.Outcome.PendingConfirm, engine(remote).processCapture(id, allowRemote = true))
        assertEquals(AutoCaptureEntity.STATUS_PENDING_CONFIRM, row(id).status)
    }

    @Test
    fun remoteFailure_keepsTheRowCaptured_neverDropsIt() = runBlocking {
        // ★ 决策 #14：断网 / 服务端故障**不得静默丢**。行留在 captured，只记一个原因码。
        val id = capture("云闪付\n消费人民币32.00元")
        val outcome = engine(FakeRemote(fail = true) { remoteDraft() }).processCapture(id, allowRemote = true)

        assertEquals(AutoBookEngine.Outcome.RemoteFailed, outcome)
        val stored = row(id)
        assertEquals(AutoCaptureEntity.STATUS_CAPTURED, stored.status)
        assertEquals("remote_failed", stored.error)
        assertTrue("原文必须留着，下次再试", stored.rawText.isNotEmpty())
        assertTrue(txCount() == 0)
    }

    @Test
    fun remoteNotTransaction_isNotRecordedAsAnEntry() = runBlocking {
        // 四类「不是消费」若由服务端判出，同样不落账：reason 留在 parsedJson 里可反查
        val id = capture("云闪付\n消费人民币32.00元")
        val remote = FakeRemote {
            remoteDraft(kind = LocalCaptureParser.KIND_NOT_TRANSACTION, reason = LocalCaptureParser.REASON_REFUND)
        }

        assertEquals(AutoBookEngine.Outcome.NotTransaction, engine(remote).processCapture(id, allowRemote = true))
        val stored = row(id)
        assertEquals(AutoCaptureEntity.STATUS_IGNORED, stored.status)
        assertEquals(LocalCaptureParser.REASON_REFUND, JSONObject(stored.parsedJson!!).getString("reason"))
        assertTrue(txCount() == 0)
    }

    // ─── 本地规则判出的四类「不是消费」：不上行，直接出局 ───

    @Test
    fun localNotTransaction_ignoresWithoutCallingTheServer() = runBlocking {
        val id = capture("[微信支付] 退款到账通知\n退款金额 ¥32.00\n已原路退回你的零钱")
        val remote = FakeRemote { remoteDraft() }

        val outcome = engine(remote).processCapture(id, allowRemote = true)

        assertEquals(AutoBookEngine.Outcome.NotTransaction, outcome)
        assertEquals("四类反例文案有强特征，本地判出就不必再花一次调用", 0, remote.calls)
        val stored = row(id)
        assertEquals(AutoCaptureEntity.STATUS_IGNORED, stored.status)
        assertEquals(LocalCaptureParser.REASON_REFUND, JSONObject(stored.parsedJson!!).getString("reason"))
        assertTrue(txCount() == 0)
    }

    // ─── L2：跨通道同笔（决策 #5 / 决策 #19 ④⑤）───

    @Test
    fun sameTransactionFromBothChannels_mergesIntoOne_andKeepsTheRicherMerchant() = runBlocking {
        // 无障碍（带商户名）比通知晚 1 秒到 —— 编排按 postedAt 倒序处理，故无障碍先定论
        val notif = capture(
            text = "微信支付 已支付¥32.00",
            channel = AutoCaptureEntity.CHANNEL_NOTIFICATION,
            event = "n1",
            at = bucket + 1_000L,
        )
        val acc = capture(
            text = "支付成功\n¥32.00\n星巴克(北京国贸店)\n零钱",
            channel = AutoCaptureEntity.CHANNEL_ACCESSIBILITY,
            pkg = "com.tencent.mm",
            event = "w1",
            at = bucket + 2_000L,
        )
        val remote = FakeRemote { remoteDraft(category = "餐饮", note = "") }

        // 一轮编排把两条都定论
        engine(remote).processPendingCaptures(allowRemote = true)

        assertEquals("合并只解决不重复入账：同一笔只写一笔", 1, txCount())
        assertEquals(1, db.autoCaptureDao().countByStatus(AutoCaptureEntity.STATUS_COMMITTED))

        val kept = row(acc)
        assertNull("无障碍信息更全 → 它是保留行", kept.mergedInto)
        assertEquals("商户名必须落在保留行上", "星巴克(北京国贸店)", kept.merchantKey)
        assertEquals("星巴克(北京国贸店)", db.transactionDao().getById(kept.transactionId!!)!!.note)

        assertEquals("另一条通道的行留痕、不留半成品账目", acc, row(notif).mergedInto)
        assertEquals(AutoCaptureEntity.STATUS_IGNORED, row(notif).status)
        assertEquals("来源数由 mergedInto 派生", 1, db.autoCaptureDao().countMergedSources(acc))
        assertEquals("两条通道各自都留了行", 2, db.autoCaptureDao().countCapturedSince(0L))
    }

    @Test
    fun notificationProcessedFirst_defersToTheRicherTwin_beforeWritingAnything() = runBlocking {
        // 反向的顺序：通知那一刻更晚（先被编排）——它必须**让位**，否则保留位被占住，
        // 后来那条带商户名的无障碍行就只能当留痕，"保留商户名"落空
        val notif = capture(
            text = "微信支付 已支付¥32.00",
            channel = AutoCaptureEntity.CHANNEL_NOTIFICATION,
            event = "n1",
            at = bucket + 2_000L,
        )
        val acc = capture(
            text = "支付成功\n¥32.00\n星巴克(北京国贸店)\n零钱",
            channel = AutoCaptureEntity.CHANNEL_ACCESSIBILITY,
            event = "w1",
            at = bucket + 1_000L,
        )
        val subject = engine(FakeRemote { remoteDraft(category = "餐饮", note = "") })

        assertEquals(
            "优先级低的那条让位给还没解析的无障碍孪生行",
            AutoBookEngine.Outcome.Deferred,
            subject.processCapture(notif, allowRemote = true),
        )
        assertTrue(txCount() == 0)

        assertEquals(1, subject.processPendingCaptures(allowRemote = true))   // 无障碍那一条落账
        assertEquals(1, txCount())
        assertEquals("星巴克(北京国贸店)", db.transactionDao().getById(row(acc).transactionId!!)!!.note)

        // 下一轮：让位的那条并到保留行上（不写第二笔）
        subject.processCapture(notif, allowRemote = true)
        assertEquals(1, txCount())
        assertEquals(acc, row(notif).mergedInto)
    }

    @Test
    fun committedRowIsNeverMergedAway_soOnePaymentNeverWritesTwoEntries() = runBlocking {
        // ★ 危险路径的守护：通知那一条**已经落账**之后，带商户名的无障碍行才进表。
        // 通道优先级只适用于"两边都还没落账"的情形——若按优先级把保留位挪到无障碍行，
        // 结果是同一笔写两条分录。代价是这条账目的商户名留在留痕行上（见已知限制）。
        val notif = capture(
            text = "微信支付 已支付¥32.00",
            channel = AutoCaptureEntity.CHANNEL_NOTIFICATION,
            event = "n1",
            at = bucket + 1_000L,
        )
        val subject = engine(FakeRemote { remoteDraft(category = "餐饮", note = "") })
        subject.processCapture(notif, allowRemote = true)
        assertEquals(1, txCount())

        val acc = capture(
            text = "支付成功\n¥32.00\n星巴克(北京国贸店)\n零钱",
            channel = AutoCaptureEntity.CHANNEL_ACCESSIBILITY,
            event = "w1",
            at = bucket + 2_000L,
        )
        assertEquals(AutoBookEngine.Outcome.MergedAway, subject.processCapture(acc, allowRemote = true))

        assertEquals("同一笔账不能写两条分录", 1, txCount())
        assertNull("已落账的行不得被改成留痕", row(notif).mergedInto)
        assertEquals(notif, row(acc).mergedInto)
        assertEquals(AutoCaptureEntity.STATUS_IGNORED, row(acc).status)
    }

    @Test
    fun sameAmountDifferentMerchants_areNotMerged_L3() = runBlocking {
        // 同额消费是日常（两杯 18 元咖啡）——L3 只提示、不合并，否则会吃掉真实账目
        val first = capture(
            text = "支付成功\n¥32.00\n星巴克(北京国贸店)\n零钱",
            channel = AutoCaptureEntity.CHANNEL_ACCESSIBILITY,
            event = "w1",
            at = bucket + 1_000L,
        )
        val second = capture(
            text = "支付成功\n¥32.00\n麦当劳(国贸店)\n零钱",
            channel = AutoCaptureEntity.CHANNEL_NOTIFICATION,
            event = "n1",
            at = bucket + 2_000L,
        )
        val subject = engine(FakeRemote { remoteDraft() })

        subject.processCapture(first, allowRemote = true)
        subject.processCapture(second, allowRemote = true)

        assertEquals("两笔真实消费都必须留下", 2, txCount())
        assertNull(row(first).mergedInto)
        assertNull(row(second).mergedInto)
        assertEquals(
            "L3 的候选查询仍在（提示由待确认箱派生）",
            1,
            db.autoCaptureDao().listSameWindowOtherChannel(
                AutoCaptureEntity.CHANNEL_NOTIFICATION,
                DedupeKeys.bucketStart(bucket + 2_000L),
                DedupeKeys.bucketStart(bucket + 2_000L) + DedupeKeys.BUCKET_MS,
            ).size,
        )
    }

    // ─── 商户记忆（决策 #11：不开新表，回查同 merchantKey 的已 committed 记录）───

    @Test
    fun merchantMemory_suppliesTheCategoryWhenTheParserCouldNot() = runBlocking {
        // 第一笔：服务端给出了分类 → 落账，商户记忆由此写下"银河书店 → 购物"
        val first = capture("支付成功\n¥45.00\n银河书店\n零钱", event = "e-1", at = bucket + 1_000L)
        val subject = engine(FakeRemote { remoteDraft(amountCents = 4500L, category = "购物", note = "银河书店") })
        subject.processCapture(first, allowRemote = true)
        assertEquals(1, txCount())

        // 第二笔：同一商户、服务端这次没能给出分类（category 为空）——记忆补上它
        val second = capture("支付成功\n¥18.00\n银河书店\n零钱", event = "e-2", at = bucket + 90_000L)
        val outcome = engine(FakeRemote { remoteDraft(amountCents = 1800L, category = "", note = "银河书店") })
            .processCapture(second, allowRemote = true)

        assertTrue("商户记忆命中 → 补齐分类后应自动入账，got $outcome", outcome is AutoBookEngine.Outcome.Committed)
        val tx = db.transactionDao().getById(row(second).transactionId!!)!!
        assertEquals("购物", categoryOf(tx.id).name)
    }

    @Test
    fun withoutMerchantMemory_theSameInputGoesToPendingBox() = runBlocking {
        // 对照：没有记忆时，分类空 → 进待确认箱（证明上一条的效果确实来自记忆）
        val id = capture("支付成功\n¥18.00\n银河书店\n零钱")
        val outcome = engine(FakeRemote { remoteDraft(amountCents = 1800L, category = "", note = "银河书店") })
            .processCapture(id, allowRemote = true)

        assertEquals(AutoBookEngine.Outcome.PendingConfirm, outcome)
        assertTrue(txCount() == 0)
    }

    // ─── 编排入口：一轮推进 ───

    @Test
    fun processPendingCaptures_advancesEverythingItCan_andStopsOnRemoteFailure() = runBlocking {
        val local = capture("支付成功\n¥32.00\n星巴克(北京国贸店)\n零钱", event = "e-1", at = bucket + 1_000L)
        val unknown = capture("云闪付\n消费人民币9.90元", event = "e-2", at = bucket + 2_000L)

        // 离线档：本地认得的那条入账，另一条留在待解析
        val advanced = engine(remote = null).processPendingCaptures(allowRemote = false)
        assertEquals(1, advanced)
        assertEquals(AutoCaptureEntity.STATUS_COMMITTED, row(local).status)
        assertEquals(AutoCaptureEntity.STATUS_CAPTURED, row(unknown).status)

        // 在线档：剩下那条上行成功 → 入账（金额以服务端解析为准：9.90 元 = 990 分）
        val online = engine(FakeRemote { remoteDraft(amountCents = 990L, category = "餐饮", note = "云闪付") })
        assertEquals(1, online.processPendingCaptures(allowRemote = true))
        val stored = row(unknown)
        assertEquals(AutoCaptureEntity.STATUS_COMMITTED, stored.status)
        assertEquals(2, txCount())
        assertEquals(990L, db.transactionDao().getById(stored.transactionId!!)!!.amountCents)
    }

    @Test
    fun drainIsGatedByTheMasterSwitch() = runBlocking {
        val id = capture("支付成功\n¥32.00\n星巴克(北京国贸店)\n零钱")
        captures.setEnabled(false)

        assertEquals(0, engine(remote = null).processPendingCaptures(allowRemote = false))
        assertEquals(AutoCaptureEntity.STATUS_CAPTURED, row(id).status)
        assertTrue(txCount() == 0)
    }

    @Test
    fun alreadyResolvedRows_areNotReprocessed() = runBlocking {
        val id = capture("支付成功\n¥32.00\n星巴克(北京国贸店)\n零钱")
        val subject = engine(remote = null)

        subject.processCapture(id, allowRemote = false)
        val again = subject.processCapture(id, allowRemote = false)

        assertEquals(AutoBookEngine.Outcome.Skipped, again)
        assertEquals("幂等：一轮编排只落一笔账", 1, txCount())
    }

    @Test
    fun transactionAndCaptureStayConsistent() = runBlocking {
        val id = capture("支付成功\n¥32.00\n星巴克(北京国贸店)\n零钱")
        engine(remote = null).processCapture(id, allowRemote = false)

        val stored = row(id)
        val tx = db.transactionDao().getById(stored.transactionId!!)!!
        assertEquals(stored.postedAt, tx.occurredAt)
        assertEquals(stored.merchantKey, tx.note)
        assertNotEquals("捕获行与交易是两个 id（捕获行不是交易）", stored.captureId, tx.id)
        assertNotNull(tx.bookId)
    }
}