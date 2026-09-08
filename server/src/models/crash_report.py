"""T-M5-05b: Crash report storage.

Stores sanitized crash summaries uploaded by the Android client via
``POST /telemetry/crash``. The client must have redacted all PII before
upload (tokens, payloads, notes). See
``app/src/main/java/com/aozora/app/crashlytics/CrashReporter.kt`` for
the redaction rules.

Table ``crash_reports`` is append-only. There is no user-facing read
endpoint — the data is consumed only by Grafana / on-call engineers.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class CrashReport(Base):
    """Sanitized crash report received from a client.

    ``stacktrace`` is truncated to [CRASH_REPORT_MAX_STACKTRACE] chars
    by the service layer to bound row size.
    """

    __tablename__ = "crash_reports"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    # Client-generated stable id for dedup (hash of exception class + top frame).
    # Lets the same crash reported by multiple devices collapse to one row in
    # analytics without leaking PII.
    crash_signature: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
    )
    app_version: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True,
    )
    android_version: Mapped[str] = mapped_column(
        String(16), nullable=False,
    )
    device_model: Mapped[str] = mapped_column(
        String(64), nullable=False,
    )
    exception_class: Mapped[str] = mapped_column(
        String(128), nullable=False,
    )
    exception_message: Mapped[str] = mapped_column(
        Text, nullable=False,
    )
    stacktrace: Mapped[str] = mapped_column(
        Text, nullable=False,
    )
    reported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    # Optional: device id hash (full SHA-256 hex, 64 chars) so we can dedup
    # reports per device without storing the real device_id.
    # T-M5-05ab fix (S2-2): expanded from 16 to 64 chars for collision resistance.
    device_id_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )
