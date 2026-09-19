"""0019: T-M7-03a 会话索引可列表 — ``ai_conversations`` 补三列 + 列表索引。

Revision ID: 0019_conversation_index
Revises: 0018_conversation_event_turns
Create Date: 2026-09-19

P1 的 ``ai_conversations``（0017 建表）只有 ``id`` / ``user_id`` / ``book_id`` /
``summary`` + 两个审计时间戳——够「按 id 取一个会话」，不够「列出我的会话」。
P2 的会话列表要三件东西，各加一列：

- ``title``：列表上的会话名，默认由首轮用户输入派生（服务层），用户可改；
- ``last_active_at``：最后一次写入事件的时间，**列表的排序键**。刻意不复用
  ``updated_at``——它被重命名 / 软删 / 换账本一并改动，列表顺序会乱跳；
- ``archived_at``：软删标记。与 ``Category`` / ``SavingsGoal`` 的 ``archived_at``
  同口径（「删除语义＝归档」），``books`` 那套是 ``deleted_at`` + ``version``，
  会话没有同步协议、不需要版本号，故随归档型实体。

索引形状 = 列表查询的形状：本人的、未归档的、按活跃时间倒序。三个都在谓词 /
排序里，故合成一个复合索引。

``title`` 的 ``server_default=''`` **保留在列上**（不像 0018 的 ``turn`` 用完即弃）：
ORM 侧同样声明了该默认值，两边一致；且后续若从别处插入会话行，空标题是合法值。

Database compatibility: PostgreSQL only（与 0017 / 0018 同；测试面走
``Base.metadata.create_all``，不经本迁移）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_conversation_index"
down_revision = "0018_conversation_event_turns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_conversations",
        sa.Column("title", sa.String(120), nullable=False, server_default=""),
    )
    op.add_column(
        "ai_conversations",
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ai_conversations",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_ai_conversations_user_archived_active",
        "ai_conversations",
        ["user_id", "archived_at", "last_active_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_conversations_user_archived_active",
        table_name="ai_conversations",
    )
    op.drop_column("ai_conversations", "archived_at")
    op.drop_column("ai_conversations", "last_active_at")
    op.drop_column("ai_conversations", "title")
