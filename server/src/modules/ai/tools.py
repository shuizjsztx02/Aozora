"""T-M7-02c: Agent 工具注册表（P1 最小集）。

工具是服务端 Agent 的能力面。**工具的 ``description`` 是模型可见的唯一语义
来源**（架构 §7.1），因此这里的文案按「模型读到它会做什么」来写，而不是按
「这个函数做什么」来写。

P1 范围（架构 §10 的 P1 期）
- **记账域**（``transaction.*``）：2 写 + 3 读
- **预算域**（``budget.*``）：5 读
- **账户账本域**（``account_*`` / ``book_*`` / ``category_list``）：5 读

工具名一律用下划线，**不沿用桥命令的 ``domain.action`` 点号命名**——provider
对 ``function.name`` 只接受 ``[a-zA-Z0-9_-]``，带点号会 400。约束、实测证据与
兜底用例见 ``PROVIDER_TOOL_NAME_PATTERN`` 与 ``test_tool_names_are_provider_safe``。

不做全量：P4 才扩到 77 个 bridge 命令对应的全集（架构 §3.2）。

写工具与 ActionProposal 的对齐（本模块最关键的约束）
--------------------------------------------------
写工具**不直接执行**，只产出 ActionProposal 草案（架构 §7.3）；草案交由
02e 的提案组装器补齐 ``confidence`` / ``proposalId`` / ``requiresConfirmation``
后，走 ``validate_proposal`` → 确认门 → ``execute_proposal``。

草案的 ``action`` 必须落在 ``contracts/action-proposal/schema.json`` 的 action
枚举内，且必须是**服务端可执行**的那几个——``AIService.execute_proposal``
只接受 ``add_tx`` / ``add_tx_batch``（其余 action 抛 400）。因此 P1 的写工具
只存在于记账域；预算 / 账户 / 账本的写操作目前由客户端本地 config 链路承载
（``prompts.py`` 的 6 个 config op），迁到服务端属 ActionProposal v2 的工作，
归 P4。

铁律（架构 §7.4，agent 化不放松）
1. 模型永不输出 UUID —— 工具参数只接受**对象名**，名→id 由本模块的确定性
   解析完成（``ToolContext`` 携带用户的实际名字集合）。读工具的**结果**里
   也不含任何 id。
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

import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from jsonschema import Draft7Validator
from sqlalchemy import String, case, cast, false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.budget import Budget, BudgetType
from src.models.ledger import Account, Book, Category, Transaction, TxType

from .validator import _FORMAT_CHECKER

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 域标识。工具按域分组注入（架构 §7.2），P1 三个域。
DOMAIN_TRANSACTION = "transaction"
DOMAIN_BUDGET = "budget"
DOMAIN_LEDGER = "ledger"

DOMAINS: tuple[str, ...] = (DOMAIN_TRANSACTION, DOMAIN_BUDGET, DOMAIN_LEDGER)

#: 架构 §7.2 的经验上限：单域工具数超过 8 会显著降低模型的选择准确率。
MAX_TOOLS_PER_DOMAIN = 8

#: provider 对 ``tools[].function.name`` 的字符集约束（OpenAI 兼容规范）。
#: DeepSeek 实测：带点号的名字（``transaction.add``）会返回 **400
#: invalid_request_error**，整轮请求直接失败 —— 而工具名是本模块提供给模型的
#: 唯一调用凭据，名字不合规等于整条 agent 路径不可用（02b 的 MockTransport
#: 单测发现不了：它不经过真实接口）。故工具名一律用下划线，**不沿用桥命令的
#: ``domain.action`` 点号命名**（桥命令是另一套命名空间，不受此约束）。
PROVIDER_TOOL_NAME_PATTERN = r"^[a-zA-Z0-9_-]{1,64}$"

#: P1 工具总数上限。防「顺手把全量工具塞进来」——全量（约 70 个）明确排除
#: 在 P1 之外，见架构 §3.2 与 §10。
MAX_TOOLS_P1 = 20

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
}

ToolKind = Literal["read", "write"]
OutcomeKind = Literal["result", "proposal", "error"]


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

    @classmethod
    def result(cls, data: dict[str, Any]) -> ToolOutcome:
        return cls(kind="result", data=data)

    @classmethod
    def proposal(cls, draft: dict[str, Any]) -> ToolOutcome:
        return cls(kind="proposal", data=draft)

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
    """

    name: str
    domain: str
    kind: ToolKind
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
        select(Category.kind, Category.name, Category.id, Category.book_id).where(
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
    for kind, name, cid, book_scoped in categories:
        if book_scoped is None:
            by_kind.setdefault(kind, {})[name] = str(cid)
    for kind, name, cid, book_scoped in categories:
        if book_scoped is not None:
            by_kind.setdefault(kind, {})[name] = str(cid)

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
    return ToolContext(
        db=db,
        user_id=user_id,
        book_id=book_id,
        categories_by_kind=by_kind,
        category_names_by_id={str(cid): name for _kind, name, cid, _b in categories},
        account_names=_unique_name_map((name, str(aid)) for name, aid in accounts),
        account_names_by_id={str(aid): name for name, aid in accounts},
        book_names=_unique_name_map((name, str(bid)) for name, bid in books),
        book_names_by_id={str(bid): name for name, bid in books},
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
            "拿不准时先调用 category_list 查询。"
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


# ---------------------------------------------------------------------------
# 内部工具函数
# ---------------------------------------------------------------------------


def _format_yuan(cents: int) -> str:
    """分 → 「12.50」形式的元（确定性格式化，模型不参与金额呈现）。"""
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}"


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
        "action": "add_tx",
        "transactions": [item],
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
        "action": "add_tx_batch",
        "transactions": items,
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

    conditions = [
        Transaction.book_id == ctx.book_id,
        Transaction.deleted_at.is_(None),
        Transaction.account_id.is_not(None),
        # 归档账户与 ``account_list`` / 名字映射同口径剔除：它不在清单里、也
        # 解析不到，若这里仍列出来，模型会看到一个用户根本记不进去的账户名
        # （「看得见却记不了」），与分类/预算侧已修过的同一类死路。
        Account.archived_at.is_(None),
    ]
    if date_from is not None:
        conditions.append(Transaction.biz_date >= date_from)
    if date_to is not None:
        conditions.append(Transaction.biz_date <= date_to)

    rows = await ctx.db.execute(
        select(
            Account.id,
            Account.name,
            func.coalesce(
                func.sum(case((Transaction.type == TxType.INCOME, Transaction.amount_cents), else_=0)), 0,
            ),
            func.coalesce(
                func.sum(case((Transaction.type == TxType.EXPENSE, Transaction.amount_cents), else_=0)), 0,
            ),
        )
        .join(Account, Transaction.account_id == Account.id)
        .where(*conditions)
        # 按 id 而非 name 分组：账户没有唯一性约束，两个同名账户按名字分组会把
        # 它们的金额合成一个数——那是错的，不是「简洁」。
        .group_by(Account.id, Account.name)
        .order_by(Account.name, Account.id),
    )
    balances = []
    for _account_id, name, income, expense in rows.all():
        income_int, expense_int = int(income), int(expense)
        balances.append({
            "name": name,
            "incomeCents": income_int,
            "expenseCents": expense_int,
            "netCents": income_int - expense_int,
        })
    return ToolOutcome.result({
        "accounts": balances,
        "note": "只包含有记录关联的账户；未出现在结果里的账户期间内没有流水。同名账户会分列成多条。",
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
        select(Category.id, Category.name, Category.kind, Category.parent_id).where(
            Category.owner_id == ctx.user_id,
            (Category.book_id == book_id) | (Category.book_id.is_(None)),
            Category.archived_at.is_(None),
        ).order_by(Category.kind, Category.created_at),
    )
    raw = rows.all()
    id_to_name = {str(cid): name for cid, name, _kind, _parent in raw}
    categories = [
        {
            "name": name,
            "kind": kind,
            "parentName": id_to_name.get(str(parent_id)) if parent_id else None,
        }
        for cid, name, kind, parent_id in raw
    ]
    return ToolOutcome.result({"categories": categories, "total": len(categories)})


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

TOOLS: tuple[Tool, ...] = (
    # ── 记账域 ──────────────────────────────────────────────────────────
    Tool(
        name="transaction_add",
        domain=DOMAIN_TRANSACTION,
        kind="write",
        proposal_action="add_tx",
        description=(
            "新增一笔收支记录。用户说「记一笔」「花了 30 块打车」「今天收入 500」"
            "这类要往账上添一笔的诉求时用它。金额单位是分，你需要自己把口语金额"
            "换算成分。分类名必须与现有分类完全一致。只记一笔时用它；一次要记"
            "多笔请用 transaction_add_batch。"
        ),
        parameters=_tx_item_schema(),
        handler=_handle_transaction_add,
    ),
    Tool(
        name="transaction_add_batch",
        domain=DOMAIN_TRANSACTION,
        kind="write",
        proposal_action="add_tx_batch",
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
        name="transaction_list",
        domain=DOMAIN_TRANSACTION,
        kind="read",
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
        description=(
            "列出某个月已经超支的预算项（分类名、额度、已用、剩余为负表示超出的"
            "金额）。用户问「我哪些预算超了」「有没有超支」时用它。"
        ),
        parameters=_object_schema({"period": _PERIOD_SCHEMA}, required=["period"]),
        handler=_handle_budget_overspent,
    ),
    # ── 账户账本域 ──────────────────────────────────────────────────────
    Tool(
        name="account_list",
        domain=DOMAIN_LEDGER,
        kind="read",
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
        domain=DOMAIN_LEDGER,
        kind="read",
        description=(
            "按账户汇总金额流向：每个账户在某段时间内的收入、支出与净额。"
            "用户问「招行卡花了多少」「各账户的收支」时用它。"
            "不传日期则统计全部历史。只能统计有关联记录到账户的交易。"
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
        name="book_list",
        domain=DOMAIN_LEDGER,
        kind="read",
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
        domain=DOMAIN_LEDGER,
        kind="read",
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
        name="category_list",
        domain=DOMAIN_LEDGER,
        kind="read",
        description=(
            "列出可用的分类（名称、收支类型、上级分类）。在你不确定分类的准确名称时"
            "用它核对，分类名必须与这里的名称逐字一致才能用于记账。"
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
)

TOOLS_BY_NAME: dict[str, Tool] = {tool.name: tool for tool in TOOLS}


# ---------------------------------------------------------------------------
# 查询与注入
# ---------------------------------------------------------------------------


def tools_for_domains(domains: Iterable[str]) -> list[Tool]:
    """按域取工具（架构 §7.2 的分组注入）。

    域未知时该域贡献 0 个工具而非报错——域路由的输入可能来自配置或预路由，
    不该因为一个笔误让整轮对话失败。P1 尚未启用域路由（架构 §12.2 已定表
    第 2 项），loop 直接注入全部三个域即可。
    """
    wanted = set(domains)
    return [tool for tool in TOOLS if tool.domain in wanted]


def get_tool(name: str) -> Tool | None:
    """按名取工具；未知名字返回 ``None``（降级契约的入口）。"""
    return TOOLS_BY_NAME.get(name)


def to_openai_tools(tools: Sequence[Tool]) -> list[dict[str, Any]]:
    """转成 OpenAI 兼容的 ``tools`` 载荷（架构 §7.1 的服务端工具清单）。"""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in tools
    ]


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
