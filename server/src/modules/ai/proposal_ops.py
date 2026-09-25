"""T-M7-05b / T-M7-05c / T-M7-05d: v2 op 的服务端执行面（``/ai/execute`` 与 loop 的执行器共用）。

v2 的 op 注册表（``contracts/action-proposal/schema-v2.json``）有 34 个成员，
但**能执行的只有登记在本模块的那些**——注册表成员身份是「这是一个提案」，
不是「服务端会执行它」。05b 落地记账域与账户账本域，05c 加上预算存钱 / 定时 /
角色三域（T-M7-01 上云的三实体），05d 补上导出 / 反馈 / 账号四条。

**05d 之后仍有两个注册表成员不在表里，且都是刻意的**（不是待办）：
``account_delete``——它的 payload 要一次性 ``deletionToken``，而凭据不进模型
上下文，AI 路径把「申请 + 确认」合并进 ``account_deletion_request``；
``reminder_set``——执行位置在设备（桥 ``reminder.set``），归 05f 的设备执行器
（见计划 §五 挂账项）。两者都以 ``ai_unsupported_action`` 拒绝。

三条口径
--------
1. **payload 的形状是 op 自己的事**（v2 契约刻意不复制第二份，见
   ``authorization.py``）。所以每个执行体自己解析 payload，形状不对 → 400
   ``ai_invalid_payload``，绝不猜。
2. **执行即权威直写**：服务端自己的写入不会经过客户端 push 链路，因此每次
   写入都必须**同事务登记一条 sync change**，否则任何设备 pull 都看不到它
   （T14-8 的教训：确认卡显示「已记录」而本机查无此账）。
3. **乐观并发只在 payload 带了 version 的 op 上生效**（改类 op）。归档 / 删除
   按对象身份（id）生效，与 REST 的同名端点同口径——不给用户一个他无法解决的
   失败模式。

05c 的离线语义（三类实体在工具路径上的一条明确结论）
---------------------------------------------------
存钱目标 / 定时规则 / 角色**在设备上都有本地副本**（Room 表 + sync 参与双向
同步）。AI 从服务端写它们时，写入的是**云端权威**，并同事务登一条 sync change
（口径 2）——于是：

- **设备在线**：下一次 pull 把这条变更合并进本地副本，用户在存钱页 / 定时页 /
  角色页看到的就是 AI 刚做的那件事（与他自己在表单里做没有区别）。
- **设备离线**：写入**照样成功**（服务端是权威，这条路径不依赖设备在线）；
  设备恢复后 pull 到同一条变更。**不存在「离线时先写本地、上线再合并」的第二
  条路径**——那会造出两个真相源。因此 AI 写入**不需要设备在场**，也不需要
  05f 的设备执行器（那是给本地偏好项准备的，见 ``DEVICE_EXECUTED_OPS``）。
- **定时规则的生成侧不变**：规则由设备上的引擎按 ``last_run_date`` 生成交易，
  服务端只存规则本身；AI 改规则不触发任何补生成。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Awaitable, Callable

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.errors import AppException
from src.models.budget import Budget, BudgetType
from src.models.ledger import Account
from src.models.persona import ChatPersona
from src.models.savings import SavingsGoal
from src.models.scheduled import ScheduledRule
from src.models.sync import SyncOperation
from src.models.user import User

# T-M7-05j: 偏好项上云——``meta_set`` 的执行体写这张表。
from src.models.user_preference import UserPreference
from src.modules.accounts.schemas import AccountCreate, AccountUpdate
from src.modules.accounts.service import AccountService
from src.modules.auth.schemas import UpdateProfileRequest
from src.modules.auth.service import AuthService
from src.modules.books.schemas import BookCreate, BookUpdate
from src.modules.books.service import BookService
from src.modules.categories.schemas import CategoryCreate, CategoryUpdate
from src.modules.categories.service import CategoryService
from src.modules.deletion.service import DeletionService
from src.modules.exports.service import ExportService

# 三域的**字段规则只有一份**，在 sync.service 里（实体上行 push 走的同一条门）。
# 这里 import 私有名是有意的：把它抄成第二份必然漂移——同一份 payload 从 sync
# 进来合法、从 AI 进来非法（或反过来）是最难查的一类不一致。05c 只统一**对外
# 的错误码**（见 ``_checked``）。
from src.modules.sync.service import (  # noqa: PLC2701 - 见上
    _RULE_REPEATS,
    _require_bool,
    _require_int_in_range,
    _validate_amount_cents,
    _validate_current_cents,
    _validate_name,
    _validate_note,
    _validate_savings_period,
)
from src.modules.telemetry.schemas import FeedbackRequest
from src.modules.telemetry.service import TelemetryService
from src.modules.transactions.schemas import TransactionUpdate
from src.modules.transactions.service import TransactionService

# 画像的键集**只有一份**（同 ``tools`` 的用法，见那里的注释）：AI 写进来的键名
# 必须与表单页 ``FIELD_ORDER`` 逐字相同，否则写进去了表单页也显示为空。
from .prompts import _PROFILE_LABELS  # noqa: PLC2701 - 见上
from .tools import DEVICE_EXECUTED_OPS

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OpReceipt:
    """一次 op 执行的结果：受影响的实体。**

    ``entity_type`` 是 sync 的实体类型（``transaction`` / ``category`` /
    ``account`` / ``book``），也是客户端 pull 时的分派键；``entity_ids`` 是本次
    写入的实体 id（改 / 归档是同一个 id 的新版本，创建是新 id）。

    05d 起它也被**不参与 sync 的 op**（导出 / 反馈 / 账号）复用：那时
    ``entity_type`` 只是一个回执标签（``export`` / ``feedback`` / ``user``），
    执行体**不登 sync change**——设备 pull 的实体白名单里没有这几类。
    """

    entity_type: str
    entity_ids: list[uuid.UUID]


def _uuid_field(payload: dict[str, Any], name: str) -> uuid.UUID:
    raw = payload.get(name)
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        raise AppException(400, "ai_invalid_payload", f"Invalid {name}") from None


def _int_field(payload: dict[str, Any], name: str) -> int:
    raw = payload.get(name)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise AppException(400, "ai_invalid_payload", f"Invalid {name}")
    return raw


def _model_or_400(model: type, data: dict[str, Any]) -> Any:
    """按模块的 schema 解析 payload；形状不对 → 400（不是 500）。

    ``extra`` 字段一律忽略：payload 里除了 schema 字段外只有服务端自己解析的
    id，多出来的东西不该让整次执行 500。
    """
    try:
        return model(**data)
    except ValidationError:
        raise AppException(400, "ai_invalid_payload", "Invalid proposal payload") from None


async def _register_change(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
    operation: str,
    version: int,
) -> None:
    """登记一条 sync change（口径 2：服务端权威直写必须自己登记）。"""
    from src.modules.sync.repository import SyncRepository

    await SyncRepository(db).write_change(
        user_id=user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        operation=SyncOperation(operation),
        version=version,
    )


async def register_balance_account_changes(
    db: AsyncSession, *, user_id: uuid.UUID, account_ids: Iterable[uuid.UUID | None],
) -> None:
    """REQ-70（T18-7）：为**余额被记账联动改过**的账户各登一条 sync change。

    交易自身的 sync change 由各执行体自己登（既有分工）；账户是**被联动改的第二个
    实体**，同样必须登记 —— 否则 AI 记一笔之后服务端余额变了而设备 pull 不到，
    用户在账户页看到的仍是旧值（T14-8 的同一条教训）。

    版本取**当前值**（``TransactionService`` 已在同一事务里把 version 加过一了）。
    账户查不到（已硬删 / 不属于该用户）就跳过，与联动本身的静默跳过同口径。
    """
    for account_id in {a for a in account_ids if a is not None}:
        account = await db.scalar(
            select(Account).where(
                Account.id == account_id, Account.owner_id == user_id,
            )
        )
        if account is None:
            continue
        await _register_change(
            db, user_id=user_id, entity_type="account", entity_id=account.id,
            operation="update", version=account.version,
        )


# ---------------------------------------------------------------------------
# payload 取字段（T-M7-05c）
# ---------------------------------------------------------------------------

#: 预算期间（``YYYY-MM``）。与 ``budgets`` 路由的 ``Path(pattern=...)`` 同口径。
_PERIOD_PATTERN = re.compile(r"^\d{4}-\d{2}$")


def _checked(fn: Callable[..., Any], *args: Any) -> Any:
    """跑一条 ``sync.service`` 的字段校验器，把它的 422 翻成 400 ``ai_invalid_payload``。

    规则本身只有一份（见文件头的 import 说明）；这里统一的是**对外的错误码**：
    v2 执行面把「payload 形状不对」一律报 400 ``ai_invalid_payload``（05b 的口径），
    而不是让同一个形状在 sync 入口报 422、在 AI 入口报 400。
    """
    try:
        return fn(*args)
    except AppException:
        raise AppException(400, "ai_invalid_payload", "Invalid proposal payload") from None


def _text_field(payload: dict[str, Any], name: str) -> str:
    """取一个**非空字符串**字段；缺失 / 类型不对 / 空串 → 400。"""
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise AppException(400, "ai_invalid_payload", f"Invalid {name}")
    return value


def _enum_field(payload: dict[str, Any], name: str, allowed: tuple[str, ...]) -> str:
    value = payload.get(name)
    if value not in allowed:
        raise AppException(400, "ai_invalid_payload", f"Invalid {name}")
    return str(value)


def _date_field(payload: dict[str, Any], name: str) -> date:
    """``YYYY-MM-DD`` → ``date``。**不存在的日期**（``2026-02-30``）也算形状不对。"""
    raw = payload.get(name)
    if not isinstance(raw, str):
        raise AppException(400, "ai_invalid_payload", f"Invalid {name}")
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise AppException(400, "ai_invalid_payload", f"Invalid {name}") from None


def _optional_uuid(payload: dict[str, Any], name: str) -> uuid.UUID | None:
    """可空 id 字段：缺席 / ``null`` / 空串都当「没有」。"""
    if payload.get(name) in (None, ""):
        return None
    return _uuid_field(payload, name)


def _optional_text(payload: dict[str, Any], name: str) -> str | None:
    """可空文本字段：缺席 / ``null`` → ``None``（表示「不动它」或「清空」）。"""
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise AppException(400, "ai_invalid_payload", f"Invalid {name}")
    return value


# ---------------------------------------------------------------------------
# 记账域
# ---------------------------------------------------------------------------


async def _execute_transaction_update(
    db: AsyncSession, *, user_id: uuid.UUID, book_id: uuid.UUID, payload: dict[str, Any],
) -> OpReceipt:
    tx_id = _uuid_field(payload, "transactionId")
    data = TransactionUpdate(
        type=payload.get("type"),
        amount_cents=payload.get("amountCents"),
        category_id=(
            _uuid_field(payload, "categoryId") if payload.get("categoryId") else None
        ),
        account_id=(
            _uuid_field(payload, "accountId") if payload.get("accountId") else None
        ),
        note=payload.get("note"),
        biz_date=payload.get("bizDate"),
        version=_int_field(payload, "version"),
    )
    service = TransactionService(db)
    tx = await service.get_transaction(tx_id, user_id)
    # 账本归属：目标必须就在提案的账本里（``get_transaction`` 只校验 owner）。
    if tx.book_id != book_id:
        raise AppException(404, "not_found", "Transaction not found")
    # REQ-70：换账户时**旧账户也要登记**——它被回补了；故在 update 之前取旧值。
    old_account_id = tx.account_id
    tx = await service.update_transaction(tx_id, user_id, data)
    await _register_change(
        db, user_id=user_id, entity_type="transaction", entity_id=tx.id,
        operation="update", version=tx.version,
    )
    await register_balance_account_changes(
        db, user_id=user_id, account_ids=[old_account_id, tx.account_id],
    )
    return OpReceipt(entity_type="transaction", entity_ids=[tx.id])


async def _execute_transaction_delete(
    db: AsyncSession, *, user_id: uuid.UUID, book_id: uuid.UUID, payload: dict[str, Any],
) -> OpReceipt:
    tx_id = _uuid_field(payload, "transactionId")
    service = TransactionService(db)
    tx = await service.get_transaction(tx_id, user_id)
    if tx.book_id != book_id:
        raise AppException(404, "not_found", "Transaction not found")
    account_id = tx.account_id
    await service.delete_transaction(tx_id, user_id)
    # 同一个 session 的同一个实例——``soft_delete`` 已经把 version 加过一了。
    await _register_change(
        db, user_id=user_id, entity_type="transaction", entity_id=tx.id,
        operation="delete", version=tx.version,
    )
    # REQ-70：软删回补了账户余额，它同样要对设备可见
    await register_balance_account_changes(
        db, user_id=user_id, account_ids=[account_id],
    )
    return OpReceipt(entity_type="transaction", entity_ids=[tx.id])


# ---------------------------------------------------------------------------
# 分类域
# ---------------------------------------------------------------------------


async def _execute_category_create(
    db: AsyncSession, *, user_id: uuid.UUID, book_id: uuid.UUID, payload: dict[str, Any],
) -> OpReceipt:
    data = _model_or_400(CategoryCreate, {
        "name": payload.get("name"),
        "kind": payload.get("kind"),
        "book_id": payload.get("bookId"),
        "parent_id": payload.get("parentId"),
    })
    # 分类只能建在本用户的账本下（授权表已校验 bookId，这里是纵深防御）。
    if data.book_id is not None and data.book_id != book_id:
        raise AppException(404, "not_found", "Book not found")
    category = await CategoryService(db).create_category(user_id, data)
    await _register_change(
        db, user_id=user_id, entity_type="category", entity_id=category.id,
        operation="insert", version=category.version,
    )
    return OpReceipt(entity_type="category", entity_ids=[category.id])


async def _execute_category_update(
    db: AsyncSession, *, user_id: uuid.UUID, book_id: uuid.UUID, payload: dict[str, Any],
) -> OpReceipt:
    category_id = _uuid_field(payload, "categoryId")
    data = _model_or_400(CategoryUpdate, {
        "name": payload.get("name"),
        "parent_id": payload.get("parentId"),
        "version": payload.get("version"),
    })
    service = CategoryService(db)
    existing = await service.get_category(category_id, user_id)
    if existing.book_id is not None and existing.book_id != book_id:
        raise AppException(404, "not_found", "Category not found")
    category = await service.update_category(category_id, user_id, data)
    await _register_change(
        db, user_id=user_id, entity_type="category", entity_id=category.id,
        operation="update", version=category.version,
    )
    return OpReceipt(entity_type="category", entity_ids=[category.id])


async def _execute_category_archive(
    db: AsyncSession, *, user_id: uuid.UUID, book_id: uuid.UUID, payload: dict[str, Any],
) -> OpReceipt:
    category_id = _uuid_field(payload, "categoryId")
    service = CategoryService(db)
    existing = await service.get_category(category_id, user_id)
    if existing.book_id is not None and existing.book_id != book_id:
        raise AppException(404, "not_found", "Category not found")
    await service.archive_category(category_id, user_id)
    await _register_change(
        db, user_id=user_id, entity_type="category", entity_id=existing.id,
        operation="delete", version=existing.version,
    )
    return OpReceipt(entity_type="category", entity_ids=[existing.id])


# ---------------------------------------------------------------------------
# 账户域
# ---------------------------------------------------------------------------


async def _execute_account_create(
    db: AsyncSession, *, user_id: uuid.UUID, book_id: uuid.UUID, payload: dict[str, Any],
) -> OpReceipt:
    data = _model_or_400(AccountCreate, {
        "name": payload.get("name"),
        "icon": payload.get("icon"),
        # REQ-69（T18-6）：分类与余额在创建时即可写入；余额缺省 = 0（未校准）
        "account_type": payload.get("accountType"),
        "balance_cents": payload.get("balanceCents", 0),
    })
    account = await AccountService(db).create_account(user_id, data)
    await _register_change(
        db, user_id=user_id, entity_type="account", entity_id=account.id,
        operation="insert", version=account.version,
    )
    return OpReceipt(entity_type="account", entity_ids=[account.id])


async def _execute_account_update(
    db: AsyncSession, *, user_id: uuid.UUID, book_id: uuid.UUID, payload: dict[str, Any],
) -> OpReceipt:
    account_id = _uuid_field(payload, "accountId")
    data = _model_or_400(AccountUpdate, {
        "name": payload.get("name"),
        "icon": payload.get("icon"),
        # REQ-69（T18-6）：None = 不修改（与 name/icon 同一局部合并语义）。
        # 余额拿的是**绝对值**（决策 #4），不是增量——重放不会重复累加。
        "account_type": payload.get("accountType"),
        "balance_cents": payload.get("balanceCents"),
        "version": payload.get("version"),
    })
    account = await AccountService(db).update_account(account_id, user_id, data)
    await _register_change(
        db, user_id=user_id, entity_type="account", entity_id=account.id,
        operation="update", version=account.version,
    )
    return OpReceipt(entity_type="account", entity_ids=[account.id])


async def _execute_account_archive(
    db: AsyncSession, *, user_id: uuid.UUID, book_id: uuid.UUID, payload: dict[str, Any],
) -> OpReceipt:
    account_id = _uuid_field(payload, "accountId")
    service = AccountService(db)
    existing = await service.get_account(account_id, user_id)
    await service.archive_account(account_id, user_id)
    await _register_change(
        db, user_id=user_id, entity_type="account", entity_id=existing.id,
        operation="delete", version=existing.version,
    )
    return OpReceipt(entity_type="account", entity_ids=[existing.id])


# ---------------------------------------------------------------------------
# 账本域
# ---------------------------------------------------------------------------


async def _execute_book_create(
    db: AsyncSession, *, user_id: uuid.UUID, book_id: uuid.UUID, payload: dict[str, Any],
) -> OpReceipt:
    data = _model_or_400(BookCreate, {
        "name": payload.get("name"),
        "icon": payload.get("icon"),
    })
    book = await BookService(db).create_book(user_id, data)
    await _register_change(
        db, user_id=user_id, entity_type="book", entity_id=book.id,
        operation="insert", version=book.version,
    )
    return OpReceipt(entity_type="book", entity_ids=[book.id])


async def _execute_book_update(
    db: AsyncSession, *, user_id: uuid.UUID, book_id: uuid.UUID, payload: dict[str, Any],
) -> OpReceipt:
    target_id = _uuid_field(payload, "bookId")
    data = _model_or_400(BookUpdate, {
        "name": payload.get("name"),
        "icon": payload.get("icon"),
        "version": payload.get("version"),
    })
    book = await BookService(db).update_book(target_id, user_id, data)
    await _register_change(
        db, user_id=user_id, entity_type="book", entity_id=book.id,
        operation="update", version=book.version,
    )
    return OpReceipt(entity_type="book", entity_ids=[book.id])


async def _execute_book_delete(
    db: AsyncSession, *, user_id: uuid.UUID, book_id: uuid.UUID, payload: dict[str, Any],
) -> OpReceipt:
    target_id = _uuid_field(payload, "bookId")
    service = BookService(db)
    existing = await service.get_book(target_id, user_id)
    await service.delete_book(target_id, user_id)
    await _register_change(
        db, user_id=user_id, entity_type="book", entity_id=existing.id,
        operation="delete", version=existing.version,
    )
    return OpReceipt(entity_type="book", entity_ids=[existing.id])


# ---------------------------------------------------------------------------
# 预算域（T-M7-05c）
# ---------------------------------------------------------------------------


async def _find_budget(
    db: AsyncSession,
    *,
    book_id: uuid.UUID,
    period: str,
    category_id: uuid.UUID | None,
) -> Budget | None:
    """某月的总预算（``category_id is None``）或某分类的预算。

    **过滤 ``deleted_at IS NULL``**：被删掉的预算行不能再被「UPSERT」到——
    ``BudgetService.upsert_budget`` 的查询没有这个过滤，走它会让「删了再设」
    改到那条已删的行上（行还在、版本涨了、金额变了，但用户永远看不到它）。
    """
    return await db.scalar(
        select(Budget).where(
            Budget.book_id == book_id,
            Budget.budget_type == BudgetType.MONTHLY,
            Budget.period == period,
            Budget.deleted_at.is_(None),
            Budget.category_id.is_(None) if category_id is None
            else Budget.category_id == category_id,
        ).order_by(Budget.id),
    )


async def _upsert_budget(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    book_id: uuid.UUID,
    payload: dict[str, Any],
    category_id: uuid.UUID | None,
) -> OpReceipt:
    """预算的 UPSERT（``budget_set`` 与 ``budget_set_category`` 共用一个体）。

    只处理**月度**预算（``budget_type=monthly``）：预算工具的期间参数是
    ``YYYY-MM``，年度预算不在这条路径上（与读工具同口径）。
    """
    period = _text_field(payload, "period")
    if not _PERIOD_PATTERN.match(period):
        raise AppException(400, "ai_invalid_payload", "Invalid period")
    amount = _int_field(payload, "amountCents")
    _checked(_validate_amount_cents, amount)

    existing = await _find_budget(
        db, book_id=book_id, period=period, category_id=category_id,
    )
    if existing is None:
        budget = Budget(
            book_id=book_id,
            budget_type=BudgetType.MONTHLY.value,
            period=period,
            amount_cents=amount,
            category_id=category_id,
        )
        db.add(budget)
        await db.flush()
        operation = "insert"
    else:
        # 改已有的额度 → payload 带 version 时按乐观并发校验（口径 3）。
        version = payload.get("version")
        if version is not None:
            if isinstance(version, bool) or not isinstance(version, int):
                raise AppException(400, "ai_invalid_payload", "Invalid version")
            if existing.version != version:
                raise AppException(
                    409, "version_conflict",
                    f"Budget version mismatch: expected {version}, current {existing.version}",
                )
        existing.amount_cents = amount
        existing.version += 1
        await db.flush()
        budget, operation = existing, "update"

    await _register_change(
        db, user_id=user_id, entity_type="budget", entity_id=budget.id,
        operation=operation, version=budget.version,
    )
    return OpReceipt(entity_type="budget", entity_ids=[budget.id])


async def _execute_budget_set(
    db: AsyncSession, *, user_id: uuid.UUID, book_id: uuid.UUID, payload: dict[str, Any],
) -> OpReceipt:
    return await _upsert_budget(
        db, user_id=user_id, book_id=book_id, payload=payload, category_id=None,
    )


async def _execute_budget_set_category(
    db: AsyncSession, *, user_id: uuid.UUID, book_id: uuid.UUID, payload: dict[str, Any],
) -> OpReceipt:
    return await _upsert_budget(
        db, user_id=user_id, book_id=book_id, payload=payload,
        category_id=_uuid_field(payload, "categoryId"),
    )


async def _execute_budget_delete(
    db: AsyncSession, *, user_id: uuid.UUID, book_id: uuid.UUID, payload: dict[str, Any],
) -> OpReceipt:
    """删一项预算（软删，与 sync 的 ``budget`` delete 同一条路：置 ``deleted_at``）。"""
    period = _text_field(payload, "period")
    if not _PERIOD_PATTERN.match(period):
        raise AppException(400, "ai_invalid_payload", "Invalid period")
    category_id = _optional_uuid(payload, "categoryId")

    existing = await _find_budget(
        db, book_id=book_id, period=period, category_id=category_id,
    )
    if existing is None:
        raise AppException(404, "not_found", "Budget not found")
    existing.deleted_at = datetime.now(UTC)
    existing.version += 1
    await db.flush()
    await _register_change(
        db, user_id=user_id, entity_type="budget", entity_id=existing.id,
        operation="delete", version=existing.version,
    )
    return OpReceipt(entity_type="budget", entity_ids=[existing.id])


# ---------------------------------------------------------------------------
# 存钱域（T-M7-05c）
# ---------------------------------------------------------------------------


async def _get_savings(
    db: AsyncSession, goal_id: uuid.UUID, user_id: uuid.UUID,
) -> SavingsGoal:
    goal = await db.scalar(
        select(SavingsGoal).where(
            SavingsGoal.id == goal_id,
            SavingsGoal.owner_id == user_id,
            SavingsGoal.archived_at.is_(None),
        ),
    )
    if goal is None:
        raise AppException(404, "not_found", "Savings goal not found")
    return goal


async def _execute_savings_create(
    db: AsyncSession, *, user_id: uuid.UUID, book_id: uuid.UUID, payload: dict[str, Any],
) -> OpReceipt:
    name = _text_field(payload, "name")
    _checked(_validate_name, name, 128, "name")
    target = _int_field(payload, "targetCents")
    _checked(_validate_amount_cents, target)
    icon = _optional_text(payload, "icon")
    if icon is not None:
        _checked(_validate_name, icon, 64, "icon")
    period_value, period_unit = _checked(
        _validate_savings_period, payload.get("periodValue"), payload.get("periodUnit"),
    )

    goal = SavingsGoal(
        owner_id=user_id, name=name, target_cents=target, current_cents=0,
        icon=icon, period_value=period_value, period_unit=period_unit,
    )
    db.add(goal)
    await db.flush()
    await _register_change(
        db, user_id=user_id, entity_type="savings_goal", entity_id=goal.id,
        operation="insert", version=goal.version,
    )
    return OpReceipt(entity_type="savings_goal", entity_ids=[goal.id])


async def _execute_savings_update(
    db: AsyncSession, *, user_id: uuid.UUID, book_id: uuid.UUID, payload: dict[str, Any],
) -> OpReceipt:
    goal = await _get_savings(db, _uuid_field(payload, "savingsId"), user_id)
    version = _int_field(payload, "version")
    if goal.version != version:
        raise AppException(
            409, "version_conflict",
            f"Savings goal version mismatch: expected {version}, current {goal.version}",
        )
    if "currentCents" in payload and "addAmountCents" in payload:
        # 「直接改成 N」与「在现有基础上加 N」是两件事，同时给没有合理优先级。
        raise AppException(
            400, "ai_invalid_payload",
            "currentCents and addAmountCents are mutually exclusive",
        )

    if "name" in payload:
        name = _text_field(payload, "name")
        _checked(_validate_name, name, 128, "name")
        goal.name = name
    if "targetCents" in payload:
        target = _int_field(payload, "targetCents")
        _checked(_validate_amount_cents, target)
        goal.target_cents = target
    if "icon" in payload:
        icon = _optional_text(payload, "icon")
        if icon is not None:
            _checked(_validate_name, icon, 64, "icon")
        goal.icon = icon
    if "currentCents" in payload:
        current = _int_field(payload, "currentCents")
        _checked(_validate_current_cents, current)
        goal.current_cents = current
    if "addAmountCents" in payload:
        # **存入**：在当前已存金额上累加（铁律 2——模型不读旧值、不做加法，
        # 于是既没有「模型算错」，也没有「两人同时存、后写的覆盖先写的」）。
        amount = _int_field(payload, "addAmountCents")
        _checked(_validate_amount_cents, amount)
        deposited = goal.current_cents + amount
        _checked(_validate_current_cents, deposited)
        goal.current_cents = deposited
    if "periodValue" in payload or "periodUnit" in payload:
        # 成对语义：两个都给（值或 null）；只给一半是形状不对。
        goal.period_value, goal.period_unit = _checked(
            _validate_savings_period, payload.get("periodValue"), payload.get("periodUnit"),
        )

    goal.version += 1
    await db.flush()
    await _register_change(
        db, user_id=user_id, entity_type="savings_goal", entity_id=goal.id,
        operation="update", version=goal.version,
    )
    return OpReceipt(entity_type="savings_goal", entity_ids=[goal.id])


async def _execute_savings_archive(
    db: AsyncSession, *, user_id: uuid.UUID, book_id: uuid.UUID, payload: dict[str, Any],
) -> OpReceipt:
    goal = await _get_savings(db, _uuid_field(payload, "savingsId"), user_id)
    goal.archived_at = datetime.now(UTC)
    goal.version += 1
    await db.flush()
    await _register_change(
        db, user_id=user_id, entity_type="savings_goal", entity_id=goal.id,
        operation="delete", version=goal.version,
    )
    return OpReceipt(entity_type="savings_goal", entity_ids=[goal.id])


# ---------------------------------------------------------------------------
# 定时域（T-M7-05c）
# ---------------------------------------------------------------------------

_RULE_TYPES = ("expense", "income")


def _rule_writes(payload: dict[str, Any]) -> dict[str, Any]:
    """规则的**可写字段** → ORM 列赋值（创建与修改共用，两处口径因此一致）。

    只处理 payload 里**出现过**的键：缺省＝不动那一列（改类 op 的语义）。
    """
    writes: dict[str, Any] = {}
    if "type" in payload:
        writes["type"] = _enum_field(payload, "type", _RULE_TYPES)
    if "name" in payload:
        name = _text_field(payload, "name")
        _checked(_validate_name, name, 64, "name")
        writes["name"] = name
    if "amountCents" in payload:
        amount = _int_field(payload, "amountCents")
        _checked(_validate_amount_cents, amount)
        writes["amount_cents"] = amount
    if "categoryId" in payload:
        writes["category_id"] = _uuid_field(payload, "categoryId")
    if "accountId" in payload:
        writes["account_id"] = _optional_uuid(payload, "accountId")
    if "includeInBudget" in payload:
        writes["include_in_budget"] = _checked(
            _require_bool, payload.get("includeInBudget"), "includeInBudget",
        )
    if "note" in payload:
        note = payload.get("note")
        _checked(_validate_note, note)
        writes["note"] = note
    if "repeat" in payload:
        repeat = str(payload.get("repeat"))
        if repeat not in _RULE_REPEATS:
            raise AppException(400, "ai_invalid_payload", "Invalid repeat")
        writes["repeat"] = repeat
    if "startDate" in payload:
        writes["start_date"] = _date_field(payload, "startDate")
    if "endDate" in payload:
        writes["end_date"] = (
            None if payload.get("endDate") in (None, "") else _date_field(payload, "endDate")
        )
    if "enabled" in payload:
        writes["enabled"] = _checked(_require_bool, payload.get("enabled"), "enabled")
    if "runHour" in payload:
        writes["run_hour"] = _checked(
            _require_int_in_range, payload.get("runHour"), 0, 23, "runHour",
        )
    if "runMinute" in payload:
        writes["run_minute"] = _checked(
            _require_int_in_range, payload.get("runMinute"), 0, 59, "runMinute",
        )
    return writes


async def _get_rule(
    db: AsyncSession, rule_id: uuid.UUID, user_id: uuid.UUID, book_id: uuid.UUID,
) -> ScheduledRule:
    """规则必须属于本人、未归档、**且就在本轮的账本里**（与授权表同口径）。"""
    rule = await db.scalar(
        select(ScheduledRule).where(
            ScheduledRule.id == rule_id,
            ScheduledRule.owner_id == user_id,
            ScheduledRule.archived_at.is_(None),
            ScheduledRule.book_id == book_id,
        ),
    )
    if rule is None:
        raise AppException(404, "not_found", "Scheduled rule not found")
    return rule


async def _execute_scheduled_create(
    db: AsyncSession, *, user_id: uuid.UUID, book_id: uuid.UUID, payload: dict[str, Any],
) -> OpReceipt:
    writes = _rule_writes(payload)
    for required in ("type", "name", "amount_cents", "category_id", "repeat", "start_date"):
        if required not in writes:
            raise AppException(400, "ai_invalid_payload", f"Missing {required}")

    # 与 sync 的 ``_apply_insert`` 同一套缺省值（``ScheduledRule`` 的列默认值）。
    fields: dict[str, Any] = {
        "note": "", "include_in_budget": True, "enabled": True,
        "run_hour": 8, "run_minute": 0, "end_date": None,
    }
    fields.update(writes)
    rule = ScheduledRule(owner_id=user_id, book_id=book_id, **fields)
    db.add(rule)
    await db.flush()
    await _register_change(
        db, user_id=user_id, entity_type="scheduled_rule", entity_id=rule.id,
        operation="insert", version=rule.version,
    )
    return OpReceipt(entity_type="scheduled_rule", entity_ids=[rule.id])


async def _execute_scheduled_update(
    db: AsyncSession, *, user_id: uuid.UUID, book_id: uuid.UUID, payload: dict[str, Any],
) -> OpReceipt:
    rule = await _get_rule(db, _uuid_field(payload, "ruleId"), user_id, book_id)
    version = _int_field(payload, "version")
    if rule.version != version:
        raise AppException(
            409, "version_conflict",
            f"Scheduled rule version mismatch: expected {version}, current {rule.version}",
        )
    writes = _rule_writes(payload)
    if not writes:
        raise AppException(400, "ai_invalid_payload", "No fields to update")
    for key, value in writes.items():
        setattr(rule, key, value)
    # 成序校验按**更新后的最终值**判定（镜像 sync 的 ``_apply_update``）。
    if rule.end_date is not None and rule.end_date < rule.start_date:
        raise AppException(400, "ai_invalid_payload", "endDate must not precede startDate")

    rule.version += 1
    await db.flush()
    await _register_change(
        db, user_id=user_id, entity_type="scheduled_rule", entity_id=rule.id,
        operation="update", version=rule.version,
    )
    return OpReceipt(entity_type="scheduled_rule", entity_ids=[rule.id])


async def _execute_scheduled_archive(
    db: AsyncSession, *, user_id: uuid.UUID, book_id: uuid.UUID, payload: dict[str, Any],
) -> OpReceipt:
    rule = await _get_rule(db, _uuid_field(payload, "ruleId"), user_id, book_id)
    rule.archived_at = datetime.now(UTC)
    rule.version += 1
    await db.flush()
    await _register_change(
        db, user_id=user_id, entity_type="scheduled_rule", entity_id=rule.id,
        operation="delete", version=rule.version,
    )
    return OpReceipt(entity_type="scheduled_rule", entity_ids=[rule.id])


# ---------------------------------------------------------------------------
# 角色域（T-M7-05c）
# ---------------------------------------------------------------------------


async def _get_persona(
    db: AsyncSession, persona_id: uuid.UUID, user_id: uuid.UUID,
) -> ChatPersona:
    persona = await db.scalar(
        select(ChatPersona).where(
            ChatPersona.id == persona_id,
            ChatPersona.owner_id == user_id,
            ChatPersona.archived_at.is_(None),
        ),
    )
    if persona is None:
        raise AppException(404, "not_found", "Persona not found")
    return persona


async def _execute_persona_create(
    db: AsyncSession, *, user_id: uuid.UUID, book_id: uuid.UUID, payload: dict[str, Any],
) -> OpReceipt:
    name = _text_field(payload, "name")
    _checked(_validate_name, name, 64, "name")
    avatar = _optional_text(payload, "avatar")
    if avatar is not None:
        _checked(_validate_name, avatar, 64, "avatar")
    text = payload.get("persona", "")
    _checked(_validate_note, text, "persona")

    persona = ChatPersona(owner_id=user_id, name=name, avatar=avatar, persona=text)
    db.add(persona)
    await db.flush()
    await _register_change(
        db, user_id=user_id, entity_type="chat_persona", entity_id=persona.id,
        operation="insert", version=persona.version,
    )
    return OpReceipt(entity_type="chat_persona", entity_ids=[persona.id])


async def _execute_persona_update(
    db: AsyncSession, *, user_id: uuid.UUID, book_id: uuid.UUID, payload: dict[str, Any],
) -> OpReceipt:
    persona = await _get_persona(db, _uuid_field(payload, "personaId"), user_id)
    version = _int_field(payload, "version")
    if persona.version != version:
        raise AppException(
            409, "version_conflict",
            f"Persona version mismatch: expected {version}, current {persona.version}",
        )

    changed = False
    if "name" in payload:
        name = _text_field(payload, "name")
        _checked(_validate_name, name, 64, "name")
        persona.name = name
        changed = True
    if "avatar" in payload:
        avatar = _optional_text(payload, "avatar")
        if avatar is not None:
            _checked(_validate_name, avatar, 64, "avatar")
        persona.avatar = avatar
        changed = True
    if "persona" in payload:
        text = payload.get("persona")
        _checked(_validate_note, text, "persona")
        persona.persona = text
        changed = True
    if not changed:
        raise AppException(400, "ai_invalid_payload", "No fields to update")

    persona.version += 1
    await db.flush()
    await _register_change(
        db, user_id=user_id, entity_type="chat_persona", entity_id=persona.id,
        operation="update", version=persona.version,
    )
    return OpReceipt(entity_type="chat_persona", entity_ids=[persona.id])


async def _execute_persona_archive(
    db: AsyncSession, *, user_id: uuid.UUID, book_id: uuid.UUID, payload: dict[str, Any],
) -> OpReceipt:
    persona = await _get_persona(db, _uuid_field(payload, "personaId"), user_id)
    persona.archived_at = datetime.now(UTC)
    persona.version += 1
    await db.flush()
    await _register_change(
        db, user_id=user_id, entity_type="chat_persona", entity_id=persona.id,
        operation="delete", version=persona.version,
    )
    return OpReceipt(entity_type="chat_persona", entity_ids=[persona.id])


# ---------------------------------------------------------------------------
# T-M7-05j: meta_set 服务端执行（偏好项上云）
# ---------------------------------------------------------------------------


#: meta_set payload 的 ``key`` → 值字段名。
#: 默认账本 / 默认账户从 05b 起就带 ``bookId`` / ``accountId``（模型按名字 resolve
#: 出 id 后填进 payload）；小组件配置 / 用户画像由模型直传 JSON 字符串（字段名
#: ``value``——handler 把模型给的 ``config`` / ``profile`` 原样放进 ``value``）。
_META_VALUE_FIELD: dict[str, str] = {
    "default_book": "bookId",
    "default_account": "accountId",
    "widget_config": "value",
    "user_profile": "value",
}

#: 工具名 ``key`` → 真正落到 ``user_preferences`` 表的 ``key``。
#: 工具的 ``key`` 是动作名（``default_book`` / ``default_account``）；sync_meta
#: 里的本地键名带 ``_id`` 后缀（``default_book_id`` / ``default_account_id``）
#: ——客户端 pull 到 sync change 后按这个键名回灌，必须与 ``MetaWhitelist`` 对齐。
_META_STORAGE_KEY: dict[str, str] = {
    "default_book": "default_book_id",
    "default_account": "default_account_id",
    "widget_config": "widget_config",
    "user_profile": "user_profile",
}

#: 唯一走**浅合并**写入语义的偏好键（T19-3 / REQ-74 决策 #4）。其余三个键是
#: 单值指针 / 整份配置，「合并」对它们没有意义。
_PROFILE_STORAGE_KEY = "user_profile"


def _merge_profile(existing: str | None, incoming: str) -> str:
    """``user_profile`` 的**浅合并**：键在场才改，未出现的键保持原值。

    T19-3（REQ-74 决策 #4）的由来：``_execute_meta_set`` 一直是**整值 upsert**，
    而 AI 只想改一项（「以后叫我爹」）会把用户已经填好的其余 6 个字段**整体抹掉**
    ——静默数据丢失。表单页的写入语义不变（它序列化的就是全部字段，用户看到的
    就是全部），这条只作用于 AI 走的那条路。

    三种输入各有明确处置：

    - **未知键**（不在 ``_PROFILE_LABELS`` 里）→ **丢弃 + 记日志**。决策 #4 在
      「丢弃」与「400」之间二选一，取宽容档：与 ``MetaWhitelist`` 的「读取端
      容错」同取向，且模型写了怪键不该让整张卡失败。
    - **非字符串值** → 同样丢弃 + 记日志。表单页 ``parseStoredForm`` 只认
      ``typeof === 'string'``，存进去也是**看不见的垃圾**（写了等于没写，却占着
      一个「已更新」的回执）。
    - **入参不是 JSON 对象** → **400**。这是刻意的**不宽容**：宽容在这里只能落成
      「照收但什么都改不了」——卡片回执说「已更新用户画像」而实际零写入，正是
      T18-3 记的「回执说谎」。且这条路径在执行期、模型已不在回路里，报错比吞掉
      更诚实（客户端会把它显示成执行失败）。

    存量值解析不出来（历史 AI 写过自然语言键、或更早版本存过非对象）时**不阻断
    本次写入**，按空对象合并并记一条 warning——那份内容按定义已经读不出来了
    （表单页对它同样回退空表单）。
    """
    try:
        parsed: Any = json.loads(incoming)
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict):
        raise AppException(
            400, "ai_invalid_payload",
            "user_profile value must be a JSON object",
        )

    base: dict[str, Any] = {}
    if existing:
        try:
            stored: Any = json.loads(existing)
        except ValueError:
            stored = None
        if isinstance(stored, dict):
            base = stored
        else:
            logger.warning(
                "meta_set: stored user_profile is not a JSON object, merging into {}",
                extra={"stored_type": type(stored).__name__},
            )

    unknown = sorted(k for k in parsed if k not in _PROFILE_LABELS)
    non_string = sorted(
        k for k in parsed if k in _PROFILE_LABELS and not isinstance(parsed[k], str)
    )
    if unknown or non_string:
        logger.info(
            "meta_set: dropped unusable profile keys",
            extra={"unknown": unknown, "non_string": non_string},
        )

    merged = {
        **base,
        **{
            k: v for k, v in parsed.items()
            if k in _PROFILE_LABELS and isinstance(v, str)
        },
    }
    return json.dumps(merged, ensure_ascii=False)


async def _execute_meta_set(
    db: AsyncSession, *, user_id: uuid.UUID, book_id: uuid.UUID, payload: dict[str, Any],
) -> OpReceipt:
    """写一个偏好键进 ``user_preferences`` 表（云端权威）并登一条 sync change。

    05b 时期 ``meta_set`` 的执行位置在设备——服务端没有这些键的存储位置，提案
    只挂起、由客户端在设备上写 ``sync_meta``。05j 把偏好键送上云后，服务端有
    ``user_preferences`` 表作权威：本执行体按 ``(user_id, key)`` upsert 一行，
    同事务登一条 sync change（口径 2：服务端权威直写必须自己登记）——其它设备
    pull 到这条 change 后把值回灌进本地 ``sync_meta`` 的同名键。

    **幂等**：命中 ``(user_id, key)`` 已存在时不覆盖 ``id``（行的 UUID 身份不
    变），只更新 ``value`` 与 ``version``——与 05c 的 ``budget_set`` 同模式（UPSERT
    语义）。

    **写入语义按键分两种**（T19-3 / REQ-74 决策 #4）：``user_profile`` 走
    **浅合并**（键在场才改，见 :func:`_merge_profile`），其余三个键仍是整值替换
    ——它们是单值指针 / 整份配置，「合并」对它们没有意义。
    """
    key = payload.get("key")
    if not isinstance(key, str) or key not in _META_VALUE_FIELD:
        raise AppException(
            400, "ai_invalid_payload",
            f"Invalid preference key: {key}",
        )
    value_field = _META_VALUE_FIELD[key]
    new_value = payload.get(value_field)
    if not isinstance(new_value, str) or not new_value:
        raise AppException(
            400, "ai_invalid_payload", f"Missing or invalid {value_field}",
        )
    # 长度上限与 sync.service 的 push 路径对齐（16KB）——handler 已做轻校验，
    # 这里是防客户端伪造 payload 的第二道门。
    if len(new_value) > 16_000:
        raise AppException(400, "ai_invalid_payload", "value too long")

    # 落到 ``user_preferences`` 表的键名：工具名 ``default_book`` → sync_meta
    # 本地键名 ``default_book_id``（客户端 pull 后按此回灌，与 ``MetaWhitelist`` 对齐）。
    storage_key = _META_STORAGE_KEY[key]

    # 按 (user_id, storage_key) upsert：命中已存在则更新 value + version++；未命中则新建。
    existing_result = await db.execute(
        select(UserPreference).where(
            UserPreference.user_id == user_id,
            UserPreference.key == storage_key,
        )
    )
    existing = existing_result.scalar_one_or_none()

    # 画像走**浅合并**（T19-3 / 决策 #4）：只改用户这次提到的键，未出现的保持原值。
    # 必须拿到存量行之后再做——所以它在 upsert 查询与写回之间，不是前置校验。
    if storage_key == _PROFILE_STORAGE_KEY:
        new_value = _merge_profile(
            existing.value if existing is not None else None, new_value,
        )
        # 合并结果可能比入参更长（存量本来就接近上限），因此合并后**再量一次**。
        if len(new_value) > 16_000:
            raise AppException(400, "ai_invalid_payload", "value too long")

    if existing is not None:
        existing.value = new_value
        existing.version += 1
        await db.flush()
        pref_id = existing.id
        pref_version = existing.version
        operation = "update"
    else:
        pref = UserPreference(
            user_id=user_id, key=storage_key, value=new_value,
        )
        db.add(pref)
        await db.flush()
        pref_id = pref.id
        pref_version = pref.version
        operation = "insert"

    await _register_change(
        db, user_id=user_id, entity_type="user_preference",
        entity_id=pref_id, operation=operation, version=pref_version,
    )
    return OpReceipt(entity_type="user_preference", entity_ids=[pref_id])


# ---------------------------------------------------------------------------
# 05d：其余服务端域（导出 / 反馈 / 账号）
# ---------------------------------------------------------------------------
#
# 这四条都**不登 sync change**——它们动的东西不是可同步实体（`sync/schemas.py`
# 的 ``entity_type`` 白名单只有八类业务实体），设备 pull 也拿不到它们。因此
# 下面的 ``OpReceipt.entity_type`` 只是**回执标签**（客户端的 ``transaction_ids``
# 里会带上这些 id），不是 pull 的分派键。

async def _load_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    """取出调用者本人。**认的是端点的鉴权结果**（``user_id``），不是提案内容。"""
    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise AppException(404, "not_found", "User not found")
    return user


async def _execute_export_create(
    db: AsyncSession, *, user_id: uuid.UUID, book_id: uuid.UUID, payload: dict[str, Any],
) -> OpReceipt:
    """建一个导出任务并**同步生成文件**（``ExportService.create_export`` 内部把文件
    IO 丢进线程池，返回时文件已就绪）。

    账本必须是本轮的那个：授权面只校验了「这本书属于本人」，跨账本导出仍然是
    越权（与 ``transaction`` 的账本收窄同口径）。
    """
    fmt = _enum_field(payload, "format", ("json", "csv"))
    target_book = _uuid_field(payload, "bookId")
    if target_book != book_id:
        raise AppException(404, "not_found", "Book not found")

    export = await ExportService(db).create_export(book_id, user_id, fmt)
    return OpReceipt(entity_type="export", entity_ids=[export.id])


async def _execute_feedback_submit(
    db: AsyncSession, *, user_id: uuid.UUID, book_id: uuid.UUID, payload: dict[str, Any],
) -> OpReceipt:
    """落一条用户反馈。

    **设备元信息一律留空**：``POST /telemetry/feedback`` 要 ``app_version`` /
    ``android_version`` / ``device_model``，而 ``/ai/agent`` 的请求里没有设备标识
    ——服务端不知道这台设备是什么型号、装的哪个版本。**留空是诚实的缺失**
    （Grafana 上看得见「这条反馈没带版本」），编一个 ``"unknown"`` 才是往运维的
    数据里掺假。用户标识同样按既有口径只存 ``sha256(user_id)[:16]``。

    路由层的内存限速（5 条/小时/用户）**不在这条路径上**——AI 通道有自己的两层
    节制：会话级配额与单轮的工具调用上限（``agent.py`` 的 ``max_tool_calls``）。
    """
    category = _enum_field(payload, "category", ("bug", "suggestion", "other"))
    user_id_hash = hashlib.sha256(str(user_id).encode()).hexdigest()[:16]
    feedback = await TelemetryService(db).submit_feedback(
        FeedbackRequest(
            category=category,
            content=_text_field(payload, "content"),
            contact=_optional_text(payload, "contact"),
            app_version="",
            android_version="",
            device_model="",
        ),
        user_id_hash,
    )
    return OpReceipt(entity_type="feedback", entity_ids=[feedback.id])


async def _execute_auth_update_profile(
    db: AsyncSession, *, user_id: uuid.UUID, book_id: uuid.UUID, payload: dict[str, Any],
) -> OpReceipt:
    """改昵称。形状校验走 auth 模块**自己那份** ``UpdateProfileRequest``
    （strip 后非空、≤64），不在这里抄第二份。"""
    data = _model_or_400(UpdateProfileRequest, {"nickname": payload.get("nickname")})
    user = await AuthService(db).update_profile(await _load_user(db, user_id), data.nickname)
    return OpReceipt(entity_type="user", entity_ids=[user.id])


async def _execute_account_deletion_request(
    db: AsyncSession, *, user_id: uuid.UUID, book_id: uuid.UUID, payload: dict[str, Any],
) -> OpReceipt:
    """注销账号：**在同一处内部走完「申请令牌 → 用令牌确认」两步**。

    既有流程（``deletion/service.py``）把这两步拆成两个 HTTP 调用，因为
    ``confirm_token`` 是**给人看的一次性凭据**（5 分钟有效）。AI 路径上不能照搬：

    - 让模型拿着 token（``account_delete`` 的 payload 就是它）＝把凭据写进模型
      上下文与下一轮请求，与 ADR-003「Token 不出安全层」同一类问题；
    - 让「申请」与「确认」隔着一次人工确认卡（用户可能几分钟后才点），token
      大概率已经过期，用户看到的会是一次莫名其妙的失败。

    所以这里由服务端**连续调用既有的两个服务方法**（顺序与 REST 路径完全一致，
    ``request_deletion_token`` → ``confirm_deletion``），**一行删号逻辑都没有重写**
    ——「不绕过既有流程」的落点正在于此：真正的删除仍由
    ``infra/cleanup/account-deletion.py`` 在 7 天冷静期之后执行，本执行体只把账号
    推进 FROZEN，撤销入口（``POST /me/deletion-cancel``）原样可用。
    """
    user = await _load_user(db, user_id)
    service = DeletionService(db)
    token = await service.request_deletion_token(user)
    await service.confirm_deletion(user, token)
    return OpReceipt(entity_type="user", entity_ids=[user.id])


#: op → 执行体。**不在表里的 op 一律 400**——注册表成员身份不是执行许可。
#: ``account_delete`` 与 ``reminder_set`` 是两个**刻意留在表外**的注册表成员：
#: 前者是「用一次性 token 确认删除」的那一步（token 不进模型上下文，AI 路径合并
#: 进 ``account_deletion_request``），后者的执行位置在设备（见 ``tools.py`` 的
#: ``DEVICE_EXECUTED_OPS`` 注释）。两者都会以 ``ai_unsupported_action`` 拒绝。
EXECUTORS: dict[str, Callable[..., Awaitable[OpReceipt]]] = {
    "transaction_update": _execute_transaction_update,
    "transaction_delete": _execute_transaction_delete,
    "category_create": _execute_category_create,
    "category_update": _execute_category_update,
    "category_archive": _execute_category_archive,
    "account_create": _execute_account_create,
    "account_update": _execute_account_update,
    "account_archive": _execute_account_archive,
    "book_create": _execute_book_create,
    "book_update": _execute_book_update,
    "book_delete": _execute_book_delete,
    # ── T-M7-05c：预算存钱 / 定时 / 角色 ────────────────────────────────
    "budget_set": _execute_budget_set,
    "budget_set_category": _execute_budget_set_category,
    "budget_delete": _execute_budget_delete,
    "savings_create": _execute_savings_create,
    "savings_update": _execute_savings_update,
    "savings_archive": _execute_savings_archive,
    "scheduled_create": _execute_scheduled_create,
    "scheduled_update": _execute_scheduled_update,
    "scheduled_archive": _execute_scheduled_archive,
    "persona_create": _execute_persona_create,
    "persona_update": _execute_persona_update,
    "persona_archive": _execute_persona_archive,
    # T-M7-05j：``meta_set`` 从 ``DEVICE_EXECUTED_OPS`` 迁入——偏好项上云后，
    # 服务端有这些键的存储位置（``user_preferences`` 表）。
    "meta_set": _execute_meta_set,
    # ``persona_switch`` 不在这里：它的执行位置在设备（当前角色指针是
    # ``sync_meta`` 的本地键），见 ``DEVICE_EXECUTED_OPS``。
    # ── T-M7-05d：其余服务端域 ─────────────────────────────────────────
    "export_create": _execute_export_create,
    "feedback_submit": _execute_feedback_submit,
    "auth_update_profile": _execute_auth_update_profile,
    "account_deletion_request": _execute_account_deletion_request,
    # ``consent_record`` / ``sync_trigger`` 的执行位置在设备（见 ``DEVICE_EXECUTED_OPS``），
    # ``account_delete`` 刻意不接线（见上）。
}

#: **服务端执行得了的 op 全集**：上面那张表 + ``transaction_add``。记账走的是
#: ``execute_proposal`` 里与 v1 共用的那条交易路径（批量、幂等、附件关联、sync
#: 登记都在那里），因此它不在这张表里，但同样「执行得了」——写一个小集合把这件
#: 事说清楚，好过让每个调用方自己去拼。
SERVER_EXECUTED_OPS: frozenset[str] = frozenset(EXECUTORS) | {"transaction_add"}


async def execute_op(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    book_id: uuid.UUID,
    op: str,
    payload: Any,
) -> OpReceipt:
    """执行一个 v2 op（调用方已完成校验与授权）。"""
    if op in DEVICE_EXECUTED_OPS:
        # 执行位置在设备（``tools.DEVICE_EXECUTED_OPS``）：服务端没有它的存储
        # 位置。客户端应当在本地执行这类提案，而不是打这个端点。
        raise AppException(
            400, "ai_device_op_not_executable",
            f"Op '{op}' is executed on the device, not on the server",
        )
    executor = EXECUTORS.get(op)
    if executor is None:
        raise AppException(
            400, "ai_unsupported_action",
            f"Action '{op}' is not executable via /ai/execute",
        )
    if not isinstance(payload, dict):
        raise AppException(400, "ai_invalid_payload", "payload must be an object")
    return await executor(db, user_id=user_id, book_id=book_id, payload=payload)
