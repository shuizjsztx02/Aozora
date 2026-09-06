"""T-M2-02: User and DeviceSession models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import AuditMixin, Base


class UserStatus(str, enum.Enum):
    GUEST = "guest"
    ACTIVE = "active"
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
