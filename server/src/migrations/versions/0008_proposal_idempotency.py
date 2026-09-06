"""0008: proposal_idempotency table for AI execute dedup.

Revision ID: 0008_proposal_idempotency
Revises: 0007_ai_usage_index
Create Date: 2026-09-06

T-M4-02b: ``POST /ai/execute`` must be idempotent on ``(user_id,
proposalId)``. Re-executing the same proposal returns the cached
``transaction_ids`` without creating duplicate transactions. The table
stores the mapping plus the ``book_id`` for cross-book safety checks.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_proposal_idempotency"
down_revision = "0007_ai_usage_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "proposal_idempotency",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("proposal_id", sa.String(length=64), nullable=False),
        sa.Column(
            "book_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("books.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("transaction_ids", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "user_id", "proposal_id",
            name="uq_proposal_idempotency_user_proposal",
        ),
    )
    op.create_index(
        "ix_proposal_idempotency_user",
        "proposal_idempotency",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_proposal_idempotency_user", table_name="proposal_idempotency")
    op.drop_table("proposal_idempotency")
