"""T-M8-04 / T-M8-05：运营后台 API（`docs/architecture/admin-console.md` §5）。

红线：

- **读端点是只读的**：走 :func:`get_admin_read_session`（PG 上 ``SET
  TRANSACTION READ ONLY``），查询层只构造 ``select``。本模块的写有：登录审计、
  ``reveal`` 审计、登录时的 ``last_login_at``，以及 §8 / T-M8-05 的三项账户
  写操作（暂停 / 恢复、guest 日配额覆盖、强制登出）——后者各写一条
  ``admin_audit_log(action="write")``，**与业务写入同事务**（共用一个可写
  session，随请求结束统一提交）。**不改任何业务数据行**。
- **列表不返回事件正文**：正文只在 ``/admin/conversations/{id}/events``。
- **默认脱敏**：``reveal=true`` 才返回原文，并**必须**落一条 ``admin_audit_log``。
- 不复用 C 端限流器；独立限流在部署侧（架构 §6）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.config import get_config
from src.common.database import get_db_session, get_session_factory
from src.common.errors import AppException
from src.models.admin import AdminAccount, AdminAuditLog
from src.models.user import DeviceSession, User, UserStatus

from . import queries
from .dependencies import (
    begin_read_only,
    client_ip,
    create_admin_token,
    get_admin_read_session,
    require_admin,
    verify_admin_password,
)
from .schemas import (
    AdminAuditItem,
    AdminAuditListResponse,
    AdminConversationDetail,
    AdminConversationItem,
    AdminConversationListResponse,
    AdminEventItem,
    AdminEventListResponse,
    AdminExportRequest,
    AdminLoginRequest,
    AdminQuotaResponse,
    AdminQuotaUpdateRequest,
    AdminRequestConversation,
    AdminRequestResponse,
    AdminRequestUsageItem,
    AdminSessionsRevokedResponse,
    AdminSuspendRequest,
    AdminTokenResponse,
    AdminTurnItem,
    AdminTurnListResponse,
    AdminUsageGroup,
    AdminUsageSummaryResponse,
    AdminUserDetail,
    AdminUserItem,
    AdminUserListResponse,
    AdminUserWriteResponse,
    mask_payload,
    mask_text,
)

router = APIRouter(tags=["admin"])

logger = logging.getLogger(__name__)

#: 会话关键词检索的最短长度（架构 §3.3：``q`` 长度 < 2 拒答）。
QUERY_MIN_CHARS = 2

#: 单次导出的上限（用户决策 14 / 架构 §7.3）：超出提示缩小范围后分批。
MAX_EXPORT_EVENTS = 100_000

#: 流式分批的批大小——每批只把这么多行留在内存里。
EXPORT_BATCH_SIZE = 500


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


# ---------------------------------------------------------------------------
# 写操作（T-M8-05，架构 §8）
#
# 三项账户操作，各写一条 ``admin_audit_log(action="write")``。**读写分离**：
# 这些端点用 :func:`get_db_session`（可写，请求结束统一提交），审计行
# ``db.add`` 在同一个 session 上——业务写入与审计同事务，要么都成功要么都回滚。
# 不触碰任何业务数据行（流水 / 账本 / 分类 / 预算）。
# ---------------------------------------------------------------------------

def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _write_user_response(user: User) -> AdminUserWriteResponse:
    return AdminUserWriteResponse(
        **_user_item(user).model_dump(),
        admin_suspended_reason=user.admin_suspended_reason,
    )


def _add_write_audit(
    db: AsyncSession,
    *,
    admin: AdminAccount,
    request: Request,
    target_user_id: uuid.UUID,
    operation: str,
    before: dict,
    after: dict,
) -> None:
    """把写审计挂到调用方的事务上（**不提交**——随请求统一提交）。"""
    db.add(AdminAuditLog(
        admin_id=admin.id,
        action="write",
        target_user_id=target_user_id,
        ip=client_ip(request),
        detail={
            "operation": operation,
            "target_user_id": str(target_user_id),
            "before": before,
            "after": after,
        },
    ))


async def _get_user_or_404(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = await queries.get_user(db, user_id)
    if user is None:
        raise AppException(404, "user_not_found", "User not found")
    return user


@router.post("/users/{user_id}/suspend", response_model=AdminUserWriteResponse)
async def suspend_user(
    user_id: uuid.UUID,
    request: Request,
    admin: Annotated[AdminAccount, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    body: AdminSuspendRequest | None = None,
):
    """暂停账户（架构 §8.1）：只设 ``admin_suspended_at / reason``，**不动
    ``status``**——``FROZEN`` 会在 7 天后触发删除任务。暂停期间的写与同步
    push 由 :func:`src.modules.auth.suspended.require_not_suspended` 拒绝。
    """
    user = await _get_user_or_404(db, user_id)
    before = {
        "admin_suspended_at": _iso(user.admin_suspended_at),
        "admin_suspended_reason": user.admin_suspended_reason,
    }
    user.admin_suspended_at = datetime.now(UTC)
    user.admin_suspended_reason = body.reason if body is not None else None
    _add_write_audit(
        db,
        admin=admin,
        request=request,
        target_user_id=user.id,
        operation="suspend",
        before=before,
        after={
            "admin_suspended_at": _iso(user.admin_suspended_at),
            "admin_suspended_reason": user.admin_suspended_reason,
        },
    )
    await db.flush()
    return _write_user_response(user)


@router.post("/users/{user_id}/resume", response_model=AdminUserWriteResponse)
async def resume_user(
    user_id: uuid.UUID,
    request: Request,
    admin: Annotated[AdminAccount, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
):
    """解除暂停：清空两列。对未暂停的用户是幂等的 no-op（仍记审计）。"""
    user = await _get_user_or_404(db, user_id)
    before = {
        "admin_suspended_at": _iso(user.admin_suspended_at),
        "admin_suspended_reason": user.admin_suspended_reason,
    }
    user.admin_suspended_at = None
    user.admin_suspended_reason = None
    _add_write_audit(
        db,
        admin=admin,
        request=request,
        target_user_id=user.id,
        operation="resume",
        before=before,
        after={"admin_suspended_at": None, "admin_suspended_reason": None},
    )
    await db.flush()
    return _write_user_response(user)


@router.put("/users/{user_id}/ai-quota", response_model=AdminQuotaResponse)
async def set_ai_quota(
    user_id: uuid.UUID,
    body: AdminQuotaUpdateRequest,
    request: Request,
    admin: Annotated[AdminAccount, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
):
    """设置 guest 日配额的用户级覆盖；``quota=null`` 清除覆盖（架构 §8.2）。

    只影响 ``UserStatus.GUEST``——ACTIVE 用户不受日配额限制，覆盖值存着也不生效
    （``check_daily_quota`` 的第一个分支先返回）。
    """
    user = await _get_user_or_404(db, user_id)
    before = {"ai_daily_quota_override": user.ai_daily_quota_override}
    user.ai_daily_quota_override = body.quota
    _add_write_audit(
        db,
        admin=admin,
        request=request,
        target_user_id=user.id,
        operation="set_ai_quota",
        before=before,
        after={"ai_daily_quota_override": body.quota},
    )
    await db.flush()
    return AdminQuotaResponse(
        user_id=user.id,
        ai_daily_quota_override=user.ai_daily_quota_override,
    )


@router.delete("/users/{user_id}/sessions", response_model=AdminSessionsRevokedResponse)
async def revoke_user_sessions(
    user_id: uuid.UUID,
    request: Request,
    admin: Annotated[AdminAccount, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
):
    """强制登出：删除该用户全部 ``device_sessions``（架构 §8.3）。

    refresh token **立即失效**（下一次 ``/auth/refresh`` 查不到会话行）；已签发
    的 access token 仍活到过期——这是 JWT 的固有边界，不做服务端吊销表。用户
    可重新登录（登录不在暂停的阻断面上）。
    """
    user = await _get_user_or_404(db, user_id)
    before_count = await queries.count_user_devices(db, user.id)
    result = await db.execute(
        delete(DeviceSession).where(DeviceSession.user_id == user.id)
    )
    deleted = result.rowcount or 0
    _add_write_audit(
        db,
        admin=admin,
        request=request,
        target_user_id=user.id,
        operation="revoke_sessions",
        before={"device_sessions": before_count},
        after={"device_sessions": 0},
    )
    await db.flush()
    return AdminSessionsRevokedResponse(user_id=user.id, deleted_sessions=deleted)


# ---------------------------------------------------------------------------
# 导出（T-M8-09，架构 §7.3）
#
# 三种范围（筛选结果 / 全库 / 单用户）× 两种内容（明文 / 打码），流式 JSONL。
# 红线：
#
# - **只读**：查询走独立 session 的只读事务，只构造 ``select``，不写业务表。
# - **不整体读进内存**：事件按 ``(conversation_id, seq)`` keyset 分批取，每批
#   :data:`EXPORT_BATCH_SIZE` 行，序列化后立刻 yield。
# - **每次导出写一条** ``admin_audit_log(action="export")``（独立 session，与
#   ``reveal`` 审计同范式）；流中途断开时在 ``finally`` 里尽力而为记一条。
# - **上限 100,000 事件**：在流开始前用 COUNT 判定，超限直接 422
#   ``export_too_large``（一旦开始流式就无法再改状态码）。
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _ExportPlan:
    scope: str
    format: str
    content: str
    #: ``ai_conversations`` 的筛选条件（与 ``GET /admin/conversations`` 同口径）。
    conditions: list[Any]
    target_user_id: uuid.UUID | None
    conversation_id: uuid.UUID | None
    filters: dict[str, Any]


def _export_filters(body: AdminExportRequest, q: str | None) -> dict[str, Any]:
    """审计 detail 里的 ``filters``：只记**实际生效**的筛选（``scope=all`` 为空）。"""
    if body.scope == "all":
        return {
            "user_id": None, "conversation_id": None, "time_from": None,
            "time_to": None, "q": None, "include_archived": False,
        }
    if body.scope == "user":
        return {
            "user_id": str(body.user_id) if body.user_id else None,
            "conversation_id": None, "time_from": None, "time_to": None,
            "q": None, "include_archived": False,
        }
    return {
        "user_id": str(body.user_id) if body.user_id else None,
        "conversation_id": str(body.conversation_id) if body.conversation_id else None,
        "time_from": _iso(body.time_from),
        "time_to": _iso(body.time_to),
        "q": q,
        "include_archived": body.include_archived,
    }


def _event_line(row: Any, *, reveal: bool) -> str:
    return json.dumps(
        {
            "kind": "event",
            "conversation_id": str(row.conversation_id),
            "seq": row.seq,
            "turn": row.turn,
            "type": row.type,
            "time": row.time.isoformat() if row.time is not None else None,
            "request_id": row.request_id,
            "data": mask_payload(row.data, reveal=reveal),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _conversation_line(conversation: Any, *, reveal: bool) -> str:
    return json.dumps(
        {
            "kind": "conversation",
            "id": str(conversation.id),
            "user_id": str(conversation.user_id),
            "book_id": str(conversation.book_id) if conversation.book_id else None,
            "title": conversation.title if reveal else mask_text(conversation.title),
            "last_active_at": _iso(conversation.last_active_at),
            "created_at": _iso(conversation.created_at),
            "archived_at": _iso(conversation.archived_at),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


async def _iter_export_events(
    session: AsyncSession, plan: _ExportPlan, counters: dict[str, int],
) -> AsyncIterator[str]:
    """按批拉取事件并逐行 yield；``counters`` 记录实际写出的条数。"""
    reveal = plan.format == "plain"
    after: tuple[uuid.UUID, int] | None = None
    last_conversation: uuid.UUID | None = None
    while True:
        rows = await queries.export_event_batch(
            session, conditions=plan.conditions, after=after,
            limit=EXPORT_BATCH_SIZE,
        )
        if not rows:
            return
        for row in rows:
            # 事件按 conversation_id 有序，只需记住上一个 id——不建集合（内存随会话数增长）。
            if row.conversation_id != last_conversation:
                last_conversation = row.conversation_id
                counters["conversations"] += 1
            counters["events"] += 1
            yield _event_line(row, reveal=reveal) + "\n"
        after = (rows[-1].conversation_id, rows[-1].seq)
        if len(rows) < EXPORT_BATCH_SIZE:
            return


async def _iter_export_conversations(
    session: AsyncSession, plan: _ExportPlan, counters: dict[str, int],
) -> AsyncIterator[str]:
    reveal = plan.format == "plain"
    after_id: uuid.UUID | None = None
    while True:
        rows = await queries.export_conversation_batch(
            session, conditions=plan.conditions, after_id=after_id,
            limit=EXPORT_BATCH_SIZE,
        )
        if not rows:
            return
        for conversation in rows:
            counters["conversations"] += 1
            yield _conversation_line(conversation, reveal=reveal) + "\n"
        after_id = rows[-1].id
        if len(rows) < EXPORT_BATCH_SIZE:
            return


async def _record_export_audit(**kwargs: Any) -> None:
    """写导出审计；失败只记日志，不影响已发出的流（与 ``_record_failure`` 同口径）。"""
    try:
        await _write_audit(**kwargs)
    except Exception:  # noqa: BLE001 — 尽力而为
        logger.warning(
            "admin export audit not recorded",
            extra={"outcome": "admin_export_audit_failed"},
        )


@router.post("/export", response_class=StreamingResponse)
async def export_data(
    body: AdminExportRequest,
    request: Request,
    admin: Annotated[AdminAccount, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_admin_read_session)],
):
    """导出（架构 §7.3）：``Content-Type: application/x-ndjson``，逐行 JSON。

    请求体见 :class:`AdminExportRequest`。响应是附件
    ``aozora-export-<UTC 时间戳>.jsonl``；每行一个对象（``kind`` 区分事件与会话）。
    ``format=plain`` 返回原文，``masked``（默认）复用 :func:`mask_text` 的 PII 打码。

    超限（> :data:`MAX_EXPORT_EVENTS`）在**开始流之前**返回 422
    ``export_too_large``——流一旦开始，状态码已不可改。
    """
    q = body.q.strip() if body.q else None
    if q is not None and q and len(q) < QUERY_MIN_CHARS:
        raise AppException(
            422, "query_too_short",
            f"q must be at least {QUERY_MIN_CHARS} characters",
        )
    q = q or None
    if body.scope == "user" and body.user_id is None:
        raise AppException(422, "user_required", "scope=user requires user_id")

    conditions = queries.export_scope_conditions(
        scope=body.scope,
        user_id=body.user_id,
        conversation_id=body.conversation_id,
        time_from=body.time_from,
        time_to=body.time_to,
        q=q,
        include_archived=body.include_archived,
    )

    if body.content == "conversations":
        total = await queries.count_export_conversations(db, conditions=conditions)
        unit = "conversations"
    else:
        total, _ = await queries.count_export_events(db, conditions=conditions)
        unit = "events"
    if total > MAX_EXPORT_EVENTS:
        raise AppException(
            422, "export_too_large",
            f"Export would include {total} {unit}, exceeding the "
            f"{MAX_EXPORT_EVENTS} limit. Narrow the time range or pick a single "
            "user and export in batches.",
        )

    plan = _ExportPlan(
        scope=body.scope,
        format=body.format,
        content=body.content,
        conditions=conditions,
        # 范围是「单用户」或筛选里指定了用户时，审计挂到该用户上。
        target_user_id=body.user_id if body.scope != "all" else None,
        conversation_id=(
            body.conversation_id if body.scope == "filtered" else None
        ),
        filters=_export_filters(body, q),
    )

    # 审计需要的事实先取出：流式生成器里不再访问 ``admin`` 的 ORM 属性
    # （请求结束/回滚后 lazy load 会炸 MissingGreenlet）。
    audit_admin_id = admin.id
    audit_ip = client_ip(request)
    started = time.monotonic()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    filename = f"aozora-export-{stamp}.jsonl"

    async def stream() -> AsyncIterator[str]:
        counters = {"events": 0, "conversations": 0}
        completed = False
        try:
            # 独立 session：依赖里的只读 session 的生命周期与 StreamingResponse
            # 不保证重叠；导出全程用自己的一条只读事务。
            factory = get_session_factory()
            async with factory() as session:
                await begin_read_only(session)
                if plan.content == "events":
                    async for line in _iter_export_events(session, plan, counters):
                        yield line
                else:
                    async for line in _iter_export_conversations(session, plan, counters):
                        yield line
            completed = True
        finally:
            detail = {
                "scope": plan.scope,
                "format": plan.format,
                "content": plan.content,
                "events": counters["events"],
                "conversations": counters["conversations"],
                "duration_ms": int((time.monotonic() - started) * 1000),
                "filters": plan.filters,
                # 流中途断开（客户端取消）时为 False——尽力而为留一条痕迹。
                "completed": completed,
            }
            # ``shield``：客户端断开时生成器被取消，审计写入仍作为一个独立任务
            # 跑完；失败只记日志，不改变已经发出的数据。
            task = asyncio.ensure_future(_record_export_audit(
                admin_id=audit_admin_id,
                action="export",
                ip=audit_ip,
                target_user_id=plan.target_user_id,
                conversation_id=plan.conversation_id,
                detail=detail,
            ))
            try:
                await asyncio.shield(task)
            except Exception:  # noqa: BLE001 — 见 _record_export_audit
                logger.warning(
                    "admin export audit task failed",
                    extra={"outcome": "admin_export_audit_failed"},
                )

    return StreamingResponse(
        stream(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/audit", response_model=AdminAuditListResponse)
async def list_audit(
    admin: Annotated[AdminAccount, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_admin_read_session)],
    action: Annotated[str | None, Query(max_length=32)] = None,
    admin_id: uuid.UUID | None = None,
    target_user_id: uuid.UUID | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    """审计记录查询（M8-10）：登录 / reveal / export / write。

    只读；keyset ``(created_at, id)`` 倒序。普通浏览不产生记录（用户决策 12）。
    """
    rows, next_cursor = await queries.list_audit_logs(
        db,
        action=action,
        admin_id=admin_id,
        target_user_id=target_user_id,
        cursor=cursor,
        limit=limit,
    )
    return AdminAuditListResponse(
        items=[AdminAuditItem.model_validate(row) for row in rows],
        next_cursor=next_cursor,
    )
