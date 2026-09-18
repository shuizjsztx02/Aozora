"""0018: T-M7-02f 复审 P2-1 — 会话事件表补 ``turn`` 列（窗口截断的唯一依据）。

Revision ID: 0018_conversation_event_turns
Revises: 0017_conversation_events
Create Date: 2026-09-18

0017 建的 ``ai_conversation_events`` 只有 ``seq``，而历史窗口原本按「每条
``user`` 消息起始」切轮——**一次 ``AgentLoop.run`` 的批次里可以有多条 user
消息**（``submit()`` 的中途输入在下一个 Step 入口落进消息流）。于是窗口起点会
落在轮中间：用户最初的提问被丢掉，只剩后补的那一句，而「追问指代」正是该卡的
核心场景。独立复审复现：N=1 时窗口只剩 ``['补充说明：12块', '好了']``。

修法：写入方在 ``append_turn`` 里一次性分配轮号（一次调用 = 一个 ``turn``），
读取方按 ``turn`` 整取。**只追加迁移、不改 0017**（仓库既有约定）。

``server_default="1"`` 只为让已有行满足 NOT NULL，随即去掉——ORM 侧不声明该
默认值，保持模型与迁移一致。

Database compatibility: PostgreSQL only（与 0017 同）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_conversation_event_turns"
down_revision = "0017_conversation_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_conversation_events",
        sa.Column("turn", sa.BigInteger, nullable=False, server_default="1"),
    )
    op.alter_column("ai_conversation_events", "turn", server_default=None)


def downgrade() -> None:
    op.drop_column("ai_conversation_events", "turn")
