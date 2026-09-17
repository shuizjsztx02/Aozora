"""T-M7-01: ChatPersona model — 聊天角色上云（第十一轮 REQ-28 / 决策 #8）。

字段对齐客户端 ``ChatPersonaEntity``（app/src/main/java/com/aozora/app/db/entity/
ChatPersonaEntity.kt）。客户端该表**无 ownerId 列**（角色是设备本地播种的配置，
随当前会话归属），上云后由服务端按 token 的 user_id 归属；pull 回客户端时
ownerId 不入本地列，故无需新增 Room 列。

``avatar`` 可为 emoji 或照片 data URI。照片版远超服务端 64 字符上限，客户端
push 时省略该键（镜像 book/account 的 ``ICON_WIRE_MAX_LEN`` 口径），服务端存
emoji/null，照片头像保持设备本地专属。

删除语义＝归档（``archived_at``），同 SavingsGoal。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import AuditMixin, Base, VersionedMixin


class ChatPersona(Base, AuditMixin, VersionedMixin):
    __tablename__ = "chat_personas"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    avatar: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 客户端校验 ≤2000 字（Repository.MAX_PERSONA_TEXT_LEN），注入 AI system 消息。
    persona: Mapped[str] = mapped_column(
        String(2000), default="", server_default="", nullable=False,
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
