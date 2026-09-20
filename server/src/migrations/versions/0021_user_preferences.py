"""0021: T-M7-05j 偏好项上云 — user_preferences。

Revision ID: 0021_user_preferences
Revises: 0020_conversation_event_log
Create Date: 2026-09-20

客户端 ``sync_meta`` 的用户级偏好键（默认账本/账户、列表排序、小组件配置、
用户画像）此前只存设备本地（桥命令 ``meta.set`` 明写「不上行」）。本迁移把
它们的**云端权威**建起来，接入既有 sync 协议（entity_type: user_preference）。

每键一行（``(user_id, key)`` 唯一，主键仍是 UUID ``id``——sync 表 ``entity_id``
需要 UUID 类型）。无 ``archived_at`` / ``deleted_at``：偏好键没有"归档"概念，
清除即删行。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0021_user_preferences"
down_revision = "0020_conversation_event_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_preferences",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("key", sa.String(64), nullable=False),
        # 值形态由客户端 MetaWhitelist 校验；服务端透传，存 Text 留余量。
        sa.Column("value", sa.Text, nullable=False, server_default=""),
        sa.Column("version", sa.BigInteger, nullable=False, server_default="1"),
        sa.UniqueConstraint(
            "user_id", "key", name="uq_user_preferences_user_key",
        ),
    )
    op.create_index("ix_user_preferences_user_id", "user_preferences", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_preferences_user_id", table_name="user_preferences")
    op.drop_table("user_preferences")
