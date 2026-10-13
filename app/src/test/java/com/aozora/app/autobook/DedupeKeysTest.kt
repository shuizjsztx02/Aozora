package com.aozora.app.autobook

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 第二十一轮 REQ-83（T21-3）：去重键纯函数测试。
 *
 * 无 Robolectric —— [DedupeKeys] 不依赖任何 Android 运行时。
 * 判据来源：ADR-016 决策 5（三级去重）/ 决策 6（禁止 LLM 参与）。
 */
class DedupeKeysTest {

    /** 桶起点：同桶内的抖动必须塌成同一个键。 */
    private val bucketStart = DedupeKeys.bucketStart(1_700_000_040_000L)
    private val nextBucket = bucketStart + DedupeKeys.BUCKET_MS

    private fun l1(
        text: String = "微信支付 已支付¥32.00",
        channel: String = "notification",
        pkg: String = "com.tencent.mm",
        event: String = "0|com.tencent.mm|1|null|10086",
        at: Long = bucketStart + 123L,
    ) = DedupeKeys.l1Key(channel, pkg, event, at, text)

    // ─── L1 ───

    @Test
    fun l1Key_isDeterministic() {
        assertEquals(l1(), l1())
    }

    @Test
    fun l1Key_ignoresMillisecondJitterWithinTheMinute() {
        // ★ 这条是本级的核心：无障碍的 TYPE_WINDOW_CONTENT_CHANGED 每次回调的
        // eventTime 都不同，用毫秒当输入等于没去重。
        assertEquals(l1(at = bucketStart), l1(at = bucketStart + 59_999))
    }

    @Test
    fun l1Key_separatesAcrossMinuteBuckets() {
        assertNotEquals(l1(at = bucketStart), l1(at = nextBucket))
    }

    @Test
    fun l1Key_changesWithTextPackageChannelAndEvent() {
        val base = l1()
        assertNotEquals(base, l1(text = "微信支付 已支付¥33.00"))
        assertNotEquals(base, l1(pkg = "com.eg.android.AlipayGphone"))
        assertNotEquals(base, l1(channel = "accessibility"))
        assertNotEquals(base, l1(event = "0|com.tencent.mm|2|null|10087"))
    }

    @Test
    fun l1Key_isStableAcrossWhitespaceAndCase() {
        val a = l1(text = "微信支付   已支付¥32.00")
        val b = l1(text = "\n微信支付\t已支付¥32.00 \n")
        val c = l1(text = "微信支付 已支付¥32.00")
        assertEquals(c, a)
        assertEquals(c, b)
    }

    @Test
    fun l1Key_isAHexDigestOfFixedLength() {
        val key = l1()
        assertEquals(64, key.length)
        assertTrue(key.all { it in "0123456789abcdef" })
    }

    /** 拼接歧义守护：分段之间必须用分隔符，否则 "ab"+"c" 与 "a"+"bc" 会撞键。 */
    @Test
    fun l1Key_doesNotCollideOnSegmentBoundaries() {
        val a = DedupeKeys.l1Key("notification", "com.a", "bc", bucketStart, "x")
        val b = DedupeKeys.l1Key("notification", "com.ab", "c", bucketStart, "x")
        assertNotEquals(a, b)
    }

    // ─── L2 ───

    @Test
    fun l2Key_isDeterministicAndIgnoresMillisecondJitter() {
        val a = DedupeKeys.l2Key("expense", 3200L, bucketStart)
        assertEquals(a, DedupeKeys.l2Key("expense", 3200L, bucketStart + 59_999))
    }

    @Test
    fun l2Key_separatesOnDirectionAmountAndBucket() {
        val base = DedupeKeys.l2Key("expense", 3200L, bucketStart)
        assertNotEquals(base, DedupeKeys.l2Key("income", 3200L, bucketStart))
        assertNotEquals(base, DedupeKeys.l2Key("expense", 3201L, bucketStart))
        assertNotEquals(base, DedupeKeys.l2Key("expense", 3200L, nextBucket))
    }

    @Test
    fun l2Key_andL1Key_neverCollide() {
        // 同一串输入下两级必须落在不同命名空间（前缀 L1 / L2）
        assertNotEquals(
            DedupeKeys.l1Key("notification", "com.tencent.mm", "expense", bucketStart, "3200"),
            DedupeKeys.l2Key("expense", 3200L, bucketStart)
        )
    }

    // ─── L2 的商户附加判据 ───

    @Test
    fun merchantCompatible_bothBlankOrEqualOnly() {
        assertTrue(DedupeKeys.merchantCompatible(null, "星巴克"))
        assertTrue(DedupeKeys.merchantCompatible("星巴克", null))
        assertTrue(DedupeKeys.merchantCompatible(null, null))
        assertTrue(DedupeKeys.merchantCompatible("", "  "))
        assertTrue(DedupeKeys.merchantCompatible("星巴克", "星巴克"))
        // ★ 商户名不同 → 不合并（L3：同额消费是日常，自动合并会吃掉真实账目）
        assertFalse(DedupeKeys.merchantCompatible("星巴克", "瑞幸"))
    }

    @Test
    fun normalizeText_keepsDigitsAndPunctuation() {
        // 只做形态归一的兑现：数字与标点必须原样留下，否则两笔真实同额消费会撞成一行
        assertEquals("¥32.00", DedupeKeys.normalizeText("  ¥32.00  "))
        assertNotEquals(
            DedupeKeys.normalizeText("¥32.00"),
            DedupeKeys.normalizeText("¥33.00")
        )
    }
}