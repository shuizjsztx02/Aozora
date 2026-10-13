package com.aozora.app.autobook

/**
 * 通知通道的预筛（第二十一轮 REQ-83 / T21-3，ADR-016 决策 4）。
 *
 * **纯函数**：输入是通知的包名与文本，输出是"要不要落库"的判定。判定全部在**设备内**
 * 完成——这是隐私承诺的兑现点：**非支付类内容当场丢弃，不落库、不上行**
 * （`docs/architecture/security.md`）。
 *
 * 判据顺序（**先排除，再取字段**）：
 * 1. 来源包不在用户选定集合 → 丢弃；
 * 2. 验证码 / 动态码 → 丢弃（支付类通知里最容易混进来的一类）；
 * 3. 广告 / 营销推送 → 丢弃（**但支付成功类文本里出现"优惠"不算广告**，
 *    见 [STRONG_PAYMENT_EVENT] 的豁免——"支付成功 已优惠 2 元"是真交易）；
 * 4. 提取不到金额 → 丢弃；
 * 5. 提取不到支付 / 收入事件词 → 丢弃（这条把聊天消息、系统通知一并挡在外面）。
 *
 * **不做的事**：不解析（不抽商户、不判分类、不出交易草稿——那是 T21-6 的活），
 * 不做网络，不落库。本对象只回答"是不是疑似交易文本"，并给出方向与金额两个线索。
 * 方向与金额**只是线索**：权威值来自解析层（T21-6）。
 */
object NotificationFilter {

    /**
     * 内置来源（ADR-016 决策 1 / 决策 9）：微信、支付宝、云闪付。
     * 银行 App 与短信通知来源由用户从已安装列表显式勾选（**读的仍是通知**，
     * 与申请短信权限无关），追加项存 `SyncMetaEntity.KEY_AUTOBOOK_SOURCE_PACKAGES`。
     */
    val DEFAULT_SOURCE_PACKAGES: Set<String> = setOf(
        "com.tencent.mm",              // 微信
        "com.eg.android.AlipayGphone", // 支付宝
        "com.unionpay",                // 云闪付
    )

    const val DIRECTION_EXPENSE = "expense"
    const val DIRECTION_INCOME = "income"

    // 丢弃原因（供用例与排障；不进业务日志——原文是敏感数据，见 security.md）
    const val REASON_SOURCE_NOT_ALLOWED = "source_not_allowed"
    const val REASON_EMPTY = "empty_text"
    const val REASON_VERIFICATION_CODE = "verification_code"
    const val REASON_AD_PUSH = "ad_push"
    const val REASON_NO_AMOUNT = "no_amount"
    const val REASON_NO_PAYMENT_KEYWORD = "no_payment_keyword"

    /** 判定结果。[accepted] 为 false 时 [reason] 给出丢弃原因，其余字段为 null。 */
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
     * 预筛一条通知。[sourcePackages] = 用户选定来源集合（内置 ∪ 用户勾选）。
     */
    fun evaluate(
        sourcePackage: String,
        title: String?,
        text: String?,
        bigText: String? = null,
        sourcePackages: Set<String> = DEFAULT_SOURCE_PACKAGES,
    ): Decision {
        if (sourcePackage !in sourcePackages) return Decision.reject(REASON_SOURCE_NOT_ALLOWED)

        val body = composeText(title, text, bigText)
        if (body.isBlank()) return Decision.reject(REASON_EMPTY)

        // 验证码类先出局：它常常同时含"动态码"与数字，金额形态的误判风险最高
        if (VERIFICATION_CODE.containsMatchIn(body)) {
            return Decision.reject(REASON_VERIFICATION_CODE)
        }

        // 广告 / 营销推送：营销文案爱写"消费满 100 元可用"这类带金额的句子。
        // 豁免：文本里出现**支付完成类**强事件词时不按广告处理——"支付成功 已优惠 2 元"
        // 是真交易（注意强事件词表**不含**"消费/支出"，否则营销句会自己给自己豁免）。
        if (AD_PUSH.containsMatchIn(body) && !STRONG_PAYMENT_EVENT.containsMatchIn(body)) {
            return Decision.reject(REASON_AD_PUSH)
        }

        val amountCents = extractAmountCents(body) ?: return Decision.reject(REASON_NO_AMOUNT)

        // 方向 = 事件词，而不是"支付"这个品牌词——"微信支付-退款到账"里的"支付"
        // 属于品牌名，据此判成支出会把退款记成消费。
        val direction = when {
            EXPENSE_EVENT.containsMatchIn(body) -> DIRECTION_EXPENSE
            INCOME_EVENT.containsMatchIn(body) -> DIRECTION_INCOME
            else -> return Decision.reject(REASON_NO_PAYMENT_KEYWORD)
        }
        return Decision.accept(direction, amountCents)
    }

    /**
     * 通知三件套 → 单段文本。空白段跳过；重复段（`text` 与 `bigText` 常常一字不差）
     * 只留一份——重复会让 L1 键跟着变，凭空造出两行。
     */
    fun composeText(title: String?, text: String?, bigText: String?): String {
        val parts = listOf(title, text, bigText)
            .map { it?.trim().orEmpty() }
            .filter { it.isNotEmpty() }
            .distinct()
        return parts.joinToString(" ")
    }

    /**
     * 金额可提取性：必须带**货币标记**（前置 `¥`/`￥` 或后置 `元`/`圆`/`CNY`/`RMB`）。
     *
     * 不带标记的纯数字不认——通知里还有订单号、卡号后四位、时间，认了就会把
     * 一条快递通知变成一笔 12 元的支出。整数分由字符串直接算，**不走浮点**
     * （NFR-7：金额永远整数分）。
     */
    fun extractAmountCents(text: String): Long? {
        val prefix = PREFIX_AMOUNT.find(text)
        val suffix = SUFFIX_AMOUNT.find(text)
        val hit = when {
            prefix == null -> suffix
            suffix == null -> prefix
            else -> if (prefix.range.first <= suffix.range.first) prefix else suffix
        } ?: return null
        val cents = parseCents(hit.groupValues[1]) ?: return null
        return if (cents in 1..MAX_AMOUNT_CENTS) cents else null
    }

    /** "1,234.5" → 123450（整数分；小数位缺失按 0 补足，多于两位不认）。 */
    private fun parseCents(raw: String): Long? {
        val normalized = raw.replace(",", "")
        val dot = normalized.indexOf('.')
        val wholePart = if (dot < 0) normalized else normalized.substring(0, dot)
        val fractionRaw = if (dot < 0) "" else normalized.substring(dot + 1)
        if (wholePart.isEmpty() || !wholePart.all { it.isDigit() }) return null
        if (fractionRaw.any { !it.isDigit() }) return null
        val fraction = fractionRaw.padEnd(2, '0')
        return runCatching {
            wholePart.toLong() * 100 + fraction.toLong()
        }.getOrNull()
    }

    /** 防御上限：1 亿元。超过它的一律按"没提取到"处理，不给下游造天文数字的机会。 */
    private const val MAX_AMOUNT_CENTS = 10_000_000_000L

    private val VERIFICATION_CODE = Regex(
        "验证码|校验码|动态码|短信码|安全码|一次性密码|verification code|one[- ]?time (code|password)",
        RegexOption.IGNORE_CASE
    )

    /** 支出**事件**词。"支付"单字不入表：它是"微信支付/支付宝"的品牌构件。 */
    private val EXPENSE_EVENT = Regex(
        "支付成功|付款成功|已支付|支付了|已付款|已成功付款|成功付款|消费|扣款|支出|" +
            "交易成功|已扣|支付完成|付款完成|购买成功"
    )

    /** 收入事件词。 */
    private val INCOME_EVENT = Regex("收款|收入|到账|入账|退款|收到|已转入")

    /** 广告 / 营销推送标记。 */
    private val AD_PUSH = Regex(
        "优惠券|领券|立即领取|限时|活动|已发放|满\\d+减|抽奖|邀请有礼|点击查看|推荐|" +
            "会员日|积分兑换|开通会员"
    )

    /** 支付**完成**类强事件词：广告守卫的豁免凭据。刻意不含"消费/支出"。 */
    private val STRONG_PAYMENT_EVENT = Regex(
        "支付成功|付款成功|已支付|已成功付款|成功付款|支付完成|付款完成|交易成功|扣款|已扣"
    )

    /** 前置货币标记：`¥32.00` / `￥32` / `CNY32.00` / `RMB32.00`。 */
    private val PREFIX_AMOUNT = Regex("(?:[¥￥]|CNY|RMB)\\s*(\\d[\\d,]*(?:\\.\\d{1,2})?)", RegexOption.IGNORE_CASE)

    /** 后置货币标记：`32元` / `32.00圆` / `32.00CNY`。 */
    private val SUFFIX_AMOUNT = Regex("(\\d[\\d,]*(?:\\.\\d{1,2})?)\\s*(?:元|圆|CNY|RMB)", RegexOption.IGNORE_CASE)
}