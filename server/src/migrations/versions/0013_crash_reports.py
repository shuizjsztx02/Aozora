"""0013: Add crash_reports table for T-M5-05b crash reporting.

Revision ID: 0013_crash_reports
Revises: 0012_account_deletion
Create Date: 2026-09-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_crash_reports"
down_revision = "0012_account_deletion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crash_reports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("crash_signature", sa.String(64), nullable=False),
        sa.Column("app_version", sa.String(32), nullable=False),
        sa.Column("android_version", sa.String(16), nullable=False),
        sa.Column("device_model", sa.String(64), nullable=False),
        sa.Column("exception_class", sa.String(128), nullable=False),
        sa.Column("exception_message", sa.Text(), nullable=False),
        sa.Column("stacktrace", sa.Text(), nullable=False),
        sa.Column(
            "reported_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("device_id_hash", sa.String(16), nullable=True),
    )
    op.create_index(
        "ix_crash_reports_signature",
        "crash_reports",
        ["crash_signature"],
    )
    op.create_index(
        "ix_crash_reports_app_version",
        "crash_reports",
        ["app_version"],
    )


def downgrade() -> None:
    op.drop_index("ix_crash_reports_app_version", table_name="crash_reports")
    op.drop_index("ix_crash_reports_signature", table_name="crash_reports")
    op.drop_table("crash_reports")
