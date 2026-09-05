"""0003: Add mutation_idempotency table for sync push dedup.

Revision ID: 0003_sync_idempotency
Revises: 0002_exports
Create Date: 2026-09-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_sync_idempotency"
down_revision = "0002_exports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mutation_idempotency",
        sa.Column("mutation_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sync_cursor", sa.BigInteger, nullable=False),
        sa.Column("result_version", sa.BigInteger, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_mutation_idempotency_user",
        "mutation_idempotency",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_mutation_idempotency_user", table_name="mutation_idempotency")
    op.drop_table("mutation_idempotency")
