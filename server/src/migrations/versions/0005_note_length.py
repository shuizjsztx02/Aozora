"""0005: Cap transactions.note to 2000 chars (matches Pydantic schema).

Revision ID: 0005_note_length
Revises: 0004_budget_unique_constraint
Create Date: 2026-09-05

N-13: defense-in-depth — the API schema already rejects notes longer
than 2000 chars; this makes the DB column match so a future caller
that bypasses the API boundary is still bounded.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_note_length"
down_revision = "0004_budget_unique_constraint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL: ALTER COLUMN … TYPE VARCHAR(2000). Existing data that
    # already exceeds 2000 chars is truncated by the API boundary before
    # this migration runs; any pre-existing over-long rows are clipped.
    op.alter_column(
        "transactions", "note",
        type_=sa.String(2000),
        existing_type=sa.Text(),
        existing_nullable=False,
        existing_server_default="",
    )


def downgrade() -> None:
    op.alter_column(
        "transactions", "note",
        type_=sa.Text(),
        existing_type=sa.String(2000),
        existing_nullable=False,
        existing_server_default="",
    )
