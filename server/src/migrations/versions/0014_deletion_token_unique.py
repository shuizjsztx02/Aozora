"""0014: deletion_tokens — partial unique index per active user.

Revision ID: 0014_deletion_token_unique
Revises: 0013_crash_reports
Create Date: 2026-09-07

Change:
- ``deletion_tokens`` gets a partial unique index on ``user_id`` where
  ``used_at IS NULL``. Closes the race in ``request_deletion_token``
  where two concurrent requests can both delete-then-insert and end up
  leaving two valid tokens.

The index is partial (only unused tokens) so historically-used rows stay
in the table for audit without conflicting with a new issuance.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_deletion_token_unique"
down_revision = "0013_crash_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Defensive pre-clean: the feature hasn't shipped yet, but any dev
    # DB that may already contain two unused tokens for the same user
    # would fail the index creation. Unused tokens are short-lived
    # (5 min) and carry no user-facing state — purging them is safe.
    # Used rows are left untouched.
    op.execute("DELETE FROM deletion_tokens WHERE used_at IS NULL")

    op.create_index(
        "uq_deletion_tokens_user_active",
        "deletion_tokens",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("used_at IS NULL"),
        sqlite_where=sa.text("used_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_deletion_tokens_user_active",
        table_name="deletion_tokens",
    )
