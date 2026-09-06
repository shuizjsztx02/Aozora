"""T-M4-02b: ProposalIdempotency — dedup AI proposal execution.

Re-executing the same ``proposalId`` by the same user returns the cached
``transaction_ids`` without creating duplicate transactions. The key is
``(user_id, proposal_id)`` because ``proposalId`` is a client-generated
string that may collide across users.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class ProposalIdempotency(Base):
    """Records each (user_id, proposalId) that has been successfully executed."""

    __tablename__ = "proposal_idempotency"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "proposal_id",
            name="uq_proposal_idempotency_user_proposal",
        ),
        Index("ix_proposal_idempotency_user", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    proposal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    book_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
    )
    # JSON list of transaction UUID strings created by this execution.
    transaction_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
