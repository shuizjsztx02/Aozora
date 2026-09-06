"""0004: Replace budget unique constraint with partial unique indexes.

SQL UNIQUE treats NULL != NULL, so a single constraint on
(book_id, budget_type, period, category_id) allows multiple rows where
category_id IS NULL.  Replace with two partial unique indexes.

Revision ID: 0004_budget_unique_constraint
Revises: 0003_sync_idempotency
Create Date: 2026-09-04
"""

from __future__ import annotations

from alembic import op

revision = "0004_budget_unique_constraint"
down_revision = "0003_sync_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the old constraint (name varies by dialect; SQLite uses the
    # generated name from the table definition).
    op.drop_constraint("uq_budgets_period_category", "budgets", type_="unique")

    # Create partial unique indexes.
    op.create_index(
        "uq_budgets_overall", "budgets",
        ["book_id", "budget_type", "period"],
        unique=True,
        sqlite_where=op.f("category_id IS NULL"),
        postgresql_where=op.f("category_id IS NULL"),
    )
    op.create_index(
        "uq_budgets_category", "budgets",
        ["book_id", "budget_type", "period", "category_id"],
        unique=True,
        sqlite_where=op.f("category_id IS NOT NULL"),
        postgresql_where=op.f("category_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_budgets_category", table_name="budgets")
    op.drop_index("uq_budgets_overall", table_name="budgets")
    op.create_unique_constraint(
        "uq_budgets_period_category",
        "budgets",
        ["book_id", "budget_type", "period", "category_id"],
    )
