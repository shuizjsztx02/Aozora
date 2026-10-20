package com.aozora.app.autobook

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 第二十一轮 REQ-83（T21-4）：无障碍预筛纯函数测试。
 *
 * **这是隐私承诺的兑现点**（决策 #13）：非支付类内容当场丢弃，不落库、不上行。
 * 反例覆盖 来源不在集合 / 空文本 / 验证码 / 广告 / 无金额 / 无事件词 六类，
 * 且每一条都断言 reason——只断言 `accepted == false` 会在"所有输入都被拒"的
 * 缺陷形态下照样全绿。
 *
 * 形态与 [NotificationFilterTest] 完全对齐，但常量独立（见 [AccessibilityFilter] 注释）。
 *
 * 无 Robolectric —— [AccessibilityFilter] 不依赖任何 Android 运行时。
 */
class AccessibilityFilterTest {

    private fun eval(
        sourcePackage: String = "com.tencent.mm",
        composedText: String,
        sources: Set<String> = NotificationFilter.DEFAULT_SOURCE_PACKAGES,
    ) = AccessibilityFilter.evaluate(sourcePackage, composedText, sources)

    // ─── 正例 ───

    @Test
    fun wechatPaymentPage_isAcceptedAsExpense() {
        // 微信支付成功页（实际页面文本简化示例）
        val d = eval(
            composedText = "微信支付\n支付成功\n收款方：星巴克\n付款金额 ¥32.00",
        )
        assertTrue(d.accepted)
        assertEquals(AccessibilityFilter.DIRECTION_EXPENSE, d.direction)
        assertEquals(3200L, d.amountCents)
        assertNull(d.reason)
    }

    @Test
    fun alipayPaymentPage_isAcceptedAsExpense() {
        val d = eval(
            sourcePackage = "com.eg.android.AlipayGphone",
            composedText = "支付宝\n已支付\n收款方：瑞幸咖啡\n¥18.50",
        )
        assertTrue(d.accepted)
        assertEquals(AccessibilityFilter.DIRECTION_EXPENSE, d.direction)
        assertEquals(1850L, d.amountCents)
    }

    @Test
    fun unionpayPaymentPage_isAcceptedAsExpense() {
        val d = eval(
            sourcePackage = "com.unionpay",
            composedText = "云闪付\n支付成功\n商户：全家便利店\n消费 12.00 元",
        )
        assertTrue(d.accepted)
        assertEquals(AccessibilityFilter.DIRECTION_EXPENSE, d.direction)
        assertEquals(1200L, d.amountCents)
    }

    @Test
    fun refundPage_isAcceptedAsIncome() {
        // ★ 方向取自事件词而不是品牌词："微信支付-退款到账"里的"支付"属于品牌名，
        // 据此判成支出会把退款记成消费（与 NotificationFilterTest 同口径）
        val d = eval(
            composedText = "微信支付\n退款到账\n¥32.00\n退款来源：星巴克",
        )
        assertTrue(d.accepted)
        assertEquals(AccessibilityFilter.DIRECTION_INCOME, d.direction)
        assertEquals(3200L, d.amountCents)
    }

    @Test
    fun bankBillDetailPage_isAcceptedAsExpense() {
        val d = eval(
            sourcePackage = "cmb.pb",
            composedText = "招商银行 · 账单详情\n消费 128.00 元\n商户：美团\n已扣款",
            sources = NotificationFilter.DEFAULT_SOURCE_PACKAGES + "cmb.pb",
        )
        assertTrue(d.accepted)
        assertEquals(AccessibilityFilter.DIRECTION_EXPENSE, d.direction)
        assertEquals(12800L, d.amountCents)
    }

    @Test
    fun incomeToAccount_isAcceptedAsIncome() {
        val d = eval(
            sourcePackage = "cmb.pb",
            composedText = "招商银行\n您尾号1234的账户 收入(工资) 8,500.00元",
            sources = NotificationFilter.DEFAULT_SOURCE_PACKAGES + "cmb.pb",
        )
        assertTrue(d.accepted)
        assertEquals(AccessibilityFilter.DIRECTION_INCOME, d.direction)
        assertEquals(850000L, d.amountCents)
    }

    /** 广告守卫的豁免：真交易页面里出现"优惠券"三个字不能把它判成广告。 */
    @Test
    fun paymentPageWithCouponWording_isNotTreatedAsAd() {
        val d = eval(
            composedText = "支付成功\n优惠券已核销\n付款金额 ¥32.00",
        )
        assertTrue(d.accepted)
        assertEquals(AccessibilityFilter.DIRECTION_EXPENSE, d.direction)
        assertEquals(3200L, d.amountCents)
    }

    // ─── T21-10 真机回归（2026-10-20）：汇总页守卫 ───
    //
    // 两条用例的 composedText 都取自真机 `auto_captures.rawText` 的真实片段
    // （支付宝账单列表页 / 支付宝首页）。修复前这两页都能通过过滤器并落库，
    // rawText 带出整页历史账单（含卡号后四位、手机号）——违反决策 #13。

    /** 账单列表页：月度汇总 + 十几行交易，显然不是"这一笔"的详情页。 */
    @Test
    fun billListPage_withMultipleAmounts_isRejected() {
        val d = eval(
            sourcePackage = "com.eg.android.AlipayGphone",
            composedText = "搜索交易记录\n搜索\n全部\n支出\n转账\n退款\n订单\n筛选\n" +
                "10月\n¥576.53\n收入\n¥0.00\n本月已省\n收支分析\n" +
                "扫收钱码付款-给*松，-0.01元，，交通出行，，今天 15:23\n" +
                "合肥市高新区可腾餐饮店，-16.51元，，餐饮美食，，今天 12:17",
        )
        assertFalse("账单列表页是多笔交易的汇总，不得当作单笔交易落库", d.accepted)
        assertEquals(AccessibilityFilter.REASON_MULTI_AMOUNT, d.reason)
    }

    /**
     * 支付宝首页的"最近消息"：两条付款提示 + 大量导航项。
     * ★ 注意本用例的是 **2 个不同金额**——它同时钉住"≥2 即拒绝"的阈值：
     * 若未来有人把判据放宽到 ≥3，本用例会变红。
     */
    @Test
    fun homePage_withRecentTransactions_isRejected() {
        val d = eval(
            sourcePackage = "com.eg.android.AlipayGphone",
            composedText = "松开刷新\n扫一扫\n收付款\n出行\n卡包\n生活缴费\n最近消息\n" +
                "160条新消息\n*松\n付款成功￥0.01\n8分钟前\n" +
                "合肥市高新区可腾餐饮店\n付款成功￥16.51\n3小时前\n为你推荐",
        )
        assertFalse("首页不是单笔交易页", d.accepted)
        assertEquals(AccessibilityFilter.REASON_MULTI_AMOUNT, d.reason)
    }

    /** 反向验证：单笔支付页即使金额出现多次（同一数值）也放行——去重后只有 1 个金额。 */
    @Test
    fun singlePaymentPage_repeatedSameAmount_isStillAccepted() {
        val d = eval(
            composedText = "支付成功\n¥32.00\n付款金额 ¥32.00\n收款方：星巴克",
        )
        assertTrue("同一个金额重复显示不构成汇总页", d.accepted)
        assertEquals(3200L, d.amountCents)
    }

    // ─── 反例（隐私承诺的兑现点，每条都要断言具体 reason）───

    @Test
    fun sourceNotInAllowedSet_isRejected() {
        // 来自未授权的包（比如浏览器）→ 即使页面像支付页也不收
        val d = eval(
            sourcePackage = "com.android.browser",
            composedText = "支付成功 ¥32.00",
        )
        assertFalse(d.accepted)
        assertEquals(AccessibilityFilter.REASON_SOURCE_NOT_ALLOWED, d.reason)
    }

    @Test
    fun emptyText_isRejected() {
        val d = eval(composedText = "   ")
        assertFalse(d.accepted)
        assertEquals(AccessibilityFilter.REASON_EMPTY, d.reason)
    }

    /** 支付页里最容易混进来的：验证码类。 */
    @Test
    fun verificationCodeScreen_isRejected() {
        val d = eval(
            composedText = "请输入动态码\n您的验证码 123456\n5分钟内有效",
        )
        assertFalse(d.accepted)
        assertEquals(AccessibilityFilter.REASON_VERIFICATION_CODE, d.reason)
    }

    /** 非支付页面的浏览行为：商品详情页 / 购物车页（只有金额形态，没有事件词）。 */
    @Test
    fun productDetailPage_isRejected() {
        // 商品详情页典型文本：有价格，但没有"支付成功/扣款/到账"事件词
        val d = eval(
            composedText = "星巴克官方旗舰店\n拿铁咖啡 ¥32.00\n加入购物车 立即购买",
        )
        assertFalse(d.accepted)
        assertEquals(AccessibilityFilter.REASON_NO_PAYMENT_KEYWORD, d.reason)
    }

    /** 聊天消息浏览：来源是微信，但内容是聊天，不是支付页。 */
    @Test
    fun chatMessagePage_isRejected() {
        val d = eval(
            composedText = "小明\n今晚一起吃饭吗？\n[图片]",
        )
        assertFalse(d.accepted)
        // 既没金额也没事件词 → 先命中"无金额"
        assertEquals(AccessibilityFilter.REASON_NO_AMOUNT, d.reason)
    }

    /** 广告 / 营销页：领券 / 限时 → 直接丢。 */
    @Test
    fun marketingPushPage_isRejected() {
        val d = eval(
            composedText = "限时特惠\n满 100 减 20\n立即领取 优惠券",
        )
        assertFalse(d.accepted)
        assertEquals(AccessibilityFilter.REASON_AD_PUSH, d.reason)
    }

    /** 只有金额没有事件词：金额形态可提取但无"支付/到账/扣款"。 */
    @Test
    fun amountWithoutEventWord_isRejected() {
        val d = eval(
            composedText = "订单号 1234567890\n¥32.00",
        )
        assertFalse(d.accepted)
        assertEquals(AccessibilityFilter.REASON_NO_PAYMENT_KEYWORD, d.reason)
    }

    // ─── 反向验证（`AI协作稳定性守则.md` §3.2）───
    //
    // 把"页面像支付页"的判据打回缺陷形态：去掉关键词、只剩金额、只剩品牌词。
    // 下面三条用例验证——**如果未来有人把 EXPENSE_EVENT / INCOME_EVENT 改宽，
    // 下面这三条用例会变红**，迫使他显式决定要不要放宽判据。

    /** 缺陷形态：只有品牌词"微信支付"而没有事件词 —— 这是"品牌词误判方向"的根因。 */
    @Test
    fun brandWordOnly_withoutEventWord_isRejected() {
        val d = eval(composedText = "微信支付\n¥32.00")
        assertFalse(
            "品牌词'微信支付'不等于事件词'支付成功'——否则会把所有支付页都判成支出",
            d.accepted,
        )
    }

    /** 缺陷形态：只有金额 + 订单号 —— 这是"商品详情页误识别为支付页"的根因。 */
    @Test
    fun amountAndOrderNumber_withoutEventWord_isRejected() {
        val d = eval(composedText = "订单号 1234567890\n付款金额 ¥32.00")
        assertFalse(
            "商品详情页也有金额与订单号 —— 事件词是把它们与支付结果页分开的关键判据",
            d.accepted,
        )
    }

    /** 缺陷形态：聊天文本 + 红包样式金额 —— 这是"聊天页误识别为支付页"的根因。 */
    @Test
    fun chatWithMoneyShape_isRejected() {
        val d = eval(
            composedText = "小明：晚饭 ¥32.00 我请客\n小红：好的",
        )
        assertFalse(
            "聊天里出现'¥32.00'不构成交付 —— 没有事件词就仍是聊天",
            d.accepted,
        )
    }
}
