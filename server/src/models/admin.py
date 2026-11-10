"""M8：运营后台的身份与审计模型。

边界（`docs/architecture/admin-console.md` §6–§7）：

- 管理员**不复用** ``users``。用户端 token 泄露不能在后台等价于管理员权限，
  两边用独立的 secret 与 audience。
- 审计记录**不随业务行删除**：``admin_id`` / ``target_user_id`` /
  ``conversation_id`` 都是 ``SET NULL``，管理员被移除或用户数据被清后，
  审计事实仍保留（只失去指向）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class AdminAccount(Base):
    """管理员账号。P0 不引入角色分级——所有管理员权限相同。"""

    __tablename__ = "admin_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="active", server_default="active", nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )


class AdminAuditLog(Base):
    """管理后台审计。

    **记录口径（用户 2026-11-10 确认）**：登录、查看明文、导出、写操作各一条；
    普通浏览会话/轮次/列表**不记录**。不要把这里做成「全量访问日志」——
    那既违背用户决策，也会让表以浏览速度增长。
    """

    __tablename__ = "admin_audit_log"
    __table_args__ = (
        Index("ix_admin_audit_log_admin_created", "admin_id", "created_at"),
        Index("ix_admin_audit_log_action_created", "action", "created_at"),
        Index("ix_admin_audit_log_target_created", "target_user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("admin_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: ``login`` / ``reveal`` / ``export`` / ``write``
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_conversations.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: 动作细节（导出范围/内容模式/条数、写操作前后摘要等）。
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default="{}", nullable=False,
    )
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
