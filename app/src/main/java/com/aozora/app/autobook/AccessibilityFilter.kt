package com.aozora.app.autobook

/**
 * 无障碍通道的预筛（第二十一轮 REQ-83 / T21-4，ADR-016 决策 3 / 4）。
 *
 * **纯函数**：输入是"来源包名 + 页面拼好的可见文本"，输出是"要不要落库"的判定。判定全部
 * 在**设备内**完成——这是隐私承诺的兑现点：**非支付类内容当场丢弃，不落库、不上行**
 * （`docs/architecture/security.md`）。
 *
 * 与 [NotificationFilter] 形态完全对齐（输出同一个 [Decision] 形状），但**常量独立定义**
 * 而不是共用——理由：
 *   - 两条通道的关键词集事实上相同（支付成功 / 到账 / 退款 / 验证码 / 广告），但**归属**
 *     的过滤器不同（一个判通知、一个判页面），语义上分开更清楚；
 *   - **避免修改 T21-3 的 NotificationFilter**：本卡红线 #7 禁止"顺手重构卡外文件"。
 * 金额提取的数学与货币标记是通道无关的，**直接复用** [NotificationFilter.extractAmountCents]
 * （公开纯函数；NFR-7 的整数分口径由它内部保证）。
 *
 * 判据顺序（**先排除，再取字段**，与 NotificationFilter 一致）：
 * 1. 来源包不在用户选定集合 → 丢弃；
 * 2. 拼好的页面文本为空 → 丢弃；
 * 3. 验证码 / 动态码 → 丢弃；
 * 4. 广告 / 营销推送 → 丢弃（**但支付完成类强事件词可豁免**，与通知侧同逻辑）；
 * 5. 提取不到金额 → 丢弃（页面没金额形态 = 不是支付结果页 / 账单详情页）；
 * 6. 提取不到支付 / 收入事件词 → 丢弃。
 *
 * **不做的事**：不解析（不抽商户、不判分类、不出交易草稿——那是 T21-6 的活），
 * 不做网络，不落库。本对象只回答"是不是疑似交易页面"，并给出方向与金额两个线索。
 * 方向与金额**只是线索**：权威值来自解析层（T21-6）。
 */
object AccessibilityFilter {

    const val DIRECTION_EXPENSE = NotificationFilter.DIRECTION_EXPENSE
    const val DIRECTION_INCOME = NotificationFilter.DIRECTION_INCOME

    // 丢弃原因（各自有独立前缀 `a11y_` 以区别于通知侧，供用例与排障；不进业务日志）
    const val REASON_SOURCE_NOT_ALLOWED = "a11y_source_not_allowed"
    const val REASON_EMPTY = "a11y_empty_text"
    const val REASON_VERIFICATION_CODE = "a11y_verification_code"
    const val REASON_AD_PUSH = "a11y_ad_push"
    const val REASON_NO_AMOUNT = "a11y_no_amount"
    const val REASON_NO_PAYMENT_KEYWORD = "a11y_no_payment_keyword"

    /** 判定结果。形态与 [NotificationFilter.Decision] 一致。 */
    data class Decision(
        val accepted: Boolean,
        val direction: String?,
        val amountCents: Long?,
        val reason: String?,
    ) {
        companion object {
            fun reject(reason: String) = Decision(false, null, null, reason)
            fun accept(direction: String, amountCents: Long) =
                Decision(true, direction, amountCents, null)
        }
    }

    /**
     * 预筛一页。[sourcePackages] = 用户选定来源集合（内置 ∪ 用户勾选）。
     *
     * [composedText] 由 [AutoBookAccessibilityService] 按行拼出——本对象不感知节点结构，
     * 只消费拼好的纯文本。这样**两条通道共用同一套解析链**是成立的（ADR-016 决策 1）。
     */
    fun evaluate(
        sourcePackage: String,
        composedText: String,
        sourcePackages: Set<String> = NotificationFilter.DEFAULT_SOURCE_PACKAGES,
    ): Decision {
        if (sourcePackage !in sourcePackages) return Decision.reject(REASON_SOURCE_NOT_ALLOWED)

        val body = composedText.trim()
        if (body.isEmpty()) return Decision.reject(REASON_EMPTY)

        // 验证码类先出局：它常常同时含"动态码"与数字，金额形态的误判风险最高
        if (VERIFICATION_CODE.containsMatchIn(body)) {
            return Decision.reject(REASON_VERIFICATION_CODE)
        }

        // 广告 / 营销推送：页面里出现"领券 / 立即领取 / 限时"这类营销文案时，按广告丢弃。
        // 豁免：文本里出现**支付完成类**强事件词时不按广告处理——"支付成功 已优惠 2 元"
        // 是真交易（与 NotificationFilter 同逻辑，见 NotificationFilter 注释）。
        if (AD_PUSH.containsMatchIn(body) && !STRONG_PAYMENT_EVENT.containsMatchIn(body)) {
            return Decision.reject(REASON_AD_PUSH)
        }

        // 金额提取复用通知侧的公开纯函数——数学与货币标记是通道无关的
        val amountCents = NotificationFilter.extractAmountCents(body)
            ?: return Decision.reject(REASON_NO_AMOUNT)

        // 方向 = 事件词，而不是"支付"这个品牌词（与通知侧同口径："微信支付-退款到账"
        // 里的"支付"属于品牌名，据此判成支出会把退款记成消费）
        val direction = when {
            EXPENSE_EVENT.containsMatchIn(body) -> DIRECTION_EXPENSE
            INCOME_EVENT.containsMatchIn(body) -> DIRECTION_INCOME
            else -> return Decision.reject(REASON_NO_PAYMENT_KEYWORD)
        }
        return Decision.accept(direction, amountCents)
    }

    /** 支出**事件**词。"支付"单字不入表：它是"微信支付/支付宝"的品牌构件。 */
    private val EXPENSE_EVENT = Regex(
        "支付成功|付款成功|已支付|支付了|已付款|已成功付款|成功付款|消费|扣款|支出|" +
            "交易成功|已扣|支付完成|付款完成|购买成功"
    )

    /** 收入事件词。 */
    private val INCOME_EVENT = Regex("收款|收入|到账|入账|退款|收到|已转入|已退款|退款到账")

    /** 广告 / 营销推送标记（页面场景下比通知更常见，多补几条页面型文案）。 */
    private val AD_PUSH = Regex(
        "优惠券|领券|立即领取|限时|活动已|满\\d+减|抽奖|邀请有礼|点击查看|推荐|" +
            "会员日|积分兑换|开通会员|猜你喜欢|今日特惠|限时抢购|去使用"
    )

    /** 支付**完成**类强事件词：广告守卫的豁免凭据。刻意不含"消费/支出"。 */
    private val STRONG_PAYMENT_EVENT = Regex(
        "支付成功|付款成功|已支付|已成功付款|成功付款|支付完成|付款完成|交易成功|扣款|已扣"
    )

    /** 验证码类（与通知侧同语义，独立常量）。 */
    private val VERIFICATION_CODE = Regex(
        "验证码|校验码|动态码|短信码|安全码|一次性密码|verification code|one[- ]?time (code|password)",
        RegexOption.IGNORE_CASE
    )
}
