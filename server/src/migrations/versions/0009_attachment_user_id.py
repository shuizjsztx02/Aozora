"""0009: Add attachments.user_id + file_size for T-M4-04c.

Revision ID: 0009_attachment_user_id
Revises: 0008_proposal_idempotency
Create Date: 2026-09-06

T-M4-04c introduces the attachment upload API. The ``attachments`` table
(0001_initial) had no owner column — every row was only identifiable via
its optional ``transaction_id`` FK, which is NULL while the attachment
is still PENDING. ``user_id`` makes ownership checkable at upload time.

``file_size`` records the byte count of the stored object for storage
accounting and policy enforcement (e.g. per-user quota in the future).

SQLite compatibility: both columns are added as nullable to satisfy
SQLite's ``ALTER TABLE`` limitation (no NOT NULL without default).
PostgreSQL production accepts the same migration; the ORM layer
enforces NOT NULL via application logic.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_attachment_user_id"
down_revision = "0008_proposal_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "attachments",
        sa.Column(
            "user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column(
        "attachments",
        sa.Column("file_size", sa.BigInteger, nullable=True),
    )
    # SQLite does not support NOT NULL without a server_default via
    # ALTER TABLE; PostgreSQL accepts it but the migration is shared.
    # Application layer enforces presence of user_id on insert.


def downgrade() -> None:
    op.drop_column("attachments", "file_size")
    op.drop_column("attachments", "user_id")
