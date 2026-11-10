"""0024: M8 运营可观测后台的数据面基线。

Revision ID: 0024_m8_admin_console
Revises: 0023_category_icon
Create Date: 2026-11-10

第二十二轮 REQ-84。M8 的**全部 schema 变更集中在这一条迁移**：Alembic 是单链，
四张卡各带迁移必然分叉；schema 先行、后续卡只写代码，并行度最高且失败面只有一处
（`docs/milestones/T-M8-admin-console-plan.md` §二）。

六处：

1. ``ai_conversations.search_text``——会话检索面（只收 user/assistant 正文）。
2. ``ai_conversation_events.request_id`` + 索引——按请求 ID 精确反查。
   历史行为 NULL，**不做近似回填**。
3. ``admin_accounts``——管理员身份（独立于 ``users``）。
4. ``admin_audit_log``——登录 / 查看明文 / 导出 / 写操作。
5. ``users.admin_suspended_at / admin_suspended_reason``——后台暂停。
   **刻意不复用 ``status=FROZEN``**：删除任务会物理删除该状态用户。
6. ``users.ai_daily_quota_override``——guest 日配额的用户级覆盖。
7. ``ai_proposal_links``——``proposal_id → conversation_id``（审批结果写回用）。

存量数据处理：``search_text`` 是派生数据，本迁移**不自动回填**——由
``server/scripts/backfill_m8_search_text.py`` 单独跑（可断点、幂等）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0024_m8_admin_console"
down_revision = "0023_category_icon"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 会话检索面
    op.add_column(
        "ai_conversations",
        sa.Column("search_text", sa.Text(), server_default="", nullable=False),
    )

    # 2. 事件级请求标识
    op.add_column(
        "ai_conversation_events",
        sa.Column("request_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_ai_conversation_events_request",
        "ai_conversation_events",
        ["request_id"],
    )

    # 3. 管理员身份
    op.create_table(
        "admin_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default="active", nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 4. 后台审计（SET NULL：审计事实不随业务行消失）
    op.create_table(
        "admin_audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "admin_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("admin_accounts.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column(
            "target_user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "conversation_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_conversations.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("detail", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index(
        "ix_admin_audit_log_admin_created",
        "admin_audit_log", ["admin_id", "created_at"],
    )
    op.create_index(
        "ix_admin_audit_log_action_created",
        "admin_audit_log", ["action", "created_at"],
    )
    op.create_index(
        "ix_admin_audit_log_target_created",
        "admin_audit_log", ["target_user_id", "created_at"],
    )

    # 5. 后台暂停（独立于 status=FROZEN）
    op.add_column(
        "users",
        sa.Column("admin_suspended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("admin_suspended_reason", sa.String(length=200), nullable=True),
    )

    # 6. guest 日配额覆盖
    op.add_column(
        "users",
        sa.Column("ai_daily_quota_override", sa.Integer(), nullable=True),
    )

    # 7. 提案 → 会话关联
    op.create_table(
        "ai_proposal_links",
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("proposal_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "conversation_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_conversations.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("turn", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index(
        "ix_ai_proposal_links_conversation",
        "ai_proposal_links", ["conversation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_proposal_links_conversation", table_name="ai_proposal_links")
    op.drop_table("ai_proposal_links")

    op.drop_column("users", "ai_daily_quota_override")
    op.drop_column("users", "admin_suspended_reason")
    op.drop_column("users", "admin_suspended_at")

    op.drop_index("ix_admin_audit_log_target_created", table_name="admin_audit_log")
    op.drop_index("ix_admin_audit_log_action_created", table_name="admin_audit_log")
    op.drop_index("ix_admin_audit_log_admin_created", table_name="admin_audit_log")
    op.drop_table("admin_audit_log")
    op.drop_table("admin_accounts")

    op.drop_index(
        "ix_ai_conversation_events_request", table_name="ai_conversation_events",
    )
    op.drop_column("ai_conversation_events", "request_id")

    op.drop_column("ai_conversations", "search_text")
