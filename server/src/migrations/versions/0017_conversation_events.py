"""0017: T-M7-02f 会话状态与配额双限 — 事件表 + ai_usage.conversation_id。

Revision ID: 0017_conversation_events
Revises: 0016_cloud_entities
Create Date: 2026-09-18

两处改动（架构 §6.2 会话存储 / §12.2 已定表第 1、3 项）：

1. 新建 ``ai_conversation_events``——P1 的最小事件形态（``conversation_id`` /
   ``seq`` / ``type`` / ``data`` / ``created_at``）。P3（T-M7-04）在此之上补
   ``ignorable`` 与 Surface 子集，演进为完整事件日志。
2. ``ai_usage`` 增 ``conversation_id``，会话级「轮数 + token」双限按该列聚合。

**外键用 SET NULL 而非 CASCADE**：``ai_conversations`` 的行会被登出清理删除，
而 ``ai_usage`` 是 guest 日配额的计数基座——若随会话一起删，用户登出再登入就能
把当天配额刷回满额。审计行必须活得比会话长。事件表则相反，用 CASCADE：事件是
会话正文的一部分，会话没了正文就该没。

Database compatibility: PostgreSQL only（``sa.UUID`` 与 ALTER TABLE ADD
FOREIGN KEY 均为 PG 路径；测试面走 ``Base.metadata.create_all``，不经本迁移）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0017_conversation_events"
down_revision = "0016_cloud_entities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── ai_conversation_events ─────────────────────────────────────────
    op.create_table(
        "ai_conversation_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("ai_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.BigInteger, nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("data", sa.JSON, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_ai_conversation_events_conv_seq",
        "ai_conversation_events",
        ["conversation_id", "seq"],
        unique=True,
    )

    # ── ai_usage.conversation_id ───────────────────────────────────────
    op.add_column(
        "ai_usage",
        sa.Column(
            "conversation_id",
            sa.UUID(),
            sa.ForeignKey("ai_conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_ai_usage_conversation_created",
        "ai_usage",
        ["conversation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_usage_conversation_created", table_name="ai_usage")
    op.drop_column("ai_usage", "conversation_id")
    op.drop_index(
        "ix_ai_conversation_events_conv_seq",
        table_name="ai_conversation_events",
    )
    op.drop_table("ai_conversation_events")
