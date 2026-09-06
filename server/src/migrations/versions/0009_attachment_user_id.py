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

Database compatibility: this migration is **PostgreSQL only**. The
``sa.UUID()`` column type maps to the native ``UUID`` type that exists
in PostgreSQL but not in SQLite or MySQL. Running this migration
against a non-PostgreSQL database will fail at the ``ALTER TABLE``
statement. This is intentional — attachment storage requires reliable
UUID generation and the production target is PostgreSQL.
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
    # NOTE: user_id is nullable at the DDL level for ALTER TABLE simplicity.
    # The ORM layer enforces NOT NULL via application logic on insert.
    #
    # TODO (Bug-C1): before production deploy with existing attachment rows:
    #   1. Backfill: UPDATE attachments SET user_id = (
    #        SELECT t.user_id FROM transactions t
    #        WHERE t.id = attachments.transaction_id
    #      ) WHERE user_id IS NULL AND transaction_id IS NOT NULL;
    #   2. Create follow-up migration 0010 to SET NOT NULL on user_id.
    #   3. Delete orphan PENDING rows with no user_id (pre-T-M4-04c data).


def downgrade() -> None:
    op.drop_column("attachments", "file_size")
    op.drop_column("attachments", "user_id")
