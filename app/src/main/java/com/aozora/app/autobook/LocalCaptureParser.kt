package com.aozora.app.autobook

/**
 * 本地规则解析器（第二十一轮 REQ-83 / T21-6，ADR-016 决策 7）。
 *
 * **纯函数 + 模板，离线可用**：输入是一段文本，输出一份交易草稿或「未命中」（null）。
 * **两条通道共用这一套**——无障碍按行拼装的页面文本与通知的标题 + 正文，到了这一层
 * 都只是「一段带金额的文本」（ADR-016 决策 1「通道关系」）。因此本对象**不认识通道**，
 * 也不做网络、不碰数据库（分类解析与商户记忆在 [AutoBookEngine]）。
 *
 * **定位是离线兜底，不是主力**：服务端模型（`POST /ai/parse-notification`）是主入口，
 * 覆盖长尾文案与「缺商户名」的情形；本地规则保证**断网时记账仍然成立**（PR-3）。
 * 所以本对象的判据刻意**粗而稳**：只认市面最常见的文案骨架（见 T21-2 探针的语料），
 * 认不出就返回 null 交给上行通道——**绝不猜**。
 *
 * 判据顺序（与 `NotificationFilter` / `AccessibilityFilter` 同族，先排除再取字段）：
 * 1. **四类「不是消费」先出局**：退款 / 转账（含信用卡还款）/ 理财基金买入 / 红包收发。
 *    它们的文案特征极强，且**都带金额、都像一笔收支**——晚判就会被记成账目污染统计
 *    （T21-2 实测：`/ai/interpret` 下这四类 8/8 被记成消费，其中信用卡还款记成
 *    支出分类「其他」）。**先判它们，是本地规则唯一能确定地做对的事**。
 * 2. 金额：复用 [NotificationFilter.extractAmountCents]（**货币标记必需**，整数分）。
 *    口径与两个预筛过滤器逐字一致——能走到这里的文本必然已经过一次同样的判定。
 * 3. 方向：取自**事件词**（"支付成功 / 消费 / 扣款" vs "到账 / 收入 / 工资"），
 *    不取"支付"这种品牌构件（"微信支付-退款到账"里的"支付"属于品牌名）。
 * 4. 商户名：先按来源文案的显式模板取（`收款方：` / `付款给` / `在X消费` / `商户 X`），
 *    取不到再退到**无障碍结果页的形态**——金额那一行的下一行常常就是商户名。
 * 5. 分类提示：商户名（或整段文本）命中一张**很小的**常用关键词表 → 分类名。
 *    只是个提示：由 [AutoBookEngine] 拿本地真实分类去解析，解析不到就作废
 *    （用户改过分类名时自然退化，不会写错分类）。
 *
 * **不做的事**：不解析日期（形态太多且易错——日期由设备侧用捕获时刻补，见
 * [AutoBookEngine]）；不判账本/账户；不做置信度校准以外的任何推断。
 */
object LocalCaptureParser {

    const val KIND_TRANSACTION = "transaction"
    const val KIND_NOT_TRANSACTION = "not_transaction"

    const val TYPE_EXPENSE = "expense"
    const val TYPE_INCOME = "income"

    /** `reason` 白名单，与 `prompts.CAPTURE_PARSE` 逐字一致（跨层同名，便于对账）。 */
    const val REASON_NONE = ""
    const val REASON_REFUND = "refund"
    const val REASON_TRANSFER = "transfer"
    const val REASON_CREDIT_CARD_REPAYMENT = "credit_card_repayment"
    const val REASON_INVESTMENT = "investment"
    const val REASON_RED_PACKET = "red_packet"
    const val REASON_OTHER = "other"

    /**
     * 置信度两档（不是连续估计——本地规则要么认出了完整骨架，要么只认出半截）。
     *
     * [CONFIDENCE_HIGH] 对齐客户端既有口径 `ai-proposal-builder.ts` 的
     * `requiresConfirmation = confidence < 0.9 || warnings.length > 0 || !categoryId`：
     * **0.9 是本仓"高置信"的既有分界**，自动入账的门槛由它派生，不另发明一个数。
     */
    const val CONFIDENCE_HIGH = 0.95
    const val CONFIDENCE_LOW = 0.6

    /**
     * 一份解析草稿。字段命名与 `prompts.CAPTURE_PARSE` 的输出**同名同义**
     * （kind / reason / type / amountCents / category / note / bizDate / confidence），
     * 两条通道的在线与离线结果因此可以走同一段编排，客户端零映射。
     */
    data class Draft(
        val kind: String,
        val reason: String = REASON_NONE,
        val type: String = "",
        val amountCents: Long = 0L,
        /** 分类名提示（`category`），可能解析不到本地分类——由编排侧定夺。 */
        val categoryHint: String = "",
        /** 商户名或对方名称（`note`）；取不到为空串。 */
        val note: String = "",
        /** 本地规则**不产出日期**（`bizDate` 恒为空串），由编排侧用捕获时刻补。 */
        val bizDate: String = "",
        val confidence: Double = CONFIDENCE_LOW,
    )

    /**
     * 解析一段捕获文本。**未命中返回 null**——调用侧据此保持 `captured`（或上行服务端）。
     */
    fun parse(rawText: String): Draft? {
        val text = rawText.trim()
        if (text.isEmpty()) return null

        // 1. 四类「不是消费」先出局（顺序即判据优先级，见对象注释）
        notTransactionReason(text)?.let { reason ->
            return Draft(kind = KIND_NOT_TRANSACTION, reason = reason, confidence = CONFIDENCE_HIGH)
        }

        // 2. 金额（货币标记必需；口径与两个预筛过滤器逐字一致）
        val amountCents = NotificationFilter.extractAmountCents(text) ?: return null

        // 3. 方向 = 事件词
        val type = when {
            EXPENSE_EVENT.containsMatchIn(text) -> TYPE_EXPENSE
            INCOME_EVENT.containsMatchIn(text) -> TYPE_INCOME
            else -> return null
        }

        // 4. 商户名（先显式模板，再结果页形态）
        val merchant = extractMerchant(text)
        // 5. 分类提示（命中不了就给空——由编排侧决定是上行还是进待确认箱）
        val hint = categoryHint(merchant, text)

        return Draft(
            kind = KIND_TRANSACTION,
            type = type,
            amountCents = amountCents,
            categoryHint = hint,
            note = merchant.orEmpty(),
            confidence = if (hint.isEmpty()) CONFIDENCE_LOW else CONFIDENCE_HIGH,
        )
    }

    /**
     * 四类「不是消费」的判定。**顺序有依赖，且顺序即判据优先级**：
     * - 红包最先（"红包已被领取""收红包"这类文本里同时有"领取/收到"等词，晚判会被读成收入）；
     * - 还款在退款与理财之前（还款通知常带"转账/转入"字样）；
     * - ★ **退款在理财之前**——"退款成功 32.00元 / 已退至余额宝"同时含"退款"与"余额宝"，
     *   理财在前就会把一笔退款判成理财买入（两个 reason 都属"不是消费"，但成因报表会失真）；
     * - 理财在转账之前（"余额宝 转入成功"里的"转入"是理财动作，不是人际转账）；
     * - 转账最后（"转入/转出"在理财与退款文本里都会出现，它是这五类里最宽的一个）。
     */
    private fun notTransactionReason(text: String): String? = when {
        RED_PACKET.containsMatchIn(text) -> REASON_RED_PACKET
        CREDIT_CARD_REPAYMENT.containsMatchIn(text) -> REASON_CREDIT_CARD_REPAYMENT
        REFUND.containsMatchIn(text) -> REASON_REFUND
        INVESTMENT.containsMatchIn(text) -> REASON_INVESTMENT
        TRANSFER.containsMatchIn(text) -> REASON_TRANSFER
        else -> null
    }

    /**
     * 商户名提取：**先显式模板，再结果页形态**。
     *
     * 显式模板覆盖通知与账单页的常见写法（语料见 `server/scripts/probe_autobook_text_parse.py`）；
     * 结果页形态是无障碍通道特有的——支付结果页把商户名单独占一行，紧跟在金额行后面
     * （"支付成功 / ¥32.00 / 星巴克(北京国贸店) / 零钱 / 账单详情 / 完成"），
     * 所以退化成"金额行的下一行，跳过事件词与付款方式那类噪音行"。
     */
    private fun extractMerchant(text: String): String? {
        for (pattern in MERCHANT_PATTERNS) {
            val candidate = pattern.find(text)?.groupValues?.get(1)?.let { clean(it) }
            if (candidate != null && isMerchantLike(candidate)) return candidate
        }
        return merchantFromResultPage(text)
    }

    /** 无障碍结果页：金额行之后的第一个"像商户名"的行（最多看 3 行）。 */
    private fun merchantFromResultPage(text: String): String? {
        val lines = text.split('\n').map { it.trim() }.filter { it.isNotEmpty() }
        val amountIdx = lines.indexOfFirst { NotificationFilter.extractAmountCents(it) != null }
        if (amountIdx < 0) return null
        val end = minOf(lines.size, amountIdx + 4)
        for (i in amountIdx + 1 until end) {
            val candidate = clean(lines[i])
            if (candidate != null && isMerchantLike(candidate)) return candidate
        }
        return null
    }

    /** 取到候选后的清洗：去掉包裹的引号与书名号，限长。 */
    private fun clean(raw: String): String? {
        val trimmed = raw.trim().trim('「', '」', '"', '\'', '“', '”').trim()
        return trimmed.take(MAX_MERCHANT_CHARS).takeIf { it.isNotEmpty() }
    }

    /**
     * "像不像商户名"：长度 2–30、**不是纯数字/日期/时间**、**不含噪音词**
     * （事件词、付款方式、UI 文案、App 名）。噪音表是这套启发式的全部防线——
     * 它宁可返回 null（交给上行通道）也不能把"付款方式"写成商户名。
     */
    private fun isMerchantLike(candidate: String): Boolean {
        if (candidate.length < 2 || candidate.length > MAX_MERCHANT_CHARS) return false
        if (candidate.none { it.isLetter() }) return false
        if (NOISE_WORDS.any { candidate.contains(it) }) return false
        if (DATE_TIME_LIKE.containsMatchIn(candidate)) return false
        return true
    }

    /**
     * 分类提示：商户名优先，其次整段文本（商户名常常取不到）。命中不了返回空串。
     *
     * 提示**不带 kind**：命中与否只看关键词，能不能用由编排侧按交易类型在本地分类上
     * 解析一次定夺（收支类型对不上自然解析不到，退化成交给上行通道）。
     */
    private fun categoryHint(merchant: String?, text: String): String {
        val scope = merchant?.takeIf { it.isNotEmpty() } ?: text
        for ((pattern, name) in CATEGORY_HINTS) {
            if (pattern.containsMatchIn(scope)) return name
        }
        return ""
    }

    /** 支出**事件**词。"支付"单字不入表：它是"微信支付 / 支付宝"的品牌构件。 */
    private val EXPENSE_EVENT = Regex(
        "支付成功|付款成功|已支付|支付了|已付款|已成功付款|成功付款|消费|扣款|支出|" +
            "交易成功|已扣|支付完成|付款完成|购买成功|已支出|快捷支付支出"
    )

    /** 收入事件词。 */
    private val INCOME_EVENT = Regex(
        "收款|收入|到账|入账|退款|收到|已转入|工资|报销|存入|转入"
    )

    private val RED_PACKET = Regex("红包")

    private val CREDIT_CARD_REPAYMENT = Regex(
        "信用卡还款|还款成功|已还款|还款入账|自动还款|还款金额"
    )

    /**
     * 理财：只认**动作**，不认"付款方式"。
     *
     * ★ 一处实测出来的边界：支付宝的支付通知里"付款方式：余额宝"是**用哪儿的钱付**，
     * 不是理财买入。裸 `余额宝` 入表会把每一笔"余额宝付款"判成理财买入——所以
     * 这里要求"余额宝"与转入 / 转出 / 买入 / 卖出 / 收益同现。
     */
    private val INVESTMENT = Regex(
        "余额宝\\s*(?:转入|转出|买入|卖出|收益)|(?:转入|转出|买入|卖出)\\s*余额宝|" +
            "理财|基金|定投|朝朝宝|份额|证券|股票"
    )

    private val REFUND = Regex("退款|退货|已退回|原路退回|退款到账")

    private val TRANSFER = Regex("转账|转给|转入|转出|汇款|转至")

    /**
     * 商户名的显式模板，**按精确度降序**（越靠前的越不可能误抓）。
     * 每条的捕获组 1 = 商户名，止于换行与常见标点。
     */
    private val MERCHANT_PATTERNS = listOf(
        Regex("收款方[:：]?\\s*([^\\n，。,；;、]{1,30})"),
        Regex("(?:收款人|对方)[:：]?\\s*([^\\n，。,；;、]{1,30})"),
        Regex("(?:商户名称|商户全称|商户|商家名称|商家)(?!订单)(?!号)[:：]?\\s*([^\\n，。,；;、]{1,30})"),
        Regex("(?:付款给|付款至|付给|转给)\\s*([^\\n，。,；;、]{1,30})"),
        Regex("在\\s*([^\\n，。,；;、]{1,30}?)\\s*(?:消费|支出|支付|付款)"),
    )

    /**
     * 噪音词：出现即**不是**商户名。分三类——事件词（"支付成功"）、
     * 付款方式/账户（"零钱""余额宝""尾号"）、UI 文案与 App 名（"账单详情""完成""云闪付"）。
     * 无障碍结果页的金额行后面常常紧跟这几类行，它们是这套启发式的主要假阳性来源。
     */
    private val NOISE_WORDS = listOf(
        // 事件 / 状态
        "支付成功", "付款成功", "交易成功", "支付完成", "已完成", "完成", "成功", "失败",
        "处理中", "待支付", "已支付", "已付款", "支出", "收入", "消费", "退款", "转账",
        // 付款方式 / 账户 / 页面字段
        "零钱", "银行卡", "信用卡", "储蓄卡", "余额", "尾号", "订单号", "单号", "交易号",
        "付款方式", "支付方式", "交易时间", "创建时间", "支付时间", "时间", "卡号",
        // UI 文案 / App 名
        "账单详情", "账单", "详情", "查看", "交易明细", "明细", "云闪付", "微信支付", "支付宝",
        "微信", "收款方", "付款给", "商户", "对方",
        // 平台 / 理财（后两者已在 not_transaction 出局，这里是二道防线）
        "余额宝", "理财", "基金",
    )

    private val DATE_TIME_LIKE = Regex("\\d+月\\d+日|\\d{1,2}:\\d{2}|\\d{4}-\\d{2}-\\d{2}")

    /**
     * 商户关键词 → 分类名提示。**故意很小**：只放最常见的几类，且只用**种子分类里
     * 确实存在的名字**（REQ-79 的默认分类树）。宁可提示为空（→ 上行 / 待确认），
     * 也不猜一个用户没有的分类。命中不到时 [AutoBookEngine] 会作废这条提示。
     */
    private val CATEGORY_HINTS: List<Pair<Regex, String>> = listOf(
        Regex("星巴克|瑞幸|蜜雪|喜茶|奈雪|肯德基|麦当劳|必胜客|汉堡|咖啡|奶茶|餐|饭|火锅|烧烤|面馆|小吃|外卖|美团|饿了么") to "餐饮",
        Regex("房租|租金|房贷|物业费") to "住房",
        Regex("菜市场|蔬菜|水果店|盒马|叮咚|买菜") to "买菜",
        Regex("滴滴|出行|打车|出租|高德|地铁|公交|火车|高铁|机票|航空|停车|单车|哈啰|青桔") to "交通",
        Regex("加油|中石化|中石油|充电桩|洗车|保养") to "爱车",
        Regex("京东|淘宝|天猫|拼多多|唯品会|苏宁|商城|超市|便利店|沃尔玛|永辉|罗森|全家|购物") to "购物",
        Regex("电影|影院|游戏|KTV|健身|演出|门票|会员") to "休闲娱乐",
        Regex("话费|流量|宽带|电费|水费|燃气|快递|顺丰|菜鸟|干洗|理发|家政|物业") to "生活服务",
        Regex("工资|薪资|薪酬|代发") to "工资",
        Regex("报销") to "报销",
    )

    private const val MAX_MERCHANT_CHARS = 30
}