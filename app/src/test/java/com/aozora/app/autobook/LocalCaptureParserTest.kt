package com.aozora.app.autobook

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 第二十一轮 REQ-83（T21-6）：本地规则解析器的用例集。
 *
 * 无 Robolectric —— [LocalCaptureParser] 是纯函数，不依赖任何 Android 运行时
 * （这本身就是「离线可用」的一半证据：它没有可失败的外部依赖）。
 *
 * **判据来源与语料**：正例与四类反例全部取自 T21-2 判据先行探针的语料
 * （`server/scripts/probe_autobook_text_parse.py` 的 `CORPUS`）——同一批真实形态的
 * 文案，服务端模型过一遍，本地规则也过一遍，两条路的口径才可比。**不是另编的样本**。
 */
class LocalCaptureParserTest {

    private fun parse(text: String) = LocalCaptureParser.parse(text)

    private fun assertExpense(text: String, amountCents: Long, merchant: String?) {
        val draft = parse(text)
        assertNotNull("应当命中：$text", draft)
        draft!!
        assertEquals(LocalCaptureParser.KIND_TRANSACTION, draft.kind)
        assertEquals(LocalCaptureParser.TYPE_EXPENSE, draft.type)
        assertEquals(amountCents, draft.amountCents)
        if (merchant == null) {
            assertEquals("取不到商户名时留空，绝不猜", "", draft.note)
        } else {
            assertTrue("note 应含商户名「$merchant」，实际「${draft.note}」", draft.note.contains(merchant))
        }
    }

    private fun assertNegative(text: String, reason: String) {
        val draft = parse(text)
        assertNotNull("四类反例必须命中（否则会被记成账目）：$text", draft)
        draft!!
        assertEquals(LocalCaptureParser.KIND_NOT_TRANSACTION, draft.kind)
        assertEquals(reason, draft.reason)
    }

    // ─── 正例：两条通道 × 五个来源（T21-2 语料）───

    @Test
    fun wechatNotification_paymentResult_isParsed() {
        assertExpense(
            "[微信支付] 已付款 ¥32.00\n收款方：星巴克(北京国贸店)\n支付方式：零钱\n时间：2026-10-13 12:30:15",
            amountCents = 3200L,
            merchant = "星巴克",
        )
    }

    @Test
    fun wechatAccessibilityResultPage_isParsed_withMerchantFromAmountLine() {
        // 无障碍结果页把商户名单独占一行、紧跟在金额后面——这是本地规则唯一
        // 需要"看结构"的地方（显式模板全都不命中时退到这里）
        assertExpense(
            "支付成功\n¥32.00\n星巴克(北京国贸店)\n零钱\n账单详情\n完成",
            amountCents = 3200L,
            merchant = "星巴克",
        )
    }

    @Test
    fun alipayNotification_paymentResult_isParsed() {
        assertExpense(
            "支付宝\n支付成功 32.00元\n付款给 星巴克咖啡\n付款方式：余额宝",
            amountCents = 3200L,
            merchant = "星巴克",
        )
    }

    @Test
    fun unionpayNotification_isParsed_withMerchantFromInlineTemplate() {
        assertExpense(
            "云闪付\n您尾号1234的银联卡于10月13日12:30在星巴克(北京国贸店)消费人民币32.00元。",
            amountCents = 3200L,
            merchant = "星巴克",
        )
    }

    @Test
    fun unionpayAccessibilityResultPage_isParsed() {
        assertExpense(
            "支付成功\n¥32.00\n星巴克(北京国贸店)\n云闪付",
            amountCents = 3200L,
            merchant = "星巴克",
        )
    }

    @Test
    fun bankAccessibilityDetail_isParsed_withMerchantField() {
        assertExpense(
            "交易明细\n支出 32.00元\n商户 星巴克(北京国贸店)\n交易时间 2026-10-13 12:30:15\n卡号尾号 1234",
            amountCents = 3200L,
            merchant = "星巴克",
        )
    }

    @Test
    fun bankNotification_withoutMerchant_keepsFieldsButNotMerchant() {
        // 银行通知常常只有金额与方向（"快捷支付支出人民币32.00元"）——取不到商户名
        // 就留空，由编排侧决定是上行还是进待确认箱
        assertExpense(
            "招商银行\n您尾号1234的储蓄卡10月13日12:30快捷支付支出人民币32.00元，余额12,345.67元。",
            amountCents = 3200L,
            merchant = null,
        )
    }

    @Test
    fun unionpayNotification_withoutMerchant_isParsed() {
        assertExpense("云闪付\n消费人民币32.00元", amountCents = 3200L, merchant = null)
    }

    @Test
    fun bankSmsNotification_withoutMerchant_isParsed() {
        // 银行短信走的是**通知监听**（不是 READ_SMS），文案与银行 App 同族
        assertExpense(
            "【中国工商银行】您尾号5678的储蓄卡10月13日12:30消费人民币32.00元，余额8,765.43元。",
            amountCents = 3200L,
            merchant = null,
        )
    }

    @Test
    fun incomeNotifications_areParsedAsIncome() {
        listOf(
            "招商银行\n您尾号1234的储蓄卡10月13日09:00入账人民币12,000.00元（代发工资），余额21,345.67元。" to 1_200_000L,
            "【中国工商银行】您尾号5678的储蓄卡10月13日09:00收入人民币8,000.00元，余额16,765.43元。" to 800_000L,
        ).forEach { (text, cents) ->
            val draft = parse(text)
            assertNotNull("应当命中：$text", draft)
            assertEquals(LocalCaptureParser.TYPE_INCOME, draft!!.type)
            assertEquals(cents, draft.amountCents)
        }
    }

    @Test
    fun amount_keepsIntegerCents_acrossThousandsSeparators() {
        // NFR-7：金额永远整数分。12,000.00 元 = 1200000 分（千分位不得引入偏差）
        assertEquals(1_200_000L, parse("招商银行 入账人民币12,000.00元")!!.amountCents)
    }

    // ─── 四类「不是消费」（T21-2 反例语料，逐条）───

    @Test
    fun fourNonConsumptionClasses_areRecognised() {
        assertNegative("[微信支付] 已发出红包 ¥8.80\n红包已被领取", LocalCaptureParser.REASON_RED_PACKET)
        assertNegative("[微信] 你收到一个红包\n8.80元，已存入零钱", LocalCaptureParser.REASON_RED_PACKET)
        assertNegative(
            "[微信支付] 退款到账通知\n退款金额 ¥32.00\n已原路退回你的零钱",
            LocalCaptureParser.REASON_REFUND,
        )
        assertNegative("[微信支付] 已转账 ¥500.00\n收款方：张三", LocalCaptureParser.REASON_TRANSFER)
        assertNegative("支付宝\n退款成功 32.00元\n已退至余额宝", LocalCaptureParser.REASON_REFUND)
        assertNegative(
            "支付宝\n信用卡还款成功 2,000.00元\n已还入招商银行信用卡(1234)",
            LocalCaptureParser.REASON_CREDIT_CARD_REPAYMENT,
        )
        assertNegative("支付宝\n余额宝 转入成功 1,000.00元", LocalCaptureParser.REASON_INVESTMENT)
        assertNegative(
            "招商银行\n您购买朝朝宝活期理财人民币5,000.00元，份额已确认。",
            LocalCaptureParser.REASON_INVESTMENT,
        )
    }

    @Test
    fun refundMentioningYuEbao_isStillRefund_notInvestment() {
        // ★ 判据顺序的守护：这条文案同时含「退款」与「余额宝」。理财排在退款之前
        // 就会把一笔退款归成理财买入——两个 reason 都属"不是消费"，但成因会失真。
        val draft = parse("支付宝\n退款成功 32.00元\n已退至余额宝")!!
        assertEquals(LocalCaptureParser.REASON_REFUND, draft.reason)
    }

    @Test
    fun notTransaction_carriesNoAmountOrType() {
        val draft = parse("支付宝\n信用卡还款成功 2,000.00元\n已还入招商银行信用卡(1234)")!!
        assertEquals(0L, draft.amountCents)
        assertEquals("", draft.type)
        assertEquals("", draft.categoryHint)
    }

    // ─── 未命中（交给上行通道，绝不猜）───

    @Test
    fun misses_returnNull_soTheRowStaysCaptured() {
        listOf(
            "",                                  // 空文本
            "   ",                               // 全是空白
            "支付成功\n查看账单详情",              // 有事件词但没有金额形态 → 预筛就该拦掉
            "验证码 123456，5分钟内有效",          // 验证码（无金额标记）
            "您的快递已到达菜鸟驿站，取件码 8-1-2345",
            "今天天气不错，记得带伞",
            "星巴克花了32块",                      // 用户口语（无货币标记）——走的是另一条链
            "账单详情\n-32.00\n交易成功\n星巴克咖啡",  // ★ 无货币标记的金额（见 known limitation）
        ).forEach { text ->
            assertNull("不该命中：$text", parse(text))
        }
    }

    @Test
    fun amountRequiresCurrencyMarker_sameRuleAsTheTwoFilters() {
        // 金额口径与 NotificationFilter / AccessibilityFilter 逐字一致：**必须带货币标记**。
        // 不带标记的纯数字不认——通知里还有订单号、卡号后四位、时间，
        // 认了就会把一条快递通知变成一笔 12 元的支出。
        assertNull(parse("京东订单 202610130001 已发货 金额 32.00 状态 已支付"))
        assertNotNull(parse("京东 已支付 32.00元"))
    }

    @Test
    fun directionComesFromEventWord_notBrandWord() {
        // "微信支付"里的"支付"是**品牌构件**。据此判成支出，会把退款记成消费。
        // 这里钉住的是：品牌词-only 的文本不产生支出判定。
        val draft = parse("微信支付 ¥32.00")
        assertNull("品牌词不含事件词 → 不命中（交给上行通道）", draft)
    }

    // ─── 商户名与分类提示 ───

    @Test
    fun merchantNoiseLines_areNeverTakenAsMerchantName() {
        // 结果页的金额行后面常常跟着"零钱/账单详情/完成/付款方式"这类噪音行。
        // 一条都不能被当成商户名 —— 猜错了商户名会污染商户记忆。
        val draft = parse("支付成功\n¥32.00\n零钱\n账单详情\n完成")!!
        assertEquals("", draft.note)
    }

    @Test
    fun unknownMerchant_hasNoCategoryHint_lowConfidence() {
        val draft = parse("云闪付\n消费人民币32.00元")!!
        assertEquals("", draft.categoryHint)
        assertEquals(LocalCaptureParser.CONFIDENCE_LOW, draft.confidence, 0.0001)
    }

    @Test
    fun knownMerchant_givesCategoryHint_highConfidence() {
        val draft = parse("支付成功\n¥32.00\n星巴克(北京国贸店)\n零钱")!!
        assertEquals("餐饮", draft.categoryHint)
        assertEquals(LocalCaptureParser.CONFIDENCE_HIGH, draft.confidence, 0.0001)
    }

    @Test
    fun confidenceHigh_isTheExistingAutoEntryThreshold() {
        // 0.95 ≥ 0.9（客户端既有的"高置信"分界）——本地规则的完整命中必须够得着
        // 快速通道，否则"离线也能入账"这条验收无从成立
        assertTrue(LocalCaptureParser.CONFIDENCE_HIGH >= 0.9)
        assertTrue(LocalCaptureParser.CONFIDENCE_LOW < 0.9)
    }
}