"""T-M8-04：运营后台鉴权与只读 API（`docs/architecture/admin-console.md` §5.1）。

红线：

- **只读**：读端点走 :func:`get_admin_read_session`（PG 上 ``SET TRANSACTION
  READ ONLY``），查询层只构造 ``select``。本模块唯一的写是登录审计、``reveal``
  审计与登录时的 ``last_login_at``——写操作（暂停 / 配额 / 强制登出）属 T-M8-05。
- **列表不返回事件正文**：正文只在 ``/admin/conversations/{id}/events``。
- **默认脱敏**：``reveal=true`` 才返回原文，并**必须**落一条 ``admin_audit_log``。
- 不复用 C 端限流器；独立限流在部署侧（架构 §6）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.config import get_config
from src.common.database import get_db_session, get_session_factory
from src.common.errors import AppException
from src.models.admin import AdminAccount, AdminAuditLog
from src.models.user import User, UserStatus

from . import queries
from .dependencies import (
    client_ip,
    create_admin_token,
    get_admin_read_session,
    require_admin,
    verify_admin_password,
)
from .schemas import (
    AdminConversationDetail,
    AdminConversationItem,
    AdminConversationListResponse,
    AdminEventItem,
    AdminEventListResponse,
    AdminLoginRequest,
    AdminRequestConversation,
    AdminRequestResponse,
    AdminRequestUsageItem,
    AdminTokenResponse,
    AdminTurnItem,
    AdminTurnListResponse,
    AdminUsageGroup,
    AdminUsageSummaryResponse,
    AdminUserDetail,
    AdminUserItem,
    AdminUserListResponse,
    mask_payload,
    mask_text,
)

router = APIRouter(tags=["admin"])

#: 会话关键词检索的最短长度（架构 §3.3：``q`` 长度 < 2 拒答）。
QUERY_MIN_CHARS = 2


# ---------------------------------------------------------------------------
# 审计（唯一的写路径）
# ---------------------------------------------------------------------------

async def _write_audit(
    *,
    admin_id: uuid.UUID,
    action: str,
    ip: str,
    target_user_id: uuid.UUID | None = None,
    conversation_id: uuid.UUID | None = None,
    detail: dict | None = None,
) -> None:
    """写一条审计并**立即提交**。

    ``reveal`` 走这条独立 session：读端点的事务在 PG 上是只读的，审计不能挂在
    它上面。调用方先回滚只读事务再写（SQLite 的读事务会挡住写）。
    """
    factory = get_session_factory()
    async with factory() as session:
        session.add(AdminAuditLog(
            admin_id=admin_id,
            action=action,
            target_user_id=target_user_id,
            conversation_id=conversation_id,
            detail=detail or {},
            ip=ip,
        ))
        await session.commit()


# ---------------------------------------------------------------------------
# 登录
# ---------------------------------------------------------------------------

@router.post("/auth/login", response_model=AdminTokenResponse)
async def admin_login(
    body: AdminLoginRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_session)],
):
    """用户名 + 密码 → 管理员 JWT。成功写一条 ``action=login`` 审计（含 IP）。"""
    admin = await queries.get_admin_by_username(db, body.username)
    if (
        admin is None
        or admin.status != "active"
        or not verify_admin_password(body.password, admin.password_hash)
    ):
        raise AppException(401, "invalid_credentials", "Invalid username or password")

    admin.last_login_at = datetime.now(UTC)
    db.add(AdminAuditLog(
        admin_id=admin.id,
        action="login",
        ip=client_ip(request),
        detail={"username": admin.username},
    ))
    await db.flush()

    cfg = get_config()
    return AdminTokenResponse(
        access_token=create_admin_token(admin.id),
        expires_in=cfg.admin_token_expire_minutes * 60,
        admin_id=admin.id,
        username=admin.username,
    )


# ---------------------------------------------------------------------------
# 用户
# ---------------------------------------------------------------------------

def _status_value(user: User) -> str:
    return user.status.value if isinstance(user.status, UserStatus) else str(user.status)


def _user_item(user: User) -> AdminUserItem:
    return AdminUserItem(
        id=user.id,
        phone=mask_text(user.phone) if user.phone else None,
        nickname=user.nickname,
        status=_status_value(user),
        created_at=user.created_at,
        admin_suspended_at=user.admin_suspended_at,
        ai_daily_quota_override=user.ai_daily_quota_override,
    )


@router.get("/users", response_model=AdminUserListResponse)
async def list_users(
    admin: Annotated[AdminAccount, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_admin_read_session)],
    q: Annotated[str | None, Query(max_length=128)] = None,
    status: UserStatus | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    """用户检索：``q``（手机号 / 昵称 / 用户 ID）、状态、注册时间区间。

    keyset 分页 ``(created_at, id)`` 倒序；手机号在响应里打码。
    """
    q = q.strip() if q else None
    rows, next_cursor = await queries.list_users(
        db,
        q=q,
        status=status,
        created_from=created_from,
        created_to=created_to,
        cursor=cursor,
        limit=limit,
    )
    return AdminUserListResponse(
        items=[_user_item(user) for user in rows],
        next_cursor=next_cursor,
    )


@router.get("/users/{user_id}", response_model=AdminUserDetail)
async def get_user_detail(
    user_id: uuid.UUID,
    admin: Annotated[AdminAccount, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_admin_read_session)],
):
    """用户详情：状态、注册时间、设备数、会话数、最近活动。"""
    user = await queries.get_user(db, user_id)
    if user is None:
        raise AppException(404, "user_not_found", "User not found")
    return AdminUserDetail(
        **_user_item(user).model_dump(),
        updated_at=user.updated_at,
        admin_suspended_reason=user.admin_suspended_reason,
        device_count=await queries.count_user_devices(db, user_id),
        conversation_count=await queries.count_user_conversations(db, user_id),
        last_activity_at=await queries.user_last_activity(db, user_id),
    )


async def _conversation_page(
    db: AsyncSession,
    *,
    q: str | None,
    user_id: uuid.UUID | None,
    time_from: datetime | None,
    time_to: datetime | None,
    include_archived: bool,
    cursor: str | None,
    limit: int,
) -> AdminConversationListResponse:
    if q is not None:
        q = q.strip()
        if q and len(q) < QUERY_MIN_CHARS:
            raise AppException(
                422, "query_too_short",
                f"q must be at least {QUERY_MIN_CHARS} characters",
            )
        q = q or None
    rows, next_cursor = await queries.list_conversations(
        db,
        q=q,
        user_id=user_id,
        time_from=time_from,
        time_to=time_to,
        include_archived=include_archived,
        cursor=cursor,
        limit=limit,
    )
    return AdminConversationListResponse(
        items=[AdminConversationItem.model_validate(row) for row in rows],
        next_cursor=next_cursor,
    )


@router.get("/users/{user_id}/conversations", response_model=AdminConversationListResponse)
async def list_user_conversations(
    user_id: uuid.UUID,
    admin: Annotated[AdminAccount, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_admin_read_session)],
    q: Annotated[str | None, Query(max_length=256)] = None,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
    include_archived: bool = False,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    """某用户的会话列表（默认排除已归档），按 ``last_active_at`` 倒序。

    ``search_text`` 命中即可，但**列表不返回事件正文**。
    """
    user = await queries.get_user(db, user_id)
    if user is None:
        raise AppException(404, "user_not_found", "User not found")
    return await _conversation_page(
        db,
        q=q,
        user_id=user_id,
        time_from=time_from,
        time_to=time_to,
        include_archived=include_archived,
        cursor=cursor,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------

@router.get("/conversations", response_model=AdminConversationListResponse)
async def list_conversations(
    admin: Annotated[AdminAccount, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_admin_read_session)],
    q: Annotated[str | None, Query(max_length=256)] = None,
    user_id: uuid.UUID | None = None,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
    include_archived: bool = False,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    """全局会话检索：``search_text ILIKE`` + 可选用户 / 时间区间。

    带 ``user_id`` 时走既有索引收窄；不带即全局顺序扫描（架构 §3.2 的 P0 口径）。
    """
    return await _conversation_page(
        db,
        q=q,
        user_id=user_id,
        time_from=time_from,
        time_to=time_to,
        include_archived=include_archived,
        cursor=cursor,
        limit=limit,
    )


@router.get("/conversations/{conversation_id}", response_model=AdminConversationDetail)
async def get_conversation_detail(
    conversation_id: uuid.UUID,
    admin: Annotated[AdminAccount, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_admin_read_session)],
):
    """会话元信息 + 轮次摘要（轮数 / 事件数 / 模型 / token / 是否报错）。

    ``summary`` 只报是否存在（``summary_present``），正文只在 ``/events``。
    """
    conversation = await queries.get_conversation(db, conversation_id)
    if conversation is None:
        raise AppException(404, "conversation_not_found", "Conversation not found")
    aggregate = await queries.conversation_aggregate(db, conversation_id)
    return AdminConversationDetail(
        **AdminConversationItem.model_validate(conversation).model_dump(),
        updated_at=conversation.updated_at,
        summary_present=bool(conversation.summary),
        search_text_chars=len(conversation.search_text or ""),
        **aggregate,
    )


@router.get(
    "/conversations/{conversation_id}/turns", response_model=AdminTurnListResponse,
)
async def list_conversation_turns(
    conversation_id: uuid.UUID,
    admin: Annotated[AdminAccount, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_admin_read_session)],
    after_turn: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    """轮次列表：每轮首条用户输入（**打码截断预览**）/ 模型 / token / 错误。"""
    conversation = await queries.get_conversation(db, conversation_id)
    if conversation is None:
        raise AppException(404, "conversation_not_found", "Conversation not found")
    turns, next_after_turn = await queries.list_turns(
        db, conversation_id, after_turn=after_turn, limit=limit,
    )
    return AdminTurnListResponse(
        conversation_id=conversation_id,
        items=[
            AdminTurnItem(
                **{**turn, "first_user_input": mask_text(turn["first_user_input"])},
            )
            for turn in turns
        ],
        next_after_turn=next_after_turn,
    )


@router.get(
    "/conversations/{conversation_id}/events", response_model=AdminEventListResponse,
)
async def list_conversation_events(
    conversation_id: uuid.UUID,
    request: Request,
    admin: Annotated[AdminAccount, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_admin_read_session)],
    after_seq: Annotated[int | None, Query(ge=0)] = None,
    turn: Annotated[int | None, Query(ge=1)] = None,
    reveal: bool = False,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
):
    """事件流（**唯一返回事件正文的端点**）。

    ``reveal=false``（默认）按正则打码手机号 / 邮箱 / 身份证 / 银行卡；
    ``reveal=true`` 返回原文，并写一条 ``admin_audit_log(action="reveal")``。
    """
    conversation = await queries.get_conversation(db, conversation_id)
    if conversation is None:
        raise AppException(404, "conversation_not_found", "Conversation not found")
    # 回滚会 expire 本 session 里的 ORM 对象（``admin`` / ``conversation``），
    # 之后的属性访问在 async 上下文里会触发 lazy load 并炸 MissingGreenlet。
    # 审计需要的事实先取出来。
    audit_admin_id = admin.id
    audit_user_id = conversation.user_id
    audit_conversation_id = conversation.id

    rows, has_more = await queries.list_events(
        db, conversation_id, after_seq=after_seq, turn=turn, limit=limit,
    )
    items = [
        AdminEventItem(
            seq=row.seq,
            turn=row.turn,
            type=row.type,
            time=row.time,
            request_id=row.request_id,
            data=mask_payload(row.data, reveal=reveal),
        )
        for row in rows
    ]
    if reveal and items:
        # 先结束只读事务，再起一个可写事务写审计（SQLite 的读事务会挡写；
        # PG 的只读事务同理不能承载审计行）。
        await db.rollback()
        await _write_audit(
            admin_id=audit_admin_id,
            action="reveal",
            ip=client_ip(request),
            target_user_id=audit_user_id,
            conversation_id=audit_conversation_id,
            detail={
                "endpoint": "conversation_events",
                "after_seq": after_seq,
                "turn": turn,
                "event_count": len(items),
            },
        )
    return AdminEventListResponse(
        conversation_id=conversation_id,
        reveal=reveal,
        items=items,
        next_after_seq=items[-1].seq if has_more and items else None,
    )


# ---------------------------------------------------------------------------
# 聚合统计
# ---------------------------------------------------------------------------

@router.get("/ai-usage/summary", response_model=AdminUsageSummaryResponse)
async def ai_usage_summary(
    admin: Annotated[AdminAccount, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_admin_read_session)],
    group_by: Literal["user", "model", "day"] = "user",
    time_from: datetime | None = None,
    time_to: datetime | None = None,
):
    """token / 成本 / 请求数 的聚合（``ai_usage`` 表；只读）。"""
    items = await queries.usage_summary(
        db, group_by=group_by, time_from=time_from, time_to=time_to,
    )
    return AdminUsageSummaryResponse(
        group_by=group_by,
        items=[AdminUsageGroup(**item) for item in items],
    )


# ---------------------------------------------------------------------------
# request_id 反查
# ---------------------------------------------------------------------------

@router.get("/requests/{request_id}", response_model=AdminRequestResponse)
async def lookup_request(
    request_id: str,
    admin: Annotated[AdminAccount, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_admin_read_session)],
):
    """按 ``request_id`` 精确反查会话与轮次；兼容 ``ai_usage.request_id``。

    事件表的 ``request_id`` 是 M8-03 起写入的精确列（历史事件为 NULL），因此
    这里不做时间窗近似——查不到就是查不到。
    """
    events = await queries.request_events(db, request_id)
    usage = await queries.request_usage(db, request_id)
    if not events and not usage:
        raise AppException(404, "request_not_found", "Request not found")

    conversation_ids: list[uuid.UUID] = []
    for event in events:
        if event.conversation_id not in conversation_ids:
            conversation_ids.append(event.conversation_id)
    for row in usage:
        if row.conversation_id is not None and row.conversation_id not in conversation_ids:
            conversation_ids.append(row.conversation_id)

    conversations = {
        row.id: row
        for row in await queries.conversations_by_ids(db, conversation_ids)
    }

    grouped: dict[uuid.UUID, list] = {}
    for event in events:
        grouped.setdefault(event.conversation_id, []).append(event)

    return AdminRequestResponse(
        request_id=request_id,
        conversations=[
            AdminRequestConversation(
                conversation_id=conversation_id,
                user_id=(
                    conversations[conversation_id].user_id
                    if conversation_id in conversations else None
                ),
                turns=sorted({event.turn for event in rows}),
                event_count=len(rows),
                first_seq=rows[0].seq,
                last_seq=rows[-1].seq,
                event_types=sorted({event.type for event in rows}),
            )
            for conversation_id, rows in grouped.items()
        ],
        usage=[
            AdminRequestUsageItem(
                provider=row.provider,
                model=row.model,
                tokens=row.tokens,
                cost=row.cost,
                conversation_id=row.conversation_id,
                created_at=row.created_at,
            )
            for row in usage
        ],
    )
