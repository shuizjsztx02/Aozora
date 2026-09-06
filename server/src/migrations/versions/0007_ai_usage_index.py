"""0007: Index ai_usage for per-user daily quota queries.

Revision ID: 0007_ai_usage_index
Revises: 0006_budget_deleted_at
Create Date: 2026-09-06

T-M4-01: the AI Gateway counts a guest user's ``ai_usage`` rows for the
current day (``WHERE user_id = ? AND created_at >= ?``) on every
``POST /ai/interpret`` request.  0001 created ``ai_usage`` without any
secondary index, so the quota check would degrade into a full table
scan as usage grows.
"""

from __future__ import annotations

from alembic import op

revision = "0007_ai_usage_index"
down_revision = "0006_budget_deleted_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_ai_usage_user_created",
        "ai_usage",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_usage_user_created", table_name="ai_usage")
