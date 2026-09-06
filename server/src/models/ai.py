"""T-M2-02: AI-related models — AIConversation, AIUsage."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import AuditMixin, Base


class AIConversation(Base, AuditMixin):
    """Reserved for future multi-turn AI conversation context.

    Current gateway flow (T-M4-01) is stateless single-shot interpret —
    there is no conversation_id in the request schema and no consumer of
    these rows. The table exists because T-M2-02 created the schema and
    the 0001_initial migration owns it; deleting the model would break
    model/migration parity.

    T-M4-01c planned "AIConversation context storage and trimming" as a
    deliverable, but the product surface (multi-turn follow-ups per
    §6.4 "追问") is not yet built out. Wired up when T-M4-02c's
    assistant dialog feature needs conversation state; until then, the
    table stays empty and the row is just schema infrastructure.
    """
    __tablename__ = "ai_conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    book_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("books.id", ondelete="SET NULL"),
        nullable=True,
    )
    summary: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)


class AIUsage(Base):
    """Per-request AI usage record — append-only audit trail."""

    __tablename__ = "ai_usage"
    __table_args__ = (
        # T-M4-01: guest daily-quota check counts rows by
        # (user_id, created_at >= day start) on every /ai/interpret call.
        Index("ix_ai_usage_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cost: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
