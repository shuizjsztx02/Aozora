"""T-M5-04a: UserConsent model — records privacy policy agreement."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class UserConsent(Base):
    """Privacy policy consent record.

    Stored per-device so consent can be recorded before the user has
    authenticated (guest registration happens after consent).  The
    ``device_id`` links the consent to the device session; once the user
    authenticates, the server can associate the consent with the user.

    A user may have multiple consent rows (one per device, or one per
    policy version upgrade).
    """

    __tablename__ = "user_consents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    device_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    consented_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
