package com.aozora.app.autobook

import android.database.sqlite.SQLiteDatabase
import com.aozora.app.db.AozoraDatabase
import com.aozora.app.db.entity.AutoCaptureEntity
import com.aozora.app.db.entity.SyncMetaEntity
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import java.io.File

/**
 * 第二十一轮 REQ-83（T21-3）：自动记账写入路径的 JVM 测试。
 *
 * 覆盖卡片 ⑦ 要求的四件事：L1 去重只一行、L2 合并（含"商户名不同则不合并"的反例）、
 * 断网捕获必落行、迁移 11 → 12 可跑通。
 *
 * ★ **本类不验"通知投递"**：`NotificationListenerService` 的现场行为只能在真机 /
 * 模拟器上取证（决策 #19：**不得用 Robolectric 的"伪造通知投递"冒充真机验收**）。
 * 这里只测纯函数与 Room 层的确定性行为。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class AutoBookRepositoryTest {

    private lateinit var db: AozoraDatabase
    private lateinit var repository: AutoBookRepository

    @Before
    fun setUp() {
        db = AozoraDatabase.createInMemory(RuntimeEnvironment.getApplication())
        repository = AutoBookRepository(db)
    }

    @After
    fun tearDown() {
        db.close()
    }

    /** 同一个分钟桶内的两个时刻：L2 判据是"分钟级时间桶"，同桶才算同笔。 */
    private val bucket = DedupeKeys.bucketStart(1_700_000_040_000L)

    private fun draft(
        channel: String = AutoCaptureEntity.CHANNEL_NOTIFICATION,
        pkg: String = "com.tencent.mm",
        text: String = "微信支付 已支付¥32.00",
        at: Long = bucket + 1_000L,
        event: String = "0|com.tencent.mm|1|null|10086",
        merchantKey: String? = null,
    ) = AutoBookRepository.CaptureDraft(
        sourceChannel = channel,
        sourcePackage = pkg,
        sourceApp = null,
        rawText = text,
        postedAt = at,
        eventIdentity = event,
        merchantKey = merchantKey,
    )

    private suspend fun capture(draft: AutoBookRepository.CaptureDraft): String {
        val outcome = repository.recordCapture(draft)
        assertTrue("expected Stored, got $outcome", outcome is AutoBookRepository.CaptureOutcome.Stored)
        return (outcome as AutoBookRepository.CaptureOutcome.Stored).captureId
    }

    private suspend fun row(id: String) = db.autoCaptureDao().getById(id)!!

    // ─── 落库 ───

    @Test
    fun recordCapture_storesRowInCapturedStateWithoutEnqueueingMutation() = runBlocking {
        val id = capture(draft())

        val stored = row(id)
        assertEquals(AutoCaptureEntity.CHANNEL_NOTIFICATION, stored.sourceChannel)
        assertEquals("com.tencent.mm", stored.sourcePackage)
        assertEquals(AutoCaptureEntity.STATUS_CAPTURED, stored.status)
        assertNull("mergeKey 由解析侧回填，采集时不写", stored.mergeKey)
        assertNull(stored.mergedInto)
        assertNull(stored.parsedJson)
        assertNull(stored.transactionId)
        assertEquals(64, stored.dedupeKey.length)
        assertTrue(stored.dedupeKey.all { it in "0123456789abcdef" })

        // ★ 本表的写入是采集侧的本地记录，不入队 pending mutation（决策 #16 的边界）
        assertTrue(
            "auto_captures 的写入不得入队 pending mutation",
            db.pendingMutationDao().listAll().isEmpty()
        )
    }

    /** 决策 #14：断网不得静默丢失——捕获路径不碰网络，落本地与联网与否无关。 */
    @Test
    fun recordCapture_landsLocallyRegardlessOfNetwork() = runBlocking {
        // 测试环境本就没有网络栈可依赖；本用例钉的是"捕获路径里没有任何网络前置条件"
        val id = capture(draft(text = "支付宝 付款成功 ¥8.80", pkg = "com.eg.android.AlipayGphone"))
        assertEquals(AutoCaptureEntity.STATUS_CAPTURED, row(id).status)
        assertEquals(1, db.autoCaptureDao().countByStatus(AutoCaptureEntity.STATUS_CAPTURED))
    }

    @Test
    fun recordCapture_truncatesOversizedRawText() = runBlocking {
        val id = capture(draft(text = "消".repeat(AutoCaptureEntity.MAX_RAW_TEXT_LEN + 500)))
        assertEquals(AutoCaptureEntity.MAX_RAW_TEXT_LEN, row(id).rawText.length)
    }

    @Test
    fun recordCapture_rejectsUnknownChannelAndMissingIdentity() = runBlocking {
        listOf(
            draft(channel = "telepathy"),
            draft(event = " "),
            draft(pkg = ""),
        ).forEach { bad ->
            try {
                repository.recordCapture(bad)
                throw AssertionError("invalid draft accepted: $bad")
            } catch (_: IllegalArgumentException) {
            }
        }
        assertEquals(0, db.autoCaptureDao().countByStatus(AutoCaptureEntity.STATUS_CAPTURED))
    }

    // ─── L1：同通道同事件 ───

    @Test
    fun duplicateDelivery_producesExactlyOneRow() = runBlocking {
        val first = capture(draft())
        val second = repository.recordCapture(draft())

        assertTrue(second is AutoBookRepository.CaptureOutcome.DuplicateL1)
        assertEquals(
            "重复投递必须回指既有行，且不新增行",
            first,
            (second as AutoBookRepository.CaptureOutcome.DuplicateL1).existingCaptureId
        )
        assertEquals(1, db.autoCaptureDao().countByStatus(AutoCaptureEntity.STATUS_CAPTURED))
    }

    @Test
    fun differentEventOrText_isNotL1Duplicate() = runBlocking {
        capture(draft())
        capture(draft(event = "0|com.tencent.mm|2|null|10087"))
        capture(draft(text = "微信支付 已支付¥33.00"))
        assertEquals(3, db.autoCaptureDao().countByStatus(AutoCaptureEntity.STATUS_CAPTURED))
    }

    // ─── L2：跨通道同笔 ───

    @Test
    fun merge_keepsAccessibilityRowWhenItArrivesSecond() = runBlocking {
        val notif = capture(
            draft(text = "微信支付 已支付¥32.00", event = "n1")
        )
        val acc = capture(
            draft(
                channel = AutoCaptureEntity.CHANNEL_ACCESSIBILITY,
                text = "付款成功 星巴克 ¥32.00 订单号 202610130001",
                event = "w1",
                at = bucket + 2_000L,
            )
        )

        // 真实顺序：解析完成后逐行判定（先到的那条先回填 mergeKey）
        assertEquals(
            AutoBookRepository.MergeOutcome.NoCandidate,
            repository.mergeIfSameTransaction(notif, "expense", 3200L, null)
        )
        val outcome = repository.mergeIfSameTransaction(acc, "expense", 3200L, null)

        assertTrue("expected Merged, got $outcome", outcome is AutoBookRepository.MergeOutcome.Merged)
        val merged = outcome as AutoBookRepository.MergeOutcome.Merged
        assertEquals("无障碍信息更全，保留它", acc, merged.keptCaptureId)
        assertTrue(merged.keptIsThisRow)

        assertEquals("被合并行留痕、不删除", acc, row(notif).mergedInto)
        assertNull("保留行自身不被标记", row(acc).mergedInto)
        assertEquals(1, db.autoCaptureDao().countMergedSources(acc))
        assertEquals(2, db.autoCaptureDao().countByStatus(AutoCaptureEntity.STATUS_CAPTURED))
    }

    @Test
    fun merge_keepsEarlierAccessibilityRowWhenNotificationArrivesSecond() = runBlocking {
        val acc = capture(
            draft(
                channel = AutoCaptureEntity.CHANNEL_ACCESSIBILITY,
                text = "付款成功 星巴克 ¥32.00",
                event = "w1",
            )
        )
        val notif = capture(
            draft(text = "微信支付 已支付¥32.00", event = "n1", at = bucket + 2_000L)
        )

        assertEquals(
            AutoBookRepository.MergeOutcome.NoCandidate,
            repository.mergeIfSameTransaction(acc, "expense", 3200L, null)
        )
        val outcome = repository.mergeIfSameTransaction(notif, "expense", 3200L, null)

        assertTrue(outcome is AutoBookRepository.MergeOutcome.Merged)
        val merged = outcome as AutoBookRepository.MergeOutcome.Merged
        assertEquals("通道优先级 无障碍 > 通知", acc, merged.keptCaptureId)
        assertFalse(merged.keptIsThisRow)
        assertEquals(acc, row(notif).mergedInto)
    }

    /** ★ L3 的反例：商户名不同**不合并**——同额消费是日常（两杯 18 元咖啡）。 */
    @Test
    fun merge_isRefusedWhenMerchantsDiffer() = runBlocking {
        val notif = capture(draft(text = "微信支付 已支付¥32.00", event = "n1", merchantKey = "星巴克"))
        val acc = capture(
            draft(
                channel = AutoCaptureEntity.CHANNEL_ACCESSIBILITY,
                text = "付款成功 瑞幸 ¥32.00",
                event = "w1",
                at = bucket + 2_000L,
                merchantKey = "瑞幸",
            )
        )

        repository.mergeIfSameTransaction(notif, "expense", 3200L, "星巴克")
        val outcome = repository.mergeIfSameTransaction(acc, "expense", 3200L, "瑞幸")

        assertTrue("expected MerchantConflict, got $outcome", outcome is AutoBookRepository.MergeOutcome.MerchantConflict)
        assertEquals(listOf(notif), (outcome as AutoBookRepository.MergeOutcome.MerchantConflict).candidateIds)
        assertNull(row(notif).mergedInto)
        assertNull(row(acc).mergedInto)
        assertEquals(0, db.autoCaptureDao().countMergedSources(acc))
    }

    @Test
    fun merge_ignoresSameChannelRows() = runBlocking {
        val a = capture(draft(text = "微信支付 已支付¥32.00", event = "n1"))
        val b = capture(draft(text = "微信支付 已支付¥32.00（第二笔）", event = "n2", at = bucket + 2_000L))

        repository.mergeIfSameTransaction(a, "expense", 3200L, null)
        assertEquals(
            "L2 只管跨通道同笔；同通道同额不算",
            AutoBookRepository.MergeOutcome.NoCandidate,
            repository.mergeIfSameTransaction(b, "expense", 3200L, null)
        )
        assertNull(row(a).mergedInto)
    }

    @Test
    fun merge_ignoresRowsOutsideTheMinuteBucket() = runBlocking {
        val notif = capture(draft(text = "微信支付 已支付¥32.00", event = "n1"))
        val acc = capture(
            draft(
                channel = AutoCaptureEntity.CHANNEL_ACCESSIBILITY,
                text = "付款成功 ¥32.00",
                event = "w1",
                at = bucket + DedupeKeys.BUCKET_MS + 1_000L,
            )
        )

        repository.mergeIfSameTransaction(notif, "expense", 3200L, null)
        assertEquals(
            AutoBookRepository.MergeOutcome.NoCandidate,
            repository.mergeIfSameTransaction(acc, "expense", 3200L, null)
        )
    }

    @Test
    fun merge_directionAndAmountMustMatch() = runBlocking {
        val notif = capture(draft(text = "微信支付 已支付¥32.00", event = "n1"))
        val acc = capture(
            draft(
                channel = AutoCaptureEntity.CHANNEL_ACCESSIBILITY,
                text = "付款成功 ¥33.00",
                event = "w1",
                at = bucket + 2_000L,
            )
        )

        repository.mergeIfSameTransaction(notif, "expense", 3200L, null)
        assertEquals(
            AutoBookRepository.MergeOutcome.NoCandidate,
            repository.mergeIfSameTransaction(acc, "expense", 3300L, null)
        )
    }

    @Test
    fun merge_doesNotRewriteAnAlreadyMergedRow() = runBlocking {
        val notif = capture(draft(text = "微信支付 已支付¥32.00", event = "n1"))
        val acc = capture(
            draft(
                channel = AutoCaptureEntity.CHANNEL_ACCESSIBILITY,
                text = "付款成功 ¥32.00",
                event = "w1",
                at = bucket + 2_000L,
            )
        )
        repository.mergeIfSameTransaction(notif, "expense", 3200L, null)
        repository.mergeIfSameTransaction(acc, "expense", 3200L, null)

        assertEquals(
            "已被合并的行不再作为合并主体（留痕行不二次改写）",
            AutoBookRepository.MergeOutcome.NoCandidate,
            repository.mergeIfSameTransaction(notif, "expense", 3200L, null)
        )
        assertEquals(acc, row(notif).mergedInto)
    }

    @Test
    fun merge_requiresPositiveAmountAndKnownRow() = runBlocking {
        try {
            repository.mergeIfSameTransaction("ghost", "expense", 0L, null)
            throw AssertionError("non-positive amount accepted")
        } catch (_: IllegalArgumentException) {
        }
        assertEquals(
            AutoBookRepository.MergeOutcome.NoCandidate,
            repository.mergeIfSameTransaction("ghost", "expense", 3200L, null)
        )
    }

    /**
     * 合并留痕是**条件更新**（`WHERE mergedInto IS NULL`）：并发下两行不允许互相指向。
     *
     * 这是"两条通道同时抓到同一笔"这一核心场景的守护——若两行各自把对方标成被合并行，
     * 结果是**两行互相指向、谁也没真的合并掉**，而且不留任何报错。
     */
    @Test
    fun markMergedInto_isConditionalSoRowsCannotMergeIntoEachOther() = runBlocking {
        val notif = capture(draft(text = "微信支付 已支付¥32.00", event = "n1"))
        val acc = capture(
            draft(
                channel = AutoCaptureEntity.CHANNEL_ACCESSIBILITY,
                text = "付款成功 ¥32.00",
                event = "w1",
                at = bucket + 2_000L,
            )
        )

        assertEquals("首次留痕生效", 1, db.autoCaptureDao().markMergedInto(notif, acc, 1L))
        assertEquals(
            "已被合并的行不接受二次改指",
            0,
            db.autoCaptureDao().markMergedInto(notif, "someone-else", 2L)
        )
        assertEquals(acc, row(notif).mergedInto)
    }

    // ─── 开关与来源清单 ───

    @Test
    fun enabledSwitch_defaultsOffAndRoundTrips() = runBlocking {
        assertFalse("默认关闭：不知情就不该开始（ADR-016 决策 9）", repository.isEnabled())
        repository.setEnabled(true)
        assertTrue(repository.isEnabled())
        repository.setEnabled(false)
        assertFalse(repository.isEnabled())
        // 开关是设备本地自管键，不碰 meta 白名单
        assertEquals("0", db.syncMetaDao().getValue(SyncMetaEntity.KEY_AUTOBOOK_ENABLED))
    }

    @Test
    fun sourcePackages_areBuiltInPlusUserSelected() = runBlocking {
        assertEquals(NotificationFilter.DEFAULT_SOURCE_PACKAGES, repository.sourcePackages())

        repository.setUserSourcePackages(listOf("cmb.pb", " com.icbc ", ""))
        assertEquals(
            NotificationFilter.DEFAULT_SOURCE_PACKAGES + setOf("cmb.pb", "com.icbc"),
            repository.sourcePackages()
        )
        // 内置三项不接受改写
        repository.setUserSourcePackages(emptyList())
        assertEquals(NotificationFilter.DEFAULT_SOURCE_PACKAGES, repository.sourcePackages())
    }

    @Test
    fun sourcePackages_toleratesCorruptedValue() = runBlocking {
        db.syncMetaDao().upsert(
            SyncMetaEntity(SyncMetaEntity.KEY_AUTOBOOK_SOURCE_PACKAGES, "{not json")
        )
        assertEquals(
            "键被写坏时按没有追加项处理，内置来源仍工作",
            NotificationFilter.DEFAULT_SOURCE_PACKAGES,
            repository.sourcePackages()
        )
    }

    // ─── schema 导出（本轮唯一的红线变动）───

    /**
     * `12.json` **恰好一份**：这是 T21-3 收口的核验项。多出 `13.json` 意味着
     * 有人在本卡之外又动了 Room 版本，少一份意味着迁移没被导出。
     */
    @Test
    fun schema_v12IsExportedExactlyOnce() {
        val dir = File("schemas/com.aozora.app.db.AozoraDatabase")
        val v12 = File(dir, "12.json")
        assertTrue("Room 必须导出 12.json", v12.exists())
        assertFalse("本卡之外不得再动 Room 版本", File(dir, "13.json").exists())
        val json = v12.readText()
        assertTrue(json.contains("auto_captures"))
        assertTrue(json.contains("index_auto_captures_dedupeKey"))
        assertTrue(json.contains("index_auto_captures_mergeKey"))
    }

    // ─── Room 11 → 12 迁移 ───

    /**
     * 迁移 11→12：建 `auto_captures` 表 + 两个索引，存量数据保留。
     *
     * 手法同 10→11（见 RepositoryAccountBalanceSyncTest）：先用当前版本建库，
     * 再手工退回 v11 形态 —— 删掉新表、把 room_master_table 的 identity_hash
     * 改回 11.json 的值、user_version 置 11。这样重开时 Room 会真实走
     * MIGRATION_11_12，并按 v12 生成代码校验 schema（DDL 与实体不一致这里就会失败）。
     */
    @Test
    fun migration11To12_createsAutoCaptureTableAndKeepsData() = runBlocking {
        val ctx = RuntimeEnvironment.getApplication()
        val name = "test-migration-11-12.db"
        ctx.deleteDatabase(name)
        val fresh = AozoraDatabase.open(ctx, name)
        try {
            fresh.syncMetaDao().upsert(SyncMetaEntity(SyncMetaEntity.KEY_CURSOR, "7"))
        } finally {
            fresh.close()
        }
        val path = ctx.getDatabasePath(name).path
        SQLiteDatabase.openDatabase(path, null, SQLiteDatabase.OPEN_READWRITE).use { raw ->
            raw.execSQL("DROP TABLE IF EXISTS `auto_captures`")
            raw.execSQL(
                "UPDATE room_master_table SET identity_hash = ?",
                arrayOf(V11_IDENTITY_HASH)
            )
            raw.version = 11
        }

        val migrated = AozoraDatabase.open(ctx, name)
        try {
            // 表真的被建出来了（否则 DAO 调用会 "no such table"）
            val id = "c-migrated"
            assertEquals(
                1L,
                migrated.autoCaptureDao().insertIgnore(
                    AutoCaptureEntity(
                        captureId = id,
                        dedupeKey = "k1",
                        mergeKey = null,
                        sourceChannel = AutoCaptureEntity.CHANNEL_NOTIFICATION,
                        sourcePackage = "com.tencent.mm",
                        sourceApp = null,
                        rawText = "微信支付 已支付¥32.00",
                        postedAt = bucket,
                        merchantKey = null,
                        status = AutoCaptureEntity.STATUS_CAPTURED,
                        parsedJson = null,
                        confidence = null,
                        transactionId = null,
                        mergedInto = null,
                        error = null,
                        createdAt = 1L,
                        updatedAt = 1L,
                    )
                )
            )
            assertNotNull(migrated.autoCaptureDao().getById(id))
            // 唯一索引真在：同 dedupeKey 的第二行被忽略
            assertEquals(
                -1L,
                migrated.autoCaptureDao().insertIgnore(
                    migrated.autoCaptureDao().getById(id)!!.copy(captureId = "c-second")
                )
            )
            // 存量数据跨迁移保留
            assertEquals("7", migrated.syncMetaDao().getValue(SyncMetaEntity.KEY_CURSOR))
        } finally {
            migrated.close()
            ctx.deleteDatabase(name)
        }
    }

    private companion object {
        /** app/schemas/com.aozora.app.db.AozoraDatabase/11.json 的 identityHash。 */
        const val V11_IDENTITY_HASH = "57aade6848db481649c94523a4048912"
    }
}