"""T-M7-02c / T-M7-05b / T-M7-05c: Agent 工具注册表。

工具是服务端 Agent 的能力面。**工具的 ``description`` 是模型可见的唯一语义
来源**（架构 §7.1），因此这里的文案按「模型读到它会做什么」来写，而不是按
「这个函数做什么」来写。

范围
----
- **P1（02c）** 交付最小集：15 个工具、3 个域，写工具只有记账的两个。
- **P4（05b）** 把**记账域**（``transaction.*`` / ``category.*``）与**账户账本域**
  （``account.*`` / ``book.*`` / ``meta_set(默认项)``）扩到全量 CRUD：27 个工具、
  6 个域（架构 §3.2 的覆盖度清单）。
- **P4（05c）** 再加**预算存钱 / 定时 / 角色**三域（``budget.*`` 的写 +
  ``savings.*`` / ``scheduled.*`` / ``persona.*``）：43 个工具、9 个域。这三类实体
  已于 T-M7-01 上云，服务端 loop 从 05c 起才够得到它们（此前「仅本地」）。
- **P4（05d）** 收尾：**分析**（``analysis``）、**导出**（``export``）、
  **反馈与隐私同意**（``privacy``）、**安全账号**（``security``）、**同步**
  （``sync``）五域 11 个工具 → 54 个工具、14 个域。架构 §3.2 覆盖度清单到此
  除「提醒」（``reminder_set``，归 05f 的设备执行器，见 §五 挂账项）外**全部落地**。
- **P4（05i）** 追加**产品帮助**域（``help``）一个读工具 ``help_search``：语料
  与检索在 ``help.py``。它**不在架构 §3.2 的覆盖度清单里**——那张表列的是「读写
  用户数据」的功能面，而「这个 App 怎么用」是**横切**问题（每一轮都可能问），
  所以它既不属于任何数据域、也无法从用户的说法里可靠地路由过去（域路由的信号
  是「名词指向哪类数据」，而「怎么导出」这类问句的信号是问句形态本身）。

工具名一律用下划线，**不沿用桥命令的 ``domain.action`` 点号命名**——provider
对 ``function.name`` 只接受 ``[a-zA-Z0-9_-]``，带点号会 400。约束、实测证据与
兜底用例见 ``PROVIDER_TOOL_NAME_PATTERN`` 与 ``test_tool_names_are_provider_safe``。

域拆分（架构 §7.2 的 ≤8）
------------------------
05b 把 P1 的 ``ledger`` 域按操作面拆成 ``category`` / ``account`` / ``book`` 三域——
「账户账本」合起来是 11 个工具，超过 §7.2 的单域上限（域是 05g 域路由的注入
单位，工具数直接决定模型的选择准确率）。``meta`` 单列一域：它只有
``meta_set`` 一个工具，而它的执行位置在**设备**（见下）。05c 的三个新域各自
5 个以内（``savings`` 4 / ``scheduled`` 4 / ``persona`` 5），预算的写并进已有的
``budget`` 域（5 读 + 3 写 = 8，正好到上限）。

写工具与 ActionProposal v2 的对齐（本模块最关键的约束）
-----------------------------------------------------
写工具**不直接执行**，只产出**规范草案**（架构 §7.3），由 02e/05b 的提案组装器
按客户端声明的契约版本装进对应车道：v2 车道 → ``{contractVersion: 2, action,
payload, ...}``；v1 车道 → 根上带 ``transactions`` 的 v1 信封（只有记账 op 装得进去）。

**草案的形状是规范 v2 形状**（``{"action": <v2 op>, "payload": {...}, "message",
"warnings"}``），车道信封由组装器加——工具层不关心客户端版本，一份草案两种车道
是同一个事实源。``Tool.proposal_action`` 是同一个 op 的注册表声明（05a 的
``canonical_op`` 把它翻成两条车道各自的动作名）。

`_DEVICE_EXECUTED_OPS` 里的 op（``meta_set`` 与 05c 的 ``persona_switch``）**服务端
不执行**：它们写的是设备本地的 ``sync_meta``（默认账本 / 默认账户 / **当前角色指针**
都是本地展示元数据，桥命令 ``meta.set`` 明写「不上行」，见 ``BridgeProtocol.kt``）。
因此这类提案只能**挂起**、由客户端在设备上执行（05f 的执行器按 op 分派），服务端
执行面（``/ai/execute``）对它报错。

铁律（架构 §7.4，agent 化不放松）
1. 模型永不输出 UUID —— 工具参数只接受**对象名**，名→id 由本模块的确定性
   解析完成（``ToolContext`` 携带用户的实际名字集合）。**改 / 删 / 归档类工具
   因此只接受「定位条件」（locator）而不是 id**：读工具的结果里刻意不含 id，
   模型只能描述它要动的那一笔，由 ``_resolve_transaction_target`` 之类的确定性
   代码把它翻成唯一的 id（匹配到 0 笔 / 多笔都报可行动的错，绝不猜）。
   读工具的**结果**里也不含任何 id。
2. 金额与统计永远由确定性代码计算 —— 工具不接受模型的算术结论，只接受原
   始参数，聚合一律走 SQL。
3. 写库必须经校验与确认门 —— 见上。

> **给 02e 的约束：** 写工具的草案**含**服务端解析出的 ``categoryId`` /
> ``accountId``——那是 ActionProposal 契约的要求，执行时必需。但**回喂给模型
> 之前必须剥掉**：把草案原样作为 tool result 塞回上下文，等于把 UUID 送进了
> 模型的可见范围，铁律 1 就名存实亡。loop 应回喂「提案已就绪」之类的摘要，
> 或至少剔除 id 字段。

降级契约：未知工具名与非法参数都不抛异常，而是返回
``ToolOutcome.failure(...)`` 的安全枚举错误码，由 loop 回喂给模型纠正
（架构 §11.1 的回退链不因一次工具调用失败而整体降级）。
"""

from __future__ import annotations

import enum
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from jsonschema import Draft7Validator
from sqlalchemy import String, and_, case, cast, false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.budget import Budget, BudgetType
from src.models.ledger import Account, Book, Category, Transaction, TxType
from src.models.persona import ChatPersona
from src.models.savings import SavingsGoal
from src.models.scheduled import ScheduledRule
from src.models.sync import SyncChange
from src.models.user import User

from .help import search as search_help
from .help import topic_titles as help_topic_titles

# 画像的键集**只有一份**，在 prompts 的标签表里（那张表同时用于渲染系统提示的
# 【用户画像】段）。这里 import 私有名是有意的：抄成第二份必然漂移，而漂移的
# 形态正是 T19-3 要修的那个 bug——模型按自然语言写键名（「说话偏好」），表单页
# 按 ``FIELD_ORDER`` 取值，于是**写进去了却显示为空**。同 ``proposal_ops`` 从
# ``sync.service`` import 私有校验器的先例。
from .prompts import _PROFILE_LABELS  # noqa: PLC2701 - 见上
from .validator import _FORMAT_CHECKER

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 域标识。工具按域分组注入（架构 §7.2）。05b 起 6 个域——P1 的 ``ledger``
#: 按操作面拆成 ``category`` / ``account`` / ``book``（合起来 11 个工具会越过
#: §7.2 的单域上限）；05c 再加 ``savings`` / ``scheduled`` / ``persona`` 三域
#: （三实体已于 T-M7-01 上云，服务端 loop 从 05c 起够得到它们）。
DOMAIN_TRANSACTION = "transaction"
DOMAIN_CATEGORY = "category"
DOMAIN_ACCOUNT = "account"
DOMAIN_BOOK = "book"
DOMAIN_BUDGET = "budget"
DOMAIN_SAVINGS = "savings"
DOMAIN_SCHEDULED = "scheduled"
DOMAIN_PERSONA = "persona"
DOMAIN_META = "meta"
#: 05d 的五个域（架构 §3.2 覆盖度清单的剩余行）。每一行一个域：``analysis``
#: 是分析行，``export`` / ``privacy`` 合起来是「导出 / 反馈 / 隐私」行（拆两个
#: 是因为导出与反馈同意在模型眼里是两件事），``security`` 是安全账号行，
#: ``sync`` 是同步行。**都远低于 §7.2 的 8 个上限**——域是 05g 域路由的注入
#: 单位，小域让路由切得更准。
DOMAIN_ANALYSIS = "analysis"
DOMAIN_EXPORT = "export"
DOMAIN_PRIVACY = "privacy"
DOMAIN_SECURITY = "security"
DOMAIN_SYNC = "sync"
#: 05i 的产品帮助域。**它不是数据域**（上面十四个域都是「某一类用户数据的读写
#: 面」），而是横切能力：语料是产品自己怎么用，跟当前账本、账户、流水全都无关。
#: 单列一域仍然是对的——域是注入单位，而它有独立的信号（问句形态），混进任何
#: 数据域都会让那个域的语义变浑（「导出」域里塞一个「怎么用导出」的工具，模型
#: 在「导出一份 CSV」与「导出在哪里下载」之间就没有可分辨的边界了）。
DOMAIN_HELP = "help"
#: 尾卡（R-PROD-38）：架构 §3.2 覆盖度清单的**最后一行**——「提醒」。
#:
#: 它与 ``scheduled`` 是**两件事**，架构把两者记成两行，这里的域边界照抄：
#: ``scheduled`` 是让**服务端**按期自动记一笔（规则存在云端）；``reminder_set``
#: 是让**这台设备**到点弹一条通知（配置存在设备本地的 ``ReminderScheduler``）。
#: 模型眼里这两句话也确实不同——「每月一号自动记一笔房租」对
#: 「明天早上八点提醒我交房租」。混进一个域会让那个域内出现两个近义工具，
#: 正是架构 §7.2 说的「域内近义面」问题。
DOMAIN_REMINDER = "reminder"

DOMAINS: tuple[str, ...] = (
    DOMAIN_TRANSACTION,
    DOMAIN_CATEGORY,
    DOMAIN_ACCOUNT,
    DOMAIN_BOOK,
    DOMAIN_BUDGET,
    DOMAIN_SAVINGS,
    DOMAIN_SCHEDULED,
    DOMAIN_PERSONA,
    DOMAIN_META,
    DOMAIN_ANALYSIS,
    DOMAIN_EXPORT,
    DOMAIN_PRIVACY,
    DOMAIN_SECURITY,
    DOMAIN_SYNC,
    DOMAIN_HELP,
    DOMAIN_REMINDER,
)

#: 架构 §7.2 的经验上限：单域工具数超过 8 会显著降低模型的选择准确率。
MAX_TOOLS_PER_DOMAIN = 8

#: provider 对 ``tools[].function.name`` 的字符集约束（OpenAI 兼容规范）。
#: DeepSeek 实测：带点号的名字（``transaction.add``）会返回 **400
#: invalid_request_error**，整轮请求直接失败 —— 而工具名是本模块提供给模型的
#: 唯一调用凭据，名字不合规等于整条 agent 路径不可用（02b 的 MockTransport
#: 单测发现不了：它不经过真实接口）。故工具名一律用下划线，**不沿用桥命令的
#: ``domain.action`` 点号命名**（桥命令是另一套命名空间，不受此约束）。
PROVIDER_TOOL_NAME_PATTERN = r"^[a-zA-Z0-9_-]{1,64}$"

#: ActionProposal 契约版本（05a 的两条车道）。
PROPOSAL_CONTRACT_V1 = 1
PROPOSAL_CONTRACT_V2 = 2
PROPOSAL_CONTRACTS: tuple[int, ...] = (PROPOSAL_CONTRACT_V1, PROPOSAL_CONTRACT_V2)

#: **v1 车道可承载的写 op。** v1 的写形状只有根上的 ``transactions``
#: （``schema.json``），且 ``/ai/execute`` 的 v1 分支只放行 ``add_tx`` /
#: ``add_tx_batch``——两者都是 ``transaction_add`` 的别名（05a 的别名表把
#: 单条与批量合成同一个 op）。**其余写 op 在 v1 车道上不可注入**：让模型看见
#: 一个产不出可用提案的工具，等于给它一条死路（提案会被旧校验器整条判无效，
#: 卡片不出、写入静默失败，见 ai-architecture.md「版本协商与降级」）。
V1_LANE_OPS: frozenset[str] = frozenset({"transaction_add"})

#: **执行位置在设备的 op**：服务端不执行，提案一律挂起、由客户端在设备上执行
#: （05f 的执行器按 op 分派）。
#:
#: T-M7-05j：``meta_set`` **移出本集合**——偏好项上云后，服务端有这些键的存储
#: 位置（``user_preferences`` 表），``meta_set`` 由服务端直接执行（见
#: ``proposal_ops._execute_meta_set``），AI 改默认账本 / 默认账户 / 小组件配置 /
#: 用户画像现在真的落库、真的进 sync change，不再只是"挂起后在设备上执行"。
#:
#: ``persona_switch``（05c）同理：**当前角色指针**是 ``sync_meta`` 的
#: ``active_persona_id`` 键（``BridgeProtocol.kt``：角色表已上云，指针不上行），
#: 换角色＝在设备上写这一个本地键——它**刻意留在这里**（角色指针是"当前选中哪一
#: 个"，不是用户偏好本身，跨设备一致语义与默认账本不同；05j 收窄时保留现状）。
#: 05d 的 ``consent_record`` / ``sync_trigger`` 同样留在这里——判据同构：
#: 服务端缺的是「设备身份」，不是存储位置（详见每条的注释）。
#: ``reminder_set``（尾卡 / R-PROD-38）判据同构：提醒配置存在设备本地的
#: ``ReminderScheduler``（桥命令 ``reminder.set`` 收 enabled/hour/minute），
#: 服务端没有它的落点——**只能挂起、由设备执行**。
DEVICE_EXECUTED_OPS: frozenset[str] = frozenset({
    "persona_switch", "consent_record", "sync_trigger", "reminder_set",
})

#: **不可自动执行的 op**（架构 §7.4 的「高风险」一类）。定义在 ``TOOLS`` 之后
#: ——它**从注册表派生**，不再手写第二份（REQ-78 / T20-3：``Tool.risk == "high"``
#: 是唯一事实源，判据原文见 :class:`Tool` 的 ``risk`` 字段）。

#: 单次批量的交易条数上限（``transaction_add_batch``）。执行是原子的，
#: 上限保护单次写操作的影响面（架构 §4.4「单 Turn 最大写操作数」的下游）。
MAX_BATCH_TRANSACTIONS = 20

# 安全枚举错误码。回喂给模型的是这些码 + 中文提示，绝不回喂异常正文
# （provider/DB 的错误正文可能回显用户输入，见 security.md）。
ERR_UNKNOWN_TOOL = "unknown_tool"
ERR_INVALID_ARGUMENTS = "invalid_arguments"
ERR_UNKNOWN_CATEGORY = "unknown_category"
ERR_UNKNOWN_ACCOUNT = "unknown_account"
ERR_AMBIGUOUS_ACCOUNT = "ambiguous_account"
ERR_UNKNOWN_BOOK = "unknown_book"
ERR_AMBIGUOUS_BOOK = "ambiguous_book"
ERR_INVALID_PERIOD = "invalid_period"
ERR_UNKNOWN_TRANSACTION = "unknown_transaction"
ERR_AMBIGUOUS_TRANSACTION = "ambiguous_transaction"
ERR_TARGET_ARCHIVED = "target_archived"
ERR_UNKNOWN_SAVINGS = "unknown_savings"
ERR_AMBIGUOUS_SAVINGS = "ambiguous_savings"
ERR_UNKNOWN_SCHEDULED = "unknown_scheduled"
ERR_AMBIGUOUS_SCHEDULED = "ambiguous_scheduled"
ERR_UNKNOWN_PERSONA = "unknown_persona"
ERR_AMBIGUOUS_PERSONA = "ambiguous_persona"
ERR_UNKNOWN_BUDGET = "unknown_budget"
ERR_BUDGET_INCOME_CATEGORY = "income_category_budget"
#: 05d：趋势查询的区间反了。与``invalid_period``（月份格式不对）分开，是因为
#: 两者的修法不同——一个是改写法，一个是调换起止。
ERR_INVALID_PERIOD_RANGE = "invalid_period_range"

_TOOL_ERROR_MESSAGES: dict[str, str] = {
    ERR_UNKNOWN_TOOL: "没有这个工具，请改用工具清单中已有的工具。",
    ERR_INVALID_ARGUMENTS: "参数不符合要求，请按 schema 修正后重试。",
    ERR_UNKNOWN_CATEGORY: "分类名不在现有分类中，请先查询分类清单再使用完全一致的名称。",
    ERR_UNKNOWN_ACCOUNT: "账户名不在现有账户中，请先查询账户清单。",
    ERR_AMBIGUOUS_ACCOUNT: (
        "存在多个同名账户，无法确定是哪一个。请向用户说明这一情况并请其指明，"
        "不要自行猜测。"
    ),
    ERR_UNKNOWN_BOOK: "账本名不在现有账本中，请先查询账本清单。",
    ERR_AMBIGUOUS_BOOK: (
        "存在多个同名账本，无法确定是哪一个。请向用户说明这一情况并请其指明，"
        "不要自行猜测。"
    ),
    ERR_INVALID_PERIOD: "月份格式必须是 YYYY-MM。",
    ERR_UNKNOWN_TRANSACTION: (
        "没有找到符合条件的交易。请先用 transaction_recent 或 transaction_list "
        "确认这笔记录的样子，再按看到的日期 / 金额 / 分类重新描述它；不要凭印象改参数。"
    ),
    ERR_AMBIGUOUS_TRANSACTION: (
        "符合条件的交易不止一笔，无法确定要改哪一笔。请补充日期、金额或分类等条件，"
        "把目标缩小到唯一一笔；也可以把候选逐条报给用户请他指明。不要自行猜测。"
    ),
    ERR_TARGET_ARCHIVED: "目标已归档，不能修改。请把它当成不存在处理。",
    ERR_UNKNOWN_SAVINGS: (
        "没有找到这个存钱目标。请先用 savings_list 确认目标的准确名称，"
        "不要凭印象新建一个同名目标。"
    ),
    ERR_AMBIGUOUS_SAVINGS: (
        "存在多个同名存钱目标，无法确定是哪一个。请向用户说明并请其指明，"
        "不要自行猜测。"
    ),
    ERR_UNKNOWN_SCHEDULED: (
        "没有找到这条定时记账规则。请先用 scheduled_list 确认规则的准确名称。"
    ),
    ERR_AMBIGUOUS_SCHEDULED: (
        "存在多条同名定时规则，无法确定是哪一条。请向用户说明并请其指明，"
        "不要自行猜测。"
    ),
    ERR_UNKNOWN_PERSONA: "没有找到这个角色。请先用 persona_list 确认角色的准确名称。",
    ERR_AMBIGUOUS_PERSONA: (
        "存在多个同名角色，无法确定是哪一个。请向用户说明并请其指明，不要自行猜测。"
    ),
    ERR_UNKNOWN_BUDGET: (
        "这个月没有找到对应的预算项（总预算或该分类的预算都没设过）。"
        "请先用 budget_list 或 budget_status 确认，不要凭空删。"
    ),
    ERR_BUDGET_INCOME_CATEGORY: (
        "预算是花销的上限，只能设在支出分类上；这个名字是收入分类。"
        "请确认用户想限制的是哪一项支出，或改用收入分类的名字重新确认。"
    ),
    ERR_INVALID_PERIOD_RANGE: (
        "起始月份晚于结束月份了，请把两个月份调换过来或改成正确的区间。"
    ),
}

ToolKind = Literal["read", "write"]
OutcomeKind = Literal["result", "proposal", "error"]

#: 写工具的**风险分级**（REQ-78 / T20-3）。**判据原文见 :class:`Tool` 的
#: ``risk`` 字段**——三档的口径在那里，这里是类型。读工具不产提案，一律 ``low``
#: （「不为读工具分级」不是漏标，是它们的处置恒为「直接执行」）。
ToolRisk = Literal["low", "medium", "high"]


# ---------------------------------------------------------------------------
# 上下文与产物
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolContext:
    """一次工具调用的可信上下文。

    **全部字段都来自服务端**，无一取自模型输出——这是「模型永不输出 UUID」
    铁律的落点：模型给名字，本对象给名字→id 的权威映射。

    映射为空时，任何名字解析都会失败并返回 ``unknown_*``，等价于「该用户
    还没有这些对象」。

    ``categories_by_kind`` 用收支类型分桶而不是扁平的 name→id：数据库对分类
    的唯一约束是 ``(book_id, kind, name)``，**同名的支出分类与收入分类可以
    共存**。扁平映射会让「其他(支出)」被「其他(收入)」覆盖，模型说「记一笔
    支出·其他」就可能落到收入分类的 id 上——而写入路径没有任何环节比对这个
    类型是否匹配。
    """

    db: AsyncSession
    user_id: uuid.UUID
    book_id: uuid.UUID
    #: kind（``expense`` / ``income``）→ 分类名 → id。
    #: 同 kind 同名时**本账本分类优先于全局分类**（见 ``load_tool_context``）——
    #: 唯一约束含 ``book_id``，两者可以共存，而随手拒绝解析会让模型看到合法
    #: 分类名却永远写不进去。
    categories_by_kind: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    #: T20-6（REQ-80）：kind → 一级分类名 → 它的二级分类名。**只为注入面的层级
    #: 渲染服务**——名字解析仍由 ``categories_by_kind`` 独占权威，写路径一个字
    #: 都没变（``resolve_category`` 依旧要求逐字一致）。
    #:
    #: 为什么必须让模型看见父子：REQ-80 的「优先二级」无从判定——模型看到的是
    #: 一堆平铺的名字，它不知道「打车」是「交通」的子类，也就无从知道该选谁。
    #: 只列一级而把二级藏着（旧形态）更糟：二级名压根不可见时，模型连「有更
    #: 具体的选择」都不知道。
    #:
    #: 父不在可见集里（已归档 / 在别的账本）时**子不进这张表**——渲染时它会按
    #: 一级独立列出，名字不丢（与 ``categories_by_kind`` 的「本账本覆盖全局」
    #: 同一取向：宁可让名字多出现一次，也不让合法分类名消失）。
    category_children_by_kind: Mapping[str, Mapping[str, tuple[str, ...]]] = field(
        default_factory=dict,
    )
    #: 未归档分类的 id → 名字（不剔除重名，服务于反查与存在性判断）。
    category_names_by_id: Mapping[str, str] = field(default_factory=dict)
    #: 账户名 → id。**只含名字唯一的账户**——账户没有唯一性约束，重名的挑任何
    #: 一个都可能把钱记到错的账户上，所以让**写**路径解析失败（见
    #: ``_resolve_account``）。
    account_names: Mapping[str, str] = field(default_factory=dict)
    #: 未归档账户的 id → 名字。**不剔除重名**：读过滤要的是「名字匹配的全集」，
    #: 剔除会让查询静默返回空集，模型据此对用户断言「这个账户没有支出」——
    #: 那是事实错误，比拒绝回答糟得多。
    account_names_by_id: Mapping[str, str] = field(default_factory=dict)
    book_names: Mapping[str, str] = field(default_factory=dict)
    #: 未删除账本的 id → 名字（不剔除重名）。只为 ``book_ids_named`` 服务：
    #: 账本重名时**读**能正常返回（``book_list`` 里两行都在），若写路径只报
    #: 「名字不存在」，模型会被叫去查一份明明列着该名字的清单——没有可尝试的
    #: 下一步。与账户侧 ``ambiguous_account`` 同口径。
    book_names_by_id: Mapping[str, str] = field(default_factory=dict)
    #: 以下三套（05c）与账户 / 账本同构：``*_names`` 只含**名字唯一**的对象
    #: （写路径要唯一解），``*_names_by_id`` 不剔除重名（读路径按名字取全集）。
    #: 三实体的名字都没有数据库唯一约束，重名是正常情形而不是脏数据。
    #:
    #: 归属范围各不相同，与 sync 的实体归属一致：存钱目标与角色是**用户级**
    #: （``owner_id``，不分账本），定时规则带 ``book_id``，因此只装**本账本**的
    #: 未归档规则——与 ``_budget_rows`` / ``transaction_*`` 同口径：一个账本的
    #: 对话不该改到另一个账本的规则（那也正是 ``authorization`` 的判定）。
    savings_names: Mapping[str, str] = field(default_factory=dict)
    savings_names_by_id: Mapping[str, str] = field(default_factory=dict)
    scheduled_names: Mapping[str, str] = field(default_factory=dict)
    scheduled_names_by_id: Mapping[str, str] = field(default_factory=dict)
    persona_names: Mapping[str, str] = field(default_factory=dict)
    persona_names_by_id: Mapping[str, str] = field(default_factory=dict)

    def resolve_category(self, kind: str, name: str) -> str | None:
        """按（收支类型, 分类名）解析。同名跨类型时由 ``kind`` 消歧。"""
        return self.categories_by_kind.get(kind, {}).get(name)

    def category_ids_named(self, name: str) -> tuple[str, ...]:
        """某名字对应的全部分类 id —— 纯读过滤用。

        走 ``category_names_by_id``（不剔除重名）而非 ``categories_by_kind``：
        查询的语义是「名字匹配的都算」，重名的两个都该出现在结果里。
        """
        return tuple(
            cid for cid, cname in self.category_names_by_id.items() if cname == name
        )

    def category_name_of(self, category_id: uuid.UUID | str) -> str | None:
        """分类 id → 名字；分类不存在或已归档时返回 ``None``。"""
        return self.category_names_by_id.get(str(category_id))

    def account_ids_named(self, name: str) -> tuple[str, ...]:
        """某名字对应的全部账户 id —— 读过滤用，含重名。"""
        return tuple(
            aid for aid, aname in self.account_names_by_id.items() if aname == name
        )

    def book_ids_named(self, name: str) -> tuple[str, ...]:
        """某名字对应的全部账本 id —— 重名判定用。"""
        return tuple(
            bid for bid, bname in self.book_names_by_id.items() if bname == name
        )

    def savings_ids_named(self, name: str) -> tuple[str, ...]:
        """某名字对应的全部存钱目标 id —— 读过滤与重名判定用。"""
        return tuple(
            gid for gid, gname in self.savings_names_by_id.items() if gname == name
        )

    def scheduled_ids_named(self, name: str) -> tuple[str, ...]:
        """某名字对应的全部定时规则 id —— 读过滤与重名判定用。"""
        return tuple(
            rid for rid, rname in self.scheduled_names_by_id.items() if rname == name
        )

    def persona_ids_named(self, name: str) -> tuple[str, ...]:
        """某名字对应的全部角色 id —— 读过滤与重名判定用。"""
        return tuple(
            pid for pid, pname in self.persona_names_by_id.items() if pname == name
        )


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """一次工具调用的产物。

    - ``kind="result"``：读工具的确定性结果，直接回喂模型并继续 loop。
    - ``kind="proposal"``：写工具的 ActionProposal 草案。**草案只含
      ``action`` / ``transactions`` / ``message`` / ``warnings``**；``proposalId``
      / ``confidence`` / ``requiresConfirmation`` 由 02e 的提案组装器补齐
      （它们取决于 loop 的 Step 数与审批策略，不属于工具层）。
    - ``kind="error"``：安全枚举错误码，回喂模型供其纠正。
    """

    kind: OutcomeKind
    data: dict[str, Any] = field(default_factory=dict)
    #: 服务端**为什么**把这条提案挂起来等用户点头（T20-4 / REQ-78 的下发通道）。
    #: 值是审批策略给出的理由码（``agent.confirm_reason`` 的四档：``high_risk`` /
    #: ``medium_risk`` / ``multi_step`` / ``low_confidence``）；``None`` = **不是**
    #: 审批策略挂的（设备 op、读工具、不需要确认的那一档）。
    #:
    #: **它是记录，不是依据**：判定本身在 ``AgentLoop._handle_proposal`` 里做完了，
    #: 这里只是把那个结论带到下行帧上（客户端据此回答「为什么问我」）。因此本字段
    #: 只影响确认卡的文案，**不参与任何放行判断**——没有它，卡片少一行解释；有它，
    #: 也不会让哪条提案多或少了确认。
    confirm_reason: str | None = None

    @classmethod
    def result(cls, data: dict[str, Any]) -> ToolOutcome:
        return cls(kind="result", data=data)

    @classmethod
    def proposal(
        cls, draft: dict[str, Any], *, confirm_reason: str | None = None,
    ) -> ToolOutcome:
        return cls(kind="proposal", data=draft, confirm_reason=confirm_reason)

    @classmethod
    def failure(cls, code: str, **extra: Any) -> ToolOutcome:
        data: dict[str, Any] = {
            "code": code,
            "message": _TOOL_ERROR_MESSAGES.get(code, "工具调用失败。"),
        }
        data.update(extra)
        return cls(kind="error", data=data)


ToolHandler = Callable[[ToolContext, dict[str, Any]], Awaitable[ToolOutcome]]


@dataclass(frozen=True, slots=True)
class Tool:
    """一个模型的可用工具。

    ``parameters`` 是 Draft-07 JSON Schema；``proposal_action`` 仅写工具使用，
    必须是 ``contracts/action-proposal/schema.json`` action 枚举中服务端可执行
    的值（见模块 docstring）。读工具该字段为 ``None``。

    ``risk`` 是**审批口径的单一事实源**（REQ-78）。三档判据**逐字如下**，
    标注时照它判、不要各判各的：

    - ``low`` —— **直接执行**。新增类写操作（往账上添、新建对象）、查询类、
      以及**可一键改回的设置项**（默认账本 / 默认账户 / 当前角色指针 / 提醒配置 /
      小组件配置）。它们的共同点是**不碰用户已有的数据行**：写坏了也就是多了一条
      或换了个默认值，用户自己再点一下就能改回来。
    - ``medium`` —— **需确认**。会**改动或删除用户既有数据**的写：交易 / 预算 /
      账户 / 账本 / 分类 / 定时 / 角色 / 余额，**以及导出域与隐私域的写**
      （数据离开 App 或涉及隐私同意——这一条按**域**判，不逐 op 再议）。
    - ``high`` —— **强制确认，且卡片明示高风险**。后果**不可通过 App 内的任何
      操作挽回**：注销账号、**清空或批量删除语义**的 op、隐私同意撤回。这把尺子
      下 56 个工具里达标的是两个——``account_deletion_request``（7 天冷静期后数据
      永久删除）与 ``book_delete``（账本连同其中**全部流水**一并不可见，是唯一带
      批量删除语义的 op）。

    **这不是「会不会写库」的分级**——写库的 op 有三十多个，全是写库就没法区分了。
    它分的是**后果的形状**：多一条 / 少一条（low）、改了用户攒下来的东西
    （medium）、改完在 App 里找不回来（high）。

    **新增工具必须显式标注**：本字段**没有默认值**，漏标在导入期就 ``TypeError``
    （不是静默降级成 low 而悄悄自动执行），``test_ai_confirmation_policy.py``
    另有一条遍历注册表的守护用例。
    """

    name: str
    domain: str
    kind: ToolKind
    risk: ToolRisk
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    proposal_action: str | None = None


# ---------------------------------------------------------------------------
# 工具上下文加载
# ---------------------------------------------------------------------------

#: 名字索引的单表行数上限。**这是失控保护，不是分页**：名字→id 索引必须
#: **完整**，截断会让读路径的「名字匹配的都算」静默退化成空集——模型据此对
#: 用户断言「这个账户没有支出」，是事实错误。故取值远高于任何真实数据规模
#: （客户端开箱只有十来个分类、几个账户），真实数据若要逼近它，正确做法是
#: 给 list 类工具加分页，而不是在这里截断索引。
_OBJECT_NAME_LIMIT = 2000


def _unique_name_map(pairs: Iterable[tuple[str, str]]) -> dict[str, str]:
    """``(名字, id)`` 序列 → 名字→id 映射；**重名的一律剔除**。

    重名时挑任何一个都可能把数据写到错误的对象上（账户没有唯一性约束），而
    工具层无法替用户裁决——所以让解析失败、由模型向用户澄清，好过默默记错账。
    读路径不走这个映射（见 ``account_names_by_id``）。
    """
    index: dict[str, str] = {}
    duplicated: set[str] = set()
    for name, obj_id in pairs:
        if name in duplicated:
            continue
        if name in index:
            duplicated.add(name)
            index.pop(name, None)
            continue
        index[name] = obj_id
    return index


async def load_tool_context(
    db: AsyncSession,
    user_id: uuid.UUID,
    book_id: uuid.UUID,
) -> ToolContext:
    """装载工具上下文（名字→id 映射）。

    分类沿用 ``context.load_validation_context`` 的可见性口径：本账本分类
    + 全局分类；账户是用户级（不按账本划分）；账本按 owner 过滤。

    只收录未归档对象——模型点名一个已归档的分类只会把写操作引向死路。

    **``book_id`` 必须属于 ``user_id``**，否则抛 404（与
    ``TransactionService._verify_book_ownership`` 同口径）。工具层不信任任何
    未经校验的账本 id：读工具的每一条查询都用 ``ctx.book_id`` 过滤，放行一个
    别人的账本等于把它的数据交出去。

    所有查询都带 ``ORDER BY``——配合行数上限，无序查询会返回任意子集，
    让名字解析的结果随数据库行序漂移。
    """
    owned = await db.scalar(
        select(Book.id).where(
            Book.id == book_id,
            Book.owner_id == user_id,
            Book.deleted_at.is_(None),
        ),
    )
    if owned is None:
        from src.common.errors import AppException
        raise AppException(404, "not_found", "Book not found")

    cat_rows = await db.execute(
        select(
            Category.kind, Category.name, Category.id, Category.book_id,
            Category.parent_id,
        ).where(
            Category.owner_id == user_id,
            (Category.book_id == book_id) | (Category.book_id.is_(None)),
            Category.archived_at.is_(None),
        ).order_by(Category.kind, Category.name, Category.id).limit(_OBJECT_NAME_LIMIT),
    )
    categories = cat_rows.all()
    # 两轮构建，让**本账本分类覆盖同名的全局分类**：唯一约束含 book_id，两者
    # 可以共存（客户端默认分类就是全局的，用户再给某个账本建一套同名分类很自然）。
    # 全局那轮打底、本账本轮覆盖，于是解析结果确定且符合直觉——比随手拒绝更好，
    # 否则模型会看到合法分类名却永远写不进去（category_list 显示两行同名，
    # 写操作恒 unknown_category，模型无路可走）。
    by_kind: dict[str, dict[str, str]] = {}
    for kind, name, cid, book_scoped, _parent in categories:
        if book_scoped is None:
            by_kind.setdefault(kind, {})[name] = str(cid)
    for kind, name, cid, book_scoped, _parent in categories:
        if book_scoped is not None:
            by_kind.setdefault(kind, {})[name] = str(cid)

    # T20-6（REQ-80）：父子关系（只服务注入面的层级渲染，见
    # ``ToolContext.category_children_by_kind``）。id→名这一跳**不剔除重名**：
    # 本账本与全局可能各有一个同名分类，而父名对两者是同一个——重名在这里
    # 不产生歧义（渲染与解析都只认名字）。
    name_of_id = {str(cid): name for _kind, name, cid, _b, _p in categories}
    children_by_kind: dict[str, dict[str, list[str]]] = {}
    for kind, name, _cid, _book_scoped, parent_id in categories:
        parent_name = name_of_id.get(str(parent_id)) if parent_id else None
        # 父不可见（已归档 / 在别的账本）或成环时不认这条边：子会在渲染时按
        # 一级独立列出，名字不丢。
        if not parent_name or parent_name == name:
            continue
        if parent_name not in by_kind.get(kind, {}):
            continue
        children_by_kind.setdefault(kind, {}).setdefault(parent_name, []).append(name)

    acc_rows = await db.execute(
        select(Account.name, Account.id).where(
            Account.owner_id == user_id,
            Account.archived_at.is_(None),
        ).order_by(Account.name, Account.id).limit(_OBJECT_NAME_LIMIT),
    )
    accounts = acc_rows.all()
    book_rows = await db.execute(
        select(Book.name, Book.id).where(
            Book.owner_id == user_id,
            Book.deleted_at.is_(None),
        ).order_by(Book.name, Book.id).limit(_OBJECT_NAME_LIMIT),
    )
    books = book_rows.all()
    # T-M7-05c：三实体（T-M7-01 上云）的名字索引。存钱目标与角色按 owner 过滤
    # （用户级，不分账本）；定时规则按**本账本**过滤（``scheduled_rules.book_id``）
    # ——与预算 / 交易的账本收窄同口径。
    savings_rows = await db.execute(
        select(SavingsGoal.name, SavingsGoal.id).where(
            SavingsGoal.owner_id == user_id,
            SavingsGoal.archived_at.is_(None),
        ).order_by(SavingsGoal.name, SavingsGoal.id).limit(_OBJECT_NAME_LIMIT),
    )
    savings = savings_rows.all()
    scheduled_rows = await db.execute(
        select(ScheduledRule.name, ScheduledRule.id).where(
            ScheduledRule.owner_id == user_id,
            ScheduledRule.book_id == book_id,
            ScheduledRule.archived_at.is_(None),
        ).order_by(ScheduledRule.name, ScheduledRule.id).limit(_OBJECT_NAME_LIMIT),
    )
    scheduled = scheduled_rows.all()
    persona_rows = await db.execute(
        select(ChatPersona.name, ChatPersona.id).where(
            ChatPersona.owner_id == user_id,
            ChatPersona.archived_at.is_(None),
        ).order_by(ChatPersona.name, ChatPersona.id).limit(_OBJECT_NAME_LIMIT),
    )
    personas = persona_rows.all()
    return ToolContext(
        db=db,
        user_id=user_id,
        book_id=book_id,
        categories_by_kind=by_kind,
        category_children_by_kind={
            kind: {parent: tuple(kids) for parent, kids in buckets.items()}
            for kind, buckets in children_by_kind.items()
        },
        category_names_by_id={str(cid): name for _kind, name, cid, _b, _p in categories},
        account_names=_unique_name_map((name, str(aid)) for name, aid in accounts),
        account_names_by_id={str(aid): name for name, aid in accounts},
        book_names=_unique_name_map((name, str(bid)) for name, bid in books),
        book_names_by_id={str(bid): name for name, bid in books},
        savings_names=_unique_name_map((name, str(gid)) for name, gid in savings),
        savings_names_by_id={str(gid): name for name, gid in savings},
        scheduled_names=_unique_name_map((name, str(rid)) for name, rid in scheduled),
        scheduled_names_by_id={str(rid): name for name, rid in scheduled},
        persona_names=_unique_name_map((name, str(pid)) for name, pid in personas),
        persona_names_by_id={str(pid): name for name, pid in personas},
    )


# ---------------------------------------------------------------------------
# 参数 schema 片段（复用，保证写/读两侧的口径一致）
# ---------------------------------------------------------------------------

_BIZ_DATE_SCHEMA: dict[str, Any] = {
    "type": "string",
    "format": "date",
    "pattern": r"^\d{4}-\d{2}-\d{2}$",
    "description": (
        "业务日期 YYYY-MM-DD。用户说「今天」「昨天」「上周五」等相对日期时，"
        "按系统提示的今天日期换算；不要臆造日期。"
    ),
}

_PERIOD_SCHEMA: dict[str, Any] = {
    "type": "string",
    "pattern": r"^\d{4}-\d{2}$",
    "description": (
        "月份 YYYY-MM。用户没有说明月份时，使用系统提示的当前月份。"
        "本工具只处理月度预算。"
    ),
}

_TX_TYPE_SCHEMA: dict[str, Any] = {
    "type": "string",
    "enum": ["expense", "income"],
    "description": "expense=支出，income=收入。",
}

#: 05d 的月度参数。与 ``_PERIOD_SCHEMA`` 只差描述（那个写着「本工具只处理月度
#: 预算」，是预算域专用的措辞）——校验口径（pattern）两者一字不差。
_MONTH_SCHEMA: dict[str, Any] = {
    "type": "string",
    "pattern": r"^\d{4}-\d{2}$",
    "description": (
        "月份 YYYY-MM。用户说「这个月」「上个月」时，按系统提示的今天日期换算；"
        "不要臆造月份。"
    ),
}

_LIMIT_SCHEMA: dict[str, Any] = {
    "type": "integer",
    "minimum": 1,
    "maximum": 50,
    "description": "最多返回的条数。",
}

#: ``transaction_add`` 的单笔参数；``add_batch`` 的数组元素复用它，
#: 使两处的字段语义、必填项与校验口径完全一致。
_TX_ITEM_PROPERTIES: dict[str, Any] = {
    "type": _TX_TYPE_SCHEMA,
    "amountCents": {
        "type": "integer",
        "minimum": 1,
        "maximum": 9007199254740991,
        "description": (
            "金额，单位是分（1 元 = 100 分）。你必须自己完成口语金额到分的换算："
            "「十二块五」= 1250，「三十」= 3000，「两块」= 200。"
        ),
    },
    "categoryName": {
        "type": "string",
        "minLength": 1,
        "description": (
            "分类名，必须与现有分类完全一致（不要写简称、不要改写）。"
            "**能对上二级就用二级**：系统提示里「交通（公交 / 打车 / 地铁…）」"
            "括号内的是二级分类，打车就填「打车」而不是「交通」；没有贴切的二级"
            "时一级本身就是合法选择。**括号只是标注归属，这里只填名字本身**"
            "（填「人情社交」，不要填「人情社交（亲戚 / 借用）」）。"
            "候选里没有完全贴切的也必须选一个最接近的，"
            "不要留空、不要编一个名单外的名字。拿不准时先调用 category_list 查询。"
        ),
    },
    "bizDate": _BIZ_DATE_SCHEMA,
    "note": {
        "type": "string",
        # 2000 = transactions.note 的列宽（migrations/versions/0005_note_length.py）。
        # REST 路径靠 Pydantic 的 max_length 拦住超长值，而写工具的提案走
        # execute_proposal → create_transaction_batch，绕开了那层；不在这里
        # 拦，超长备注在 PostgreSQL 上会以 StringDataRightTruncation 直接 500
        # （SQLite 不校验长度，测试永远发现不了）。
        "maxLength": 2000,
        "description": "备注原文，没有就省略或留空字符串。",
    },
    "accountName": {
        "type": "string",
        "description": (
            "账户名，必须与现有账户完全一致。用户明确提到账户（如「招行卡」）时才填，"
            "否则省略。"
        ),
    },
}

_TX_ITEM_REQUIRED: list[str] = ["type", "amountCents", "categoryName", "bizDate"]

#: 账户分类（REQ-69 / T18-6）：**自由文案**，与客户端 AccountsPage 同值域
#: （储蓄卡/信用卡/微信…或用户自定义）。上限 64 = ``accounts.account_type`` 列宽。
#: T18-6 之前 AI 只改名字与图标，这条字段是本卡放开的。
_ACCOUNT_TYPE_SCHEMA: dict[str, Any] = {
    "type": "string",
    "minLength": 1,
    "maxLength": 64,
    "description": (
        "账户的分类名，用用户自己的说法（如「储蓄卡」「信用卡」「微信」）。"
        "**不是**记账分类（餐饮/交通那些），也不是「收入账户」「支出账户」"
        "这类笼统标签。用户没提分类就省略。"
    ),
}

#: 账户余额（REQ-69 / T18-6）：单位**分**，恒为**绝对值**（决策 #4 的幂等铁律），
#: **允许负值** = 信用卡欠款。
_ACCOUNT_BALANCE_SCHEMA: dict[str, Any] = {
    "type": "integer",
    "minimum": -9007199254740991,
    "maximum": 9007199254740991,
    "description": (
        "账户当前余额，单位是分（1 元 = 100 分），可正可负（信用卡欠款为负）。"
        "你必须自己完成元到分的换算：「五千」= 500000、「欠两千」= -200000。"
        "必须填**更新后的绝对值**，不要填增减量：用户说「再充 500，现在 3500」"
        "就填 350000。用户没提金额就省略。"
    ),
}


def _tx_item_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": dict(_TX_ITEM_PROPERTIES),
        "required": list(_TX_ITEM_REQUIRED),
        "additionalProperties": False,
    }


def _object_schema(
    properties: dict[str, Any],
    required: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


#: 「定位条件」——改 / 删类工具指向某一笔交易的方式。
#:
#: **这不是 id。** 读工具刻意不回传 id（铁律 1），模型因此只能**描述**它要动的
#: 那一笔；服务端把描述翻成唯一的 id（``_resolve_transaction_target``）。条件是
#: 合取关系，给得越全越准；匹配到 0 笔 / 多笔都不猜，各报一个可行动的错。
_TX_LOCATOR_PROPERTIES: dict[str, Any] = {
    "bizDate": {
        "type": "string",
        "format": "date",
        "pattern": r"^\d{4}-\d{2}-\d{2}$",
        "description": "这笔交易的业务日期，必须完全一致。",
    },
    "amountCents": {
        "type": "integer",
        "minimum": 1,
        "description": "这笔交易的金额，单位是分，必须完全一致。",
    },
    "type": _TX_TYPE_SCHEMA,
    "categoryName": {
        "type": "string",
        "description": "这笔交易的分类名，必须与现有分类完全一致。",
    },
    "accountName": {
        "type": "string",
        "description": "这笔交易的账户名，必须与现有账户完全一致。",
    },
    "keyword": {
        "type": "string",
        "description": "备注或分类名里包含的词。",
    },
}

#: 改一笔交易时**允许改的字段**。缺省＝不动那个字段（与 ``TransactionUpdate``
#: 的 None 语义一致）。改不了的字段（如清空账户）刻意不在这里——能表达的动作
#: 越少，模型越不容易做出它无法完成的事。
_TX_CHANGE_PROPERTIES: dict[str, Any] = {
    "type": _TX_TYPE_SCHEMA,
    "amountCents": {
        "type": "integer",
        "minimum": 1,
        "maximum": 9007199254740991,
        "description": (
            "新的金额，单位是分（1 元 = 100 分）。你必须自己完成口语金额到分的"
            "换算。"
        ),
    },
    "categoryName": {
        "type": "string",
        "description": (
            "新的分类名，必须与现有分类完全一致。改成收入 / 支出另一侧的分类时，"
            "请同时给出 type。"
        ),
    },
    "accountName": {
        "type": "string",
        "description": "新的账户名，必须与现有账户完全一致。不能用来清空账户。",
    },
    "note": {
        "type": "string",
        # 2000 = transactions.note 的列宽，同 ``_TX_ITEM_PROPERTIES.note``。
        "maxLength": 2000,
        "description": "新的备注；清空备注就传空字符串。",
    },
    "bizDate": _BIZ_DATE_SCHEMA,
}


def _tx_locator_schema() -> dict[str, Any]:
    """定位条件对象；``minProperties: 1`` 钉住「至少给一个条件」。"""
    return {**_object_schema(_TX_LOCATOR_PROPERTIES), "minProperties": 1}


def _tx_changes_schema() -> dict[str, Any]:
    """改动对象；``minProperties: 1`` 钉住「至少改一样」。"""
    return {**_object_schema(_TX_CHANGE_PROPERTIES), "minProperties": 1}


# ---------------------------------------------------------------------------
# 内部工具函数
# ---------------------------------------------------------------------------


def _format_yuan(cents: int) -> str:
    """分 → 「12.50」形式的元（确定性格式化，模型不参与金额呈现）。"""
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}"


def _format_signed_yuan(cents: int) -> str:
    """分 → 「¥12.50」/「-¥12.50」——余额等**可为负**的金额（`_format_yuan` 的
    负号在数字前，直接加 ¥ 会得到「¥-12.50」）。"""
    return f"{'-' if cents < 0 else ''}¥{_format_yuan(abs(cents))}"


def _period_range(period: str) -> tuple[date, date]:
    """``YYYY-MM`` → ``[月首日, 次月首日)``。非法月份抛 ``ValueError``。"""
    year_str, month_str = period.split("-")
    year, month = int(year_str), int(month_str)
    start = date(year, month, 1)  # 月份越界时抛 ValueError
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, end


def _resolve_period(raw: str) -> tuple[date, date] | None:
    try:
        return _period_range(raw)
    except (ValueError, AttributeError):
        return None


def _parse_biz_date(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


def _resolve_category(ctx: ToolContext, kind: str, name: str) -> str | ToolOutcome:
    """（收支类型, 分类名）→ id。解析失败返回 ``ToolOutcome.failure``。

    带上 ``kind`` 是必需的：同名的支出分类与收入分类可以共存，只按名字解析
    会让「记一笔支出·其他」落到收入分类上。
    """
    resolved = ctx.resolve_category(kind, name)
    if resolved is None:
        return ToolOutcome.failure(ERR_UNKNOWN_CATEGORY, argument=name)
    return resolved


def _resolve_account(ctx: ToolContext, name: str) -> str | ToolOutcome:
    """账户名 → id。

    ``accounts`` 没有唯一性约束，重名是可能的。**写入**必须唯一解，所以重名
    时区分出``ambiguous_account``（可行动：请用户指明）而不是笼统的「不存在」；
    读路径不走这里——它按名字取全集（``account_ids_named``）。
    """
    resolved = ctx.account_names.get(name)
    if resolved is not None:
        return resolved
    if len(ctx.account_ids_named(name)) > 1:
        return ToolOutcome.failure(ERR_AMBIGUOUS_ACCOUNT, argument=name)
    return ToolOutcome.failure(ERR_UNKNOWN_ACCOUNT, argument=name)


def _resolve_book(ctx: ToolContext, name: str | None) -> uuid.UUID | ToolOutcome:
    """账本名 → id；``name`` 为空时返回当前账本。

    账本重名时区分出 ``ambiguous_book``（可行动：请用户指明）而不是笼统的
    「不存在」——``book_list`` 会把两行同名都列出来，若这里报「名字不在清单
    中」，模型面对一份列着该名字的清单将无路可走。
    """
    if name is None:
        return ctx.book_id
    resolved = ctx.book_names.get(name)
    if resolved is not None:
        return uuid.UUID(resolved)
    if len(ctx.book_ids_named(name)) > 1:
        return ToolOutcome.failure(ERR_AMBIGUOUS_BOOK, argument=name)
    return ToolOutcome.failure(ERR_UNKNOWN_BOOK, argument=name)


def _normalize_note(raw: Any) -> str:
    return raw if isinstance(raw, str) else ""


def _resolve_savings(ctx: ToolContext, name: str) -> str | ToolOutcome:
    """存钱目标名 → id。重名时 ``ambiguous_savings``（可行动：请用户指明）。

    与 ``_resolve_account`` 同形：名字没有唯一约束，写入必须唯一解。
    """
    resolved = ctx.savings_names.get(name)
    if resolved is not None:
        return resolved
    if len(ctx.savings_ids_named(name)) > 1:
        return ToolOutcome.failure(ERR_AMBIGUOUS_SAVINGS, argument=name)
    return ToolOutcome.failure(ERR_UNKNOWN_SAVINGS, argument=name)


def _resolve_scheduled(ctx: ToolContext, name: str) -> str | ToolOutcome:
    """定时规则名 → id（**只在本账本的未归档规则里找**，见 ``ToolContext``）。"""
    resolved = ctx.scheduled_names.get(name)
    if resolved is not None:
        return resolved
    if len(ctx.scheduled_ids_named(name)) > 1:
        return ToolOutcome.failure(ERR_AMBIGUOUS_SCHEDULED, argument=name)
    return ToolOutcome.failure(ERR_UNKNOWN_SCHEDULED, argument=name)


def _resolve_persona(ctx: ToolContext, name: str) -> str | ToolOutcome:
    """角色名 → id。重名时 ``ambiguous_persona``。"""
    resolved = ctx.persona_names.get(name)
    if resolved is not None:
        return resolved
    if len(ctx.persona_ids_named(name)) > 1:
        return ToolOutcome.failure(ERR_AMBIGUOUS_PERSONA, argument=name)
    return ToolOutcome.failure(ERR_UNKNOWN_PERSONA, argument=name)


# ---------------------------------------------------------------------------
# 记账域 —— 写工具
# ---------------------------------------------------------------------------


def _build_tx_item(
    ctx: ToolContext,
    params: dict[str, Any],
) -> tuple[dict[str, Any] | None, ToolOutcome | None]:
    """把一条工具参数转成契约里的 TransactionItem。

    返回 ``(item, None)`` 或 ``(None, failure)``。分类 / 账户解析是这里唯一的
    名→id 转换点，也是模型永远拿不到 UUID 的原因。
    """
    category_id = _resolve_category(ctx, params["type"], params["categoryName"])
    if isinstance(category_id, ToolOutcome):
        return None, category_id

    item: dict[str, Any] = {
        "type": params["type"],
        "amountCents": params["amountCents"],
        "categoryId": category_id,
        "bizDate": params["bizDate"],
        "note": _normalize_note(params.get("note")),
    }
    account_name = params.get("accountName")
    if account_name:
        account_id = _resolve_account(ctx, account_name)
        if isinstance(account_id, ToolOutcome):
            return None, account_id
        item["accountId"] = account_id
    return item, None


def _tx_message(params: dict[str, Any]) -> str:
    verb = "支出" if params["type"] == "expense" else "收入"
    return f"记一笔{verb} ¥{_format_yuan(params['amountCents'])} · {params['categoryName']}"


async def _handle_transaction_add(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    item, failure = _build_tx_item(ctx, params)
    if failure is not None:
        return failure
    assert item is not None  # _build_tx_item 的两条返回路径互斥
    return ToolOutcome.proposal({
        "action": "transaction_add",
        "payload": {"transactions": [item]},
        "message": _tx_message(params),
        "warnings": [],
    })


async def _handle_transaction_add_batch(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    items: list[dict[str, Any]] = []
    for raw in params["transactions"]:
        item, failure = _build_tx_item(ctx, raw)
        if failure is not None:
            return failure
        assert item is not None
        items.append(item)

    total = sum(item["amountCents"] for item in items)
    return ToolOutcome.proposal({
        "action": "transaction_add",
        "payload": {"transactions": items},
        "message": f"记 {len(items)} 笔，合计 ¥{_format_yuan(total)}",
        "warnings": [],
    })


# ---------------------------------------------------------------------------
# 记账域 —— 读工具
# ---------------------------------------------------------------------------


def _tx_row(row: Any) -> dict[str, Any]:
    """交易行 → 模型可见视图。

    **刻意不含 id**：模型拿到 id 就会有把它写回参数的机会，而铁律是模型永不
    输出 UUID。后续轮次需要定位某笔交易时，由确定性代码按 filter 条件圈定。
    """
    return {
        "type": row.type,
        "amountCents": int(row.amount_cents),
        "categoryName": row.category_name,
        "accountName": row.account_name,
        "note": row.note,
        "bizDate": row.biz_date.isoformat(),
    }


def _tx_query(
    ctx: ToolContext,
    book_id: uuid.UUID,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    tx_type: str | None = None,
):
    """构造交易明细查询（含分类 / 账户名，供模型直接阅读）。

    不复用 ``TransactionRepository.list_by_book``：该接口只支持日期区间与分页，
    而模型需要按分类名 / 关键词 / 收支类型 / 账户名过滤，且过滤必须发生在
    SQL 里（否则分页语义错误）。
    """
    conditions = [
        Transaction.book_id == book_id,
        Transaction.deleted_at.is_(None),
    ]
    if date_from is not None:
        conditions.append(Transaction.biz_date >= date_from)
    if date_to is not None:
        conditions.append(Transaction.biz_date <= date_to)
    if tx_type is not None:
        conditions.append(Transaction.type == tx_type)

    return (
        select(
            Transaction.type,
            Transaction.amount_cents,
            Category.name.label("category_name"),
            Account.name.label("account_name"),
            Transaction.note,
            Transaction.biz_date,
        )
        .join(Category, Transaction.category_id == Category.id)
        .outerjoin(Account, Transaction.account_id == Account.id)
        .where(*conditions)
    )


def _escaped_like(keyword: str) -> str:
    """关键词 → LIKE 模式，把 ``%`` / ``_`` / ``\\`` 转义为字面量。"""
    escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def _handle_transaction_list(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    book_id = ctx.book_id
    date_from = _parse_biz_date(params["dateFrom"]) if params.get("dateFrom") else None
    date_to = _parse_biz_date(params["dateTo"]) if params.get("dateTo") else None
    limit = int(params.get("limit", 20))

    base = _tx_query(
        ctx, book_id,
        date_from=date_from, date_to=date_to, tx_type=params.get("type"),
    )
    keyword = params.get("keyword")
    if keyword:
        pattern = _escaped_like(keyword)
        base = base.where(
            Transaction.note.like(pattern, escape="\\") | Category.name.like(pattern, escape="\\"),
        )
    if params.get("accountName"):
        # 取名字匹配的**全部**账户 id（含重名）。用 ``account_names`` 那个唯一解
        # 映射会让重名账户静默变成空集，模型据此对用户断言「该账户没有支出」——
        # 事实错误，比拒绝回答糟得多。
        account_ids = ctx.account_ids_named(params["accountName"])
        # 名字一个都匹配不上 → 恒假条件：查无结果，而不是报错（空集已经是
        # 「没有这样的账户」的足够信号，再抛错误只会多花一轮）。
        base = base.where(
            Transaction.account_id.in_([uuid.UUID(aid) for aid in account_ids])
            if account_ids else false(),
        )
    if params.get("categoryName"):
        category_ids = ctx.category_ids_named(params["categoryName"])
        base = base.where(
            Transaction.category_id.in_([uuid.UUID(cid) for cid in category_ids])
            if category_ids else false(),
        )

    total = await ctx.db.scalar(
        select(func.count()).select_from(base.subquery()),
    )
    rows = await ctx.db.execute(
        base.order_by(Transaction.biz_date.desc(), Transaction.created_at.desc()).limit(limit),
    )
    items = [_tx_row(r) for r in rows.all()]
    return ToolOutcome.result({
        "transactions": items,
        "total": int(total or 0),
        "truncated": int(total or 0) > len(items),
    })


async def _handle_transaction_recent(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    limit = int(params.get("limit", 10))
    base = _tx_query(ctx, ctx.book_id)
    rows = await ctx.db.execute(
        base.order_by(Transaction.biz_date.desc(), Transaction.created_at.desc()).limit(limit),
    )
    return ToolOutcome.result({"transactions": [_tx_row(r) for r in rows.all()]})


async def _handle_transaction_summary(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    date_from = _parse_biz_date(params["dateFrom"])
    date_to = _parse_biz_date(params["dateTo"])
    if date_from is None or date_to is None:
        return ToolOutcome.failure(ERR_INVALID_ARGUMENTS, argument="dateFrom/dateTo")

    conditions = [
        Transaction.book_id == ctx.book_id,
        Transaction.deleted_at.is_(None),
        Transaction.biz_date >= date_from,
        Transaction.biz_date <= date_to,
    ]
    if params.get("type"):
        conditions.append(Transaction.type == params["type"])
    if params.get("categoryName"):
        category_ids = ctx.category_ids_named(params["categoryName"])
        conditions.append(
            Transaction.category_id.in_([uuid.UUID(cid) for cid in category_ids])
            if category_ids else false(),
        )
    if params.get("accountName"):
        account_ids = ctx.account_ids_named(params["accountName"])
        conditions.append(
            Transaction.account_id.in_([uuid.UUID(aid) for aid in account_ids])
            if account_ids else false(),
        )

    totals = (await ctx.db.execute(
        select(
            func.coalesce(
                func.sum(case((Transaction.type == TxType.INCOME, Transaction.amount_cents), else_=0)), 0,
            ),
            func.coalesce(
                func.sum(case((Transaction.type == TxType.EXPENSE, Transaction.amount_cents), else_=0)), 0,
            ),
            func.count(),
        ).where(*conditions),
    )).one()
    total_income, total_expense, count = int(totals[0]), int(totals[1]), int(totals[2])

    payload: dict[str, Any] = {
        "dateFrom": date_from.isoformat(),
        "dateTo": date_to.isoformat(),
        "totalIncomeCents": total_income,
        "totalExpenseCents": total_expense,
        "balanceCents": total_income - total_expense,
        "transactionCount": count,
    }

    group_by = params.get("groupBy")
    if group_by:
        payload["groups"] = await _summary_groups(ctx, conditions, group_by)
    return ToolOutcome.result(payload)


async def _summary_groups(
    ctx: ToolContext,
    conditions: list[Any],
    group_by: str,
) -> list[dict[str, Any]]:
    """按维度分组求和。

    分组键一律带上收支类型——「按分类」若不分收支，餐饮的支出和退款收入会被
    加成一个没有意义的数。每个分组项自带 ``type``，调用方无需猜测口径。

    ``cast(biz_date AS VARCHAR)`` 而非 ``substr(biz_date, ...)``：后者在
    PostgreSQL 上没有 date 重载（生产库是 PG，测试库是 SQLite，两边都要能跑）。
    """
    if group_by == "category":
        key_col = Category.name
        query = (
            select(key_col, Transaction.type, func.sum(Transaction.amount_cents), func.count())
            .join(Category, Transaction.category_id == Category.id)
            .where(*conditions)
            .group_by(key_col, Transaction.type)
            .order_by(func.sum(Transaction.amount_cents).desc())
        )
    elif group_by == "day":
        query = (
            select(Transaction.biz_date, Transaction.type,
                   func.sum(Transaction.amount_cents), func.count())
            .where(*conditions)
            .group_by(Transaction.biz_date, Transaction.type)
            .order_by(Transaction.biz_date)
        )
    else:  # month
        month_col = func.substr(cast(Transaction.biz_date, String), 1, 7)
        query = (
            select(month_col, Transaction.type,
                   func.sum(Transaction.amount_cents), func.count())
            .where(*conditions)
            .group_by(month_col, Transaction.type)
            .order_by(month_col)
        )

    rows = (await ctx.db.execute(query)).all()
    return [
        {
            "key": row[0].isoformat() if isinstance(row[0], date) else str(row[0]),
            "type": row[1],
            "totalCents": int(row[2]),
            "count": int(row[3]),
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# 记账域 —— 写工具：改一笔 / 删一笔（T-M7-05b）
# ---------------------------------------------------------------------------


def _tx_verb(tx_type: str | None) -> str:
    return "收入" if tx_type == "income" else "支出"


def _describe_locator(locator: Mapping[str, Any]) -> str:
    """定位条件 → 一句人话（进提案 message，给用户在确认卡上看）。"""
    parts: list[str] = []
    if locator.get("bizDate"):
        parts.append(str(locator["bizDate"]))
    if locator.get("amountCents") is not None:
        parts.append(f"¥{_format_yuan(int(locator['amountCents']))}")
    if locator.get("categoryName"):
        parts.append(str(locator["categoryName"]))
    if locator.get("accountName"):
        parts.append(str(locator["accountName"]))
    if locator.get("keyword"):
        parts.append(f"含「{locator['keyword']}」")
    if locator.get("type"):
        parts.append(_tx_verb(locator["type"]))
    return " ".join(parts) or "未加条件"


def _describe_changes(changes: Mapping[str, Any]) -> str:
    parts: list[str] = []
    if changes.get("type"):
        parts.append(f"类型={_tx_verb(changes['type'])}")
    if changes.get("amountCents") is not None:
        parts.append(f"金额=¥{_format_yuan(int(changes['amountCents']))}")
    if changes.get("categoryName"):
        parts.append(f"分类={changes['categoryName']}")
    if changes.get("accountName"):
        parts.append(f"账户={changes['accountName']}")
    if changes.get("bizDate"):
        parts.append(f"日期={changes['bizDate']}")
    if "note" in changes:
        parts.append(f"备注={changes['note'] or '（清空）'}")
    return "、".join(parts) or "无改动"


async def _resolve_transaction_target(
    ctx: ToolContext,
    locator: Mapping[str, Any],
) -> tuple[Any, ToolOutcome | None]:
    """定位条件 → 唯一一笔交易行 ``(id, version, type)``。

    读工具刻意不回传 id（铁律 1），所以「改 / 删哪一笔」只能靠描述来指认；把
    描述翻成 id 是本函数的事——**这是写路径上唯一产生交易 id 的地方**。

    匹配到 0 笔 → ``unknown_transaction``；匹配到多笔 → ``ambiguous_transaction``
    （可行动：补充条件或请用户指明）。**绝不猜**：猜错就是改错账，而账目被悄悄
    改掉比「改不动」糟得多。取 2 行即足以判定「唯一」，不做全量物化。
    """
    conditions = [
        Transaction.book_id == ctx.book_id,
        Transaction.deleted_at.is_(None),
    ]
    stmt = select(Transaction.id, Transaction.version, Transaction.type).where(*conditions)

    if locator.get("bizDate"):
        parsed = _parse_biz_date(str(locator["bizDate"]))
        if parsed is None:
            return None, ToolOutcome.failure(ERR_INVALID_ARGUMENTS, argument="locator.bizDate")
        stmt = stmt.where(Transaction.biz_date == parsed)
    if locator.get("amountCents") is not None:
        stmt = stmt.where(Transaction.amount_cents == int(locator["amountCents"]))
    if locator.get("type"):
        stmt = stmt.where(Transaction.type == locator["type"])
    if locator.get("categoryName"):
        # 同名分类取全集（含重名），与读工具的过滤口径一致。
        ids = ctx.category_ids_named(str(locator["categoryName"]))
        stmt = stmt.where(
            Transaction.category_id.in_([uuid.UUID(c) for c in ids]) if ids else false(),
        )
    if locator.get("accountName"):
        ids = ctx.account_ids_named(str(locator["accountName"]))
        stmt = stmt.where(
            Transaction.account_id.in_([uuid.UUID(a) for a in ids]) if ids else false(),
        )
    if locator.get("keyword"):
        pattern = _escaped_like(str(locator["keyword"]))
        stmt = stmt.join(Category, Transaction.category_id == Category.id).where(
            Transaction.note.like(pattern, escape="\\")
            | Category.name.like(pattern, escape="\\"),
        )

    rows = (
        await ctx.db.execute(
            stmt.order_by(
                Transaction.biz_date.desc(), Transaction.created_at.desc(),
            ).limit(2),
        )
    ).all()
    if not rows:
        return None, ToolOutcome.failure(ERR_UNKNOWN_TRANSACTION, argument="locator")
    if len(rows) > 1:
        return None, ToolOutcome.failure(ERR_AMBIGUOUS_TRANSACTION, argument="locator")
    return rows[0], None


def _build_tx_changes(
    ctx: ToolContext,
    changes: Mapping[str, Any],
    *,
    current_type: str,
) -> tuple[dict[str, Any], ToolOutcome | None]:
    """改动参数 → 契约 payload 里那几个字段（名 → id 在这里发生）。

    ``current_type`` 是目标交易当前的收支类型：分类解析要带 kind，而改 ``type``
    时分类必须按**新的** kind 解析——否则「把支出改成收入」会落到支出侧的同名
    分类上（数据库对分类的唯一约束含 kind，两侧同名可以共存）。
    """
    payload: dict[str, Any] = {}
    if changes.get("type"):
        payload["type"] = changes["type"]
    if changes.get("amountCents") is not None:
        payload["amountCents"] = int(changes["amountCents"])
    if changes.get("bizDate"):
        payload["bizDate"] = changes["bizDate"]
    if "note" in changes:
        payload["note"] = _normalize_note(changes["note"])

    kind = str(changes.get("type") or current_type)
    if changes.get("categoryName"):
        category_id = _resolve_category(ctx, kind, str(changes["categoryName"]))
        if isinstance(category_id, ToolOutcome):
            return {}, category_id
        payload["categoryId"] = category_id
    if changes.get("accountName"):
        account_id = _resolve_account(ctx, str(changes["accountName"]))
        if isinstance(account_id, ToolOutcome):
            return {}, account_id
        payload["accountId"] = account_id
    return payload, None


async def _handle_transaction_update(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    locator = params["locator"]
    changes = params["changes"]
    row, failure = await _resolve_transaction_target(ctx, locator)
    if failure is not None:
        return failure
    tx_id, version, current_type = row

    payload, failure = _build_tx_changes(ctx, changes, current_type=str(current_type))
    if failure is not None:
        return failure

    return ToolOutcome.proposal({
        "action": "transaction_update",
        # ``version`` 是**服务端读到的当前版本**（乐观并发令牌，不是模型输出）：
        # 提案挂起期间目标被改过时，执行会以 409 拒绝，而不是把那次改动覆盖掉。
        "payload": {"transactionId": str(tx_id), "version": int(version), **payload},
        "message": (
            f"修改一笔{_tx_verb(current_type)}记录（{_describe_locator(locator)}）："
            f"{_describe_changes(changes)}"
        ),
        "warnings": [],
    })


async def _handle_transaction_delete(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    locator = params["locator"]
    row, failure = await _resolve_transaction_target(ctx, locator)
    if failure is not None:
        return failure
    tx_id, _version, current_type = row

    return ToolOutcome.proposal({
        "action": "transaction_delete",
        # 不带 version：删除按**对象身份**（id）生效，与 REST 的 DELETE 同口径
        # （``delete_transaction`` 没有版本参数）。定位条件在提案产出时已经把
        # 「哪一笔」定死了。
        "payload": {"transactionId": str(tx_id)},
        "message": f"删除一笔{_tx_verb(current_type)}记录（{_describe_locator(locator)}）",
        "warnings": [],
    })


# ---------------------------------------------------------------------------
# 预算域 —— 全部为读（P1）
# ---------------------------------------------------------------------------


async def _budget_rows(
    ctx: ToolContext,
    period: str,
    *,
    category_ids: Sequence[uuid.UUID] | None = None,
) -> list[Budget]:
    """某月的月度预算行。

    归档分类的预算被剔除：``budget_status`` 对归档分类返回 ``unknown_category``，
    列表若仍显示它，模型就会「看得见却查不到」，也无法向用户解释那一条是什么。
    """
    conditions = [
        Budget.book_id == ctx.book_id,
        Budget.budget_type == BudgetType.MONTHLY,
        Budget.period == period,
        Budget.deleted_at.is_(None),
    ]
    if category_ids is not None:
        conditions.append(Budget.category_id.in_(category_ids))
    # 确定序：``budget_status`` / ``budget_check`` 命中多条时取 ``rows[0]``
    # （支出侧与收入侧的同名分类各挂一条预算时），无序会让「给哪条」随数据库
    # 行序漂移。
    rows = await ctx.db.execute(
        select(Budget).where(*conditions).order_by(Budget.category_id, Budget.id),
    )
    return [
        budget for budget in rows.scalars().all()
        if budget.category_id is None or ctx.category_name_of(budget.category_id) is not None
    ]


async def _spent_by_category(
    ctx: ToolContext,
    start: date,
    end: date,
) -> tuple[int, dict[uuid.UUID, int]]:
    """期间内的支出合计与按分类的支出合计。

    口径与 ``fact_pack._query_budget`` 一致：只统计未删除的支出。
    """
    rows = await ctx.db.execute(
        select(Transaction.category_id, func.sum(Transaction.amount_cents))
        .where(
            Transaction.book_id == ctx.book_id,
            Transaction.deleted_at.is_(None),
            Transaction.type == TxType.EXPENSE,
            Transaction.biz_date >= start,
            Transaction.biz_date < end,
        )
        .group_by(Transaction.category_id),
    )
    per_category: dict[uuid.UUID, int] = {}
    total = 0
    for category_id, amount in rows.all():
        amount_int = int(amount)
        per_category[category_id] = amount_int
        total += amount_int
    return total, per_category


def _category_ids_for(ctx: ToolContext, name: str) -> list[uuid.UUID] | ToolOutcome:
    """分类名 → 一组 id。解析失败返回 ``ToolOutcome.failure``。

    返回列表而非单值：同名跨收支类型时预算查询要看全部同名的分类。
    """
    ids = ctx.category_ids_named(name)
    if not ids:
        return ToolOutcome.failure(ERR_UNKNOWN_CATEGORY, argument=name)
    return [uuid.UUID(cid) for cid in ids]


async def _budget_status_payload(
    ctx: ToolContext,
    period: str,
) -> list[dict[str, Any]]:
    start, end = _period_range(period)
    rows = await _budget_rows(ctx, period)
    total_spent, spent_by_category = await _spent_by_category(ctx, start, end)

    items: list[dict[str, Any]] = []
    for budget in rows:
        # 总预算（category_id 为空）的「已用」是全账本支出；分类预算是该分类
        # 的支出。两类不在同一层面，所以由 isOverall 标明，不做隐式混合。
        is_overall = budget.category_id is None
        spent = total_spent if is_overall else spent_by_category.get(budget.category_id, 0)
        items.append({
            "isOverall": is_overall,
            "categoryName": None if is_overall else ctx.category_name_of(budget.category_id),
            "period": budget.period,
            "budgetCents": budget.amount_cents,
            "spentCents": spent,
            "remainingCents": budget.amount_cents - spent,
            "pctUsed": round(spent / budget.amount_cents * 100, 1) if budget.amount_cents else 0.0,
            "exceeded": spent > budget.amount_cents,
        })
    return items


async def _handle_budget_list(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    period = params["period"]
    if _resolve_period(period) is None:
        return ToolOutcome.failure(ERR_INVALID_PERIOD, argument=period)
    # 刻意不给「各项之和」：总预算与分类预算并存时相加会重复计数，而那个数
    # 会与 budget_summary 的口径打架。逐项本来就已含全部信息。
    return ToolOutcome.result({
        "period": period,
        "items": await _budget_status_payload(ctx, period),
    })


async def _handle_budget_status(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    period = params["period"]
    if _resolve_period(period) is None:
        return ToolOutcome.failure(ERR_INVALID_PERIOD, argument=period)
    category_name = params["categoryName"]
    category_ids = _category_ids_for(ctx, category_name)
    if isinstance(category_ids, ToolOutcome):
        return category_ids

    start, end = _period_range(period)
    rows = await _budget_rows(ctx, period, category_ids=category_ids)
    if not rows:
        return ToolOutcome.result({
            "categoryName": category_name,
            "period": period,
            "budgetCents": None,
            "hasBudget": False,
        })
    _, spent_by_category = await _spent_by_category(ctx, start, end)
    budget = rows[0]
    spent = spent_by_category.get(budget.category_id, 0)
    return ToolOutcome.result({
        "categoryName": category_name,
        "period": period,
        "hasBudget": True,
        "budgetCents": budget.amount_cents,
        "spentCents": spent,
        "remainingCents": budget.amount_cents - spent,
        "pctUsed": round(spent / budget.amount_cents * 100, 1) if budget.amount_cents else 0.0,
        "exceeded": spent > budget.amount_cents,
    })


async def _handle_budget_check(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    period = params["period"]
    if _resolve_period(period) is None:
        return ToolOutcome.failure(ERR_INVALID_PERIOD, argument=period)
    category_name = params["categoryName"]
    category_ids = _category_ids_for(ctx, category_name)
    if isinstance(category_ids, ToolOutcome):
        return category_ids

    amount_cents = int(params["amountCents"])
    start, end = _period_range(period)
    rows = await _budget_rows(ctx, period, category_ids=category_ids)
    if not rows:
        return ToolOutcome.result({
            "categoryName": category_name,
            "period": period,
            "hasBudget": False,
            "willExceed": False,
        })

    _, spent_by_category = await _spent_by_category(ctx, start, end)
    budget = rows[0]
    spent = spent_by_category.get(budget.category_id, 0)
    after = spent + amount_cents
    return ToolOutcome.result({
        "categoryName": category_name,
        "period": period,
        "hasBudget": True,
        "budgetCents": budget.amount_cents,
        "spentCents": spent,
        "remainingCents": budget.amount_cents - spent,
        "afterCents": after,
        "willExceed": after > budget.amount_cents,
    })


async def _handle_budget_summary(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    period = params["period"]
    if _resolve_period(period) is None:
        return ToolOutcome.failure(ERR_INVALID_PERIOD, argument=period)

    start, end = _period_range(period)
    rows = await _budget_rows(ctx, period)
    total_spent, spent_by_category = await _spent_by_category(ctx, start, end)

    # 总预算与分类预算是两个层面：前者覆盖全部支出，后者只覆盖本分类。把两者
    # 的额度或已用相加会重复计数（一条餐饮支出既进总预算的已用、又进餐饮预算
    # 的已用），所以分开报，由模型按用户问的是哪一层来取。
    overall = next((b for b in rows if b.category_id is None), None)
    per_category = [b for b in rows if b.category_id is not None]

    exceeded_categories: list[str] = []
    category_budget_total = 0
    category_spent_total = 0
    for budget in per_category:
        spent = spent_by_category.get(budget.category_id, 0)
        category_budget_total += budget.amount_cents
        category_spent_total += spent
        if spent > budget.amount_cents:
            exceeded_categories.append(ctx.category_name_of(budget.category_id) or "未命名分类")

    overall_exceeded = overall is not None and total_spent > overall.amount_cents
    return ToolOutcome.result({
        "period": period,
        "overall": None if overall is None else {
            "budgetCents": overall.amount_cents,
            "spentCents": total_spent,
            "remainingCents": overall.amount_cents - total_spent,
        },
        "categories": {
            "count": len(per_category),
            "budgetCents": category_budget_total,
            "spentCents": category_spent_total,
            "remainingCents": category_budget_total - category_spent_total,
        },
        "exceededCount": len(exceeded_categories) + (1 if overall_exceeded else 0),
        "exceededCategories": (["总预算"] if overall_exceeded else []) + exceeded_categories,
    })


async def _handle_budget_overspent(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    period = params["period"]
    if _resolve_period(period) is None:
        return ToolOutcome.failure(ERR_INVALID_PERIOD, argument=period)

    items = await _budget_status_payload(ctx, period)
    overspent = [i for i in items if i["exceeded"]]
    return ToolOutcome.result({
        "period": period,
        "overspent": overspent,
        "overspentCount": len(overspent),
    })


# ---------------------------------------------------------------------------
# 账户账本域 —— 全部为读（P1）
# ---------------------------------------------------------------------------


async def _handle_account_list(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    rows = await ctx.db.execute(
        select(Account.name, Account.icon).where(
            Account.owner_id == ctx.user_id,
            Account.archived_at.is_(None),
        ).order_by(Account.created_at),
    )
    accounts = [{"name": name, "icon": icon} for name, icon in rows.all()]
    return ToolOutcome.result({"accounts": accounts, "total": len(accounts)})


async def _handle_account_balances(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    date_from = _parse_biz_date(params["dateFrom"]) if params.get("dateFrom") else None
    date_to = _parse_biz_date(params["dateTo"]) if params.get("dateTo") else None

    # 期间/账本限定只约束**流水聚合**，故全部落在 JOIN 的 ON 上（外连接）：
    # 没有任何流水的账户也要出现——它可能有用户手动校准过的余额，用户问
    # 「我这张卡有多少钱」时若它不在结果里，模型只能回「没有流水」，与账户页
    # 显示的余额自相矛盾（REQ-69 / 决策 #3 要消掉的就是这一类两个答案）。
    tx_conditions = [
        # 关联条件本身也在 ON 上：外连接后它不再由 ``join(Account, ...)`` 提供，
        # 漏掉它就退化成「每个账户都汇总全部流水」的笛卡尔积。
        Transaction.account_id == Account.id,
        Transaction.book_id == ctx.book_id,
        Transaction.deleted_at.is_(None),
        Transaction.account_id.is_not(None),
    ]
    if date_from is not None:
        tx_conditions.append(Transaction.biz_date >= date_from)
    if date_to is not None:
        tx_conditions.append(Transaction.biz_date <= date_to)

    rows = await ctx.db.execute(
        select(
            Account.id,
            Account.name,
            Account.balance_cents,
            func.coalesce(
                func.sum(case((Transaction.type == TxType.INCOME, Transaction.amount_cents), else_=0)), 0,
            ),
            func.coalesce(
                func.sum(case((Transaction.type == TxType.EXPENSE, Transaction.amount_cents), else_=0)), 0,
            ),
        )
        .select_from(Account)
        .join(Transaction, and_(*tx_conditions), isouter=True)
        # 账户是用户级实体（流水才是账本级），列出账户时这一条过滤必须自己带——
        # 原查询靠 JOIN 流水间接限定归属，改成外连接后 JOIN 不再起这个作用。
        .where(
            Account.owner_id == ctx.user_id,
            # 归档账户与 ``account_list`` / 名字映射同口径剔除：它不在清单里、也
            # 解析不到，若这里仍列出来，模型会看到一个用户根本记不进去的账户名
            # （「看得见却记不了」），与分类/预算侧已修过的同一类死路。
            Account.archived_at.is_(None),
        )
        # 按 id 而非 name 分组：账户没有唯一性约束，两个同名账户按名字分组会把
        # 它们的金额合成一个数——那是错的，不是「简洁」。
        .group_by(Account.id, Account.name, Account.balance_cents)
        .order_by(Account.name, Account.id),
    )
    balances = []
    for _account_id, name, stored_cents, income, expense in rows.all():
        income_int, expense_int = int(income), int(expense)
        balances.append({
            "name": name,
            "incomeCents": income_int,
            "expenseCents": expense_int,
            "netCents": income_int - expense_int,
            # REQ-69（决策 #3）：存量余额——与 netCents 口径不同，二者不等是正常的
            "balanceCents": int(stored_cents or 0),
        })
    return ToolOutcome.result({
        "accounts": balances,
        "note": (
            "列出的账户是所有未归档账户。incomeCents/expenseCents/netCents 是"
            "所选期间的流水（没有流水的账户为 0）；balanceCents 是账户**当前余额**，"
            "由用户手动设置或记账联动维护，**不随期间变化**。"
            "两者不相等是正常的：差额来自手动校准与期间之外的流水。"
            "回答「账户有多少钱」用 balanceCents；回答「这段时间花了多少」用 netCents。"
        ),
    })


async def _handle_book_list(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    rows = await ctx.db.execute(
        select(Book.id, Book.name, Book.icon).where(
            Book.owner_id == ctx.user_id,
            Book.deleted_at.is_(None),
        ).order_by(Book.created_at),
    )
    books = [
        {"name": name, "icon": icon, "current": str(book_id) == str(ctx.book_id)}
        for book_id, name, icon in rows.all()
    ]
    return ToolOutcome.result({"books": books, "total": len(books)})


async def _handle_book_detail(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    book_id = _resolve_book(ctx, params.get("bookName"))
    if isinstance(book_id, ToolOutcome):
        return book_id

    book = await ctx.db.scalar(
        select(Book).where(
            Book.id == book_id,
            Book.owner_id == ctx.user_id,
            Book.deleted_at.is_(None),
        ),
    )
    if book is None:
        return ToolOutcome.failure(ERR_UNKNOWN_BOOK, argument=params.get("bookName"))

    category_count = await ctx.db.scalar(
        select(func.count()).select_from(Category).where(
            Category.owner_id == ctx.user_id,
            (Category.book_id == book_id) | (Category.book_id.is_(None)),
            Category.archived_at.is_(None),
        ),
    )
    tx_stats = (await ctx.db.execute(
        select(func.count(), func.min(Transaction.biz_date), func.max(Transaction.biz_date))
        .where(Transaction.book_id == book_id, Transaction.deleted_at.is_(None)),
    )).one()

    return ToolOutcome.result({
        "name": book.name,
        "icon": book.icon,
        "current": str(book_id) == str(ctx.book_id),
        "categoryCount": int(category_count or 0),
        "transactionCount": int(tx_stats[0] or 0),
        "firstBizDate": tx_stats[1].isoformat() if tx_stats[1] else None,
        "lastBizDate": tx_stats[2].isoformat() if tx_stats[2] else None,
    })


async def _handle_category_list(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    book_id = _resolve_book(ctx, params.get("bookName"))
    if isinstance(book_id, ToolOutcome):
        return book_id

    rows = await ctx.db.execute(
        select(Category.id, Category.name, Category.kind, Category.parent_id, Category.icon).where(
            Category.owner_id == ctx.user_id,
            (Category.book_id == book_id) | (Category.book_id.is_(None)),
            Category.archived_at.is_(None),
        ).order_by(Category.kind, Category.created_at),
    )
    raw = rows.all()
    id_to_name = {str(cid): name for cid, name, _kind, _parent, _icon in raw}
    categories = [
        {
            "name": name,
            "kind": kind,
            "parentName": id_to_name.get(str(parent_id)) if parent_id else None,
            # REQ-73（T19-2，ADR-014）：图标随读结果出，让模型能参照现有风格
            # 挑一个（``None`` = 客户端按名字映射默认 emoji，不是「没有图标」）。
            "icon": icon,
        }
        for cid, name, kind, parent_id, icon in raw
    ]
    return ToolOutcome.result({"categories": categories, "total": len(categories)})


# ---------------------------------------------------------------------------
# 分类域 —— 写工具（T-M7-05b）
# ---------------------------------------------------------------------------


async def _current_version(ctx: ToolContext, model: Any, obj_id: uuid.UUID) -> int | None:
    """读某个对象的**当前版本**（乐观并发令牌）。

    ``version`` 是服务端读出来的整数，不是模型输出——铁律 1 管的是 id，但治理
    字段同理：任何决定写入行为的量都不该由模型给。对象不存在 / 不属于本用户时
    返回 ``None``（调用方按「定位失败」处理）。
    """
    return await ctx.db.scalar(
        select(model.version).where(model.id == obj_id, model.owner_id == ctx.user_id),
    )


async def _handle_category_create(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    name = str(params["name"])
    kind = str(params["kind"])
    book_id = _resolve_book(ctx, params.get("bookName"))
    if isinstance(book_id, ToolOutcome):
        return book_id

    parent_id: str | None = None
    parent_name = params.get("parentName")
    if parent_name:
        resolved = _resolve_category(ctx, kind, str(parent_name))
        if isinstance(resolved, ToolOutcome):
            return resolved
        parent_id = resolved

    payload: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "bookId": str(book_id),
        "parentId": parent_id,
    }
    # REQ-73（T19-2）：用户没提图标就不发这个键（与 account_create 同）。
    if params.get("icon"):
        payload["icon"] = str(params["icon"])

    return ToolOutcome.proposal({
        "action": "category_create",
        "payload": payload,
        "message": (
            f"新建{_tx_verb(kind)}分类「{name}」"
            + (f"，放在「{parent_name}」下" if parent_name else "")
        ),
        "warnings": [],
    })


async def _handle_category_update(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    kind = str(params["kind"])
    resolved = _resolve_category(ctx, kind, str(params["categoryName"]))
    if isinstance(resolved, ToolOutcome):
        return resolved

    payload: dict[str, Any] = {"categoryId": resolved}
    version = await _current_version(ctx, Category, uuid.UUID(resolved))
    if version is None:
        return ToolOutcome.failure(ERR_UNKNOWN_CATEGORY, argument=params["categoryName"])
    payload["version"] = int(version)

    notes: list[str] = []
    if params.get("newName"):
        payload["name"] = str(params["newName"])
        notes.append(f"改名为「{params['newName']}」")
    # REQ-73（T19-2）：只换图标也是一种改动（与 account_update / book_update 同）。
    if params.get("icon"):
        payload["icon"] = str(params["icon"])
        notes.append("更换图标")
    if params.get("parentName"):
        parent = _resolve_category(ctx, kind, str(params["parentName"]))
        if isinstance(parent, ToolOutcome):
            return parent
        payload["parentId"] = parent
        notes.append(f"移到「{params['parentName']}」下")
    if len(payload) == 2:  # 只有 categoryId + version → 没有任何改动
        return ToolOutcome.failure(ERR_INVALID_ARGUMENTS, argument="changes")

    return ToolOutcome.proposal({
        "action": "category_update",
        "payload": payload,
        "message": f"修改分类「{params['categoryName']}」：{'、'.join(notes)}",
        "warnings": [],
    })


async def _handle_category_archive(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    kind = str(params["kind"])
    resolved = _resolve_category(ctx, kind, str(params["categoryName"]))
    if isinstance(resolved, ToolOutcome):
        return resolved

    return ToolOutcome.proposal({
        "action": "category_archive",
        # 不带 version：归档按**对象身份**（id）生效，与 REST 的 DELETE 同口径
        # （``archive_category`` 没有版本参数）。定位在提案产出时已经定死。
        "payload": {"categoryId": resolved},
        "message": f"归档{_tx_verb(kind)}分类「{params['categoryName']}」（不再出现在分类清单里）",
        # 归档会让**已有交易**的分类在清单上消失——用户可能没料到这一步。
        "warnings": ["归档后这个分类不再出现在分类清单中，已记录的交易仍保留原分类。"],
    })


# ---------------------------------------------------------------------------
# 账户域 —— 写工具（T-M7-05b）
# ---------------------------------------------------------------------------


async def _handle_account_create(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    name = str(params["name"])
    payload: dict[str, Any] = {"name": name}
    if params.get("icon"):
        payload["icon"] = str(params["icon"])
    # REQ-69（T18-6）：分类与余额在**创建时**就能给（用户常说「建一个储蓄卡账户，
    # 里面有五千」；只给 update 会逼模型分两步走）。
    detail: list[str] = []
    if params.get("accountType"):
        payload["accountType"] = str(params["accountType"])
        detail.append(str(params["accountType"]))
    if params.get("balanceCents") is not None:
        payload["balanceCents"] = int(params["balanceCents"])
        detail.append(f"余额 {_format_signed_yuan(int(params['balanceCents']))}")
    return ToolOutcome.proposal({
        "action": "account_create",
        "payload": payload,
        "message": f"新建账户「{name}」" + (f"（{'，'.join(detail)}）" if detail else ""),
        "warnings": [],
    })


async def _handle_account_update(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    resolved = _resolve_account(ctx, str(params["accountName"]))
    if isinstance(resolved, ToolOutcome):
        return resolved
    version = await _current_version(ctx, Account, uuid.UUID(resolved))
    if version is None:
        return ToolOutcome.failure(ERR_UNKNOWN_ACCOUNT, argument=params["accountName"])

    payload: dict[str, Any] = {"accountId": resolved, "version": int(version)}
    notes: list[str] = []
    if params.get("newName"):
        payload["name"] = str(params["newName"])
        notes.append(f"改名为「{params['newName']}」")
    if params.get("icon"):
        payload["icon"] = str(params["icon"])
        notes.append("更换图标")
    # REQ-69（T18-6）：放开旧口径写死的「改不动账目余额、只改名字与图标」。
    # 余额传的是**绝对值**（执行体按 None=不修改 的局部合并语义写入）。
    if params.get("accountType"):
        payload["accountType"] = str(params["accountType"])
        notes.append(f"分类改为「{params['accountType']}」")
    if params.get("balanceCents") is not None:
        payload["balanceCents"] = int(params["balanceCents"])
        notes.append(f"余额改为 {_format_signed_yuan(int(params['balanceCents']))}")
    if len(payload) == 2:
        return ToolOutcome.failure(ERR_INVALID_ARGUMENTS, argument="changes")

    return ToolOutcome.proposal({
        "action": "account_update",
        "payload": payload,
        "message": f"修改账户「{params['accountName']}」：{'、'.join(notes)}",
        "warnings": [],
    })


async def _handle_account_archive(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    resolved = _resolve_account(ctx, str(params["accountName"]))
    if isinstance(resolved, ToolOutcome):
        return resolved

    return ToolOutcome.proposal({
        "action": "account_archive",
        "payload": {"accountId": resolved},
        "message": f"归档账户「{params['accountName']}」",
        "warnings": ["归档后这个账户不再出现在账户清单中，已记录的交易仍保留原账户。"],
    })


# ---------------------------------------------------------------------------
# 账本域 —— 写工具（T-M7-05b）
# ---------------------------------------------------------------------------


async def _handle_book_create(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    name = str(params["name"])
    payload: dict[str, Any] = {"name": name}
    if params.get("icon"):
        payload["icon"] = str(params["icon"])
    return ToolOutcome.proposal({
        "action": "book_create",
        "payload": payload,
        "message": f"新建账本「{name}」",
        "warnings": [],
    })


async def _handle_book_update(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    resolved = _resolve_book(ctx, params.get("bookName"))
    if isinstance(resolved, ToolOutcome):
        return resolved
    version = await _current_version(ctx, Book, resolved)
    if version is None:
        return ToolOutcome.failure(ERR_UNKNOWN_BOOK, argument=params.get("bookName"))

    payload: dict[str, Any] = {"bookId": str(resolved), "version": int(version)}
    notes: list[str] = []
    if params.get("newName"):
        payload["name"] = str(params["newName"])
        notes.append(f"改名为「{params['newName']}」")
    if params.get("icon"):
        payload["icon"] = str(params["icon"])
        notes.append("更换图标")
    if len(payload) == 2:
        return ToolOutcome.failure(ERR_INVALID_ARGUMENTS, argument="changes")

    return ToolOutcome.proposal({
        "action": "book_update",
        "payload": payload,
        "message": f"修改账本「{params.get('bookName') or '当前账本'}」：{'、'.join(notes)}",
        "warnings": [],
    })


async def _handle_book_delete(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    resolved = _resolve_book(ctx, params.get("bookName"))
    if isinstance(resolved, ToolOutcome):
        return resolved

    name = params.get("bookName") or "当前账本"
    warnings: list[str] = []
    if str(resolved) == str(ctx.book_id):
        # 删掉当前账本会让这一轮的上下文失效（下一轮 book_id 查不到）。工具不
        # 替用户做这个决定，但要**明确告诉他**——warnings 非空还会把置信度降档，
        # 于是单步也强制出确认卡（02e 的策略），不会静默执行。
        warnings.append("这是当前正在使用的账本，删除后需要切换到别的账本才能继续记账。")

    return ToolOutcome.proposal({
        "action": "book_delete",
        "payload": {"bookId": str(resolved)},
        "message": f"删除账本「{name}」",
        "warnings": warnings,
    })


# ---------------------------------------------------------------------------
# meta 域 —— 写工具（T-M7-05b；T-M7-05j：执行位置从设备迁回服务端）
# ---------------------------------------------------------------------------


async def _handle_meta_set(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    """写一个偏好键（服务端执行；T-M7-05j 上云后 AI 改默认项 / 用户画像真的落库）。

    05b 时期这两个键（默认账本 / 默认账户）存在设备本地的 ``sync_meta`` 里，桥
    命令 ``meta.set`` 明写「不上行」，所以 ``meta_set`` 是 ``DEVICE_EXECUTED_OPS``
    的一员——提案在服务端只挂起、由客户端在设备上执行。05j 把偏好键送上云后，
    服务端有了 ``user_preferences`` 表作为这些键的云端权威，``meta_set`` 改走
    服务端执行路径（同 ``persona_*`` / ``budget_*`` 的 ``proposal_ops`` 范式）。

    **当前角色 / 小组件配置 / 用户画像四个键的取舍**：

    - ``default_book`` / ``default_account``（05b）：UI 的默认指针，跨设备一致
      符合直觉。AI 改默认项走这两键。
    - ``widget_config`` / ``user_profile``（05d 登记成扩展键候选）：现在它们
      上云后 AI 才改得了——这是 05j 的真目标。
    - **当前角色**仍走 ``persona_switch``（05c，单独 op，不并进本工具的 key
      枚举——理由见 ``_handle_persona_switch`` 注释）。
    - **设备自管量**（``sync_cursor`` / ``master_book_id`` / ``budget_carryover``
      / ``personas_seeded`` 等）**永远不进工具**：它们的语义只属于当前设备。
    """
    key = str(params["key"])
    if key == "default_book":
        book_id = _resolve_book(ctx, params.get("bookName"))
        if isinstance(book_id, ToolOutcome):
            return book_id
        payload: dict[str, Any] = {"key": key, "bookId": str(book_id)}
        label = params.get("bookName") or "当前账本"
        message = f"把默认账本设为「{label}」"
    elif key == "default_account":
        account_name = params.get("accountName")
        if not account_name:
            return ToolOutcome.failure(ERR_INVALID_ARGUMENTS, argument="accountName")
        account_id = _resolve_account(ctx, str(account_name))
        if isinstance(account_id, ToolOutcome):
            return account_id
        payload = {"key": key, "accountId": account_id}
        label = str(account_name)
        message = f"把默认账户设为「{label}」"
    elif key == "widget_config":
        # 小组件配置：模型直接给出 JSON 字符串（与客户端 ``MetaWhitelist`` 校验
        # 的 JSON object 形态一致）。这里只做**非空字符串**的轻校验——严格形态
        # 校验留给执行体（``proposal_ops._execute_meta_set``），与 05b 的账本 /
        # 账户 id 校验路径对齐（handler 不重复做 payload 的形状校验）。
        raw = params.get("config")
        if not isinstance(raw, str) or not raw:
            return ToolOutcome.failure(ERR_INVALID_ARGUMENTS, argument="config")
        payload = {"key": key, "value": raw}
        message = "更新小组件配置"
    elif key == "user_profile":
        # 用户画像：模型给出的 JSON 字符串（称呼 / 职业 / 节奏 / 风格等，
        # 字段由客户端 ``UserSettingsPage`` 定义；服务端同样只透传）。
        raw = params.get("profile")
        if not isinstance(raw, str) or not raw:
            return ToolOutcome.failure(ERR_INVALID_ARGUMENTS, argument="profile")
        payload = {"key": key, "value": raw}
        message = "更新用户画像"
    else:
        return ToolOutcome.failure(
            ERR_INVALID_ARGUMENTS,
            argument=f"Unsupported key: {key}",
        )

    return ToolOutcome.proposal({
        "action": "meta_set",
        "payload": payload,
        "message": message,
        "warnings": [],
    })


# ---------------------------------------------------------------------------
# 预算域 —— 写工具（T-M7-05c）
# ---------------------------------------------------------------------------


def _resolve_budget_category(ctx: ToolContext, name: str) -> str | ToolOutcome:
    """支出分类名 → id（**预算只能设在支出分类上**）。

    预算的「已用」口径是支出（``_spent_by_category`` 只统计 ``TxType.EXPENSE``），
    设在收入分类上等于给它一个永远为 0 的进度条——用户看不出来的陷阱。所以这里
    明确拒绝并说清原因，而不是「记下了但没用」。
    """
    resolved = ctx.resolve_category("expense", name)
    if resolved is not None:
        return resolved
    if ctx.resolve_category("income", name) is not None:
        return ToolOutcome.failure(ERR_BUDGET_INCOME_CATEGORY, argument=name)
    return ToolOutcome.failure(ERR_UNKNOWN_CATEGORY, argument=name)


async def _budget_row(
    ctx: ToolContext, period: str, category_id: uuid.UUID | None,
) -> Budget | None:
    """某月的**总预算**（``category_id is None``）或**某分类的预算**；没有则 ``None``。

    只认月度预算：预算工具的参数只有 ``YYYY-MM``（``_PERIOD_SCHEMA``），年度预算
    不在这条路径上——与 P1 的五个读工具同口径。
    """
    conditions = [
        Budget.book_id == ctx.book_id,
        Budget.budget_type == BudgetType.MONTHLY,
        Budget.period == period,
        Budget.deleted_at.is_(None),
        Budget.category_id.is_(None) if category_id is None else Budget.category_id == category_id,
    ]
    return await ctx.db.scalar(select(Budget).where(*conditions).order_by(Budget.id))


async def _handle_budget_set(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    period = str(params["period"])
    if _resolve_period(period) is None:
        return ToolOutcome.failure(ERR_INVALID_PERIOD, argument=period)
    amount = int(params["amountCents"])

    payload: dict[str, Any] = {"period": period, "amountCents": amount}
    existing = await _budget_row(ctx, period, None)
    note = ""
    if existing is not None:
        # 改已有的总预算 → 带上服务端读到的版本（乐观并发，与 05b 的改类 op 同口径）。
        payload["version"] = int(existing.version)
        note = f"（原来是 ¥{_format_yuan(existing.amount_cents)}）"
    return ToolOutcome.proposal({
        "action": "budget_set",
        "payload": payload,
        "message": f"把 {period} 的整体预算设为 ¥{_format_yuan(amount)}{note}",
        "warnings": [],
    })


async def _handle_budget_set_category(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    period = str(params["period"])
    if _resolve_period(period) is None:
        return ToolOutcome.failure(ERR_INVALID_PERIOD, argument=period)
    category_name = str(params["categoryName"])
    category_id = _resolve_budget_category(ctx, category_name)
    if isinstance(category_id, ToolOutcome):
        return category_id
    amount = int(params["amountCents"])

    payload: dict[str, Any] = {
        "period": period, "categoryId": category_id, "amountCents": amount,
    }
    existing = await _budget_row(ctx, period, uuid.UUID(category_id))
    note = ""
    if existing is not None:
        payload["version"] = int(existing.version)
        note = f"（原来是 ¥{_format_yuan(existing.amount_cents)}）"
    return ToolOutcome.proposal({
        "action": "budget_set_category",
        "payload": payload,
        "message": f"把「{category_name}」{period} 的预算设为 ¥{_format_yuan(amount)}{note}",
        # 覆盖已有额度会让「这个月还能花多少」当场变化，用户未必料到。
        "warnings": ([f"覆盖 {period} 已有的分类预算"] if existing is not None else []),
    })


async def _handle_budget_delete(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    period = str(params["period"])
    if _resolve_period(period) is None:
        return ToolOutcome.failure(ERR_INVALID_PERIOD, argument=period)

    category_name = params.get("categoryName")
    if category_name:
        category_id = _resolve_budget_category(ctx, str(category_name))
        if isinstance(category_id, ToolOutcome):
            return category_id
        existing = await _budget_row(ctx, period, uuid.UUID(category_id))
        if existing is None:
            # 「删一个不存在的东西」不该产出一张必然失败的确认卡。
            return ToolOutcome.failure(ERR_UNKNOWN_BUDGET, argument=str(category_name))
        payload: dict[str, Any] = {"period": period, "categoryId": category_id}
        message = f"删掉「{category_name}」{period} 的预算"
    else:
        existing = await _budget_row(ctx, period, None)
        if existing is None:
            return ToolOutcome.failure(ERR_UNKNOWN_BUDGET, argument=period)
        payload = {"period": period}
        message = f"删掉 {period} 的整体预算"

    return ToolOutcome.proposal({
        "action": "budget_delete",
        "payload": payload,
        "message": message,
        "warnings": ["删除后这个月不再有这项预算，已记录的交易不受影响。"],
    })


# ---------------------------------------------------------------------------
# 存钱域 —— 读 / 写（T-M7-05c；实体已上云，T-M7-01）
# ---------------------------------------------------------------------------


async def _handle_savings_list(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    """列出全部**未归档**的存钱目标（用户级，不分账本）。结果里不含 id。"""
    rows = await ctx.db.execute(
        select(SavingsGoal).where(
            SavingsGoal.owner_id == ctx.user_id,
            SavingsGoal.archived_at.is_(None),
        ).order_by(SavingsGoal.name, SavingsGoal.id).limit(_OBJECT_NAME_LIMIT),
    )
    items: list[dict[str, Any]] = []
    for goal in rows.scalars().all():
        items.append({
            "name": goal.name,
            "targetCents": goal.target_cents,
            "currentCents": goal.current_cents,
            # 进度与「还差多少」都在这算：铁律 2，模型不做金额算术。
            "remainingCents": max(goal.target_cents - goal.current_cents, 0),
            "progressPct": (
                round(goal.current_cents / goal.target_cents * 100, 1)
                if goal.target_cents else 0.0
            ),
            "achieved": goal.current_cents >= goal.target_cents,
            "icon": goal.icon,
            "periodValue": goal.period_value,
            "periodUnit": goal.period_unit,
        })
    return ToolOutcome.result({"savings": items, "total": len(items)})


async def _handle_savings_create(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    name = str(params["name"])
    payload: dict[str, Any] = {"name": name, "targetCents": int(params["targetCents"])}
    if params.get("icon"):
        payload["icon"] = str(params["icon"])
    period = _savings_period_payload(params)
    if isinstance(period, ToolOutcome):
        return period
    payload.update(period)

    detail = f"目标 ¥{_format_yuan(int(params['targetCents']))}"
    if payload.get("periodValue") and payload.get("periodUnit"):
        detail += f"，{payload['periodValue']} 个{'月' if payload['periodUnit'] == 'month' else '周'}内存到"
    return ToolOutcome.proposal({
        "action": "savings_create",
        "payload": payload,
        "message": f"新建存钱目标「{name}」（{detail}）",
        "warnings": [],
    })


def _savings_period_payload(params: Mapping[str, Any]) -> dict[str, Any] | ToolOutcome:
    """存款期限的两个字段 → payload 片段（**成对**：要么都给，要么都不给）。

    客户端与服务端的既有口径都是「value / unit 必须成对，值或 null 一起下发」
    （``Repository.requireValidSavingsPeriod`` / sync 的 ``_validate_savings_period``）。
    只给一半是模型把两个字段当成两个独立可选项的典型误用，这里挡在产出之前。
    """
    has_value = "periodValue" in params
    has_unit = "periodUnit" in params
    if has_value != has_unit:
        return ToolOutcome.failure(
            ERR_INVALID_ARGUMENTS, argument="periodValue/periodUnit",
        )
    if not has_value:
        return {}
    return {"periodValue": params["periodValue"], "periodUnit": params["periodUnit"]}


async def _handle_savings_update(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    resolved = _resolve_savings(ctx, str(params["savingsName"]))
    if isinstance(resolved, ToolOutcome):
        return resolved
    version = await _current_version(ctx, SavingsGoal, uuid.UUID(resolved))
    if version is None:
        return ToolOutcome.failure(ERR_UNKNOWN_SAVINGS, argument=params["savingsName"])
    if params.get("addAmountCents") is not None and params.get("currentCents") is not None:
        # 「在现有基础上加 N」与「直接改成 N」是两件事——同时给没有合理优先级，
        # 挡在产出之前（执行面还有一道同样的闸，那是防伪造 payload 的）。
        return ToolOutcome.failure(
            ERR_INVALID_ARGUMENTS, argument="addAmountCents/currentCents",
        )

    payload: dict[str, Any] = {"savingsId": resolved, "version": int(version)}
    notes: list[str] = []
    if params.get("newName"):
        payload["name"] = str(params["newName"])
        notes.append(f"改名为「{params['newName']}」")
    if params.get("targetCents") is not None:
        payload["targetCents"] = int(params["targetCents"])
        notes.append(f"目标额改为 ¥{_format_yuan(int(params['targetCents']))}")
    if params.get("icon"):
        payload["icon"] = str(params["icon"])
        notes.append("更换图标")
    if params.get("addAmountCents") is not None:
        # **存入**：金额在服务端读当前值后累加（铁律 2——模型不读旧值、不做加法，
        # 也就没有「模型算错了」和「两个人同时存、后写的覆盖先写的」两种错法）。
        payload["addAmountCents"] = int(params["addAmountCents"])
        notes.append(f"存入 ¥{_format_yuan(int(params['addAmountCents']))}")
    if params.get("currentCents") is not None:
        payload["currentCents"] = int(params["currentCents"])
        notes.append(f"已存金额直接改为 ¥{_format_yuan(int(params['currentCents']))}")
    period = _savings_period_payload(params)
    if isinstance(period, ToolOutcome):
        return period
    if period:
        payload.update(period)
        if period["periodValue"] is None:
            notes.append("去掉存款期限")
        else:
            notes.append("调整存款期限")
    if len(payload) == 2:  # 只有 savingsId + version → 没有任何改动
        return ToolOutcome.failure(ERR_INVALID_ARGUMENTS, argument="changes")

    return ToolOutcome.proposal({
        "action": "savings_update",
        "payload": payload,
        "message": f"修改存钱目标「{params['savingsName']}」：{'、'.join(notes)}",
        "warnings": [],
    })


async def _handle_savings_archive(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    resolved = _resolve_savings(ctx, str(params["savingsName"]))
    if isinstance(resolved, ToolOutcome):
        return resolved

    return ToolOutcome.proposal({
        "action": "savings_archive",
        "payload": {"savingsId": resolved},
        "message": f"归档存钱目标「{params['savingsName']}」（不再出现在存钱页）",
        "warnings": ["归档后这个目标不再出现在存钱页，已存入的进度记录也不再可见。"],
    })


# ---------------------------------------------------------------------------
# 定时域 —— 读 / 写（T-M7-05c；实体已上云，T-M7-01）
# ---------------------------------------------------------------------------

#: 规则重复方式 → 人话（进提案 message）。
_REPEAT_LABELS: dict[str, str] = {
    "daily": "每天",
    "weekly": "每周",
    "monthly": "每月",
    "yearly": "每年",
}


def _rule_schedule_text(params: Mapping[str, Any]) -> str:
    """重复方式 + 起点 → 「每月，从 2026-10-01 起」这样的片段。"""
    repeat = _REPEAT_LABELS.get(str(params.get("repeat")), str(params.get("repeat")))
    text = f"{repeat}，从 {params.get('startDate')} 起"
    if params.get("endDate"):
        text += f" 到 {params['endDate']} 止"
    return text


async def _handle_scheduled_list(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    """列出**本账本**未归档的定时记账规则（与预算 / 交易的账本收窄同口径）。"""
    rows = await ctx.db.execute(
        select(ScheduledRule).where(
            ScheduledRule.owner_id == ctx.user_id,
            ScheduledRule.book_id == ctx.book_id,
            ScheduledRule.archived_at.is_(None),
        ).order_by(ScheduledRule.name, ScheduledRule.id).limit(_OBJECT_NAME_LIMIT),
    )
    items: list[dict[str, Any]] = []
    for rule in rows.scalars().all():
        items.append({
            "name": rule.name,
            "type": rule.type,
            "amountCents": rule.amount_cents,
            "categoryName": ctx.category_name_of(rule.category_id),
            "accountName": (
                ctx.account_names_by_id.get(str(rule.account_id)) if rule.account_id else None
            ),
            "repeat": rule.repeat,
            "startDate": rule.start_date.isoformat(),
            "endDate": rule.end_date.isoformat() if rule.end_date else None,
            "runHour": rule.run_hour,
            "runMinute": rule.run_minute,
            "enabled": rule.enabled,
            "includeInBudget": rule.include_in_budget,
            "note": rule.note,
            "lastRunDate": rule.last_run_date.isoformat() if rule.last_run_date else None,
        })
    return ToolOutcome.result({"rules": items, "total": len(items)})


def _scheduled_rule_payload(
    ctx: ToolContext, params: Mapping[str, Any], *, fallback_kind: str = "expense",
) -> dict[str, Any] | ToolOutcome:
    """规则的可写字段 → payload 片段（创建与修改共用，两处的解析口径因此一致）。

    ``fallback_kind`` 是**解析分类时用的收支类型**：改类工具没带 ``type`` 时，
    用规则**当前**的类型（改分类名而不改收支类型是常规操作，缺了它会把收入规则
    的分类按支出侧解析——同名的两个分类会挂错）。
    """
    payload: dict[str, Any] = {}
    if params.get("type"):
        payload["type"] = str(params["type"])
    # 创建用 ``name``、修改用 ``newName``（改类工具不能沿用必填的 ``name`` 语义）。
    if params.get("name") or params.get("newName"):
        payload["name"] = str(params.get("name") or params.get("newName"))
    if params.get("amountCents") is not None:
        payload["amountCents"] = int(params["amountCents"])
    if params.get("categoryName"):
        # 分类按**规则的收支类型**解析：同名的支出 / 收入分类是两个分类，
        # 只按名字解析会把「每月记一笔收入」挂到支出分类上。
        kind = str(params.get("type") or fallback_kind)
        category_id = _resolve_category(ctx, kind, str(params["categoryName"]))
        if isinstance(category_id, ToolOutcome):
            return category_id
        payload["categoryId"] = category_id
    if params.get("accountName"):
        account_id = _resolve_account(ctx, str(params["accountName"]))
        if isinstance(account_id, ToolOutcome):
            return account_id
        payload["accountId"] = account_id
    if params.get("repeat"):
        payload["repeat"] = str(params["repeat"])
    if params.get("startDate"):
        payload["startDate"] = str(params["startDate"])
    if "endDate" in params:
        payload["endDate"] = params["endDate"]
    if params.get("note") is not None:
        payload["note"] = str(params["note"])
    if params.get("includeInBudget") is not None:
        payload["includeInBudget"] = bool(params["includeInBudget"])
    if params.get("enabled") is not None:
        payload["enabled"] = bool(params["enabled"])
    if params.get("runHour") is not None:
        payload["runHour"] = int(params["runHour"])
    if params.get("runMinute") is not None:
        payload["runMinute"] = int(params["runMinute"])
    return payload


async def _handle_scheduled_create(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    payload = _scheduled_rule_payload(ctx, params)
    if isinstance(payload, ToolOutcome):
        return payload
    name = str(params["name"])
    kind = _tx_verb(str(params["type"]))
    return ToolOutcome.proposal({
        "action": "scheduled_create",
        "payload": payload,
        "message": (
            f"新建定时记账规则「{name}」：{_rule_schedule_text(params)}"
            f"，{kind} ¥{_format_yuan(int(params['amountCents']))}"
            f"（分类「{params['categoryName']}」）"
        ),
        "warnings": [],
    })


async def _handle_scheduled_update(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    resolved = _resolve_scheduled(ctx, str(params["ruleName"]))
    if isinstance(resolved, ToolOutcome):
        return resolved
    current = (await ctx.db.execute(
        select(ScheduledRule.type, ScheduledRule.version).where(
            ScheduledRule.id == uuid.UUID(resolved),
            ScheduledRule.owner_id == ctx.user_id,
            ScheduledRule.archived_at.is_(None),
        ),
    )).first()
    if current is None:
        return ToolOutcome.failure(ERR_UNKNOWN_SCHEDULED, argument=params["ruleName"])
    current_type, version = current

    changes = _scheduled_rule_payload(ctx, params, fallback_kind=str(current_type))
    if isinstance(changes, ToolOutcome):
        return changes
    if not changes:
        return ToolOutcome.failure(ERR_INVALID_ARGUMENTS, argument="changes")

    payload: dict[str, Any] = {"ruleId": resolved, "version": int(version), **changes}
    notes: list[str] = []
    if "name" in changes:
        notes.append(f"改名为「{changes['name']}」")
    if "amountCents" in changes:
        notes.append(f"金额改为 ¥{_format_yuan(changes['amountCents'])}")
    if "categoryId" in changes:
        notes.append(f"分类改为「{params['categoryName']}」")
    if "accountId" in changes:
        notes.append(f"账户改为「{params['accountName']}」")
    if "repeat" in changes or "startDate" in changes or "endDate" in changes:
        notes.append("调整执行时间")
    if "runHour" in changes or "runMinute" in changes:
        notes.append("调整执行时刻")
    if changes.get("enabled") is not None:
        notes.append("启用" if changes["enabled"] else "暂停")
    if "note" in changes:
        notes.append("修改备注")
    if "includeInBudget" in changes:
        notes.append("计入预算" if changes["includeInBudget"] else "不计入预算")

    return ToolOutcome.proposal({
        "action": "scheduled_update",
        "payload": payload,
        "message": f"修改定时规则「{params['ruleName']}」：{'、'.join(notes)}",
        "warnings": [],
    })


async def _handle_scheduled_archive(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    resolved = _resolve_scheduled(ctx, str(params["ruleName"]))
    if isinstance(resolved, ToolOutcome):
        return resolved

    return ToolOutcome.proposal({
        "action": "scheduled_archive",
        "payload": {"ruleId": resolved},
        "message": f"归档定时记账规则「{params['ruleName']}」（不再自动记账）",
        "warnings": ["归档后这条规则不再自动记账，已经生成的记录仍然保留。"],
    })


# ---------------------------------------------------------------------------
# 角色域 —— 读 / 写（T-M7-05c；实体已上云，T-M7-01）
# ---------------------------------------------------------------------------


async def _handle_persona_list(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    """列出全部**未归档**的聊天角色。

    **结果里没有「当前角色」标记**：那个指针（``sync_meta.active_persona_id``）是
    设备本地的，服务端看不到（``BridgeProtocol.kt``：角色表上云、指针不上行）。
    用户问「现在用的是哪个角色」时，请如实说明要他在角色页上看，**不要猜**。
    """
    rows = await ctx.db.execute(
        select(ChatPersona).where(
            ChatPersona.owner_id == ctx.user_id,
            ChatPersona.archived_at.is_(None),
        ).order_by(ChatPersona.name, ChatPersona.id).limit(_OBJECT_NAME_LIMIT),
    )
    items = [
        {"name": p.name, "avatar": p.avatar, "persona": p.persona}
        for p in rows.scalars().all()
    ]
    return ToolOutcome.result({"personas": items, "total": len(items)})


async def _handle_persona_create(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    name = str(params["name"])
    payload: dict[str, Any] = {"name": name, "persona": _normalize_note(params.get("persona"))}
    if params.get("avatar"):
        payload["avatar"] = str(params["avatar"])
    return ToolOutcome.proposal({
        "action": "persona_create",
        "payload": payload,
        "message": f"新建聊天角色「{name}」",
        "warnings": [],
    })


async def _handle_persona_update(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    resolved = _resolve_persona(ctx, str(params["personaName"]))
    if isinstance(resolved, ToolOutcome):
        return resolved
    version = await _current_version(ctx, ChatPersona, uuid.UUID(resolved))
    if version is None:
        return ToolOutcome.failure(ERR_UNKNOWN_PERSONA, argument=params["personaName"])

    payload: dict[str, Any] = {"personaId": resolved, "version": int(version)}
    notes: list[str] = []
    if params.get("newName"):
        payload["name"] = str(params["newName"])
        notes.append(f"改名为「{params['newName']}」")
    if params.get("avatar"):
        payload["avatar"] = str(params["avatar"])
        notes.append("更换头像")
    if params.get("persona") is not None:
        payload["persona"] = str(params["persona"])
        notes.append("修改人设描述")
    if len(payload) == 2:
        return ToolOutcome.failure(ERR_INVALID_ARGUMENTS, argument="changes")

    return ToolOutcome.proposal({
        "action": "persona_update",
        "payload": payload,
        "message": f"修改聊天角色「{params['personaName']}」：{'、'.join(notes)}",
        "warnings": [],
    })


async def _handle_persona_archive(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    resolved = _resolve_persona(ctx, str(params["personaName"]))
    if isinstance(resolved, ToolOutcome):
        return resolved

    return ToolOutcome.proposal({
        "action": "persona_archive",
        "payload": {"personaId": resolved},
        "message": f"归档聊天角色「{params['personaName']}」",
        "warnings": ["归档后这个角色不再出现在角色页。"],
    })


async def _handle_persona_switch(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    """切换**当前聊天角色**。

    **执行位置是设备**：当前角色指针是 ``sync_meta`` 的 ``active_persona_id`` 键
    （本地展示元数据，桥 ``meta.set`` 不上行），服务端没有它的落点——提案只
    挂起、由客户端在设备上执行（``DEVICE_EXECUTED_OPS``）。
    """
    resolved = _resolve_persona(ctx, str(params["personaName"]))
    if isinstance(resolved, ToolOutcome):
        return resolved

    return ToolOutcome.proposal({
        "action": "persona_switch",
        "payload": {"personaId": resolved},
        "message": f"把当前聊天角色切换为「{params['personaName']}」",
        "warnings": [],
    })


# ---------------------------------------------------------------------------
# 分析域 —— 读工具（T-M7-05d）
# ---------------------------------------------------------------------------

def _enum_text(value: Any) -> str:
    """枚举（或已经是字符串的值）→ 它的**取值**。

    ``ExportFormat`` / ``ExportStatus`` / ``UserStatus`` 都是 ``(str, Enum)`` 混入类，
    而 ``str(member)`` 在 Python 3.12 上给的是 ``"ExportFormat.CSV"``（``Enum.__str__``
    赢过 ``str.__str__``）——直接把 ``str(row.format)`` 回喂给模型，它会读到
    ``ExportFormat.CSV`` 这种既不是取值也不是人话的东西。
    """
    return value.value if isinstance(value, enum.Enum) else str(value)


def _fact_category_row(cat: Any) -> dict[str, Any]:
    """``fact_pack.CategoryFact`` → 回喂形状。**剥掉 ``category_id``**（铁律 1）。"""
    return {
        "name": cat.category_name,
        "totalCents": cat.total_cents,
        "count": cat.count,
    }


def _fact_tx_row(tx: Any) -> dict[str, Any]:
    """``fact_pack.TransactionFact`` → 回喂形状。**剥掉 ``id``**（同上）。"""
    return {
        "amountCents": tx.amount_cents,
        "categoryName": tx.category_name,
        "note": tx.note,
        "bizDate": tx.biz_date,
    }


def _fact_pack_payload(pack: Any) -> dict[str, Any]:
    """``fact_pack.FactPack`` → 回喂形状。

    **这是 ``/ai/report`` 的数据面**，不是它的叙事面：``generate_report`` 会在这一
    份事实之上再调一次模型写一段分析（并逐条核对数字），而对话里模型自己就会写
    分析——把叙事层也搬进工具等于对同一份数据付两次模型费用，还把一段模型生成的
    文字当成「工具事实」回喂给另一个模型。故这里只给确定性事实（架构 §7.4 铁律 2）。

    ``data_hash`` 刻意不出现在结果里：它是缓存键，对模型没有语义。
    """
    budget = pack.budget
    return {
        "period": pack.period,
        "totalIncomeCents": pack.total_income,
        "totalExpenseCents": pack.total_expense,
        "balanceCents": pack.balance,
        "transactionCount": pack.transaction_count,
        "expenseByCategory": [_fact_category_row(c) for c in pack.expense_by_category],
        "incomeByCategory": [_fact_category_row(c) for c in pack.income_by_category],
        "budget": None if budget is None else {
            "budgetCents": budget.amount_cents,
            "spentCents": budget.spent_cents,
            "remainingCents": budget.remaining_cents,
            "pctUsed": budget.pct_used,
        },
        "largestExpense": (
            None if pack.largest_expense is None else _fact_tx_row(pack.largest_expense)
        ),
        "largestIncome": (
            None if pack.largest_income is None else _fact_tx_row(pack.largest_income)
        ),
        "trend": [
            {
                "period": point.period,
                "totalIncomeCents": point.total_income,
                "totalExpenseCents": point.total_expense,
                "balanceCents": point.balance,
                "transactionCount": point.transaction_count,
            }
            for point in pack.trend
        ],
    }


async def _handle_stats_monthly(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    """单月完整财务事实（收入 / 支出 / 结余 / 分类明细 / 预算执行 / 最大单笔 / 近 6 期趋势）。"""
    period = str(params["period"])
    if _resolve_period(period) is None:
        return ToolOutcome.failure(ERR_INVALID_PERIOD)

    from .fact_pack import FactPackService

    pack = await FactPackService(ctx.db).build(ctx.book_id, ctx.user_id, period)
    return ToolOutcome.result(_fact_pack_payload(pack))


async def _handle_stats_trends(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    """指定区间的逐月趋势（含区间内的空月）。"""
    period_from = str(params["periodFrom"])
    period_to = str(params["periodTo"])
    if _resolve_period(period_from) is None or _resolve_period(period_to) is None:
        return ToolOutcome.failure(ERR_INVALID_PERIOD)
    if period_from > period_to:
        # 字符串比较对 ``YYYY-MM`` 等价于时间先后（零填充定长）。
        return ToolOutcome.failure(ERR_INVALID_PERIOD_RANGE)

    from src.modules.statistics.service import StatisticsService

    stats = await StatisticsService(ctx.db).get_trend_stats(
        ctx.book_id, ctx.user_id, period_from, period_to,
    )
    return ToolOutcome.result({
        "periodFrom": period_from,
        "periodTo": period_to,
        "points": [
            {
                "period": point.period,
                "totalIncomeCents": point.total_income,
                "totalExpenseCents": point.total_expense,
                "balanceCents": point.balance,
                "transactionCount": point.transaction_count,
            }
            for point in stats.points
        ],
    })


# ---------------------------------------------------------------------------
# 导出域（T-M7-05d）
# ---------------------------------------------------------------------------

async def _handle_export_create(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    """产出导出的提案。**账本由服务端填**（``ctx.book_id``），模型只选格式。"""
    fmt = str(params["format"])
    book_name = ctx.book_names_by_id.get(str(ctx.book_id))
    return ToolOutcome.proposal({
        "action": "export_create",
        "payload": {"bookId": str(ctx.book_id), "format": fmt},
        "message": (
            f"把「{book_name}」的流水导出成 {fmt.upper()} 文件"
            if book_name else f"把当前账本的流水导出成 {fmt.upper()} 文件"
        ),
        "warnings": [],
    })


async def _handle_export_list(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    """列出这个用户已经建过的导出任务（**不含 id**——模型拿不到下载凭据）。"""
    from src.modules.exports.service import ExportService

    rows = await ExportService(ctx.db).list_exports(ctx.user_id)
    return ToolOutcome.result({
        "count": len(rows),
        "exports": [
            {
                "format": _enum_text(row.format),
                "status": _enum_text(row.status),
                "createdAt": row.created_at.isoformat(),
                "expiresAt": (
                    row.expires_at.isoformat() if row.expires_at is not None else None
                ),
                "fileSizeBytes": row.file_size,
            }
            for row in rows
        ],
    })


# ---------------------------------------------------------------------------
# 反馈与隐私域（T-M7-05d）
# ---------------------------------------------------------------------------

async def _handle_feedback_submit(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    """产出反馈的提案。**设备元信息由执行体补**（见 ``proposal_ops``）。"""
    payload: dict[str, Any] = {
        "category": str(params["category"]),
        "content": str(params["content"]),
    }
    contact = params.get("contact")
    if contact:
        payload["contact"] = str(contact)

    return ToolOutcome.proposal({
        "action": "feedback_submit",
        "payload": payload,
        "message": "把这条反馈提交给产品团队",
        "warnings": [],
    })


async def _handle_consent_record(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    """产出隐私同意的提案（**执行位置＝设备**，见 ``DEVICE_EXECUTED_OPS``）。"""
    payload: dict[str, Any] = {}
    version = params.get("policyVersion")
    if version:
        payload["policyVersion"] = str(version)

    return ToolOutcome.proposal({
        "action": "consent_record",
        "payload": payload,
        "message": "记录这台设备的隐私政策同意",
        # 同意是**法律意义上的表态**，用户最好在设置页里点那个按钮完成它——
        # 回喂给模型的这句话会让它把用户引到界面上（而不是当成一次普通写操作）。
        "warnings": ["隐私同意也可以在「设置 → 隐私」里完成"],
    })


async def _handle_consent_status(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    """这个用户是否同意过隐私政策，以及同意的是哪一版、什么时候。"""
    from src.modules.consent.service import ConsentService

    latest = await ConsentService(ctx.db).get_latest_user_consent(ctx.user_id)
    if latest is None:
        return ToolOutcome.result({"hasConsented": False})
    return ToolOutcome.result({
        "hasConsented": True,
        "policyVersion": latest.policy_version,
        "consentedAt": latest.consented_at.isoformat(),
    })


# ---------------------------------------------------------------------------
# 安全账号域（T-M7-05d）
# ---------------------------------------------------------------------------

async def _handle_auth_update_profile(
    ctx: ToolContext, params: dict[str, Any],
) -> ToolOutcome:
    """改昵称。**只开放昵称**——``PATCH /me`` 能改的也只有它（账号是手机号 +
    验证码，没有密码与邮箱字段，见 ``auth/schemas.py`` 的 ``UpdateProfileRequest``）。"""
    nickname = str(params["nickname"])
    # ``minLength: 1`` 拦不住 ``"   "``（strip 后是空串），而 auth 侧的
    # ``UpdateProfileRequest`` 会拒。在这里先挡一次：执行时才失败对模型是
    # 「不可行动的错」——它拿不到「哪个字段不对」的反馈。
    if not nickname.strip():
        return ToolOutcome.failure(ERR_INVALID_ARGUMENTS, argument="nickname")
    return ToolOutcome.proposal({
        "action": "auth_update_profile",
        "payload": {"nickname": nickname},
        "message": f"把昵称改成「{nickname}」",
        "warnings": [],
    })


async def _handle_account_deletion_request(
    ctx: ToolContext, params: dict[str, Any],
) -> ToolOutcome:
    """注销账号（**最高危 op**，见 ``HIGH_RISK_OPS``）。

    ``warnings`` 非空会同时把置信度压到 ``_WARNED_CONFIDENCE``——于是「低置信」
    与「高风险」两条路径都要求确认卡，这是刻意的双保险：判据分别落在
    ``confirm_reason`` 的两个分支上，任一条被将来的改动放松，另一条仍然拦得住。
    """
    return ToolOutcome.proposal({
        "action": "account_deletion_request",
        "payload": {},
        "message": "申请注销账号，账号将进入 7 天冷静期",
        "warnings": [
            "注销后同步与数据上报立即停止",
            "7 天内可以撤销，7 天后账号与全部数据被永久删除",
        ],
    })


# ---------------------------------------------------------------------------
# 同步域（T-M7-05d）
# ---------------------------------------------------------------------------

async def _handle_sync_status(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    """同步能不能跑、服务端这边的数据到哪一步了。

    **服务端不知道设备侧的游标**（``sync_cursor`` 是 ``sync_meta`` 的本地键，
    不上行），所以这里给的是**同步的前提条件**与**服务端最后一笔变更的时间**：
    用户问「怎么同步不了」时，答案通常在这两项里（未同意隐私政策 / 账号在冷静期）。
    """
    from src.modules.consent.service import ConsentService

    status = await ctx.db.scalar(select(User.status).where(User.id == ctx.user_id))
    last_change = await ctx.db.scalar(
        select(func.max(SyncChange.changed_at)).where(SyncChange.user_id == ctx.user_id),
    )
    consented = await ConsentService(ctx.db).has_user_consented(ctx.user_id)
    account_status = _enum_text(status) if status is not None else "unknown"
    return ToolOutcome.result({
        "accountStatus": account_status,
        "consented": consented,
        # frozen（注销冷静期）与 deactivated 的账号 push 会被服务端拒掉，
        # 两类都算「同步跑不起来」。
        "canSync": consented and account_status in ("guest", "active"),
        "lastServerChangeAt": (
            last_change.isoformat() if last_change is not None else None
        ),
    })


async def _handle_sync_trigger(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    """产出「让设备立刻同步一次」的提案（**执行位置＝设备**，见 ``DEVICE_EXECUTED_OPS``）。"""
    return ToolOutcome.proposal({
        "action": "sync_trigger",
        "payload": {},
        "message": "让这台设备立刻同步一次",
        "warnings": [],
    })


# ---------------------------------------------------------------------------
# 提醒域（尾卡 / R-PROD-38）
# ---------------------------------------------------------------------------


async def _handle_reminder_set(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    """产出「设每日提醒」的提案（**执行位置＝设备**，见 ``DEVICE_EXECUTED_OPS``）。

    payload 与桥命令 ``reminder.set`` 的参数**逐字对应**（``enabled`` / ``hour`` /
    ``minute``）——设备侧的执行器把 payload 原样递过去、中间不做翻译，两边因此
    不会漂移（同 ``sync_trigger`` 的做法）。

    越界的 ``hour`` / ``minute`` 在这里就拒掉：schema 也会拦，但工具层不信任上游
    ——模型可能经别的路径带进一个不合规的取值，而一个 ``hour=25`` 到了设备侧
    要么静默变成别的时间、要么让 ``AlarmManager`` 抛异常。
    """
    hour = params.get("hour")
    minute = params.get("minute")
    if not isinstance(hour, int) or isinstance(hour, bool) or not 0 <= hour <= 23:
        return ToolOutcome.failure(ERR_INVALID_ARGUMENTS, field="hour")
    if not isinstance(minute, int) or isinstance(minute, bool) or not 0 <= minute <= 59:
        return ToolOutcome.failure(ERR_INVALID_ARGUMENTS, field="minute")
    enabled = bool(params.get("enabled", True))
    hhmm = f"{hour:02d}:{minute:02d}"
    return ToolOutcome.proposal({
        "action": "reminder_set",
        "payload": {"enabled": enabled, "hour": hour, "minute": minute},
        "message": (
            f"把这台设备的每日提醒设为 {hhmm}" if enabled
            else "关掉这台设备的每日提醒"
        ),
        "warnings": [],
    })


# ---------------------------------------------------------------------------
# 帮助域（05i）
# ---------------------------------------------------------------------------


async def _handle_help_search(ctx: ToolContext, params: dict[str, Any]) -> ToolOutcome:
    """查产品帮助语料（纯函数，**不碰数据库、不看用户数据**）。

    没命中时不返回空结果了事——把**全部条目标题**一并回给模型，它因此能就最
    接近的那条澄清、或如实说「帮助里没有」。只回一句「没找到」会被模型转述成
    「这个功能不存在」，那是比不知道更糟的答案。
    """
    matches = search_help(params["query"])
    if not matches:
        return ToolOutcome.result({
            "matched": False,
            "topics": [],
            "availableTopics": help_topic_titles(),
            "note": (
                "产品帮助里没有这一条。如实告诉用户帮助里没有相关内容，"
                "或按上面最接近的一条引导；不要凭印象补充功能细节。"
            ),
        })
    return ToolOutcome.result({
        "matched": True,
        "topics": [
            {"title": topic.title, "entry": topic.entry, "content": topic.body}
            for topic in matches
        ],
    })


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

#: 画像的 7 个键与中文含义——**写给模型看的那一份**（T19-3 / REQ-74 决策 #4）。
#: 直接由 ``prompts._PROFILE_LABELS`` 生成，因此两边不可能漂移；它与客户端表单页
#: （``UserSettingsPage.FIELD_ORDER``）的一致由 ``tests/test_ai_profile_vocabulary.py``
#: 钉住。写成一句话而不是让模型自由发挥，是这张卡的核心修复：实测模型对
#: 「我的说话偏好是温柔一点」会写成 ``{"说话偏好": "温柔一点"}``——键名对不上，
#: 表单页按 ``FIELD_ORDER`` 取值 → 显示为空，而数据其实已经存进去了。
_PROFILE_KEYS_HINT: str = " / ".join(
    f"{key}（{label}）" for key, label in _PROFILE_LABELS.items()
)

TOOLS: tuple[Tool, ...] = (
    # ── 记账域 ──────────────────────────────────────────────────────────
    Tool(
        name="transaction_add",
        domain=DOMAIN_TRANSACTION,
        kind="write",
        risk="low",
        proposal_action="transaction_add",
        description=(
            "新增一笔收支记录。用户说「记一笔」「花了 30 块打车」「今天收入 500」"
            "这类要往账上添一笔的诉求时用它。金额单位是分，你需要自己把口语金额"
            "换算成分。分类名必须与现有分类完全一致，且**尽量落到最具体的那个**"
            "（有贴切的二级就用二级名，如「打车」而非「交通」）；一个都不合适时"
            "也要选个最近的，再顺口问一句要不要新建分类。只记一笔时用它；一次要记"
            "多笔请用 transaction_add_batch。"
        ),
        parameters=_tx_item_schema(),
        handler=_handle_transaction_add,
    ),
    Tool(
        name="transaction_add_batch",
        domain=DOMAIN_TRANSACTION,
        kind="write",
        risk="low",
        proposal_action="transaction_add",
        description=(
            "一次新增多笔收支记录，整批要么全部写入要么全部不写。用户一口气报"
            "多笔账（「早饭 12、午饭 25、打车 30」）时用它，比连续调用 transaction_add "
            "更快也更安全。每笔的字段规则与 transaction_add 完全相同。"
        ),
        parameters=_object_schema(
            {
                "transactions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_BATCH_TRANSACTIONS,
                    "items": _tx_item_schema(),
                    "description": "要记录的多笔交易，字段规则与 transaction_add 相同。",
                },
            },
            required=["transactions"],
        ),
        handler=_handle_transaction_add_batch,
    ),
    Tool(
        name="transaction_update",
        domain=DOMAIN_TRANSACTION,
        kind="write",
        risk="medium",
        proposal_action="transaction_update",
        description=(
            "修改**已经记过**的一笔交易的金额、分类、账户、备注、日期或收支类型。"
            "用户说「刚才那笔记错了」「把昨天 12 块的午饭改成 15」时用它。"
            "用 locator 描述要改的是哪一笔（日期 / 金额 / 分类 / 账户 / 备注关键词，"
            "尽量给全），再用 changes 说明改成什么。locator 匹配到不止一笔时服务端"
            "会拒绝并要求你补充条件——那时请向用户确认是哪一笔，**不要**自己挑一笔。"
            "改收入 / 支出的方向时请同时给出 type。要新记一笔请用 transaction_add。"
        ),
        parameters=_object_schema(
            {
                "locator": {
                    **_tx_locator_schema(),
                    "description": "要修改的是哪一笔（条件之间是「并且」）。",
                },
                "changes": {
                    **_tx_changes_schema(),
                    "description": "要改成的值（没提到的字段保持不变）。",
                },
            },
            required=["locator", "changes"],
        ),
        handler=_handle_transaction_update,
    ),
    Tool(
        name="transaction_delete",
        domain=DOMAIN_TRANSACTION,
        kind="write",
        risk="medium",
        proposal_action="transaction_delete",
        description=(
            "删除一笔已经记过的交易（软删除，记录不再出现在流水里）。用户说"
            "「把刚才那笔删掉」「昨天那笔 30 块记重了」时用它。用 locator 描述要删"
            "的是哪一笔，匹配到不止一笔时服务端会拒绝并要求补充条件——请向用户"
            "确认，**不要**自己挑一笔。删了就没了，删除前请确认用户说的是这笔。"
        ),
        parameters=_object_schema(
            {
                "locator": {
                    **_tx_locator_schema(),
                    "description": "要删除的是哪一笔（条件之间是「并且」）。",
                },
            },
            required=["locator"],
        ),
        handler=_handle_transaction_delete,
    ),
    Tool(
        name="transaction_list",
        domain=DOMAIN_TRANSACTION,
        kind="read",
        risk="low",
        description=(
            "按条件查询交易明细，返回每笔的收支类型、金额、分类名、账户名、备注和日期。"
            "用户想看「上周都记了什么」「餐饮花了哪些」这类带条件的明细时用它。"
            "结果按日期倒序。所有条件都是可选的，条件越具体结果越准。"
            "只想看最近几笔、没有任何筛选条件时请用 transaction_recent。"
        ),
        parameters=_object_schema({
            "dateFrom": {
                "type": "string",
                "format": "date",
                "pattern": r"^\d{4}-\d{2}-\d{2}$",
                "description": "起始日期（含当天），YYYY-MM-DD。",
            },
            "dateTo": {
                "type": "string",
                "format": "date",
                "pattern": r"^\d{4}-\d{2}-\d{2}$",
                "description": "结束日期（含当天），YYYY-MM-DD。",
            },
            "categoryName": {
                "type": "string",
                "description": "按分类名筛选，必须是现有分类的完整名称。",
            },
            "accountName": {
                "type": "string",
                "description": "按账户名筛选，必须是现有账户的完整名称。",
            },
            "keyword": {
                "type": "string",
                "description": "关键词，备注或分类名命中其一即算。",
            },
            "type": _TX_TYPE_SCHEMA,
            "limit": _LIMIT_SCHEMA,
        }),
        handler=_handle_transaction_list,
    ),
    Tool(
        name="transaction_recent",
        domain=DOMAIN_TRANSACTION,
        kind="read",
        risk="low",
        description=(
            "查看最近几笔交易，不含任何筛选条件。结果按**业务日期**倒序"
            "（补充录入的旧日期账目会排在后面），**不是按录入顺序**。用户说"
            "「我最近记了什么」「最近几笔账」时用它。需要按时间、分类、关键词"
            "筛选时请改用 transaction_list。它不能用来修改或删除记录。"
        ),
        parameters=_object_schema({
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "description": "返回最近多少笔，默认 10。",
            },
        }),
        handler=_handle_transaction_recent,
    ),
    Tool(
        name="transaction_summary",
        domain=DOMAIN_TRANSACTION,
        kind="read",
        risk="low",
        description=(
            "统计某段时间的收支合计与笔数（收入、支出、结余），可选按分类、按日或"
            "按月分组。用户问「这个月花了多少」「上个月收支情况」「最近哪天花得最多」"
            "时用它。金额由服务端计算，你不需要也不应该自己算。"
            "要的是逐笔明细而不是合计时请用 transaction_list。"
        ),
        parameters=_object_schema(
            {
                "dateFrom": {
                    "type": "string",
                    "format": "date",
                    "pattern": r"^\d{4}-\d{2}-\d{2}$",
                    "description": "统计起始日期（含当天），YYYY-MM-DD。",
                },
                "dateTo": {
                    "type": "string",
                    "format": "date",
                    "pattern": r"^\d{4}-\d{2}-\d{2}$",
                    "description": "统计结束日期（含当天），YYYY-MM-DD。",
                },
                "type": {
                    "type": "string",
                    "enum": ["expense", "income"],
                    "description": "只看单一收支类型时填写；不填则合计全部。",
                },
                "groupBy": {
                    "type": "string",
                    "enum": ["category", "day", "month"],
                    "description": (
                        "分组维度：category=按分类、day=按日、month=按月。"
                        "不填则只返回总计。"
                    ),
                },
                "categoryName": {
                    "type": "string",
                    "description": "只看某个分类时填写，必须是现有分类的完整名称。",
                },
                "accountName": {
                    "type": "string",
                    "description": "只看某个账户时填写，必须是现有账户的完整名称。",
                },
            },
            required=["dateFrom", "dateTo"],
        ),
        handler=_handle_transaction_summary,
    ),
    # ── 预算域 ──────────────────────────────────────────────────────────
    Tool(
        name="budget_list",
        domain=DOMAIN_BUDGET,
        kind="read",
        risk="low",
        description=(
            "列出某个月的月度预算，含每项的分类名、额度、已用、剩余与使用百分比。"
            "用户问「我这个月有哪些预算」「预算都设了多少」时用它。"
            "查单个分类的预算请用 budget_status。"
        ),
        parameters=_object_schema({"period": _PERIOD_SCHEMA}, required=["period"]),
        handler=_handle_budget_list,
    ),
    Tool(
        name="budget_status",
        domain=DOMAIN_BUDGET,
        kind="read",
        risk="low",
        description=(
            "查某个分类在某个月的预算执行情况：额度、已用、剩余、百分比、是否已超支。"
            "用户问「餐饮预算还剩多少」「这个月交通超了吗」时用它。"
            "该分类没有设预算时会明确返回 hasBudget=false，不要凭空编造额度。"
        ),
        parameters=_object_schema(
            {
                "categoryName": {
                    "type": "string",
                    "description": "分类名，必须是现有分类的完整名称。",
                },
                "period": _PERIOD_SCHEMA,
            },
            required=["categoryName", "period"],
        ),
        handler=_handle_budget_status,
    ),
    Tool(
        name="budget_check",
        domain=DOMAIN_BUDGET,
        kind="read",
        risk="low",
        description=(
            "预检：如果现在某分类再花掉一笔金额，会不会超出该月预算。返回追加后的"
            "占用额与 willExceed 判断。用户问「再花 200 会超预算吗」「这笔能花吗」"
            "时用它。金额单位是分，换算由你完成；是否超支由服务端算，不要自己判断。"
        ),
        parameters=_object_schema(
            {
                "categoryName": {
                    "type": "string",
                    "description": "分类名，必须是现有分类的完整名称。",
                },
                "amountCents": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "打算花掉的金额，单位是分（1 元 = 100 分）。",
                },
                "period": _PERIOD_SCHEMA,
            },
            required=["categoryName", "amountCents", "period"],
        ),
        handler=_handle_budget_check,
    ),
    Tool(
        name="budget_summary",
        domain=DOMAIN_BUDGET,
        kind="read",
        risk="low",
        description=(
            "某个月预算的整体执行概览：总预算（若设了）的额度/已用/剩余，加上"
            "分类预算的条数、额度合计与已用合计，以及超支项数与超支清单。"
            "总预算覆盖全部支出、分类预算只覆盖本分类，两者是两个层面，"
            "结果里分开给出、不相加。用户问「这个月预算情况怎么样」「预算还够用吗」"
            "时用它。要看逐项的明细请用 budget_list。"
        ),
        parameters=_object_schema({"period": _PERIOD_SCHEMA}, required=["period"]),
        handler=_handle_budget_summary,
    ),
    Tool(
        name="budget_overspent",
        domain=DOMAIN_BUDGET,
        kind="read",
        risk="low",
        description=(
            "列出某个月已经超支的预算项（分类名、额度、已用、剩余为负表示超出的"
            "金额）。用户问「我哪些预算超了」「有没有超支」时用它。"
        ),
        parameters=_object_schema({"period": _PERIOD_SCHEMA}, required=["period"]),
        handler=_handle_budget_overspent,
    ),
    Tool(
        name="budget_set",
        domain=DOMAIN_BUDGET,
        kind="write",
        risk="medium",
        proposal_action="budget_set",
        description=(
            "设置某个月的**整体预算**（这个月全部支出的上限，不分分类）。用户说"
            "「这个月总共只能花 5000」「九月整体预算改成 6000」时用它。金额单位"
            "是分，换算由你完成。这个月已经设过就覆盖，没设过就新建。只想限制"
            "某一类（餐饮、交通等）请用 budget_set_category。"
        ),
        parameters=_object_schema(
            {
                "period": _PERIOD_SCHEMA,
                "amountCents": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 9007199254740991,
                    "description": "预算额度，单位是分（1 元 = 100 分）。",
                },
            },
            required=["period", "amountCents"],
        ),
        handler=_handle_budget_set,
    ),
    Tool(
        name="budget_set_category",
        domain=DOMAIN_BUDGET,
        kind="write",
        risk="medium",
        proposal_action="budget_set_category",
        description=(
            "设置某个月**某个分类**的预算（如「餐饮每月 1500」）。用户说「给餐饮"
            "设个预算」「这个月交通预算是 300」时用它。分类名必须与现有分类完全"
            "一致，且**必须是支出分类**——预算是花销上限，收入分类没有「已用」。"
            "这个月该分类已经设过就覆盖，没设过就新建。整体预算请用 budget_set。"
        ),
        parameters=_object_schema(
            {
                "period": _PERIOD_SCHEMA,
                "categoryName": {
                    "type": "string",
                    "description": "分类名，必须是现有**支出**分类的完整名称。",
                },
                "amountCents": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 9007199254740991,
                    "description": "预算额度，单位是分（1 元 = 100 分）。",
                },
            },
            required=["period", "categoryName", "amountCents"],
        ),
        handler=_handle_budget_set_category,
    ),
    Tool(
        name="budget_delete",
        domain=DOMAIN_BUDGET,
        kind="write",
        risk="medium",
        proposal_action="budget_delete",
        description=(
            "删除某个月的预算：只给 period 表示删**整体预算**，再给 categoryName "
            "表示删**该分类的预算**。用户说「把这个月的总预算去掉」「餐饮预算不要"
            "了」时用它。删掉的是预算本身，已记录的交易和统计不受影响。"
            "要改额度（而不是删掉）请用 budget_set / budget_set_category。"
        ),
        parameters=_object_schema({
            "period": _PERIOD_SCHEMA,
            "categoryName": {
                "type": "string",
                "description": (
                    "要删的分类预算的分类名；**不填表示删整体预算**。"
                ),
            },
        }, required=["period"]),
        handler=_handle_budget_delete,
    ),
    # ── 账户域 ──────────────────────────────────────────────────────────
    Tool(
        name="account_list",
        domain=DOMAIN_ACCOUNT,
        kind="read",
        risk="low",
        description=(
            "列出用户现有的全部账户（名称与图标），不含已归档账户。用户问「我有"
            "哪些账户」时用它，或在需要确认账户名时用它核对。"
            "查账户下的金额流向请用 account_balances。"
        ),
        parameters=_object_schema({}),
        handler=_handle_account_list,
    ),
    Tool(
        name="account_balances",
        domain=DOMAIN_ACCOUNT,
        kind="read",
        risk="low",
        description=(
            "按账户汇总：每个账户在某段时间内的收入、支出、净额，以及账户的"
            "**当前余额**。用户问「招行卡花了多少」「各账户的收支」「我这张卡还有"
            "多少钱」时用它。不传日期则统计全部历史。"
            "「当前余额」是用户手动设置 / 记账联动维护的存量数，**不随期间变化**；"
            "它与期间净额不相等是正常的（差额来自手动校准与期间之外的流水）。"
            "回答「账户有多少钱」用当前余额，回答「这段时间花了多少」用净额。"
        ),
        parameters=_object_schema({
            "dateFrom": {
                "type": "string",
                "format": "date",
                "pattern": r"^\d{4}-\d{2}-\d{2}$",
                "description": "起始日期（含当天），YYYY-MM-DD。不填表示不限起点。",
            },
            "dateTo": {
                "type": "string",
                "format": "date",
                "pattern": r"^\d{4}-\d{2}-\d{2}$",
                "description": "结束日期（含当天），YYYY-MM-DD。不填表示不限终点。",
            },
        }),
        handler=_handle_account_balances,
    ),
    Tool(
        name="account_create",
        domain=DOMAIN_ACCOUNT,
        kind="write",
        risk="low",
        proposal_action="account_create",
        description=(
            "新建一个账户（如「招行卡」「现金」「支付宝」）。用户说「帮我建一个"
            "账户」时用它。账户名不要和已有账户重名——先用 account_list 核对。"
            "用户同时说了分类（「建一个储蓄卡账户」）或余额（「里面有五千」）时"
            "一并填上；没说就省略。"
        ),
        parameters=_object_schema(
            {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 64,
                    "description": "账户名，用户怎么称呼它就怎么写。",
                },
                "icon": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "图标标识；用户没提就不要填（会走默认图标）。",
                },
                "accountType": _ACCOUNT_TYPE_SCHEMA,
                "balanceCents": _ACCOUNT_BALANCE_SCHEMA,
            },
            required=["name"],
        ),
        handler=_handle_account_create,
    ),
    Tool(
        name="account_update",
        domain=DOMAIN_ACCOUNT,
        kind="write",
        risk="medium",
        proposal_action="account_update",
        description=(
            "修改一个已有账户的名称、图标、分类或当前余额。用户说「把招行卡改名成"
            "工资卡」「把交行卡分类到储蓄卡」「给这张卡设余额五千」时用它。"
            "accountName 必须与现有账户完全一致（先用 account_list 核对）；重名"
            "账户会被拒绝，那时请向用户指明是哪一个。改余额前先用 account_balances"
            "看当前值；余额一律给更新后的**绝对值**，不是增减量。"
        ),
        parameters=_object_schema(
            {
                "accountName": {
                    "type": "string",
                    "description": "要修改的账户名，必须与现有账户完全一致。",
                },
                "newName": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 64,
                    "description": "新的账户名；不改名字就不要填。",
                },
                "icon": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "新的图标标识；不换图标就不要填。",
                },
                "accountType": _ACCOUNT_TYPE_SCHEMA,
                "balanceCents": _ACCOUNT_BALANCE_SCHEMA,
            },
            required=["accountName"],
        ),
        handler=_handle_account_update,
    ),
    Tool(
        name="account_archive",
        domain=DOMAIN_ACCOUNT,
        kind="write",
        risk="medium",
        proposal_action="account_archive",
        description=(
            "归档（停用）一个账户。用户说「这个账户不用了」「把旧卡删掉」时用它。"
            "归档不是删除：账户不再出现在账户清单里，但已记录的交易仍保留原账户，"
            "统计也不会因此变动。想彻底抹掉记录请用 transaction_delete 逐笔处理。"
        ),
        parameters=_object_schema(
            {
                "accountName": {
                    "type": "string",
                    "description": "要归档的账户名，必须与现有账户完全一致。",
                },
            },
            required=["accountName"],
        ),
        handler=_handle_account_archive,
    ),
    # ── 账本域 ──────────────────────────────────────────────────────────
    Tool(
        name="book_list",
        domain=DOMAIN_BOOK,
        kind="read",
        risk="low",
        description=(
            "列出用户现有的全部账本（名称、图标，并标出当前正在使用的账本）。"
            "用户问「我有几个账本」「现在用的是哪个账本」时用它，"
            "或在需要确认账本名时用它核对。"
        ),
        parameters=_object_schema({}),
        handler=_handle_book_list,
    ),
    Tool(
        name="book_detail",
        domain=DOMAIN_BOOK,
        kind="read",
        risk="low",
        description=(
            "看某个账本的概况：分类数、交易笔数、记账的时间跨度。用户问「这个账本"
            "记了多少笔」「XX 账本是记什么的」时用它。不传账本名则看当前账本。"
        ),
        parameters=_object_schema({
            "bookName": {
                "type": "string",
                "description": "账本名，必须是现有账本的完整名称。不填表示当前账本。",
            },
        }),
        handler=_handle_book_detail,
    ),
    Tool(
        name="book_create",
        domain=DOMAIN_BOOK,
        kind="write",
        risk="low",
        proposal_action="book_create",
        description=(
            "新建一个账本（如「家庭账本」「旅行账本」）。用户说「再建一个账本」时"
            "用它。新建的账本是空的：不带分类、不带任何记录。"
        ),
        parameters=_object_schema(
            {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "description": "账本名，用户怎么称呼它就怎么写。",
                },
                "icon": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "图标标识；用户没提就不要填。",
                },
            },
            required=["name"],
        ),
        handler=_handle_book_create,
    ),
    Tool(
        name="book_update",
        domain=DOMAIN_BOOK,
        kind="write",
        risk="medium",
        proposal_action="book_update",
        description=(
            "修改一个已有账本的名称或图标。用户说「把旅行账本改名成蜜月账本」时用"
            "它。bookName 必须与现有账本完全一致（先用 book_list 核对）；不填表示"
            "当前账本。"
        ),
        parameters=_object_schema(
            {
                "bookName": {
                    "type": "string",
                    "description": "要修改的账本名，必须与现有账本完全一致。不填表示当前账本。",
                },
                "newName": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "description": "新的账本名；不改名字就不要填。",
                },
                "icon": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "新的图标标识；不换图标就不要填。",
                },
            },
        ),
        handler=_handle_book_update,
    ),
    Tool(
        name="book_delete",
        domain=DOMAIN_BOOK,
        kind="write",
        risk="high",
        proposal_action="book_delete",
        description=(
            "删除一个账本及其中的记录。用户说「把这个账本删掉」时用它。删除前"
            "务必确认用户指的是哪个账本——**删掉的账本连同里面的流水一起不再可见**，"
            "这是本工具集里破坏性最强的操作之一。不填账本名表示当前账本。"
        ),
        parameters=_object_schema({
            "bookName": {
                "type": "string",
                "description": "要删除的账本名，必须与现有账本完全一致。不填表示当前账本。",
            },
        }),
        handler=_handle_book_delete,
    ),
    # ── 分类域 ──────────────────────────────────────────────────────────
    Tool(
        name="category_list",
        domain=DOMAIN_CATEGORY,
        kind="read",
        risk="low",
        description=(
            "列出可用的分类（名称、收支类型、上级分类、当前图标 emoji）。在你不确定"
            "分类的准确名称时用它核对，分类名必须与这里的名称逐字一致才能用于记账；"
            "给分类改图标时也可以照这里的现有风格挑一个。"
            "不传账本名则看当前账本（含全局分类）。"
        ),
        parameters=_object_schema({
            "bookName": {
                "type": "string",
                "description": "账本名，必须是现有账本的完整名称。不填表示当前账本。",
            },
        }),
        handler=_handle_category_list,
    ),
    Tool(
        name="category_create",
        domain=DOMAIN_CATEGORY,
        kind="write",
        risk="low",
        proposal_action="category_create",
        description=(
            "新建一个分类。**只在用户明确要求时用它**（「加一个分类叫宠物」）——"
            "记账时分类都不合适只在回复里问一句，等用户答「好」了下一轮再建，"
            "不要顺手建一个。必须给出收支类型（expense 支出 / income 收入）——"
            "同名的支出分类与收入分类是两个不同的分类。可选用 parentName 挂在某个"
            "已有分类下面（分类只有两级，二级挂在一级下）。"
            "新分类建在当前账本下。**新建的分类要等用户在确认卡确认后才生效——"
            "同一轮里不要拿它去记账**。"
        ),
        parameters=_object_schema(
            {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 64,
                    "description": "分类名。",
                },
                "kind": _TX_TYPE_SCHEMA,
                "icon": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "分类图标的 emoji（如 🚗），最多 64 字符；用户没提就不要填。",
                },
                "parentName": {
                    "type": "string",
                    "description": "上级分类名（可选），必须是同类型下已有的分类。",
                },
                "bookName": {
                    "type": "string",
                    "description": "建在哪个账本下；不填表示当前账本。",
                },
            },
            required=["name", "kind"],
        ),
        handler=_handle_category_create,
    ),
    Tool(
        name="category_update",
        domain=DOMAIN_CATEGORY,
        kind="write",
        risk="medium",
        proposal_action="category_update",
        description=(
            "修改一个已有分类的名称、图标或它挂在哪个上级分类下。用户说「把宠物改叫"
            "猫主子」「把交通分类的图标改成 🚗」时用它。categoryName + kind 一起定位"
            "要改的分类（同名跨收支类型时靠 kind 区分）。不能改收支类型——那等于换"
            "一个分类，请用 category_create 建新的。"
        ),
        parameters=_object_schema(
            {
                "categoryName": {
                    "type": "string",
                    "description": "要修改的分类名，必须与现有分类完全一致。",
                },
                "kind": _TX_TYPE_SCHEMA,
                "newName": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 64,
                    "description": "新的分类名；不改名字就不要填。",
                },
                "icon": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "新的分类图标 emoji（如 🚗），最多 64 字符；不换图标就不要填。",
                },
                "parentName": {
                    "type": "string",
                    "description": "新的上级分类名；不移动就不要填。",
                },
            },
            required=["categoryName", "kind"],
        ),
        handler=_handle_category_update,
    ),
    Tool(
        name="category_archive",
        domain=DOMAIN_CATEGORY,
        kind="write",
        risk="medium",
        proposal_action="category_archive",
        description=(
            "归档（停用）一个分类。用户说「这个分类不用了」时用它。归档不是删除："
            "分类不再出现在分类清单里、也不能再用于记账，但已记录的交易仍保留原"
            "分类。归档前先确认用户说的就是这个分类——归档后它会从清单上消失。"
        ),
        parameters=_object_schema(
            {
                "categoryName": {
                    "type": "string",
                    "description": "要归档的分类名，必须与现有分类完全一致。",
                },
                "kind": _TX_TYPE_SCHEMA,
            },
            required=["categoryName", "kind"],
        ),
        handler=_handle_category_archive,
    ),
    # ── 存钱域（T-M7-05c）───────────────────────────────────────────────
    Tool(
        name="savings_list",
        domain=DOMAIN_SAVINGS,
        kind="read",
        risk="low",
        description=(
            "列出全部存钱目标（名称、目标金额、已存金额、还差多少、进度百分比、"
            "图标、存款期限）。用户问「我的存钱目标有哪些」「旅行基金存到多少了」"
            "时用它。存钱目标是用户级的，不分账本。"
        ),
        parameters=_object_schema({}),
        handler=_handle_savings_list,
    ),
    Tool(
        name="savings_create",
        domain=DOMAIN_SAVINGS,
        kind="write",
        risk="low",
        proposal_action="savings_create",
        description=(
            "新建一个存钱目标（如「旅行基金，目标 8000」）。用户说「我想攒钱买个"
            "相机」「建一个存钱目标」时用它。金额单位是分，换算由你完成。"
            "用户提到期限（「三个月内存到 8000」）时给出 periodValue 与 periodUnit，"
            "**两个必须一起给**。新建时不需要给已存金额，之后用 savings_update 存。"
        ),
        parameters=_object_schema(
            {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "description": "目标名称，用户怎么称呼它就怎么写。",
                },
                "targetCents": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 9007199254740991,
                    "description": "目标金额，单位是分（1 元 = 100 分）。",
                },
                "icon": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "图标标识；用户没提就不要填。",
                },
                "periodValue": {
                    "type": ["integer", "null"],
                    "minimum": 1,
                    "description": "存款期限的数量（如「三个月」的 3）。给就要和 periodUnit 一起给。",
                },
                "periodUnit": {
                    "type": ["string", "null"],
                    "enum": ["week", "month", None],
                    "description": "期限单位：week=周，month=月。给就要和 periodValue 一起给。",
                },
            },
            required=["name", "targetCents"],
        ),
        handler=_handle_savings_create,
    ),
    Tool(
        name="savings_update",
        domain=DOMAIN_SAVINGS,
        kind="write",
        risk="medium",
        proposal_action="savings_update",
        description=(
            "修改一个已有存钱目标：改名、改目标金额、换图标、调整期限，以及"
            "**存入 / 更正已存金额**。用户说「旅行基金存 500」「给相机目标存 2000」"
            "时用 addAmountCents（服务端会在当前已存金额上累加，你**不要**自己去查"
            "旧值再算总数）；用户说「我其实已经存了 3000」这种直接改数时用"
            "currentCents。savingsName 必须与现有目标完全一致（先用 savings_list 核对）。"
        ),
        parameters=_object_schema(
            {
                "savingsName": {
                    "type": "string",
                    "description": "要修改的目标名，必须与现有目标完全一致。",
                },
                "newName": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "description": "新的目标名；不改名字就不要填。",
                },
                "targetCents": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 9007199254740991,
                    "description": "新的目标金额，单位是分；不改就不要填。",
                },
                "icon": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "新的图标标识；不换图标就不要填。",
                },
                "addAmountCents": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 9007199254740991,
                    "description": "**本次存入**的金额，单位是分。服务端累加，不要自己算总数。",
                },
                "currentCents": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 9007199254740991,
                    "description": "把已存金额**直接改成**这个数（更正录错的进度）；单位是分。",
                },
                "periodValue": {
                    "type": ["integer", "null"],
                    "minimum": 1,
                    "description": "新的期限数量；要与 periodUnit 一起给。两个都给 null 表示去掉期限。",
                },
                "periodUnit": {
                    "type": ["string", "null"],
                    "enum": ["week", "month", None],
                    "description": "新的期限单位；要与 periodValue 一起给。两个都给 null 表示去掉期限。",
                },
            },
            required=["savingsName"],
        ),
        handler=_handle_savings_update,
    ),
    Tool(
        name="savings_archive",
        domain=DOMAIN_SAVINGS,
        kind="write",
        risk="medium",
        proposal_action="savings_archive",
        description=(
            "归档（停用）一个存钱目标。用户说「旅行基金不攒了」「把这个目标删掉」"
            "时用它。归档不是删除：目标不再出现在存钱页，进度记录也不再可见。"
            "归档前请确认用户说的就是这个目标。"
        ),
        parameters=_object_schema(
            {
                "savingsName": {
                    "type": "string",
                    "description": "要归档的目标名，必须与现有目标完全一致。",
                },
            },
            required=["savingsName"],
        ),
        handler=_handle_savings_archive,
    ),
    # ── 定时域（T-M7-05c）───────────────────────────────────────────────
    Tool(
        name="scheduled_list",
        domain=DOMAIN_SCHEDULED,
        kind="read",
        risk="low",
        description=(
            "列出**当前账本**的定时记账规则（名称、收支类型、金额、分类、账户、"
            "重复方式、起止日期、每天几点执行、是否启用、最近一次生成日期）。"
            "用户问「我有哪些定时记账」「房租那条规则还在吗」时用它。"
            "只列当前账本的规则，别的账本的规则不在这里。"
        ),
        parameters=_object_schema({}),
        handler=_handle_scheduled_list,
    ),
    Tool(
        name="scheduled_create",
        domain=DOMAIN_SCHEDULED,
        kind="write",
        risk="low",
        proposal_action="scheduled_create",
        description=(
            "新建一条定时记账规则（到期自动记一笔账，如「每个月 1 号自动记房租"
            "3000」）。用户说「以后每天自动记一笔早餐 15」「设置每月自动记房租」"
            "时用它。金额单位是分，换算由你完成；分类名必须与现有分类完全一致。"
            "startDate 是首次生效日期，用户没说就从下个周期算起并在回复里说明。"
        ),
        parameters=_object_schema(
            {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 64,
                    "description": "规则名（如「房租」「早餐」），用户怎么叫它就怎么写。",
                },
                "type": _TX_TYPE_SCHEMA,
                "amountCents": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 9007199254740991,
                    "description": "每次自动记录的金额，单位是分。",
                },
                "categoryName": {
                    "type": "string",
                    "description": "分类名，必须是与 type 同侧的现有分类的完整名称。",
                },
                "accountName": {
                    "type": "string",
                    "description": "账户名，必须是现有账户的完整名称；用户没提就省略。",
                },
                "repeat": {
                    "type": "string",
                    "enum": ["daily", "weekly", "monthly", "yearly"],
                    "description": "重复方式：daily=每天、weekly=每周、monthly=每月、yearly=每年。",
                },
                "startDate": {
                    **_BIZ_DATE_SCHEMA,
                    "description": "首次生效日期 YYYY-MM-DD（含当天）。",
                },
                "endDate": {
                    "type": ["string", "null"],
                    "format": "date",
                    "pattern": r"^\d{4}-\d{2}-\d{2}$",
                    "description": "结束日期 YYYY-MM-DD（含当天）；一直重复就不要填。",
                },
                "runHour": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 23,
                    "description": "每天几点生成（0-23），不填默认 8 点。",
                },
                "runMinute": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 59,
                    "description": "生成时刻的分钟（0-59），不填默认 0 分。",
                },
                "note": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "规则的备注（会拼进自动生成的记录的备注里）；没有就省略。",
                },
                "includeInBudget": {
                    "type": "boolean",
                    "description": "自动生成的记录是否计入预算，不填默认计入。",
                },
            },
            required=["name", "type", "amountCents", "categoryName", "repeat", "startDate"],
        ),
        handler=_handle_scheduled_create,
    ),
    Tool(
        name="scheduled_update",
        domain=DOMAIN_SCHEDULED,
        kind="write",
        risk="medium",
        proposal_action="scheduled_update",
        description=(
            "修改一条已有的定时记账规则：金额、分类、账户、重复方式、起止日期、"
            "执行时刻、备注、是否计入预算，以及**暂停 / 启用**（enabled=false 暂停）。"
            "用户说「房租涨到 3500 了，改一下」「早餐那条先暂停」时用它。"
            "ruleName 必须与现有规则完全一致（先用 scheduled_list 核对）。"
            "已经生成的历史记录不受影响。要新建请用 scheduled_create。"
        ),
        parameters=_object_schema(
            {
                "ruleName": {
                    "type": "string",
                    "description": "要修改的规则名，必须与现有规则完全一致。",
                },
                "newName": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 64,
                    "description": "新的规则名；不改名字就不要填。",
                },
                "type": _TX_TYPE_SCHEMA,
                "amountCents": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 9007199254740991,
                    "description": "新的金额，单位是分；不改就不要填。",
                },
                "categoryName": {
                    "type": "string",
                    "description": "新的分类名；不换分类就不要填。改收支类型时请同时给 type。",
                },
                "accountName": {
                    "type": "string",
                    "description": "新的账户名；不换账户就不要填。",
                },
                "repeat": {
                    "type": "string",
                    "enum": ["daily", "weekly", "monthly", "yearly"],
                    "description": "新的重复方式；不改就不要填。",
                },
                "startDate": {
                    **_BIZ_DATE_SCHEMA,
                    "description": "新的首次生效日期；不改就不要填。",
                },
                "endDate": {
                    "type": ["string", "null"],
                    "format": "date",
                    "pattern": r"^\d{4}-\d{2}-\d{2}$",
                    "description": "新的结束日期；给 null 表示改成一直重复。",
                },
                "runHour": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 23,
                    "description": "新的执行小时（0-23）；不改就不要填。",
                },
                "runMinute": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 59,
                    "description": "新的执行分钟（0-59）；不改就不要填。",
                },
                "note": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "新的备注；清空就传空字符串。",
                },
                "includeInBudget": {
                    "type": "boolean",
                    "description": "是否计入预算；不改就不要填。",
                },
                "enabled": {
                    "type": "boolean",
                    "description": "false=暂停这条规则（不再自动记账），true=恢复。用户说「暂停 / 恢复」时给。",
                },
            },
            required=["ruleName"],
        ),
        handler=_handle_scheduled_update,
    ),
    Tool(
        name="scheduled_archive",
        domain=DOMAIN_SCHEDULED,
        kind="write",
        risk="medium",
        proposal_action="scheduled_archive",
        description=(
            "归档（删掉）一条定时记账规则，之后不再自动记账。用户说「房租那条规则"
            "删了吧」「不要自动记了」时用它——**只是暂时不想用请改用 "
            "scheduled_update 的 enabled=false**，那更接近用户通常的意思。"
            "已经生成的记录不受影响。"
        ),
        parameters=_object_schema(
            {
                "ruleName": {
                    "type": "string",
                    "description": "要归档的规则名，必须与现有规则完全一致。",
                },
            },
            required=["ruleName"],
        ),
        handler=_handle_scheduled_archive,
    ),
    # ── 角色域（T-M7-05c）───────────────────────────────────────────────
    Tool(
        name="persona_list",
        domain=DOMAIN_PERSONA,
        kind="read",
        risk="low",
        description=(
            "列出全部聊天角色（名称、头像、人设描述）。用户问「我有哪些角色」"
            "「知性姐姐的人设是怎么写的」时用它。**它只读不改**：改人设用 "
            "persona_update、换当前角色用 persona_switch——用户要的是「改」时，"
            "列完清单还要把那个工具调下去。**结果里没有「当前角色」标记**"
            "——那个指针存在设备本地，服务端看不到；用户问「现在用的是哪个角色」"
            "时如实说明要他在角色页上看，不要猜。"
        ),
        parameters=_object_schema({}),
        handler=_handle_persona_list,
    ),
    Tool(
        name="persona_create",
        domain=DOMAIN_PERSONA,
        kind="write",
        risk="low",
        proposal_action="persona_create",
        description=(
            "新建一个聊天角色。用户说「再建一个角色叫猫娘」「加一个会毒舌的角色」"
            "时用它。persona 是这个角色的人设描述（会作为系统提示注入对话），"
            "用户怎么描述就怎么写，**不要替他编造人设**；他没说就留空并在回复里"
            "说明人设是空的。"
        ),
        parameters=_object_schema(
            {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 64,
                    "description": "角色名，用户怎么称呼它就怎么写。",
                },
                "avatar": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "头像（一个 emoji 或短字符串）；用户没提就不要填。",
                },
                "persona": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "人设描述原文；用户没说就省略或留空。",
                },
            },
            required=["name"],
        ),
        handler=_handle_persona_create,
    ),
    Tool(
        name="persona_update",
        domain=DOMAIN_PERSONA,
        kind="write",
        risk="medium",
        proposal_action="persona_update",
        description=(
            "修改一个已有角色的名称、头像或人设描述。用户说「把猫娘改叫小猫咪」"
            "「让知性姐姐说话再温柔些」（→ 改 persona）时用它。personaName 必须与"
            "现有角色完全一致（先用 persona_list 核对）。**只改当前用哪个角色**请用 "
            "persona_switch，那是另一件事。"
        ),
        parameters=_object_schema(
            {
                "personaName": {
                    "type": "string",
                    "description": "要修改的角色名，必须与现有角色完全一致。",
                },
                "newName": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 64,
                    "description": "新的角色名；不改名字就不要填。",
                },
                "avatar": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "新的头像；不换头像就不要填。",
                },
                "persona": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "新的人设描述（整段替换，不是追加）；不改就不要填。",
                },
            },
            required=["personaName"],
        ),
        handler=_handle_persona_update,
    ),
    Tool(
        name="persona_archive",
        domain=DOMAIN_PERSONA,
        kind="write",
        risk="medium",
        proposal_action="persona_archive",
        description=(
            "归档（删掉）一个聊天角色。用户说「猫娘这个角色不要了」时用它。"
            "归档后角色不再出现在角色页。归档前请确认用户说的就是这个角色。"
        ),
        parameters=_object_schema(
            {
                "personaName": {
                    "type": "string",
                    "description": "要归档的角色名，必须与现有角色完全一致。",
                },
            },
            required=["personaName"],
        ),
        handler=_handle_persona_archive,
    ),
    Tool(
        name="persona_switch",
        domain=DOMAIN_PERSONA,
        kind="write",
        risk="low",
        proposal_action="persona_switch",
        description=(
            "把**当前聊天角色**切换成某一个（之后聊天就用这个人设）。用户说"
            "「换成猫娘」「用知性姐姐跟我聊」时用它。personaName 必须是现有角色"
            "的完整名称（先用 persona_list 核对）。这只切当前用哪个角色，"
            "不改角色本身的内容——那用 persona_update。"
        ),
        parameters=_object_schema(
            {
                "personaName": {
                    "type": "string",
                    "description": "要切换到的角色名，必须与现有角色完全一致。",
                },
            },
            required=["personaName"],
        ),
        handler=_handle_persona_switch,
    ),
    # ── meta 域（T-M7-05j：执行位置从设备迁回服务端）─────────────────────
    Tool(
        name="meta_set",
        domain=DOMAIN_META,
        kind="write",
        risk="low",
        proposal_action="meta_set",
        description=(
            "设置用户级偏好：默认账本（记账时默认用哪个账本）/ 默认账户（新记录默认"
            "挂在哪个账户上）/ 小组件配置（桌面小组件的显示选项 JSON）/ 用户画像"
            "（**设置页「用户设定」表单里的那份个人信息**，也是 AI 注入上下文的那"
            "一份）。用户说「默认用旅行账本」「以后都记在招行卡上」「把我的风格设成"
            "保守型」「**以后叫我老王**」「我的消费偏好是保守一点」时用它。"
            "**这只改默认值 / 配置，不动任何已有记录。** 要新增 / 修改账本或账户"
            "本身，请用 book_* / account_* 那几个工具。**当前角色**请用 "
            "persona_switch（单独 op，不与本工具混用，理由见 §7.2 经验上限）。"
        ),
        parameters=_object_schema(
            {
                "key": {
                    "type": "string",
                    "enum": [
                        "default_book", "default_account",
                        "widget_config", "user_profile",
                    ],
                    "description": (
                        "default_book=默认账本；default_account=默认账户；"
                        "widget_config=小组件配置（同时传 config 字段，JSON 字符串）；"
                        "user_profile=用户画像（同时传 profile 字段，JSON 字符串，"
                        "**只含这次要改的键**）。"
                    ),
                },
                "bookName": {
                    "type": "string",
                    "description": "key=default_book 时填：选哪个账本，必须是现有账本的完整名称。不填表示当前账本。",
                },
                "accountName": {
                    "type": "string",
                    "description": "key=default_account 时填：选哪个账户，必须是现有账户的完整名称。",
                },
                "config": {
                    "type": "string",
                    "description": "key=widget_config 时填：小组件配置 JSON 字符串。",
                },
                "profile": {
                    "type": "string",
                    "description": (
                        "key=user_profile 时填：用户画像 JSON **对象**字符串。"
                        "**只写用户这次要改的键**——没提到的键一律不要出现，它们在"
                        "服务端按浅合并保留原值（写全量会覆盖掉用户原本填好的其它"
                        "字段，那是静默数据丢失）。"
                        f"可用键固定为这 7 个：{_PROFILE_KEYS_HINT}。"
                        "用户说「以后叫我老王」「以后称呼我为老板」「我的说话偏好"
                        "是温柔一点」「语气温柔一点」时用它——这些内容在设置页的"
                        "「用户设定」表单里，用户能直接看到和修改。"
                        "**注意**：改的是 AI 对你的称呼，不是账号昵称——用户明确说"
                        "「账号昵称」时才用 auth_update_profile。"
                    ),
                },
            },
            required=["key"],
        ),
        handler=_handle_meta_set,
    ),
    # ── 分析域（05d）────────────────────────────────────────────────────
    Tool(
        name="stats_monthly",
        domain=DOMAIN_ANALYSIS,
        kind="read",
        risk="low",
        description=(
            "查某个月的收支全貌：收入、支出、结余、笔数、支出与收入的分类明细、"
            "当月预算执行情况、最大的一笔收入与支出，以及包含该月在内的近 6 个月"
            "趋势。用户问「这个月花了多少」「上个月怎么样」「预算超了吗」时用它。"
            "要跨月对比请用 stats_trends。金额单位是分。"
        ),
        parameters=_object_schema({"period": _MONTH_SCHEMA}, required=["period"]),
        handler=_handle_stats_monthly,
    ),
    Tool(
        name="stats_trends",
        domain=DOMAIN_ANALYSIS,
        kind="read",
        risk="low",
        description=(
            "查一段时间的逐月收支趋势，用来做跨月对比（「最近半年」「今年前三个月」）。"
            "起止月份都要填，区间内没有记录的月份也会以 0 出现。"
            "只看某一个月请用 stats_monthly。金额单位是分。"
        ),
        parameters=_object_schema(
            {
                "periodFrom": {**_MONTH_SCHEMA, "description": "起始月份 YYYY-MM（含）。"},
                "periodTo": {**_MONTH_SCHEMA, "description": "结束月份 YYYY-MM（含）。"},
            },
            required=["periodFrom", "periodTo"],
        ),
        handler=_handle_stats_trends,
    ),
    # ── 导出域（05d）────────────────────────────────────────────────────
    Tool(
        name="export_create",
        domain=DOMAIN_EXPORT,
        kind="write",
        risk="medium",
        proposal_action="export_create",
        description=(
            "把当前账本的流水导出成文件。用户说「把账导出来」「导一份 CSV」时用它。"
            "导出文件 24 小时内有效，之后会被自动清理；文件生成后要请用户到"
            "「设置 → 数据导出」里下载。"
        ),
        parameters=_object_schema(
            {
                "format": {
                    "type": "string",
                    "enum": ["json", "csv"],
                    "description": (
                        "导出格式：json=结构化数据，csv=表格文件。用户没指定时用 json。"
                    ),
                },
            },
            required=["format"],
        ),
        handler=_handle_export_create,
    ),
    Tool(
        name="export_list",
        domain=DOMAIN_EXPORT,
        kind="read",
        risk="low",
        description=(
            "列出这个账号已经建过的导出任务（格式、状态、生成时间、过期时间、文件大小）。"
            "用户问「我之前导出的文件还在吗」时用它。结果里没有下载地址——下载请让"
            "用户到「设置 → 数据导出」里做。"
        ),
        parameters=_object_schema({}),
        handler=_handle_export_list,
    ),
    # ── 反馈与隐私域（05d）──────────────────────────────────────────────
    Tool(
        name="feedback_submit",
        domain=DOMAIN_PRIVACY,
        kind="write",
        risk="medium",
        proposal_action="feedback_submit",
        description=(
            "把用户对产品的反馈提交给开发团队。用户说「我要反馈一个问题」"
            "「建议加个功能」并给出了具体内容时用它。content 用用户的原话，"
            "不要替他改写、总结或补充他没说的事；分类拿不准就用 other。"
        ),
        parameters=_object_schema(
            {
                "category": {
                    "type": "string",
                    "enum": ["bug", "suggestion", "other"],
                    "description": "反馈类型：bug=功能出问题，suggestion=功能建议，other=其它。",
                },
                "content": {
                    "type": "string",
                    "minLength": 1,
                    # 4000 = telemetry 的 MAX_CONTENT_CHARS（超长会被服务端截断）。
                    "maxLength": 4000,
                    "description": "反馈正文，用用户的原话。",
                },
                "contact": {
                    "type": "string",
                    # 128 = telemetry 的 MAX_CONTACT_CHARS。
                    "maxLength": 128,
                    "description": "用户主动留下的联系方式；他没给就省略。",
                },
            },
            required=["category", "content"],
        ),
        handler=_handle_feedback_submit,
    ),
    Tool(
        name="consent_record",
        domain=DOMAIN_PRIVACY,
        kind="write",
        risk="medium",
        proposal_action="consent_record",
        description=(
            "记录这台设备已同意隐私政策。**只有用户在对话里明确表达了同意**"
            "（「我同意隐私政策」）时才用它——不要替他默认同意，也不要在他只是"
            "询问政策内容时调用。同意之后同步功能才会可用。"
        ),
        parameters=_object_schema(
            {
                "policyVersion": {
                    "type": "string",
                    "description": "隐私政策版本，用户没提就省略（由设备按当前弹窗版本填）。",
                },
            },
        ),
        handler=_handle_consent_record,
    ),
    Tool(
        name="consent_status",
        domain=DOMAIN_PRIVACY,
        kind="read",
        risk="low",
        description=(
            "查这个账号是否同意过隐私政策，以及同意的是哪一版、什么时候同意的。"
            "用户问「我同意过隐私政策吗」时用它。没同意过时只返回 hasConsented=false。"
        ),
        parameters=_object_schema({}),
        handler=_handle_consent_status,
    ),
    # ── 安全账号域（05d）────────────────────────────────────────────────
    Tool(
        name="auth_update_profile",
        domain=DOMAIN_SECURITY,
        kind="write",
        risk="medium",
        proposal_action="auth_update_profile",
        description=(
            "修改用户**账号资料里的昵称**——「账号安全」页面顶部显示的那个名字。"
            "只有当用户明确说「账号昵称」「把昵称改成…」时用它。"
            "**用户说「以后叫我小王」「以后称呼我为老板」时不要用它**：那是希望 AI "
            "换个叫法（AI 对他的称呼来自用户画像的 nickname，见 meta_set），不是改"
            "账号资料——用错工具用户会发现「叫他新名字 AI 还是不理」。"
            "只能改昵称——手机号不能通过对话修改，用户要换手机号请让他到设置页去。"
        ),
        parameters=_object_schema(
            {
                "nickname": {
                    "type": "string",
                    "minLength": 1,
                    # 64 = auth 的 UpdateProfileRequest.nickname 的 max_length。
                    "maxLength": 64,
                    "description": "新的昵称，1-64 个字符。",
                },
            },
            required=["nickname"],
        ),
        handler=_handle_auth_update_profile,
    ),
    Tool(
        name="account_deletion_request",
        domain=DOMAIN_SECURITY,
        kind="write",
        risk="high",
        proposal_action="account_deletion_request",
        description=(
            "申请注销账号。账号会进入 7 天冷静期：期间同步与数据上报立即停止，"
            "7 天后账号与全部数据被永久删除。**只有用户明确要求注销账号时才能调用**，"
            "调用前必须先向他讲清楚后果。用户只是想退出登录时不要用它——"
            "退出登录在设置页里做。"
        ),
        parameters=_object_schema({}),
        handler=_handle_account_deletion_request,
    ),
    # ── 同步域（05d）────────────────────────────────────────────────────
    Tool(
        name="sync_status",
        domain=DOMAIN_SYNC,
        kind="read",
        risk="low",
        description=(
            "查同步能不能跑：账号是否处于可同步状态（注销冷静期、未同意隐私政策都会"
            "让同步停摆），以及服务端最后一笔数据变更发生在什么时候。"
            "用户问「怎么同步不了」「我的数据同步了吗」时先用它排查。"
        ),
        parameters=_object_schema({}),
        handler=_handle_sync_status,
    ),
    Tool(
        name="sync_trigger",
        domain=DOMAIN_SYNC,
        kind="write",
        risk="low",
        proposal_action="sync_trigger",
        description=(
            "让这台设备立刻同步一次：把本机的改动推上去、把云端的变更拉下来。"
            "用户说「同步一下」时用它。正常情况下 App 会自己同步，"
            "用户通常不需要手动触发。"
        ),
        parameters=_object_schema({}),
        handler=_handle_sync_trigger,
    ),
    # ── 提醒域（尾卡 / R-PROD-38）──────────────────────────────────────
    Tool(
        name="reminder_set",
        domain=DOMAIN_REMINDER,
        kind="write",
        risk="low",
        proposal_action="reminder_set",
        description=(
            "给**这台设备**设每日提醒：到点弹一条通知。用户说「提醒我」"
            "「到点叫我」「每天九点提醒我记账」时用它。"
            "它**不是**定时记账——「每月一号自动记一笔房租」要用 scheduled_create。"
            "两者容易混：定时记账是让服务端按期落一笔账，提醒只是弹通知、不动账本。"
            "用户只是想静音某条通知时也不用它（那是系统设置里的事）。"
        ),
        parameters=_object_schema(
            {
                "enabled": {"type": "boolean", "description": "开还是关"},
                "hour": {
                    "type": "integer", "minimum": 0, "maximum": 23,
                    "description": "24 小时制的小时，例如晚上八点是 20",
                },
                "minute": {
                    "type": "integer", "minimum": 0, "maximum": 59,
                    "description": "分钟，例如八点整是 0",
                },
            },
            required=["enabled", "hour", "minute"],
        ),
        handler=_handle_reminder_set,
    ),
    # ── 帮助域（05i）────────────────────────────────────────────────────
    Tool(
        name="help_search",
        domain=DOMAIN_HELP,
        kind="read",
        risk="low",
        description=(
            "查这个 App 自己的用法：功能在哪里、怎么设置、有什么限制"
            "（「怎么导出数据」「在哪改默认账户」「预算怎么用」「能不能语音记账」）。"
            "用户问的是**产品怎么用**时先用它，不要凭印象回答；答案在返回的条目里，"
            "照条目说，条目里没有的就如实说不知道。"
            "它**不能**记账、查账或改任何数据（那些用别的工具），也**不回答理财问题**"
            "——没有行情、产品或收益建议。"
        ),
        parameters=_object_schema(
            {
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 200,
                    "description": (
                        "用户的问题原话或其中的关键词，例如「怎么导出」「默认账户」"
                        "「定时记账」。用中文，不要翻译成英文。"
                    ),
                },
            },
            required=["query"],
        ),
        handler=_handle_help_search,
    ),
)

TOOLS_BY_NAME: dict[str, Tool] = {tool.name: tool for tool in TOOLS}

#: **op（``proposal_action``）→ 风险等级**。审批策略的判定量是 **op**，不是工具名
#: （``AgentLoop._handle_proposal`` 把 ``canonical_op(tool.proposal_action)`` 交给
#: :func:`agent.confirm_reason`），所以索引按 op 建。**两个工具可以共用一个 op**
#: ——``transaction_add_batch`` 的 ``proposal_action`` 就是 ``transaction_add``
#: （单条与批量的差别只在 payload 的条数）。共用时**风险必须一致**：不一致在这里
#: 就炸，而不是让「先注册的那个」静默赢。
TOOL_RISK_BY_OP: dict[str, ToolRisk] = {}
for _tool in TOOLS:
    if _tool.proposal_action is None:
        continue
    _seen = TOOL_RISK_BY_OP.setdefault(_tool.proposal_action, _tool.risk)
    if _seen != _tool.risk:
        raise RuntimeError(
            f"op {_tool.proposal_action!r} 被两个工具标成了不同的风险"
            f"（{_seen} / {_tool.risk}）——共用一个 op 的工具必须同级"
        )
del _tool, _seen

#: **不可自动执行的 op**：即使单 Step + 高置信，也必须过确认卡（架构 §7.4）。
#: 判据不是「会写库」（写库的 op 有三十多个），而是**后果不可通过 App 内的任何
#: 操作挽回**——两个都达标：``account_deletion_request`` 把账号冻结进 7 天冷静期，
#: 期间同步与遥测全停（``sync/routes.py`` / ``telemetry/routes.py`` 的 frozen
#: 分支），用户唯一能做的是撤销；``book_delete`` 删掉的账本**连同里面的全部流水**
#: 一并不可见（本工具集里唯一的批量删除语义）。
#:
#: **从注册表派生，不另写一份**：这正是 R-PROD-47「同一把尺子有三层、必须同时是
#: 64」的同族陷阱——``frozenset({"account_deletion_request"})`` 手写在两个文件里，
#: 迟早有一处忘记跟。守护用例（``tests/test_ai_confirmation_policy.py``）另钉两条：
#: 本集合 == 「注册表里 risk=="high" 的 op」，且 == 「risk=="high" 的**工具名**」
#: ——第二条把「名与 op 恰好同名」也钉住，防将来出现一个名字与 op 不同名的高风险
#: 工具（那时按名查的口径会静默漏掉它）。
HIGH_RISK_OPS: frozenset[str] = frozenset(
    op for op, risk in TOOL_RISK_BY_OP.items() if risk == "high"
)


# ---------------------------------------------------------------------------
# 查询与注入
# ---------------------------------------------------------------------------


def tool_supports_contract(tool: Tool, contract_version: int) -> bool:
    """这个工具能否在指定契约车道上**产出可用的提案**（T-M7-05b）。

    读工具与两条车道都无关（结果只回喂模型，不进客户端校验器），一律 ``True``。
    写工具只有 ``V1_LANE_OPS`` 里的 op 能在 v1 车道上用——v1 的写形状只有根上的
    ``transactions``，且 ``/ai/execute`` 的 v1 分支只放行它的两个别名。
    **其余写工具在 v1 车道上一律不可注入**：让模型看见一个产不出可用提案的工具
    等于给它一条死路（提案被旧校验器整条判无效，卡片不出、写入静默失败）。

    未知的 ``contract_version`` 按 v1 处理（**倾向少注入**而不是多注入：多注入
    的后果是死路，少注入的后果只是能力不可用）。
    """
    if tool.kind == "read":
        return True
    if contract_version != PROPOSAL_CONTRACT_V2:
        return tool.proposal_action in V1_LANE_OPS
    return True


def tools_for_domains(
    domains: Iterable[str],
    *,
    contract_version: int = PROPOSAL_CONTRACT_V1,
) -> list[Tool]:
    """按域取工具（架构 §7.2 的分组注入），并按契约车道过滤（05b）。

    域未知时该域贡献 0 个工具而非报错——域路由的输入可能来自配置或预路由，
    不该因为一个笔误让整轮对话失败。P1 尚未启用域路由（架构 §12.2 已定表
    第 2 项），loop 直接注入全部域即可。

    ``contract_version`` **默认 v1**：调用方忘了传时拿到的是「旧客户端那一套」，
    行为与 P1 逐字一致（fail-closed，见 :func:`tool_supports_contract`）。
    """
    wanted = set(domains)
    return [
        tool for tool in TOOLS
        if tool.domain in wanted and tool_supports_contract(tool, contract_version)
    ]


def get_tool(name: str) -> Tool | None:
    """按名取工具；未知名字返回 ``None``（降级契约的入口）。"""
    return TOOLS_BY_NAME.get(name)


#: M8-成本：provider 载荷里的描述上限（字符）。工具描述截到**首句**、参数描述截
#: 到一句——描述只占全量 schema 的 20%/13%，但仍是每个 Step 都要重发的纯文本；
#: 参数名/类型/required/enum **一个不动**（模型选择靠它们）。
_TOOL_DESC_LIMIT = 80
_PARAM_DESC_LIMIT = 40


def _compact_text(text: str, limit: int) -> str:
    """截到首行首句，超过 ``limit`` 再硬截。保留语义、去掉示例与展开说明。"""
    if not text:
        return text
    first = text.strip().split("\n", 1)[0]
    for sep in ("。", "；"):
        if sep in first:
            first = first.split(sep, 1)[0] + sep
            break
    return first if len(first) <= limit else first[: limit - 1] + "…"


def _compact_parameters(
    params: dict[str, Any], *, drop_descriptions: bool,
) -> dict[str, Any]:
    """参数结构原样保留，只压缩/去掉 ``description`` 文本（递归到 items/properties）。"""

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for key, value in node.items():
                if key == "description" and isinstance(value, str):
                    if not drop_descriptions:
                        out[key] = _compact_text(value, _PARAM_DESC_LIMIT)
                else:
                    out[key] = walk(value)
            return out
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    return walk(params)


def to_openai_tools(
    tools: Sequence[Tool], *, compact: bool = False, bare: bool = False,
) -> list[dict[str, Any]]:
    """转成 OpenAI 兼容的 ``tools`` 载荷（架构 §7.1 的服务端工具清单）。

    ``compact``（M8-成本，默认由 agent 打开）：工具/参数描述截到首句；``bare``：
    在 ``compact`` 之上**去掉参数描述**（兜底轮——工具少、模型对这四个域熟悉，
    参数名足够）。两者都不改参数名、类型、required、enum。
    """
    out: list[dict[str, Any]] = []
    for tool in tools:
        description = tool.description
        parameters = tool.parameters
        # **写工具的完整描述不压缩**：它承载安全语义（「一次只删一条」「必须先读
        # 再写」），截断会让模型误用——语料实测 TX-NEG-01 在截断后对「把所有的
        # 交易记录都删掉」循环调用了 14 次 delete、产出 5 个提案。读工具描述与
        # 所有参数描述照常压缩。
        if (compact or bare) and tool.kind != "write":
            description = _compact_text(description, _TOOL_DESC_LIMIT)
        if bare:
            parameters = _compact_parameters(parameters, drop_descriptions=True)
        elif compact:
            parameters = _compact_parameters(parameters, drop_descriptions=False)
        out.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": description,
                "parameters": parameters,
            },
        })
    return out


_PARAM_VALIDATORS: dict[str, Draft7Validator] = {
    # format: date 必须真的被断言（validator.py 的同款 checker），
    # 否则 2026-02-30 这类不存在的日期会被 pattern 放过。
    tool.name: Draft7Validator(tool.parameters, format_checker=_FORMAT_CHECKER)
    for tool in TOOLS
}

# jsonschema 校验器名 → 中文提示。**刻意不回传原生 message**：原生措辞会
# 回显实例值（"'transfer' is not one of [...]"），而实例值可能是用户原话。
_ARG_ERROR_HINTS: dict[str, str] = {
    "required": "缺少必填参数",
    "type": "参数类型不对",
    "enum": "取值不在允许范围内",
    "minimum": "数值太小",
    "maximum": "数值太大",
    "format": "格式不合法（日期必须是真实存在的 YYYY-MM-DD）",
    "pattern": "格式不合法",
    "minLength": "内容太短",
    "maxLength": "内容太长",
    "minItems": "至少需要一项",
    "maxItems": "项数超过上限",
    "additionalProperties": "出现了不支持的参数",
}


def _describe_arg_error(err: Any) -> list[dict[str, str]]:
    """把一条 jsonschema 错误压成安全的字段路径 + 规则 + 中文提示。

    ``required`` 的 ``absolute_path`` 指向**对象**（缺字段的那个），缺失字段名
    要自己接在后面——否则 ``add_batch`` 里第 2 笔的错误会丢掉 ``transactions/1``
    这段，模型看不出是哪一笔、容易改错行。
    """
    hint = _ARG_ERROR_HINTS.get(err.validator, "参数不合法")
    prefix = "/".join(str(p) for p in err.absolute_path)
    if err.validator == "required":
        instance = err.instance if isinstance(err.instance, Mapping) else {}
        return [
            {"path": f"{prefix}/{name}" if prefix else name, "rule": "required", "message": hint}
            for name in err.validator_value if name not in instance
        ]
    return [{"path": prefix or "(root)", "rule": str(err.validator), "message": hint}]


async def invoke_tool(
    name: str,
    ctx: ToolContext,
    arguments: Mapping[str, Any] | None = None,
) -> ToolOutcome:
    """校验参数并执行一个工具。

    未知工具名 / 参数不合 schema 都不抛异常，而是返回带安全错误码的
    ``ToolOutcome.failure``——loop 把它作为 tool result 回喂给模型，模型据此
    改选工具或修正参数，不必中断整轮对话（架构 §11.1）。
    """
    tool = get_tool(name)
    if tool is None:
        return ToolOutcome.failure(ERR_UNKNOWN_TOOL, argument=name)

    # 参数可能不是对象（模型把 arguments 写成 JSON 字符串、数字或数组）。
    # 这是「模型输出不可信」的一部分，同样走降级而非抛异常。
    if arguments is not None and not isinstance(arguments, Mapping):
        return ToolOutcome.failure(
            ERR_INVALID_ARGUMENTS,
            argument=name,
            details=[{"path": "(root)", "rule": "type", "message": "参数必须是一个 JSON 对象"}],
        )

    params = dict(arguments or {})
    errors = sorted(
        _PARAM_VALIDATORS[name].iter_errors(params),
        key=lambda e: list(e.absolute_path),
    )
    if errors:
        details: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for err in errors:
            for detail in _describe_arg_error(err):
                key = (detail["path"], detail["rule"])
                if key in seen:
                    continue
                seen.add(key)
                details.append(detail)
                if len(details) >= 5:
                    break
            if len(details) >= 5:
                break
        return ToolOutcome.failure(ERR_INVALID_ARGUMENTS, argument=name, details=details)

    return await tool.handler(ctx, params)
