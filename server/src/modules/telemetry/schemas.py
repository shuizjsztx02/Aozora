"""T-M5-05b: Pydantic schemas for telemetry endpoints.

All text fields are sanitized client-side; the server still re-applies
length caps as a defense-in-depth measure (see service.py).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field


# ── POST /telemetry/crash ───────────────────────────────────────────────

class CrashReportRequest(BaseModel):
    """Crash report body from Android client.

    ``stacktrace`` and ``exception_message`` are expected to already be
    redacted of PII. Server-side cap applies regardless.
    """

    crash_signature: Annotated[str, Field(
        min_length=8, max_length=64,
        description="Stable hash of exception class + top stack frame",
    )]
    app_version: Annotated[str, Field(max_length=32)]
    android_version: Annotated[str, Field(max_length=16)]
    device_model: Annotated[str, Field(max_length=64)]
    exception_class: Annotated[str, Field(max_length=128)]
    exception_message: Annotated[str, Field(max_length=2000)]
    stacktrace: Annotated[str, Field(max_length=16_000)]
    reported_at: datetime
    device_id_hash: Annotated[str | None, Field(
        default=None, max_length=16,
        description="SHA-256(device_id)[:16] — optional device dedup key",
    )]


class CrashReportResponse(BaseModel):
    id: str
    received: bool = True


# ── POST /telemetry/heartbeat ───────────────────────────────────────────

class HeartbeatRequest(BaseModel):
    """Periodic heartbeat — used to populate active_devices_total."""

    app_version: Annotated[str, Field(max_length=32)]


class HeartbeatResponse(BaseModel):
    ack: bool = True
