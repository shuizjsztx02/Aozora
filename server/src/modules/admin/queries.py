"""T-M8-04：admin **只读**查询层。

纪律（`docs/architecture/admin-console.md` §5「读端点用只读事务」）：本模块
**只构造 ``select``**，不做 add / update / delete，也不提交事务。唯一的写路径
（登录审计、``reveal`` 审计、``last_login_at``）在 ``routes.py`` 里。

分页一律 keyset（``(排序键, id)``），**禁止 offset**（架构 §3.3）：游标是
base64url 编码的 JSON，解析失败报 422 ``invalid_cursor``。
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.errors import AppException
from src.models.admin import AdminAccount
from src.models.ai import AIConversation, AIConversationEvent, AIUsage
from src.models.user import DeviceSession, User, UserStatus

#: 轮次列表里首条用户输入的预览长度。**列表不返回事件正文**（红线 4）——
#: 这里给的是截断预览，且由路由层打码。
TURN_PREVIEW_CHARS = 120

#: 会话的排序/时间筛选用「最后活跃时间」，没写过轮次的会话用创建时间兜
#: （与 ``ConversationService.list_conversations`` 同口径）。
_ACTIVE_KEY = func.coalesce(AIConversation.last_active_at, AIConversation.created_at)


# ---------------------------------------------------------------------------
# keyset 游标
# ---------------------------------------------------------------------------

def encode_cursor(**values: Any) -> str:
    raw = json.dumps(values, separators=(",", ":"), ensure_ascii=False)
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except Exception:
        raise AppException(422, "invalid_cursor", "Invalid pagination cursor") from None
    if not isinstance(data, dict):
        raise AppException(422, "invalid_cursor", "Invalid pagination cursor")
    return data


def _cursor_time(data: dict[str, Any]) -> datetime:
    try:
        return datetime.fromisoformat(str(data["t"]))
    except (KeyError, TypeError, ValueError):
        raise AppException(422, "invalid_cursor", "Invalid pagination cursor") from None


def _cursor_uuid(data: dict[str, Any]) -> uuid.UUID:
    try:
        return uuid.UUID(str(data["id"]))
    except (KeyError, TypeError, ValueError):
        raise AppException(422, "invalid_cursor", "Invalid pagination cursor") from None


def _escape_like(text: str) -> str:
    """转义 LIKE 通配符——否则用户输入的 ``%`` 会变成「匹配一切」。"""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------------------
# 用户
# ---------------------------------------------------------------------------

def _user_query_condition(q: str):
    """``q`` 先按 UUID 认；否则手机号 / 昵称模糊匹配。"""
    text = q.strip()
    try:
        return User.id == uuid.UUID(text)
    except ValueError:
        pass
    pattern = f"%{_escape_like(text)}%"
    return or_(
        User.phone.ilike(pattern, escape="\\"),
        User.nickname.ilike(pattern, escape="\\"),
    )


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await db.scalar(select(User).where(User.id == user_id))


async def get_admin_by_username(
    db: AsyncSession, username: str,
) -> AdminAccount | None:
    return await db.scalar(
        select(AdminAccount).where(AdminAccount.username == username)
    )


async def list_users(
    db: AsyncSession,
    *,
    q: str | None,
    status: UserStatus | None,
    created_from: datetime | None,
    created_to: datetime | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[User], str | None]:
    conditions = []
    if q:
        conditions.append(_user_query_condition(q))
    if status is not None:
        conditions.append(User.status == status.value)
    if created_from is not None:
        conditions.append(User.created_at >= created_from)
    if created_to is not None:
        conditions.append(User.created_at < created_to)
    if cursor:
        data = _decode_cursor(cursor)
        created_at, user_id = _cursor_time(data), _cursor_uuid(data)
        conditions.append(or_(
            User.created_at < created_at,
            and_(User.created_at == created_at, User.id < user_id),
        ))

    rows = list((await db.scalars(
        select(User)
        .where(*conditions)
        .order_by(User.created_at.desc(), User.id.desc())
        .limit(limit + 1)
    )).all())

    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = encode_cursor(t=last.created_at.isoformat(), id=str(last.id))
    return rows, next_cursor


async def count_user_devices(db: AsyncSession, user_id: uuid.UUID) -> int:
    return int(await db.scalar(
        select(func.count(DeviceSession.id)).where(DeviceSession.user_id == user_id)
    ) or 0)


async def count_user_conversations(db: AsyncSession, user_id: uuid.UUID) -> int:
    return int(await db.scalar(
        select(func.count(AIConversation.id)).where(
            AIConversation.user_id == user_id,
        )
    ) or 0)


async def user_last_activity(
    db: AsyncSession, user_id: uuid.UUID,
) -> datetime | None:
    return await db.scalar(
        select(func.max(_ACTIVE_KEY)).where(AIConversation.user_id == user_id)
    )


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------

async def get_conversation(
    db: AsyncSession, conversation_id: uuid.UUID,
) -> AIConversation | None:
    return await db.scalar(
        select(AIConversation).where(AIConversation.id == conversation_id)
    )


async def list_conversations(
    db: AsyncSession,
    *,
    q: str | None,
    user_id: uuid.UUID | None,
    time_from: datetime | None,
    time_to: datetime | None,
    include_archived: bool,
    cursor: str | None,
    limit: int,
) -> tuple[list[AIConversation], str | None]:
    conditions = []
    if not include_archived:
        conditions.append(AIConversation.archived_at.is_(None))
    if user_id is not None:
        conditions.append(AIConversation.user_id == user_id)
    if q:
        pattern = f"%{_escape_like(q)}%"
        conditions.append(AIConversation.search_text.ilike(pattern, escape="\\"))
    if time_from is not None:
        conditions.append(_ACTIVE_KEY >= time_from)
    if time_to is not None:
        conditions.append(_ACTIVE_KEY < time_to)
    if cursor:
        data = _decode_cursor(cursor)
        active_at, conversation_id = _cursor_time(data), _cursor_uuid(data)
        conditions.append(or_(
            _ACTIVE_KEY < active_at,
            and_(_ACTIVE_KEY == active_at, AIConversation.id < conversation_id),
        ))

    rows = list((await db.scalars(
        select(AIConversation)
        .where(*conditions)
        .order_by(_ACTIVE_KEY.desc(), AIConversation.id.desc())
        .limit(limit + 1)
    )).all())

    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        active_at = last.last_active_at or last.created_at
        next_cursor = encode_cursor(t=active_at.isoformat(), id=str(last.id))
    return rows, next_cursor


async def conversation_aggregate(
    db: AsyncSession, conversation_id: uuid.UUID,
) -> dict[str, Any]:
    """会话的轮次/事件/用量聚合（**不读事件正文以外的东西**，只数行）。"""
    event_count, turn_count, max_turn, first_at, last_at = (
        await db.execute(
            select(
                func.count(AIConversationEvent.id),
                func.count(func.distinct(AIConversationEvent.turn)),
                func.coalesce(func.max(AIConversationEvent.turn), 0),
                func.min(AIConversationEvent.time),
                func.max(AIConversationEvent.time),
            ).where(AIConversationEvent.conversation_id == conversation_id)
        )
    ).one()
    tokens, cost = (await db.execute(
        select(
            func.coalesce(func.sum(AIUsage.tokens), 0),
            func.coalesce(func.sum(AIUsage.cost), 0),
        ).where(AIUsage.conversation_id == conversation_id)
    )).one()
    models = list((await db.scalars(
        select(AIUsage.model)
        .where(AIUsage.conversation_id == conversation_id)
        .distinct()
        .order_by(AIUsage.model)
    )).all())
    error_count = int(await db.scalar(
        select(func.count(AIConversationEvent.id)).where(
            AIConversationEvent.conversation_id == conversation_id,
            AIConversationEvent.type == "error",
        )
    ) or 0)
    return {
        "event_count": int(event_count or 0),
        "turn_count": int(turn_count or 0),
        "max_turn": int(max_turn or 0),
        "first_event_at": first_at,
        "last_event_at": last_at,
        "tokens": int(tokens or 0),
        "cost": int(cost or 0),
        "models": models,
        "has_error": error_count > 0,
    }


async def list_turns(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    after_turn: int | None,
    limit: int,
) -> tuple[list[dict[str, Any]], int | None]:
    """按 ``turn`` 分组的事件摘要（每轮：首条用户输入预览 / 模型 / token / 错误）。

    预览由路由层打码——这里给的是原文的前 :data:`TURN_PREVIEW_CHARS` 个字符。
    """
    stmt = select(AIConversationEvent).where(
        AIConversationEvent.conversation_id == conversation_id,
    )
    if after_turn is not None:
        stmt = stmt.where(AIConversationEvent.turn > after_turn)
    rows = list((await db.scalars(
        stmt.order_by(AIConversationEvent.turn, AIConversationEvent.seq)
    )).all())

    grouped: dict[int, list[AIConversationEvent]] = {}
    order: list[int] = []
    for row in rows:
        if row.turn not in grouped:
            grouped[row.turn] = []
            order.append(row.turn)
        grouped[row.turn].append(row)

    selected = order[:limit]
    turns = [_summarize_turn(turn, grouped[turn]) for turn in selected]
    next_after_turn = selected[-1] if len(order) > limit else None
    return turns, next_after_turn


def _summarize_turn(
    turn: int, rows: list[AIConversationEvent],
) -> dict[str, Any]:
    first_user_input = ""
    model: str | None = None
    tokens = 0
    has_error = False
    times: list[datetime] = []
    for row in rows:
        if row.time is not None:
            times.append(row.time)
        data = row.data if isinstance(row.data, dict) else {}
        if row.type == "user" and not first_user_input:
            content = data.get("content")
            if isinstance(content, str):
                first_user_input = " ".join(content.split())
        elif row.type == "quota":
            if isinstance(data.get("model"), str):
                model = data["model"]
            value = data.get("tokens")
            if isinstance(value, int) and not isinstance(value, bool):
                tokens += value
        elif row.type == "error":
            has_error = True
    return {
        "turn": turn,
        "first_user_input": first_user_input[:TURN_PREVIEW_CHARS],
        "model": model,
        "tokens": tokens,
        "has_error": has_error,
        "event_count": len(rows),
        "started_at": min(times) if times else None,
        "ended_at": max(times) if times else None,
    }


async def list_events(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    after_seq: int | None,
    turn: int | None,
    limit: int,
) -> tuple[list[AIConversationEvent], bool]:
    stmt = select(AIConversationEvent).where(
        AIConversationEvent.conversation_id == conversation_id,
    )
    if after_seq is not None:
        stmt = stmt.where(AIConversationEvent.seq > after_seq)
    if turn is not None:
        stmt = stmt.where(AIConversationEvent.turn == turn)
    rows = list((await db.scalars(
        stmt.order_by(AIConversationEvent.seq).limit(limit + 1)
    )).all())
    has_more = len(rows) > limit
    return rows[:limit], has_more


# ---------------------------------------------------------------------------
# 聚合统计
# ---------------------------------------------------------------------------

def _day_key(dialect_name: str):
    """按天分组的 key。

    用户决策 16（北京时间）：PG 下日界必须是 ``Asia/Shanghai``，而不是会话时区
    （默认 UTC）——否则「今天」会从北京时间 08:00 才算起。SQLite 测试后端没有
    ``timezone()``，退化为它的 ``date()``；日界精确性由 PG 承担，SQLite 用例只验
    分组结构。
    """
    if dialect_name == "postgresql":
        return func.date(func.timezone("Asia/Shanghai", AIUsage.created_at))
    return func.date(AIUsage.created_at)


async def usage_summary(
    db: AsyncSession,
    *,
    group_by: str,
    time_from: datetime | None,
    time_to: datetime | None,
) -> list[dict[str, Any]]:
    conditions = []
    if time_from is not None:
        conditions.append(AIUsage.created_at >= time_from)
    if time_to is not None:
        conditions.append(AIUsage.created_at < time_to)

    if group_by == "user":
        key = AIUsage.user_id
    elif group_by == "model":
        key = AIUsage.model
    else:
        bind = db.get_bind()
        key = _day_key(bind.dialect.name if bind is not None else "")

    rows = (await db.execute(
        select(
            key.label("key"),
            func.count(AIUsage.id),
            func.coalesce(func.sum(AIUsage.tokens), 0),
            func.coalesce(func.sum(AIUsage.cost), 0),
            func.count(func.distinct(AIUsage.user_id)),
        )
        .where(*conditions)
        .group_by(key)
        .order_by(key)
    )).all()

    return [
        {
            "key": str(raw_key),
            "requests": int(requests or 0),
            "tokens": int(tokens or 0),
            "cost": int(cost or 0),
            "users": int(users or 0),
        }
        for raw_key, requests, tokens, cost, users in rows
    ]


# ---------------------------------------------------------------------------
# request_id 反查
# ---------------------------------------------------------------------------

async def request_events(
    db: AsyncSession, request_id: str,
) -> list[AIConversationEvent]:
    return list((await db.scalars(
        select(AIConversationEvent)
        .where(AIConversationEvent.request_id == request_id)
        .order_by(AIConversationEvent.conversation_id, AIConversationEvent.seq)
    )).all())


async def request_usage(db: AsyncSession, request_id: str) -> list[AIUsage]:
    return list((await db.scalars(
        select(AIUsage)
        .where(AIUsage.request_id == request_id)
        .order_by(AIUsage.created_at)
    )).all())


async def conversations_by_ids(
    db: AsyncSession, conversation_ids: list[uuid.UUID],
) -> list[AIConversation]:
    if not conversation_ids:
        return []
    return list((await db.scalars(
        select(AIConversation).where(AIConversation.id.in_(conversation_ids))
    )).all())


# ---------------------------------------------------------------------------
# 导出（T-M8-09）
#
# 导出走**独立 session 的只读事务**（见 ``routes._stream_export``），查询层只
# 构造 ``select``。事件按 ``(conversation_id, seq)`` keyset 分批取，每批只保留
# 一个批次的行——不把结果集整体读进内存（架构 §7.3「流式 JSONL」）。
# ---------------------------------------------------------------------------

def export_scope_conditions(
    *,
    scope: str,
    user_id: uuid.UUID | None,
    conversation_id: uuid.UUID | None,
    time_from: datetime | None,
    time_to: datetime | None,
    q: str | None,
    include_archived: bool,
) -> list[Any]:
    """把导出的范围/筛选参数翻译成 ``ai_conversations`` 的条件列表。

    - ``all``：不做任何收窄（含已归档）。
    - ``user``：只按 ``user_id`` 收窄（"该用户全部会话"），忽略其余筛选。
    - ``filtered``：与 ``GET /admin/conversations`` 同口径；给了具体
      ``conversation_id`` 时只导出该会话，且**不受** ``include_archived`` 限制
      —— 回放页可以对任一打开着的会话发起导出。
    """
    if scope == "all":
        return []
    if scope == "user":
        return [AIConversation.user_id == user_id]
    if conversation_id is not None:
        return [AIConversation.id == conversation_id]

    conditions: list[Any] = []
    if not include_archived:
        conditions.append(AIConversation.archived_at.is_(None))
    if user_id is not None:
        conditions.append(AIConversation.user_id == user_id)
    if q:
        pattern = f"%{_escape_like(q)}%"
        conditions.append(AIConversation.search_text.ilike(pattern, escape="\\"))
    if time_from is not None:
        conditions.append(_ACTIVE_KEY >= time_from)
    if time_to is not None:
        conditions.append(_ACTIVE_KEY < time_to)
    return conditions


async def count_export_events(
    db: AsyncSession, *, conditions: list[Any],
) -> tuple[int, int]:
    """范围内的事件数与涉及会话数（用于上限判定与审计 detail）。"""
    scope_ids = select(AIConversation.id).where(*conditions)
    row = (await db.execute(
        select(
            func.count(AIConversationEvent.id),
            func.count(func.distinct(AIConversationEvent.conversation_id)),
        ).where(AIConversationEvent.conversation_id.in_(scope_ids))
    )).one()
    return int(row[0] or 0), int(row[1] or 0)


async def count_export_conversations(
    db: AsyncSession, *, conditions: list[Any],
) -> int:
    return int(await db.scalar(
        select(func.count(AIConversation.id)).where(*conditions)
    ) or 0)


async def export_event_batch(
    db: AsyncSession,
    *,
    conditions: list[Any],
    after: tuple[uuid.UUID, int] | None,
    limit: int,
) -> list[Any]:
    """按 ``(conversation_id, seq)`` 取下一批事件（keyset，无 offset）。"""
    scope_ids = select(AIConversation.id).where(*conditions)
    stmt = select(
        AIConversationEvent.conversation_id,
        AIConversationEvent.seq,
        AIConversationEvent.turn,
        AIConversationEvent.type,
        AIConversationEvent.time,
        AIConversationEvent.request_id,
        AIConversationEvent.data,
    ).where(AIConversationEvent.conversation_id.in_(scope_ids))
    if after is not None:
        conversation_id, seq = after
        stmt = stmt.where(or_(
            AIConversationEvent.conversation_id > conversation_id,
            and_(
                AIConversationEvent.conversation_id == conversation_id,
                AIConversationEvent.seq > seq,
            ),
        ))
    stmt = stmt.order_by(
        AIConversationEvent.conversation_id, AIConversationEvent.seq,
    ).limit(limit)
    return list((await db.execute(stmt)).all())


async def export_conversation_batch(
    db: AsyncSession,
    *,
    conditions: list[Any],
    after_id: uuid.UUID | None,
    limit: int,
) -> list[AIConversation]:
    stmt = select(AIConversation).where(*conditions)
    if after_id is not None:
        stmt = stmt.where(AIConversation.id > after_id)
    stmt = stmt.order_by(AIConversation.id).limit(limit)
    return list((await db.scalars(stmt)).all())
