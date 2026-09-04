"""T-M2-02: Declarative base and reusable mixins.

All models share:
- UUID primary keys (native PostgreSQL ``uuid`` type)
- ``created_at`` / ``updated_at`` audit timestamps (server-side UTC)

Versioned entities additionally carry an integer ``version`` column.
Soft-deletable entities carry a nullable ``deleted_at`` timestamp.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base for all Aozora models."""


class AuditMixin:
    """``created_at`` / ``updated_at`` audit timestamps."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class VersionedMixin:
    """Optimistic-concurrency ``version`` counter (starts at 1)."""

    version: Mapped[int] = mapped_column(
        BigInteger,
        default=1,
        server_default="1",
        nullable=False,
    )


class SoftDeleteMixin:
    """Soft-delete marker — ``NULL`` means not deleted."""

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
