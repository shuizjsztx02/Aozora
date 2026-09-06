"""T-M2-02 + T-M4-04c: Attachment model.

T-M4-04c added ``user_id`` (FK users.id, NOT NULL) so every attachment has
an explicit owner — required for upload-time ownership checks before the
attachment is linked to a transaction. ``file_size`` records the byte
count of the stored object for storage accounting.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import AuditMixin, Base


class AttachmentStatus(str, enum.Enum):
    PENDING = "pending"
    PARSED = "parsed"
    EXPIRED = "expired"
    LINKED = "linked"


class Attachment(Base, AuditMixin):
    __tablename__ = "attachments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    # T-M4-04c: explicit owner. Required at upload time (before the
    # attachment is linked to any transaction) so ownership can be
    # enforced without joining through transaction → book → user.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="SET NULL"),
        nullable=True,
    )
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[AttachmentStatus] = mapped_column(
        String(20),
        default=AttachmentStatus.PENDING,
        server_default=AttachmentStatus.PENDING.value,
        nullable=False,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
