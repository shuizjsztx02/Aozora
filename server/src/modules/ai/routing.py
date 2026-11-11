"""T-M7-05g: 域路由（决定这一轮**注入哪些工具域**）。

架构依据 §7.2（域分组：单域 5–8 个工具，域是注入单位）与 §12.2 已定表第 2 项
（P1 不做域路由、归 P4——域扩展到全量工具时才需要）。风险登记 R-PROD-17。

**与入口推理分级是两件事**（架构 §12.2 的「易混淆点」）：本模块决定**注入哪些
工具**；``agent.classify_reasoning`` 决定**推理开销档位**（thinking 开关必须在
进入模型前定下来）。两者都在 ``AgentLoop.run()`` 里用同一句用户输入算一次，但
判据、产物、失败后果全不相同，因此**各写各的、不合并实现**。

为什么是规则而不是轻量模型
--------------------------
方案对比与决策见 ``docs/adr/ADR-011-domain-routing.md``。结论落在三条硬约束上：

① **不额外调用模型**（架构 §12.2 已定第 2 项：简单输入的快路径由 loop 自然产生，
   步数即复杂度）。轻量分类器会给**每一轮**加一次模型调用——记账 App 的绝大多数
   输入是简单输入，这笔开销正好花在最不该花的地方；且它自己也会错，还要再兜底。
② **决策必须在进入第一步之前完成**：模型请求的 ``tools`` 载荷一旦发出就无法收回
   （``AgentLoop`` 的注入面在 ``run()`` 里定下来）。
③ **可解释、可测**：词表命中是确定性的，误路由/漏注入因此能被**离线**量出来
   （``scripts/probe_tool_selection.py --routing``），不依赖模型与网络。

兜底：**宁可多注入，不可少注入**
--------------------------------
没命中任何信号 → 注入**全部域**（:data:`~.tools.DOMAINS`），即 P1 的注入面。
这是刻意的误差方向，两边的代价不对称：

- **漏注入**（该给的域没给）= 能力消失。模型只能回一句「这个我做不了」，
  用户看到的是**功能不见了**。
- **误注入**（多给了几个域）= 选择面变宽。R-PROD-17 的实测口径下（54 个工具
  全量注入）首调仍有 71%、两步内 90%，多注入的代价是**准确率**而非可用性。

因此凡是不确定的输入——闲聊、指代（「那改成 6000 吧」）、词表没覆盖的新说法
——一律退回兜底。**这条不是保守，是把「不确定」与「确定不用」分开**：只有明确
认出域时才收窄。

**兜底面本身在 2026-11-11 从「全部域」收窄为记账核心四域**（``FALLBACK_DOMAINS``，
M8 成本决策）：实测「吃饭78」这类短句未命中时，全量 56 工具 ≈ 12–15k tokens/Step，
简单记账一轮两 Step 约 3 万；收窄后约 1.5 万。低频域的覆盖改由词表承担（同批补充了
「吃/喝/买/打车/再存/切换到/怎么」等口语信号）——**代价是词表没覆盖的新说法会
少几个域**，这是用「每轮上万 token」换来的，方向由用户明确要求。

词表的维护面（下一步的债，已登记 R-PROD-17）
------------------------------------------
信号是**手写的中文短语**，覆盖度靠实测（探针的用例集就是第一批语料）。**新域上线
时必须同时补词**——:func:`unrouteable_domains` + 用例
（``test_every_domain_has_at_least_one_signal``）把「有域没有信号」变成一次可见的
失败。真实语料里的**漏配**形态是静默的（表现为退化成全量注入，用户无感），只能
靠 ``matched=False`` 的比例在生产上观察。
"""

from __future__ import annotations

from dataclasses import dataclass

from .tools import (
    DOMAIN_ACCOUNT,
    DOMAIN_ANALYSIS,
    DOMAIN_BOOK,
    DOMAIN_BUDGET,
    DOMAIN_CATEGORY,
    DOMAIN_EXPORT,
    DOMAIN_HELP,
    DOMAIN_META,
    DOMAIN_PERSONA,
    DOMAIN_PRIVACY,
    DOMAIN_REMINDER,
    DOMAIN_SAVINGS,
    DOMAIN_SCHEDULED,
    DOMAIN_SECURITY,
    DOMAIN_SYNC,
    DOMAIN_TRANSACTION,
    DOMAINS,
)

# ---------------------------------------------------------------------------
# 词表
# ---------------------------------------------------------------------------

#: **(信号, 它指向的域)**。一个信号可以指向多个域——这不是含糊，是**近义工具
#: 分属两域**时必须有的表达（见下面「花了 / 支出」一组）。
#:
#: 取舍原则：
#:
#: - **名词优先**。「预算 / 分类 / 存钱」这类词一旦出现，用户的意图几乎无歧义。
#: - **动词只做同域短句**，不做单字。「删」跨七个域（删交易 / 删预算 / 归档账户…），
#:   单字动词会把「删账本」错路由成记账域——**漏注入**，正是要避免的方向。
#: - **跨域的近义信号指向两域**（`transaction_summary` 与 `stats_monthly` 在
#:   「这个月花了多少」这种输入上都是正确答案）。指向一域就是一次漏注入。
_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # ── 记账流水（transaction）──
    ("记一笔", (DOMAIN_TRANSACTION,)),
    ("记账", (DOMAIN_TRANSACTION,)),
    ("记上", (DOMAIN_TRANSACTION,)),
    ("记下", (DOMAIN_TRANSACTION,)),
    ("记了", (DOMAIN_TRANSACTION,)),
    ("记录", (DOMAIN_TRANSACTION,)),
    ("记错", (DOMAIN_TRANSACTION,)),
    ("记重", (DOMAIN_TRANSACTION,)),
    ("补记", (DOMAIN_TRANSACTION,)),
    ("一笔", (DOMAIN_TRANSACTION,)),
    ("两笔", (DOMAIN_TRANSACTION,)),
    ("几笔", (DOMAIN_TRANSACTION,)),
    ("这笔", (DOMAIN_TRANSACTION,)),
    ("那笔", (DOMAIN_TRANSACTION,)),
    ("流水", (DOMAIN_TRANSACTION,)),
    ("报销", (DOMAIN_TRANSACTION,)),
    # M8-成本：口语里**直接描述消费动作**的短句（「吃饭78」「吃粉丝69」「买咖啡」
    # 「打车 20」）原先一条信号都不命中 → 退回全量注入（56 工具 ≈ 12–15k
    # tokens/Step，两 Step 约 3 万）。这些单字有误命中面（「吃了吗」闲聊），但
    # 误差方向是**多注入记账域**（选择面宽一点），与模块头的取向一致；漏注入的
    # 代价则是每轮上万 token。
    ("吃", (DOMAIN_TRANSACTION,)),
    ("喝", (DOMAIN_TRANSACTION,)),
    ("买", (DOMAIN_TRANSACTION,)),
    ("打车", (DOMAIN_TRANSACTION,)),
    ("加油", (DOMAIN_TRANSACTION,)),
    ("缴费", (DOMAIN_TRANSACTION,)),
    ("充值", (DOMAIN_TRANSACTION,)),
    # ── 记账 × 分析（近义工具分属两域，指一域就是漏注入）──
    ("花了", (DOMAIN_TRANSACTION, DOMAIN_ANALYSIS)),
    ("花销", (DOMAIN_TRANSACTION, DOMAIN_ANALYSIS)),
    ("开销", (DOMAIN_TRANSACTION, DOMAIN_ANALYSIS)),
    ("支出", (DOMAIN_TRANSACTION, DOMAIN_ANALYSIS)),
    ("收入", (DOMAIN_TRANSACTION, DOMAIN_ANALYSIS)),
    ("收支", (DOMAIN_TRANSACTION, DOMAIN_ANALYSIS)),
    ("消费", (DOMAIN_TRANSACTION, DOMAIN_ANALYSIS)),
    # ── 分类（category）──
    ("分类", (DOMAIN_CATEGORY,)),
    ("类别", (DOMAIN_CATEGORY,)),
    ("类目", (DOMAIN_CATEGORY,)),
    # ── 账户（account）──
    ("账户", (DOMAIN_ACCOUNT,)),
    ("账号", (DOMAIN_ACCOUNT,)),
    ("银行卡", (DOMAIN_ACCOUNT,)),
    ("信用卡", (DOMAIN_ACCOUNT,)),
    ("卡", (DOMAIN_ACCOUNT,)),
    ("钱包", (DOMAIN_ACCOUNT,)),
    ("余额", (DOMAIN_ACCOUNT,)),
    ("支付宝", (DOMAIN_ACCOUNT,)),
    ("微信支付", (DOMAIN_ACCOUNT,)),
    # ── 账本（book）──
    ("账本", (DOMAIN_BOOK,)),
    ("账簿", (DOMAIN_BOOK,)),
    # ── 预算（budget）──
    ("预算", (DOMAIN_BUDGET,)),
    # ── 存钱（savings）──
    ("存钱", (DOMAIN_SAVINGS,)),
    ("存款", (DOMAIN_SAVINGS,)),
    ("储蓄", (DOMAIN_SAVINGS,)),
    ("攒", (DOMAIN_SAVINGS,)),
    ("基金", (DOMAIN_SAVINGS,)),
    # 「再存 500」这类增量存入（语料 SAV-UPD-02）；单看「存」会误伤「存到哪」。
    ("再存", (DOMAIN_SAVINGS,)),
    # ── 定时（scheduled）──
    ("定时", (DOMAIN_SCHEDULED,)),
    ("自动记", (DOMAIN_SCHEDULED,)),
    ("房租", (DOMAIN_SCHEDULED,)),
    ("周期", (DOMAIN_SCHEDULED,)),
    ("重复记账", (DOMAIN_SCHEDULED,)),
    # ── 提醒（reminder，尾卡 / R-PROD-38）──
    # 与 ``scheduled`` 刻意**不合并**：定时记账是让服务端按期落一笔账，提醒只是
    # 让这台设备弹通知。两者在「提醒我交房租」这种句子上会**同时命中**
    # （「提醒」→ reminder、「房租」→ scheduled），而路由是并集语义——两个域都
    # 注入，由模型按语义选，这正是我们要的：它的判据比词表细。
    # 「叫我」有误命中面（「叫我小明」），但误差方向是**多注入一个域**（选择面宽
    # 一点），与「宁可多注入」的既定取向一致；漏注入的代价才是能力消失。
    ("提醒", (DOMAIN_REMINDER,)),
    ("闹钟", (DOMAIN_REMINDER,)),
    ("叫我", (DOMAIN_REMINDER,)),
    ("到点", (DOMAIN_REMINDER,)),
    # ── 角色（persona）──
    ("角色", (DOMAIN_PERSONA,)),
    ("人设", (DOMAIN_PERSONA,)),
    ("人格", (DOMAIN_PERSONA,)),
    ("跟我聊", (DOMAIN_PERSONA,)),
    ("陪我聊", (DOMAIN_PERSONA,)),
    ("聊天风格", (DOMAIN_PERSONA,)),
    # 「切换到米粒」（语料 PER-SWITCH-01）——「换成」不收：误伤面太大。
    ("切换到", (DOMAIN_PERSONA,)),
    # ── 默认项与用户画像（meta）──
    # T19-3（REQ-74 决策 #7）：「用户设定」这一整类措辞原先**一个信号都没有**
    # ——画像 / 称呼 / 习惯 / 偏好全不认。实测：「我的说话偏好是温柔一点」退回
    # 兜底全量，「以后每次叫我爹」只命中 security + reminder，于是 ``meta`` 域被
    # 排除在注入面外、模型手里**根本没有** ``meta_set``，只能对用户说「你自己去
    # 设置页填」（附件 #147）。漏注入的代价是**能力消失**，故按模块头「宁可多
    # 注入」补上这一组。
    #
    # 只收**复合词**：单字泛词（「偏好」「习惯」）会让「我更偏好用现金」这类输入
    # 从兜底全量**收窄**成 meta 单域——记账工具被收走，那是模块头明令避免的漏注入
    # 方向。复合词则相反：它出现时用户谈的确实是设定本身。
    ("默认", (DOMAIN_META,)),
    ("用户设定", (DOMAIN_META,)),
    # 「画像」是「用户画像」的子串，一条即覆盖两者。
    ("画像", (DOMAIN_META,)),
    ("称呼我", (DOMAIN_META,)),
    # 「叫我」在 reminder 与 security 两组里各有一条；这里是**第三条并列**而不是
    # 把信号从那里挪过来（决策 #7）：并集语义下三个域都注入，由模型按语义选。
    # 挪走的代价是「提醒我交房租」丢掉 reminder 域——一次真的漏注入。
    ("叫我", (DOMAIN_META,)),
    # 以下七条逐条对应画像表单的字段名（``prompts._PROFILE_LABELS`` 的键）。
    ("职业", (DOMAIN_META,)),
    ("生活节奏", (DOMAIN_META,)),
    ("固定支出", (DOMAIN_META,)),
    ("记账习惯", (DOMAIN_META,)),
    ("消费习惯", (DOMAIN_META,)),
    ("消费偏好", (DOMAIN_META,)),
    ("消费风格", (DOMAIN_META,)),
    ("沟通风格", (DOMAIN_META,)),
    ("说话偏好", (DOMAIN_META,)),
    ("说话风格", (DOMAIN_META,)),
    ("语气", (DOMAIN_META,)),
    # ── 分析（analysis）──
    ("分析", (DOMAIN_ANALYSIS,)),
    ("统计", (DOMAIN_ANALYSIS,)),
    ("报表", (DOMAIN_ANALYSIS,)),
    ("报告", (DOMAIN_ANALYSIS,)),
    ("趋势", (DOMAIN_ANALYSIS,)),
    ("概览", (DOMAIN_ANALYSIS,)),
    ("总览", (DOMAIN_ANALYSIS,)),
    ("占比", (DOMAIN_ANALYSIS,)),
    ("对比", (DOMAIN_ANALYSIS,)),
    # ── 导出（export）──
    ("导出", (DOMAIN_EXPORT,)),
    ("备份", (DOMAIN_EXPORT,)),
    # ── 反馈与隐私（privacy）──
    ("反馈", (DOMAIN_PRIVACY,)),
    ("意见", (DOMAIN_PRIVACY,)),
    ("建议", (DOMAIN_PRIVACY,)),
    ("隐私", (DOMAIN_PRIVACY,)),
    ("同意", (DOMAIN_PRIVACY,)),
    # ── 安全账号（security）──
    ("注销", (DOMAIN_SECURITY,)),
    ("删号", (DOMAIN_SECURITY,)),
    ("昵称", (DOMAIN_SECURITY,)),
    ("叫我", (DOMAIN_SECURITY,)),
    # ── 同步（sync）──
    ("同步", (DOMAIN_SYNC,)),
    ("云端", (DOMAIN_SYNC,)),
    ("上传", (DOMAIN_SYNC,)),
    # ── 产品帮助（help，05i）──
    # 这一组与其它组不同：信号不是**名词**（哪类数据），而是**问法**（在问怎么
    # 用）。它要覆盖的是**既有数据域信号、又是在问功能**的输入——「预算怎么用」
    # （预算 → budget）、「导出在哪」（导出 → export）、「支持语音记账吗」
    # （记账 → transaction）。没有这一组，这类输入会收窄到数据域，模型手里
    # 只剩「去做」的工具，于是对「支持吗」只能凭印象答。
    #
    # 泛问句（支持 / 能不能 / 可以吗）也在这一组里，代价是**误注入**：它们会把
    # help 域带进「能不能把这一笔删了」这种纯操作请求。这笔交换是按模块头
    # 「宁可多注入」的误差方向做的——help 域只有 1 个**无数据访问**的工具，
    # 误注入的代价是选择面宽一点；而漏注入的代价是模型对产品能力**编答案**。
    # 「怎么」覆盖「怎么导出数据」「怎么导入数据」这类——下面的「怎么用/怎么设置」
    # 是它的子集，保留是为了不依赖这一条单独成立。
    ("怎么", (DOMAIN_HELP,)),
    ("怎么用", (DOMAIN_HELP,)),
    ("怎么设置", (DOMAIN_HELP,)),
    ("怎么弄", (DOMAIN_HELP,)),
    ("怎么改", (DOMAIN_HELP,)),
    ("怎么换", (DOMAIN_HELP,)),
    ("怎么删", (DOMAIN_HELP,)),
    ("怎么看", (DOMAIN_HELP,)),
    ("是什么意思", (DOMAIN_HELP,)),
    ("为什么要", (DOMAIN_HELP,)),
    ("在哪", (DOMAIN_HELP,)),
    ("帮助", (DOMAIN_HELP,)),
    ("教程", (DOMAIN_HELP,)),
    ("使用说明", (DOMAIN_HELP,)),
    ("支持", (DOMAIN_HELP,)),
    ("能不能", (DOMAIN_HELP,)),
    ("可以吗", (DOMAIN_HELP,)),
)

#: 大小写不敏感的拉丁信号（中文信号走子串匹配，两者共用一张表）。
_LATIN_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("csv", (DOMAIN_EXPORT,)),
    ("excel", (DOMAIN_EXPORT,)),
)

_ALL_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = _SIGNALS + _LATIN_SIGNALS

#: 未命中任何信号时的**兜底注入面**（M8 成本决策，2026-11-11）。
#:
#: 原先是「全部域」——实测短句（「吃饭78」）命不中信号，于是每个 Step 注入 56
#: 个工具（35,791 字符 ≈ 12–15k tokens），简单记账一轮两 Step 就要 3 万。收窄为
#: **记账核心四域**（15,646 字符）后，简单任务约 1.5 万；低频域（savings /
#: persona / help 等）靠词表命中，而不是靠全量兜底——词表的 M8 补充见上面各组。
FALLBACK_DOMAINS: frozenset[str] = frozenset({
    DOMAIN_TRANSACTION, DOMAIN_ACCOUNT, DOMAIN_CATEGORY, DOMAIN_BOOK,
})


def unrouteable_domains() -> frozenset[str]:
    """**没有任何信号**的域（正常应为空）。

    它存在的意义是让「新增域时忘了补词」变成一次**可见的失败**，由用例钉住
    （``test_every_domain_has_a_signal``，与 ``stream.TOOL_PROGRESS`` 的
    「每个工具都要有步骤卡文案」同一范式）。

    **不在导入期报错**是刻意的：一个模块级的 ``raise`` 会让整个服务端起不来
    （爆炸半径远大于「新域暂时路由不到」），而它要防的只是配置疏漏——那是用例
    该回答的问题，不是运行期该拒绝的请求。漏配的真实后果只是该域**只能靠兜底
    注入**（``matched=False`` 时全量），功能不消失、只是选择面变宽。
    """
    return frozenset(DOMAINS) - {d for _, targets in _ALL_SIGNALS for d in targets}


@dataclass(frozen=True)
class RoutingDecision:
    """一次域选择的结果。

    ``matched=False`` 表示**没有命中任何信号**、退回全量注入（见模块头「兜底」）。
    它与「命中了但恰好命中全部域」是两件事：前者说明词表没覆盖这句输入
    （生产上值得观察的比例），后者说明这句话真的横跨很多东西。
    """

    #: 要注入的域（按 :data:`~.tools.DOMAINS` 的顺序，确定性输出）
    domains: tuple[str, ...]
    #: 是否命中信号（``False`` = 兜底全量）
    matched: bool
    #: 命中的信号短语（仅观测用：日志、用例、探针。**不进模型上下文**）
    signals: tuple[str, ...]


def route_domains(text: str) -> RoutingDecision:
    """按词表选域（纯函数、无 IO、**不调用模型**）。

    只认**这一轮的用户输入**，不看历史：

    - 指代性的追问（「那改成 6000 吧」）本来就命中不了信号 → 兜底全量，能力不受影响；
    - 拿历史来路由会把决策变成非确定（同一句话在不同会话里路由到不同域），
      而它要换来的只是「少注入几个域」——不划算。

    ``text`` 为空（或全空白）时同样兜底全量：空输入没有可路由的信息。
    """
    raw = (text or "").strip()
    if not raw:
        return RoutingDecision(domains=DOMAINS, matched=False, signals=())

    lowered = raw.lower()
    hits: list[str] = []
    matched: set[str] = set()
    for signal, targets in _ALL_SIGNALS:
        if signal in lowered:
            hits.append(signal)
            matched.update(targets)

    if not matched:
        return RoutingDecision(domains=DOMAINS, matched=False, signals=())

    return RoutingDecision(
        # 按 DOMAINS 归一化顺序：同一组域在帧载荷、日志与用例里逐字可比。
        domains=tuple(domain for domain in DOMAINS if domain in matched),
        matched=True,
        signals=tuple(hits),
    )
