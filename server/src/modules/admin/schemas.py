"""T-M8-04：admin API 的请求/响应模型与 PII 脱敏。

脱敏口径（`docs/architecture/admin-console.md` §7.1）：手机号 ``138****1234``、
邮箱、身份证、银行卡号按正则打码。**如实说明**：正则只覆盖结构化 PII，
用户原话里的姓名、金额、地址无法自动识别——那部分靠访问审计约束。
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# PII 脱敏
# ---------------------------------------------------------------------------

#: 手机号：保留前三后四。``(?<!\d)`` / ``(?!\d)`` 防止从更长的数字串里截一段。
_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)")
#: 邮箱：本地部分只留首字符。
_EMAIL_RE = re.compile(
    r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"
)
#: 身份证（18 位，最后一位可为 X）：保留前 6 后 4。
_ID_CARD_RE = re.compile(r"(?<!\d)(\d{6})\d{8}(\d{3}[\dXx])(?!\d)")
#: 银行卡号（16–19 位）：保留前 4 后 4。
_BANK_CARD_RE = re.compile(r"(?<!\d)(\d{4})\d{8,11}(\d{4})(?!\d)")


def mask_text(value: str) -> str:
    """按正则给单段文本打码。顺序：邮箱 → 身份证 → 银行卡 → 手机号。

    18 位银行卡会先被身份证规则命中——两者都要打码，先命中哪个不影响结果。
    """
    masked = _EMAIL_RE.sub(lambda m: f"{m.group(1)}***{m.group(2)}", value)
    masked = _ID_CARD_RE.sub(
        lambda m: f"{m.group(1)}********{m.group(2)}", masked,
    )
    masked = _BANK_CARD_RE.sub(
        lambda m: f"{m.group(1)}{'*' * (len(m.group(0)) - 8)}{m.group(2)}", masked,
    )
    return _PHONE_RE.sub(lambda m: f"{m.group(1)}****{m.group(2)}", masked)


def mask_payload(value: Any, *, reveal: bool) -> Any:
    """递归给事件载荷打码；``reveal=True`` 原样返回（调用方负责写审计）。"""
    if reveal:
        return value
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, dict):
        return {key: mask_payload(item, reveal=False) for key, item in value.items()}
    if isinstance(value, list):
        return [mask_payload(item, reveal=False) for item in value]
    return value


# ---------------------------------------------------------------------------
# 登录
# ---------------------------------------------------------------------------

class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class AdminTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    admin_id: uuid.UUID
    username: str


# ---------------------------------------------------------------------------
# 用户
# ---------------------------------------------------------------------------

class AdminUserItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    phone: str | None = None
    nickname: str | None = None
    status: str
    created_at: datetime
    admin_suspended_at: datetime | None = None
    ai_daily_quota_override: int | None = None


class AdminUserListResponse(BaseModel):
    items: list[AdminUserItem]
    next_cursor: str | None = None


class AdminUserDetail(AdminUserItem):
    updated_at: datetime
    admin_suspended_reason: str | None = None
    device_count: int
    conversation_count: int
    last_activity_at: datetime | None = None


# ---------------------------------------------------------------------------
# 写操作（T-M8-05）
# ---------------------------------------------------------------------------

class AdminSuspendRequest(BaseModel):
    #: 与 ``users.admin_suspended_reason`` 的列宽一致。
    reason: str | None = Field(default=None, max_length=200)


class AdminQuotaUpdateRequest(BaseModel):
    #: ``null`` = 清除覆盖，回落到 ``cfg.ai_daily_quota_guest``。
    quota: int | None = Field(ge=0)


class AdminUserWriteResponse(AdminUserItem):
    admin_suspended_reason: str | None = None


class AdminQuotaResponse(BaseModel):
    user_id: uuid.UUID
    ai_daily_quota_override: int | None


class AdminSessionsRevokedResponse(BaseModel):
    user_id: uuid.UUID
    deleted_sessions: int


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------

class AdminConversationItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    book_id: uuid.UUID | None = None
    title: str
    last_active_at: datetime | None = None
    created_at: datetime
    archived_at: datetime | None = None


class AdminConversationListResponse(BaseModel):
    items: list[AdminConversationItem]
    next_cursor: str | None = None


class AdminConversationDetail(AdminConversationItem):
    updated_at: datetime
    #: ``summary`` 是正文的压缩（窗口外历史），列表/详情不返回它的文本——
    #: 正文只在 ``/events``。这里只说明它是否存在。
    summary_present: bool
    search_text_chars: int
    turn_count: int
    event_count: int
    max_turn: int
    has_error: bool
    models: list[str]
    tokens: int
    cost: int
    first_event_at: datetime | None = None
    last_event_at: datetime | None = None


class AdminTurnItem(BaseModel):
    turn: int
    #: 首条用户输入的**打码截断预览**——列表不返回事件正文。
    first_user_input: str
    model: str | None = None
    tokens: int
    has_error: bool
    event_count: int
    started_at: datetime | None = None
    ended_at: datetime | None = None


class AdminTurnListResponse(BaseModel):
    conversation_id: uuid.UUID
    items: list[AdminTurnItem]
    next_after_turn: int | None = None


class AdminEventItem(BaseModel):
    seq: int
    turn: int
    type: str
    time: datetime
    request_id: str | None = None
    data: dict[str, Any]


class AdminEventListResponse(BaseModel):
    conversation_id: uuid.UUID
    reveal: bool
    items: list[AdminEventItem]
    next_after_seq: int | None = None


# ---------------------------------------------------------------------------
# 聚合统计
# ---------------------------------------------------------------------------

class AdminUsageGroup(BaseModel):
    key: str
    requests: int
    tokens: int
    cost: int
    users: int


class AdminUsageSummaryResponse(BaseModel):
    group_by: str
    items: list[AdminUsageGroup]


# ---------------------------------------------------------------------------
# request_id 反查
# ---------------------------------------------------------------------------

class AdminRequestConversation(BaseModel):
    conversation_id: uuid.UUID
    user_id: uuid.UUID | None = None
    turns: list[int]
    event_count: int
    first_seq: int
    last_seq: int
    event_types: list[str]


class AdminRequestUsageItem(BaseModel):
    provider: str
    model: str
    tokens: int
    cost: int
    conversation_id: uuid.UUID | None = None
    created_at: datetime


class AdminRequestResponse(BaseModel):
    request_id: str
    conversations: list[AdminRequestConversation]
    usage: list[AdminRequestUsageItem]
