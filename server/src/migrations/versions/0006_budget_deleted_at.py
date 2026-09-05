"""0006: Add budgets.deleted_at for durable sync delete.

Revision ID: 0006_budget_deleted_at
Revises: 0005_note_length
Create Date: 2026-09-05

T-M3-02: ``SyncService._apply_delete`` sets ``deleted_at`` for every
non-archived entity type, but ``budgets`` had no such column, so budget
deletes were silently lost server-side.  Add the column and extend the
two partial unique indexes with ``deleted_at IS NULL`` so a soft-deleted
budget does not block re-creating the same (book, type, period) budget.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_budget_deleted_at"
down_revision = "0005_note_length"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "budgets",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Recreate the partial unique indexes (0004) including deleted_at.
    op.drop_index("uq_budgets_overall", table_name="budgets")
    op.drop_index("uq_budgets_category", table_name="budgets")
    op.create_index(
        "uq_budgets_overall",
        "budgets",
        ["book_id", "budget_type", "period"],
        unique=True,
        postgresql_where=sa.text("category_id IS NULL AND deleted_at IS NULL"),
        sqlite_where=sa.text("category_id IS NULL AND deleted_at IS NULL"),
    )
    op.create_index(
        "uq_budgets_category",
        "budgets",
        ["book_id", "budget_type", "period", "category_id"],
        unique=True,
        postgresql_where=sa.text("category_id IS NOT NULL AND deleted_at IS NULL"),
        sqlite_where=sa.text("category_id IS NOT NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_budgets_category", table_name="budgets")
    op.drop_index("uq_budgets_overall", table_name="budgets")
    op.create_index(
        "uq_budgets_overall",
        "budgets",
        ["book_id", "budget_type", "period"],
        unique=True,
        postgresql_where=sa.text("category_id IS NULL"),
        sqlite_where=sa.text("category_id IS NULL"),
    )
    op.create_index(
        "uq_budgets_category",
        "budgets",
        ["book_id", "budget_type", "period", "category_id"],
        unique=True,
        postgresql_where=sa.text("category_id IS NOT NULL"),
        sqlite_where=sa.text("category_id IS NOT NULL"),
    )
    op.drop_column("budgets", "deleted_at")
