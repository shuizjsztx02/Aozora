"""0016: T-M7-01 三实体上云 — savings_goals / scheduled_rules / chat_personas。

Revision ID: 0016_cloud_entities
Revises: 0015_feedback
Create Date: 2026-09-17

三张表此前只存在于客户端 Room（savings_goals 为 Room v5、scheduled_rules /
chat_personas 为 Room v8）。本迁移把它们的**云端权威**建起来，接入既有 sync
协议（entity_type: savings_goal / scheduled_rule / chat_persona）。

均采用 archived_at 归档软删语义（镜像 categories / accounts），不使用 deleted_at。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0016_cloud_entities"
down_revision = "0015_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── savings_goals ──────────────────────────────────────────────────
    op.create_table(
        "savings_goals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("target_cents", sa.BigInteger, nullable=False),
        sa.Column("current_cents", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("icon", sa.String(64), nullable=True),
        sa.Column("period_value", sa.Integer, nullable=True),
        sa.Column("period_unit", sa.String(8), nullable=True),
        sa.Column("version", sa.BigInteger, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("target_cents > 0", name="ck_savings_goals_target_positive"),
        sa.CheckConstraint("current_cents >= 0", name="ck_savings_goals_current_non_negative"),
    )
    op.create_index("ix_savings_goals_owner_id", "savings_goals", ["owner_id"])

    # ── scheduled_rules ────────────────────────────────────────────────
    op.create_table(
        "scheduled_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(10), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("amount_cents", sa.BigInteger, nullable=False),
        sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("category_id", UUID(as_uuid=True), sa.ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("account_id", UUID(as_uuid=True), sa.ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("include_in_budget", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("note", sa.String(2000), nullable=False, server_default=""),
        sa.Column("repeat", sa.String(10), nullable=False),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("end_date", sa.Date, nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("last_run_date", sa.Date, nullable=True),
        sa.Column("run_hour", sa.Integer, nullable=False, server_default="8"),
        sa.Column("run_minute", sa.Integer, nullable=False, server_default="0"),
        sa.Column("version", sa.BigInteger, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("amount_cents > 0", name="ck_scheduled_rules_amount_positive"),
        sa.CheckConstraint("run_hour >= 0 AND run_hour <= 23", name="ck_scheduled_rules_run_hour"),
        sa.CheckConstraint("run_minute >= 0 AND run_minute <= 59", name="ck_scheduled_rules_run_minute"),
    )
    op.create_index("ix_scheduled_rules_owner_id", "scheduled_rules", ["owner_id"])
    op.create_index("ix_scheduled_rules_book_id", "scheduled_rules", ["book_id"])

    # ── chat_personas ──────────────────────────────────────────────────
    op.create_table(
        "chat_personas",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("avatar", sa.String(64), nullable=True),
        sa.Column("persona", sa.String(2000), nullable=False, server_default=""),
        sa.Column("version", sa.BigInteger, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_chat_personas_owner_id", "chat_personas", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_personas_owner_id", table_name="chat_personas")
    op.drop_table("chat_personas")
    op.drop_index("ix_scheduled_rules_book_id", table_name="scheduled_rules")
    op.drop_index("ix_scheduled_rules_owner_id", table_name="scheduled_rules")
    op.drop_table("scheduled_rules")
    op.drop_index("ix_savings_goals_owner_id", table_name="savings_goals")
    op.drop_table("savings_goals")
