"""0010: Backfill attachments.user_id, set NOT NULL, add index.

Revision ID: 0010_attachment_user_id_backfill
Revises: 0009_attachment_user_id
Create Date: 2026-09-07

Bug-C1 fix: migration 0009 added ``user_id`` as nullable for ALTER TABLE
simplicity, with a TODO to backfill existing rows before tightening.  This
migration:

  1. Backfills ``user_id`` from ``transactions.user_id`` for rows that
     have a ``transaction_id`` but no ``user_id`` (pre-T-M4-04c data).
  2. Deletes orphan PENDING rows that have neither ``user_id`` nor a
     recoverable ``transaction_id`` (pre-T-M4-04c stale data).
  3. Alters ``user_id`` to NOT NULL (PostgreSQL only).
  4. Creates ``ix_attachments_user_id`` to avoid full-table scans on
     every ownership-filtered query (NEW-1).

Database compatibility: PostgreSQL only (matches 0009).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_attachment_user_id_backfill"
down_revision = "0009_attachment_user_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Step 1: Backfill user_id from transactions → books → owner_id.
    op.execute(
        sa.text(
            "UPDATE attachments SET user_id = ("
            "  SELECT b.owner_id FROM transactions t"
            "  JOIN books b ON b.id = t.book_id"
            "  WHERE t.id = attachments.transaction_id"
            ") WHERE user_id IS NULL AND transaction_id IS NOT NULL"
        )
    )

    # Step 2: Delete orphan PENDING rows that have no user_id and no
    # recoverable transaction_id (pre-T-M4-04c stale data).
    op.execute(
        sa.text(
            "DELETE FROM attachments"
            " WHERE user_id IS NULL"
        )
    )

    # Step 3: Tighten to NOT NULL now that all rows have a value.
    op.alter_column(
        "attachments",
        "user_id",
        existing_type=sa.UUID(),
        nullable=False,
    )

    # Step 4: Create index for ownership-filtered queries (NEW-1).
    op.create_index("ix_attachments_user_id", "attachments", ["user_id"])


def downgrade() -> None:
    # ⚠ DATA-LOSS WARNING:
    # upgrade() deletes rows (PENDING attachments with no user_id / transaction_id)
    # and backfills user_id from transactions.  downgrade() can NOT recover that
    # deleted data, nor re-null user_id on rows that were backfilled.  It only
    # restores the nullable constraint and drops the index.  Running downgrade
    # on a production database that has been upgraded will leave orphan rows
    # unrecoverable.  Treat downgrade as non-reversible for data.
    op.drop_index("ix_attachments_user_id", table_name="attachments")
    op.alter_column(
        "attachments",
        "user_id",
        existing_type=sa.UUID(),
        nullable=True,
    )
