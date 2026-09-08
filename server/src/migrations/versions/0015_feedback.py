"""0015: Add feedbacks table for T-M5-05c beta feedback collection.

Revision ID: 0015_feedback
Revises: 0014_deletion_token_unique
Create Date: 2026-09-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_feedback"
down_revision = "0014_deletion_token_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feedbacks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id_hash", sa.String(16), nullable=False),
        sa.Column("device_id_hash", sa.String(64), nullable=True),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("contact", sa.String(128), nullable=True),
        sa.Column("app_version", sa.String(32), nullable=False),
        sa.Column("android_version", sa.String(16), nullable=False),
        sa.Column("device_model", sa.String(64), nullable=False),
        sa.Column(
            "submitted_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_feedbacks_user_id_hash", "feedbacks", ["user_id_hash"])
    op.create_index("ix_feedbacks_category", "feedbacks", ["category"])


def downgrade() -> None:
    op.drop_index("ix_feedbacks_category", table_name="feedbacks")
    op.drop_index("ix_feedbacks_user_id_hash", table_name="feedbacks")
    op.drop_table("feedbacks")
