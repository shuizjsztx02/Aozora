"""0023: 分类图标两端化 — categories.icon。

Revision ID: 0023_category_icon
Revises: 0022_account_balance_type
Create Date: 2026-09-25

第十九轮 REQ-73（T19-1，ADR-014）。``icon`` 此前只存在于客户端 Room：上行时
被 sync 静默忽略（无列、insert/update 不读、序列化也不出）、pull 靠一层本地
保护才没被冲刷、bootstrap 路径连保护都没有（走一次快照重建，分类图标全丢）。

跨端只承载 **≤64 字符的 emoji**：照片版 icon（``data:`` URI 可达 48KB）由客户端
在超长时**省略键**，保持「仅本机可见」的现状。放宽松会把整批 push 变成 422 毒丸
（第十八轮命名过的形态），故不做。

存量行：``icon`` 保持 NULL（客户端 pull 侧把「服务端空值」当「无信息」，保留本地
已有的 emoji，不冲刷 —— 与 ``accounts.account_type`` 同口径）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023_category_icon"
down_revision = "0022_account_balance_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "categories",
        sa.Column("icon", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("categories", "icon")
