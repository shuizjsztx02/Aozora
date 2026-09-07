"""0011: Add user_consents table for T-M5-04a.

Revision ID: 0011_user_consents
Revises: 0010_attachment_user_id_backfill
Create Date: 2026-09-07

Records privacy policy consent per device.  Allows pre-auth consent
recording (device_id links consent to device before user registers).

Database compatibility: PostgreSQL + SQLite (no PG-specific types).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_user_consents"
down_revision = "0010_attachment_user_id_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_consents",
        sa.Column("id", sa.Uuid, nullable=False),
        sa.Column("device_id", sa.String(128), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_consents_device_id", "user_consents", ["device_id"])


def downgrade() -> None:
    op.drop_index("ix_user_consents_device_id", table_name="user_consents")
    op.drop_table("user_consents")
