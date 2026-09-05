"""T-M3-01: MutationIdempotency — tracks processed client mutations for dedup."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class MutationIdempotency(Base):
    """Records each mutationId that has been successfully applied.

    The primary key is the client-generated ``mutation_id`` (UUID), so a
    repeated push with the same id is a no-op lookup.  ``sync_cursor``
    points at the ``SyncChange`` row created for this mutation, enabling
    the client to resume from exactly the right position after a retry.
    """

    __tablename__ = "mutation_idempotency"
    __table_args__ = (
        Index("ix_mutation_idempotency_user", "user_id"),
    )

    mutation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False,
    )
    # BigInteger for PostgreSQL, Integer for SQLite (tests).
    sync_cursor: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        nullable=False,
    )
    result_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
