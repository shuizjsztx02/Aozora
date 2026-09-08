"""T-M5-05c: Feedback storage.

Stores user-submitted feedback uploaded via ``POST /telemetry/feedback``.
Each row is one feedback item from one authenticated user. ``user_id_hash``
is the SHA-256 of the user id (stable dedup key for Grafana) — the real
user id FK is intentionally not kept so the table cannot be reverse-joined
to ``users``.

The table is append-only. There is no user-facing read endpoint — the
ops team queries the table directly for beta program triage.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class Feedback(Base):
    """One piece of feedback submitted by a beta user.

    ``content`` is truncated to ``FEEDBACK_MAX_CONTENT_CHARS`` by the
    service layer to bound row size.
    """

    __tablename__ = "feedbacks"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    # SHA-256(user_id)[:16] — stable dedup key for Grafana.
    user_id_hash: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # T-M5-05ab fix (N-2): full 64-char SHA-256 hex, consistent with crash_reports.device_id_hash
    device_id_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # One of: bug | suggestion | other — kept short by service layer.
    category: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Optional self-supplied contact (email / handle). Truncated by service.
    contact: Mapped[str | None] = mapped_column(String(128), nullable=True)
    app_version: Mapped[str] = mapped_column(String(32), nullable=False)
    android_version: Mapped[str] = mapped_column(String(16), nullable=False)
    device_model: Mapped[str] = mapped_column(String(64), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
