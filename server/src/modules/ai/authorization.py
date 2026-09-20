"""T-M7-05b: v2 提案的 payload 级授权。

05a 的 v2 契约**只校验信封**——payload 的字段形状由 op 自己的工具注册表条目
拥有，契约刻意不复制第二份。代价是明确的：**结构通过不是执行许可**
（``ai-architecture.md``「payload 的所有权」）。payload 里的分类 / 账户 / 账本 /
交易目标是否真的属于这个用户与这个账本，必须由服务端在**组装与执行时**施加。
本模块就是那三处（loop 组装、``/ai/validate``、``/ai/execute``）共用的授权面。

与 v1 的关系
------------
v1 的 ``ProposalValidationContext``（categoryIds / accountIds）对拍的是**客户端
组装**的提案（ADR-003）。v2 的提案由服务端 loop 组装，但 ``/ai/validate`` 与
``/ai/execute`` 是客户端可达的端点——一份**客户端伪造的 v2 提案**同样会打到这里。
所以授权在这一层做，理由不是「谁组装的」，而是「谁能打到这个端点」。

授权口径（每个 id 都必须落在服务端自己的查询结果里）
---------------------------------------------------
========================================  ==========================================
目标类型                                  判定
========================================  ==========================================
``category``                              属于本用户、未归档、且（全局分类 或 本账本）
``account``                               属于本用户、未归档
``book``                                  属于本用户、未软删
``transaction``                           未软删、属于本用户，**且就在提案的账本里**
``savings_goal``                          属于本用户、未归档（**用户级**，不分账本）
``scheduled_rule``                        属于本用户、未归档，**且就在提案的账本里**
``chat_persona``                          属于本用户、未归档（**用户级**，不分账本）
========================================  ==========================================

后三行是 05c 加的（T-M7-01 上云的三实体）。归属范围与 ``sync`` 的实现一致：
存钱目标与角色按 ``owner_id``（用户级），定时规则按 ``book_id``（账本级）。

「本账本」这一条对分类是有意的收窄：账本 A 的分类不该被账本 B 的提案改到
（``load_tool_context`` 的名字索引同样只装本账本分类 + 全局分类）。交易单列一条
是因为它不在名字索引里——一个用户的流水可以上万条，索引装不下也不该装，按 id
单查一次并带上账本归属即可。

fail-closed
-----------
**未登记授权规则的 op 一律不放行**（新增 op 时必须在这里表态，哪怕写一个空元组
表示「这个 op 没有 id 要授权」）。默认放行会让「忘了登记」变成一个静默的越权面。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.ledger import Account, Book, Category, Transaction
from src.models.persona import ChatPersona
from src.models.savings import SavingsGoal
from src.models.scheduled import ScheduledRule

#: id 目标的类型标签。
_CATEGORY = "category"
_ACCOUNT = "account"
_BOOK = "book"
_TRANSACTION = "transaction"
_SAVINGS = "savings_goal"
_SCHEDULED = "scheduled_rule"
_PERSONA = "chat_persona"

#: op → 该 op 的 payload 里需要授权的字段：``(取值路径, 目标类型)``。
#: 路径只有两种写法：``字段名`` 与 ``数组字段[].子字段``。
_PAYLOAD_ID_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "transaction_add": (
        ("transactions[].categoryId", _CATEGORY),
        ("transactions[].accountId", _ACCOUNT),
    ),
    "transaction_update": (
        ("transactionId", _TRANSACTION),
        ("categoryId", _CATEGORY),
        ("accountId", _ACCOUNT),
    ),
    "transaction_delete": (("transactionId", _TRANSACTION),),
    "category_create": (("bookId", _BOOK), ("parentId", _CATEGORY)),
    "category_update": (("categoryId", _CATEGORY), ("parentId", _CATEGORY)),
    "category_archive": (("categoryId", _CATEGORY),),
    # 账户 / 账本的创建只带名字，没有 id 可授权——空元组是**明确表态**，不是遗漏。
    "account_create": (),
    "account_update": (("accountId", _ACCOUNT),),
    "account_archive": (("accountId", _ACCOUNT),),
    "book_create": (),
    "book_update": (("bookId", _BOOK),),
    "book_delete": (("bookId", _BOOK),),
    # meta_set：默认项指向的对象同样要属于本人（执行体现在是服务端——T-M7-05j
    # 偏好项上云——但提案 payload 是服务端组装/可被客户端伪造的，授权口径不因
    # 执行位置放松；widget_config / user_profile 两个新 key 没有 id 要授权）。
    "meta_set": (("bookId", _BOOK), ("accountId", _ACCOUNT)),
    # ── T-M7-05c：预算存钱 / 定时 / 角色 ────────────────────────────────
    # 预算的账本来自提案信封（``book_id``，端点已校验归属），payload 里只有
    # 分类 / 期间——**空元组是明确表态**，不是遗漏（同 account_create）。
    "budget_set": (),
    "budget_set_category": (("categoryId", _CATEGORY),),
    "budget_delete": (("categoryId", _CATEGORY),),
    "savings_create": (),
    "savings_update": (("savingsId", _SAVINGS),),
    "savings_archive": (("savingsId", _SAVINGS),),
    # 定时规则带账本归属：``bookId`` 由服务端按当前账本填，分类 / 账户要授权。
    "scheduled_create": (("categoryId", _CATEGORY), ("accountId", _ACCOUNT)),
    "scheduled_update": (
        ("ruleId", _SCHEDULED),
        ("categoryId", _CATEGORY),
        ("accountId", _ACCOUNT),
    ),
    "scheduled_archive": (("ruleId", _SCHEDULED),),
    "persona_create": (),
    "persona_update": (("personaId", _PERSONA),),
    "persona_archive": (("personaId", _PERSONA),),
    # persona_switch 的执行位置在设备，payload 里的角色 id 同样要授权。
    "persona_switch": (("personaId", _PERSONA),),
    # ── T-M7-05d：分析之外的四个服务端域 ────────────────────────────────
    # 导出按**账本**授权：payload 的 ``bookId`` 由服务端按当前账本填，但一份
    # 客户端伪造的提案同样会打到这里，放行它等于让 AI 导出别人的账本。
    "export_create": (("bookId", _BOOK),),
    # 反馈 / 昵称 / 注销 / 同意 / 同步**都没有 id 目标**——空元组是**明确表态**，
    # 不是遗漏（同 ``account_create``）。它们的授权面不在 payload 里：反馈与注销
    # 认的是调用者本人（``user_id`` 由端点的鉴权给出，不取自提案），
    # 同意与同步的执行位置在设备（``DEVICE_EXECUTED_OPS``），服务端只挂起。
    "feedback_submit": (),
    "consent_record": (),
    "auth_update_profile": (),
    "account_deletion_request": (),
    "sync_trigger": (),
    #: ``account_delete`` **刻意不在这里**：它的语义是「拿一个一次性 token 确认
    #: 删除」，而那个 token 是凭据——服务端绝不该把它交给模型（模型会把它写进
    #: 下一轮 payload，凭据随即进入模型上下文与日志）。AI 路径上的注销走
    #: ``account_deletion_request``：执行体在服务端内部完成「申请 → 确认」两步，
    #: token 不出服务端（见 ``proposal_ops``）。未登记授权规则的 op 一律拒绝，
    #: 所以伪造的 ``account_delete`` 提案在授权面就被挡下（fail-closed）。
}


def _values_at(payload: dict[str, Any], path: str) -> tuple[list[str], bool]:
    """取路径上的全部 id 取值。返回 ``(取值列表, 结构是否可读)``。

    缺席（字段没给 / 给了 null）＝这些规则不适用，返回空列表。**给了但结构不对**
    返回 ``ok=False``，由调用方判成授权失败——一份形状不对的 payload 不该因为
    「取不出 id」而被放行。
    """
    if "[]." in path:
        field, sub = path.split("[].", 1)
        raw = payload.get(field)
        if raw is None:
            return [], True
        if not isinstance(raw, list):
            return [], False
        values: list[str] = []
        for item in raw:
            if not isinstance(item, dict):
                return [], False
            value = item.get(sub)
            if value not in (None, ""):
                values.append(str(value))
        return values, True

    value = payload.get(path)
    if value in (None, ""):
        return [], True
    return [str(value)], True


async def _is_authorized(
    db: AsyncSession,
    kind: str,
    value: str,
    *,
    user_id: uuid.UUID,
    book_id: uuid.UUID,
) -> bool:
    """一个 id 是否落在服务端自己的授权集合里。非法 UUID 一律判否。"""
    try:
        target = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False

    if kind == _CATEGORY:
        return await db.scalar(
            select(Category.id).where(
                Category.id == target,
                Category.owner_id == user_id,
                Category.archived_at.is_(None),
                (Category.book_id == book_id) | (Category.book_id.is_(None)),
            ),
        ) is not None
    if kind == _ACCOUNT:
        return await db.scalar(
            select(Account.id).where(
                Account.id == target,
                Account.owner_id == user_id,
                Account.archived_at.is_(None),
            ),
        ) is not None
    if kind == _BOOK:
        return await db.scalar(
            select(Book.id).where(
                Book.id == target,
                Book.owner_id == user_id,
                Book.deleted_at.is_(None),
            ),
        ) is not None
    if kind == _TRANSACTION:
        # 交易挂在账本上（``transactions`` 没有 owner_id 列），归属靠 join 账本。
        # 提案的账本也必须对上：跨账本删一笔别人的账本里的流水，等于越权。
        return await db.scalar(
            select(Transaction.id)
            .join(Book, Transaction.book_id == Book.id)
            .where(
                Transaction.id == target,
                Transaction.deleted_at.is_(None),
                Transaction.book_id == book_id,
                Book.owner_id == user_id,
                Book.deleted_at.is_(None),
            ),
        ) is not None
    if kind == _SAVINGS:
        # 存钱目标是用户级（``owner_id``，不分账本）——与账户同口径。
        return await db.scalar(
            select(SavingsGoal.id).where(
                SavingsGoal.id == target,
                SavingsGoal.owner_id == user_id,
                SavingsGoal.archived_at.is_(None),
            ),
        ) is not None
    if kind == _SCHEDULED:
        # 定时规则**带账本归属**（``scheduled_rules.book_id``）——与交易同口径：
        # 一个账本的对话不该改到另一个账本的规则。
        return await db.scalar(
            select(ScheduledRule.id).where(
                ScheduledRule.id == target,
                ScheduledRule.owner_id == user_id,
                ScheduledRule.archived_at.is_(None),
                ScheduledRule.book_id == book_id,
            ),
        ) is not None
    if kind == _PERSONA:
        return await db.scalar(
            select(ChatPersona.id).where(
                ChatPersona.id == target,
                ChatPersona.owner_id == user_id,
                ChatPersona.archived_at.is_(None),
            ),
        ) is not None
    return False


async def authorize_payload(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    book_id: uuid.UUID,
    op: str,
    payload: Any,
) -> list[dict[str, str]]:
    """授权一份 v2 payload；返回错误列表（空列表＝通过）。

    错误形状与校验器的 ``{"path", "message"}`` 一致，可直接并进
    ``/ai/validate`` 的响应。``message`` **不含取值**（脱敏口径：payload 里的
    id 是服务端解析的产物，回显它等于把内部标识写进客户端可见的错误里）。
    """
    rules = _PAYLOAD_ID_FIELDS.get(op)
    if rules is None:
        return [{
            "path": "action",
            "message": "this op has no payload authorization rule (fail-closed)",
        }]
    if not isinstance(payload, dict):
        return [{"path": "payload", "message": "payload must be an object"}]

    errors: list[dict[str, str]] = []
    for path, kind in rules:
        values, readable = _values_at(payload, path)
        if not readable:
            errors.append({"path": path, "message": "malformed payload field"})
            continue
        for value in values:
            if not await _is_authorized(
                db, kind, value, user_id=user_id, book_id=book_id,
            ):
                errors.append({
                    "path": path,
                    "message": f"{kind} is unresolved or not authorized",
                })
    return errors


def has_authorization_rule(op: str) -> bool:
    """这个 op 是否在授权表里表过态（新增 op 的自检入口，见模块 docstring）。"""
    return op in _PAYLOAD_ID_FIELDS
