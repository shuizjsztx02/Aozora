package com.aozora.app.autobook

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 第二十一轮 REQ-83（T21-3）：通知预筛纯函数测试。
 *
 * **这是隐私承诺的兑现点**（决策 #13）：非支付类内容当场丢弃，不落库、不上行。
 * 反例覆盖 验证码 / 聊天消息 / 系统通知 / 广告推送 / 来源不在选定集合 五类，
 * 且每一条都断言 reason——只断言 `accepted == false` 会在"所有输入都被拒"的
 * 缺陷形态下照样全绿。
 *
 * 无 Robolectric —— [NotificationFilter] 不依赖任何 Android 运行时。
 */
class NotificationFilterTest {

    private fun eval(
        title: String? = null,
        text: String? = null,
        bigText: String? = null,
        sourcePackage: String = "com.tencent.mm",
        sources: Set<String> = NotificationFilter.DEFAULT_SOURCE_PACKAGES,
    ) = NotificationFilter.evaluate(sourcePackage, title, text, bigText, sources)

    // ─── 正例 ───

    @Test
    fun wechatPayment_isAcceptedAsExpense() {
        val d = eval(title = "微信支付", text = "已支付¥32.00")
        assertTrue(d.accepted)
        assertEquals(NotificationFilter.DIRECTION_EXPENSE, d.direction)
        assertEquals(3200L, d.amountCents)
        assertNull(d.reason)
    }

    @Test
    fun alipayPaymentSentence_isAcceptedAsExpense() {
        val d = eval(
            title = "支付宝",
            text = "你已成功付款32.00元",
            sourcePackage = "com.eg.android.AlipayGphone",
        )
        assertTrue(d.accepted)
        assertEquals(NotificationFilter.DIRECTION_EXPENSE, d.direction)
        assertEquals(3200L, d.amountCents)
    }

    @Test
    fun refundToAccount_isAcceptedAsIncome() {
        // ★ 方向取自事件词而不是品牌词："微信支付"里的"支付"是品牌构件，
        // 据此判成支出会把退款记成消费。
        val d = eval(title = "微信支付", text = "退款到账 ¥32.00")
        assertTrue(d.accepted)
        assertEquals(NotificationFilter.DIRECTION_INCOME, d.direction)
        assertEquals(3200L, d.amountCents)
    }

    @Test
    fun bankIncome_isAcceptedAsIncome() {
        val d = eval(
            title = "招商银行",
            text = "您尾号1234的账户 收入(工资) 8,500.00元",
            sourcePackage = "cmb.pb",
            sources = NotificationFilter.DEFAULT_SOURCE_PACKAGES + "cmb.pb",
        )
        assertTrue(d.accepted)
        assertEquals(NotificationFilter.DIRECTION_INCOME, d.direction)
        assertEquals(850000L, d.amountCents)
    }

    /** 广告守卫的豁免：真交易里出现"优惠券"三个字不能把它判成广告。 */
    @Test
    fun paymentWithCouponWording_isNotTreatedAsAd() {
        val d = eval(title = "微信支付", text = "支付成功，优惠券已核销 ¥32.00")
        assertTrue(d.accepted)
        assertEquals(NotificationFilter.DIRECTION_EXPENSE, d.direction)
    }

    // ─── 反例：五类非支付内容必须零落行 ───

    @Test
    fun verificationCode_isDiscarded() {
        val d = eval(title = "微信", text = "验证码 123456，请勿向任何人泄露")
        assertFalse(d.accepted)
        assertEquals(NotificationFilter.REASON_VERIFICATION_CODE, d.reason)
    }

    @Test
    fun chatMessage_isDiscarded() {
        // 聊天消息与微信支付同包名，只能靠文本形态区分
        val d = eval(title = "李四", text = "晚上一起吃饭吗")
        assertFalse(d.accepted)
        assertEquals(NotificationFilter.REASON_NO_AMOUNT, d.reason)
    }

    @Test
    fun systemNotification_isDiscarded() {
        val d = eval(
            title = "系统更新",
            text = "系统更新已就绪，重启后生效",
            sourcePackage = "android",
            sources = NotificationFilter.DEFAULT_SOURCE_PACKAGES + "android",
        )
        assertFalse(d.accepted)
        assertEquals(NotificationFilter.REASON_NO_AMOUNT, d.reason)
    }

    @Test
    fun adPushWithAmountLikeWording_isDiscarded() {
        // 营销文案里带金额是常态——"消费满100元可用"既有"消费"又有"100元"
        val d = eval(
            title = "支付宝",
            text = "优惠券已发放，消费满100元可用",
            sourcePackage = "com.eg.android.AlipayGphone",
        )
        assertFalse(d.accepted)
        assertEquals(NotificationFilter.REASON_AD_PUSH, d.reason)
    }

    @Test
    fun adPushWithoutPaymentEvent_isDiscarded() {
        val d = eval(title = "云闪付", text = "限时活动，立减5元", sourcePackage = "com.unionpay")
        assertFalse(d.accepted)
        assertEquals(NotificationFilter.REASON_AD_PUSH, d.reason)
    }

    @Test
    fun sourceOutsideSelectedSet_isDiscarded() {
        val d = eval(title = "某银行", text = "支出 100.00元", sourcePackage = "com.example.bank")
        assertFalse(d.accepted)
        assertEquals(NotificationFilter.REASON_SOURCE_NOT_ALLOWED, d.reason)
    }

    @Test
    fun userSelectedSource_isAccepted() {
        val d = eval(
            title = "某银行",
            text = "支出 100.00元",
            sourcePackage = "com.example.bank",
            sources = NotificationFilter.DEFAULT_SOURCE_PACKAGES + "com.example.bank",
        )
        assertTrue(d.accepted)
        assertEquals(NotificationFilter.DIRECTION_EXPENSE, d.direction)
    }

    @Test
    fun blankText_isDiscarded() {
        val d = eval(title = " ", text = null)
        assertFalse(d.accepted)
        assertEquals(NotificationFilter.REASON_EMPTY, d.reason)
    }

    @Test
    fun transferWithoutPaymentEvent_isDiscarded() {
        // 转账（含信用卡还款）是"不是消费"的四类之一（ADR-016 决策 7）——
        // 预筛层不认它，避免在下游漏标时被记成消费
        val d = eval(title = "微信支付", text = "你有一笔转账 100.00元")
        assertFalse(d.accepted)
        assertEquals(NotificationFilter.REASON_NO_PAYMENT_KEYWORD, d.reason)
    }

    // ─── 金额提取 ───

    @Test
    fun extractAmountCents_handlesCommonForms() {
        assertEquals(3200L, NotificationFilter.extractAmountCents("已支付¥32.00"))
        assertEquals(3200L, NotificationFilter.extractAmountCents("已支付￥32"))
        assertEquals(3200L, NotificationFilter.extractAmountCents("消费32元"))
        assertEquals(123456L, NotificationFilter.extractAmountCents("消费1,234.56元"))
        assertEquals(1230L, NotificationFilter.extractAmountCents("消费12.3元"))
        assertEquals(3200L, NotificationFilter.extractAmountCents("已支付 CNY32.00"))
        // 0.01 元 = 1 分；分是最小单位，不进位
        assertEquals(1L, NotificationFilter.extractAmountCents("收款0.01元"))
    }

    @Test
    fun extractAmountCents_requiresCurrencyMarker() {
        // 订单号 / 卡号后四位 / 时间都不是金额
        assertNull(NotificationFilter.extractAmountCents("订单号 12345678"))
        assertNull(NotificationFilter.extractAmountCents("尾号 1234 的账户"))
        assertNull(NotificationFilter.extractAmountCents("10:32 充值提醒"))
    }

    @Test
    fun extractAmountCents_picksTheEarlierMarker() {
        // 前一个金额是货币标记、后一个是"元"字时，取文本中更靠前的那个
        assertEquals(3200L, NotificationFilter.extractAmountCents("支付¥32.00，另有红包100元"))
    }

    @Test
    fun extractAmountCents_rejectsZeroAndAbsurd() {
        assertNull(NotificationFilter.extractAmountCents("支付0.00元"))
        assertNull(NotificationFilter.extractAmountCents("支付99999999999.99元"))
    }

    // ─── 文本拼装 ───

    @Test
    fun composeText_skipsBlanksAndDeduplicatesIdenticalParts() {
        assertEquals(
            "微信支付 已支付¥32.00",
            NotificationFilter.composeText("微信支付", "已支付¥32.00", null)
        )
        // text 与 bigText 一字不差是常态，留两份会让 L1 键跟着变
        assertEquals(
            "微信支付 已支付¥32.00",
            NotificationFilter.composeText("微信支付", "已支付¥32.00", "已支付¥32.00")
        )
    }

    @Test
    fun builtInSources_areWechatAlipayAndUnionPay() {
        assertEquals(
            setOf("com.tencent.mm", "com.eg.android.AlipayGphone", "com.unionpay"),
            NotificationFilter.DEFAULT_SOURCE_PACKAGES
        )
    }
}