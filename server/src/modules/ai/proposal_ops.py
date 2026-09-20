"""T-M7-05b: v2 op 的服务端执行面（``/ai/execute`` 与 loop 的执行器共用）。

v2 的 op 注册表（``contracts/action-proposal/schema-v2.json``）有 34 个成员，
但**能执行的只有登记在本模块的那些**——注册表成员身份是「这是一个提案」，
不是「服务端会执行它」。05b 落地记账域与账户账本域，其余 op（预算 / 定时 /
角色 / 导出 / 删号……）归 05c / 05d；**没登记的 op 一律 400**，包括
``account_delete`` 这类最高危 op（它必须等 05d 连上「最严审批 + 不绕过既有删号
流程」之后才能放行）。

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
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.errors import AppException
from src.models.sync import SyncOperation
from src.modules.accounts.schemas import AccountCreate, AccountUpdate
from src.modules.accounts.service import AccountService
from src.modules.books.schemas import BookCreate, BookUpdate
from src.modules.books.service import BookService
from src.modules.categories.schemas import CategoryCreate, CategoryUpdate
from src.modules.categories.service import CategoryService
from src.modules.transactions.schemas import TransactionUpdate
from src.modules.transactions.service import TransactionService

from .tools import DEVICE_EXECUTED_OPS


@dataclass(frozen=True, slots=True)
class OpReceipt:
    """一次 op 执行的结果：受影响的实体。**

    ``entity_type`` 是 sync 的实体类型（``transaction`` / ``category`` /
    ``account`` / ``book``），也是客户端 pull 时的分派键；``entity_ids`` 是本次
    写入的实体 id（改 / 归档是同一个 id 的新版本，创建是新 id）。
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
    tx = await service.update_transaction(tx_id, user_id, data)
    await _register_change(
        db, user_id=user_id, entity_type="transaction", entity_id=tx.id,
        operation="update", version=tx.version,
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
    await service.delete_transaction(tx_id, user_id)
    # 同一个 session 的同一个实例——``soft_delete`` 已经把 version 加过一了。
    await _register_change(
        db, user_id=user_id, entity_type="transaction", entity_id=tx.id,
        operation="delete", version=tx.version,
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


#: op → 执行体。**不在表里的 op 一律 400**（含 05c / 05d 尚未落地的 op 与最高危
#: 的 ``account_delete``）——注册表成员身份不是执行许可。
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
