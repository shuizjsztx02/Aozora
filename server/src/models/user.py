"""T-M2-02 + T-M5-04c: User and DeviceSession models.

T-M5-04c adds ``FROZEN`` status plus ``frozen_at`` / ``deletion_requested_at``
columns to support the 7-day account-deletion grace window.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import AuditMixin, Base


class UserStatus(str, enum.Enum):
    GUEST = "guest"
    ACTIVE = "active"
    # T-M5-04c: user requested deletion. Data is preserved for 7 days
    # (grace window) before physical deletion by infra/cleanup/account-deletion.py.
    # Frozen users cannot push new data; pull returns empty.
    FROZEN = "frozen"
    DEACTIVATED = "deactivated"
    DELETED = "deleted"


# R-M2-19: phone format is enforced at the Pydantic schema layer
# (PhoneLoginRequest: pattern ``^\d{11}$``). A DB-level CHECK constraint
# would need PostgreSQL-specific regex syntax (``~``), which breaks the
# SQLite test backend. Since the ORM is the only write path, schema-layer
# validation is sufficient.


class User(Base, AuditMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    phone: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    nickname: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[UserStatus] = mapped_column(
        String(20),
        default=UserStatus.GUEST,
        server_default=UserStatus.GUEST.value,
        nullable=False,
    )
    timezone: Mapped[str] = mapped_column(
        String(64),
        default="Asia/Shanghai",
        server_default="Asia/Shanghai",
        nullable=False,
    )
    # T-M5-04c: account-deletion grace window tracking.
    # ``frozen_at`` is set when the user confirms deletion; physical
    # deletion occurs 7 days later via infra/cleanup/account-deletion.py.
    # ``deletion_requested_at`` mirrors the original request timestamp
    # (which may differ from frozen_at if the user re-confirms).
    frozen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    deletion_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    # M8-05：管理后台的「暂停账户」。**刻意不复用 ``status = FROZEN``**——
    # ``infra/cleanup/account-deletion.py`` 会物理删除
    # ``status=FROZEN AND frozen_at <= now-7d`` 的用户；后台若借用该状态，
    # 冻结 7 天后数据会被删除任务清掉。独立字段让「用户主动删号」与
    # 「运营暂停」互不影响；暂停期间只拒绝写与同步，数据完整保留。
    admin_suspended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    admin_suspended_reason: Mapped[str | None] = mapped_column(
        String(200), nullable=True,
    )
    # M8-05：guest 日配额的用户级覆盖。NULL = 回落 ``ai_daily_quota_guest``。
    ai_daily_quota_override: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )

    # relationships
    device_sessions: Mapped[list[DeviceSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan",
    )


class DeviceSession(Base, AuditMixin):
    __tablename__ = "device_sessions"
    __table_args__ = (
        UniqueConstraint("user_id", "device_id", name="uq_device_sessions_user_device"),
        # R-M2-06: every refresh / logout looks up by token hash; without an
        # index this is a full-table scan.
        Index("ix_device_sessions_refresh_token_hash", "refresh_token_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    refresh_token_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )

    user: Mapped[User] = relationship(back_populates="device_sessions")
