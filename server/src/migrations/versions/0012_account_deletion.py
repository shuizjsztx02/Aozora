"""0012: Account deletion, frozen status, attachment lifecycle — T-M5-04c.

Revision ID: 0012_account_deletion
Revises: 0011_user_consents
Create Date: 2026-09-07

Changes:
- users.frozen_at, users.deletion_requested_at (grace window tracking)
- deletion_tokens (short-lived confirm_token for DELETE /me)
- deletion_audit (permanent proof-of-deletion record)
- attachments.keep_after_parse (opt-out of 24h parsed-image purge)

Database compatibility: PostgreSQL + SQLite (no PG-specific types).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_account_deletion"
down_revision = "0011_user_consents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── users: frozen_at / deletion_requested_at ──────────────────────
    op.add_column(
        "users",
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── deletion_tokens ───────────────────────────────────────────────
    op.create_table(
        "deletion_tokens",
        sa.Column("id", sa.Uuid, nullable=False),
        sa.Column("user_id", sa.Uuid, nullable=False),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token"),
    )
    op.create_index("ix_deletion_tokens_user_id", "deletion_tokens", ["user_id"])
    op.create_index("ix_deletion_tokens_token", "deletion_tokens", ["token"])

    # ── deletion_audit ────────────────────────────────────────────────
    op.create_table(
        "deletion_audit",
        sa.Column("id", sa.Uuid, nullable=False),
        sa.Column("user_id_hash", sa.String(64), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_tables", sa.Text, nullable=False),
        sa.Column("rows_count", sa.BigInteger, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id_hash"),
    )
    op.create_index(
        "ix_deletion_audit_user_id_hash", "deletion_audit", ["user_id_hash"],
    )

    # ── attachments.keep_after_parse ──────────────────────────────────
    op.add_column(
        "attachments",
        sa.Column(
            "keep_after_parse", sa.Boolean,
            nullable=False, server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("attachments", "keep_after_parse")
    op.drop_index("ix_deletion_audit_user_id_hash", table_name="deletion_audit")
    op.drop_table("deletion_audit")
    op.drop_index("ix_deletion_tokens_token", table_name="deletion_tokens")
    op.drop_index("ix_deletion_tokens_user_id", table_name="deletion_tokens")
    op.drop_table("deletion_tokens")
    op.drop_column("users", "deletion_requested_at")
    op.drop_column("users", "frozen_at")
