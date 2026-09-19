"""0020: T-M7-04a 事件日志完整化 — ``ai_conversation_events`` 补 ``time`` / ``ignorable`` + ``type`` 加宽。

Revision ID: 0020_conversation_event_log
Revises: 0019_conversation_index
Create Date: 2026-09-19

P1 的事件表（0017 建、0018 补 ``turn``）是「够多轮上下文读写」的最小形态：
``type`` 就是消息角色，读出来整批喂回 loop。T-M7-04 要把它演进为**完整事件
日志**（架构 §6.2：append-only 事件日志 + 派生投影），三处改动：

- ``time``：**事件写下**的时刻（业务时间轴），与 ``created_at`` 分开。注意 PG 的
  ``now()`` 是**事务**时刻（``transaction_timestamp()``）而本列由写入方取时刻——
  一轮 loop 跑完才落库，dev 实测二者相差数秒。``server_default=now()`` 只为让已有
  行满足 NOT NULL，同时作为 DB 兜底（与 ``created_at`` 同口径，ORM 侧同样声明了它）。
- ``ignorable``：结构性事件标记——True 表示这条事件**不进模型上下文**（投影 A），
  只进投影 B（UI）与投影 C（审计）。写侧由 ``conversation.SURFACE_EVENT_TYPES``
  派生，读侧过滤以该常量为准。
- ``type`` 加宽 ``varchar(16)`` → ``varchar(32)``。**实测（PG 17.11）：**
  ``varchar(16)`` 按字符数计，全集里最长的 ``approval_request`` 恰为 16 字符，
  顶格且零余量；17 字符即 ``StringDataRightTruncationError``。加宽不改变存储
  语义（varchar 变长），只是让后续新增类型名不必再来一次迁移。

**不回填 ``time``**：已有行的 ``time`` 取迁移执行时刻（``server_default``），
与它们的 ``created_at`` 不一定相等。回填需要 ``UPDATE … SET time = created_at``
——本卡**刻意不做**：P1/P2 的事件都是整轮写入，两个时间戳的差异只在秒级，而
回填会在一次长事务里锁写全表。若 04b 的恢复路径需要精确的历史事件时间，在那张
卡里连同逐事件落库一起处理。

Database compatibility: PostgreSQL only（与 0017 / 0018 / 0019 同；测试面走
``Base.metadata.create_all``，不经本迁移）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_conversation_event_log"
down_revision = "0019_conversation_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "ai_conversation_events", "type",
        existing_type=sa.String(16),
        type_=sa.String(32),
        existing_nullable=False,
    )
    op.add_column(
        "ai_conversation_events",
        sa.Column(
            "time", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "ai_conversation_events",
        sa.Column(
            "ignorable", sa.Boolean,
            nullable=False, server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("ai_conversation_events", "ignorable")
    op.drop_column("ai_conversation_events", "time")
    # 收窄前先截断：降级路径不该因为「有行超宽」而整条失败。超宽的只可能是
    # P3 之后新增的类型名，截断会让那些行读出来是个未知类型——但这正是降级的
    # 语义（旧代码不认识它们）。
    op.execute(
        "UPDATE ai_conversation_events SET type = left(type, 16) "
        "WHERE length(type) > 16"
    )
    op.alter_column(
        "ai_conversation_events", "type",
        existing_type=sa.String(32),
        type_=sa.String(16),
        existing_nullable=False,
    )
