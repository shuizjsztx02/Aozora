"""0022: 账户分类与当前余额两端化 — accounts.account_type / balance_cents。

Revision ID: 0022_account_balance_type
Revises: 0021_user_preferences
Create Date: 2026-09-23

第十八轮 REQ-69（T18-5，ADR-013）。两列此前只存在于客户端 Room：

- ``account_type``（自由文本，如「储蓄卡」）**上行时被 sync 静默忽略**、pull 又
  把它冲刷成 NULL —— 用户改的分类在一轮同步后消失（与 REQ-67 同源）。
- ``balance_cents`` 服务端完全没有，而产品口径「账户不计算余额」本轮作废。

两条与 ``savings_goals.current_cents`` 刻意不同的口径：

1. ``balance_cents`` **不加非负 CHECK** —— 信用卡欠款是常态，负余额必须能存；
2. 它是**存量字段**（可被手动校准），与「按交易现算的净额」不保证相等，
   差额即手动校准量。

存量行：``balance_cents`` 取 0（「未设置/未校准」）、``account_type`` 保持
NULL（客户端 pull 侧对空值有本地保护，不会冲掉本地已有分类）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022_account_balance_type"
down_revision = "0021_user_preferences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("account_type", sa.String(64), nullable=True),
    )
    op.add_column(
        "accounts",
        sa.Column(
            "balance_cents", sa.BigInteger, nullable=False, server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("accounts", "balance_cents")
    op.drop_column("accounts", "account_type")
