"""T-M5-05b: Telemetry service — crash report ingestion.

Storage strategy:
- Crash reports are written directly to ``crash_reports``. There is no
  per-user ownership because reports are device-scoped and arrive
  already redacted of PII — the authenticated user is only used for
  rate limiting (not stored).

Defenses:
- ``MAX_STACKTRACE_CHARS`` caps row size (16 KB). Longer stacktraces
  are truncated with a marker suffix so they remain symbolizable.
- ``MAX_REPORTS_PER_DEVICE_PER_HOUR`` bounds write amplification from a
  single misbehaving device (crash loop protection).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.crash_report import CrashReport
from src.modules.telemetry.schemas import CrashReportRequest

logger = logging.getLogger(__name__)

MAX_STACKTRACE_CHARS = 16_000
MAX_MESSAGE_CHARS = 2_000
TRUNCATION_MARKER = "\n…[truncated by server]"

# In-memory rate limiter: {device_id_hash: [timestamps]}
# Bounded by _MAX_DEVICES; stale entries are evicted periodically.
_recent_reports: dict[str, list[float]] = defaultdict(list)
_last_cleanup: float = 0.0
_MAX_DEVICES = 5_000
MAX_REPORTS_PER_DEVICE_PER_HOUR = 20
RATE_WINDOW_SECONDS = 3600


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER


def _check_rate_limit(device_id_hash: str | None) -> bool:
    """Return True if the device is within the hourly quota.

    No device hash = anonymous device; rate limit by IP is enforced at
    the middleware layer, so we let it through here.
    """
    if device_id_hash is None:
        return True

    now = datetime.now(UTC).timestamp()
    global _last_cleanup

    # Periodic eviction (same bounded strategy as auth.dependencies).
    if now - _last_cleanup > RATE_WINDOW_SECONDS:
        cutoff = now - RATE_WINDOW_SECONDS
        for key in list(_recent_reports):
            _recent_reports[key] = [t for t in _recent_reports[key] if t > cutoff]
            if not _recent_reports[key]:
                del _recent_reports[key]
        _last_cleanup = now

    if len(_recent_reports) >= _MAX_DEVICES and device_id_hash not in _recent_reports:
        # Evict oldest device to bound memory.
        oldest = min(_recent_reports, key=lambda k: _recent_reports[k][0])
        del _recent_reports[oldest]

    recent = _recent_reports[device_id_hash]
    cutoff = now - RATE_WINDOW_SECONDS
    _recent_reports[device_id_hash] = [t for t in recent if t > cutoff]
    if len(_recent_reports[device_id_hash]) >= MAX_REPORTS_PER_DEVICE_PER_HOUR:
        return False
    _recent_reports[device_id_hash].append(now)
    return True


class TelemetryService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def record_crash(self, body: CrashReportRequest) -> CrashReport:
        """Persist a sanitized crash report.

        Re-applies server-side truncation in case a client bypassed
        client-side redaction. Updates the ``crash_reports_total``
        counter for Grafana.
        """
        from src.common.telemetry import record_crash_report
        record_crash_report(body.app_version)

        report = CrashReport(
            crash_signature=body.crash_signature[:64],
            app_version=body.app_version[:32],
            android_version=body.android_version[:16],
            device_model=body.device_model[:64],
            exception_class=body.exception_class[:128],
            exception_message=_truncate(body.exception_message, MAX_MESSAGE_CHARS),
            stacktrace=_truncate(body.stacktrace, MAX_STACKTRACE_CHARS),
            reported_at=body.reported_at,
            device_id_hash=body.device_id_hash[:16] if body.device_id_hash else None,
        )
        self.db.add(report)
        await self.db.commit()
        await self.db.refresh(report)
        return report

    async def heartbeat(self, app_version: str, device_id_hash: str | None) -> None:
        """Record a device heartbeat → active_devices_total metric.

        No storage — the metric alone is sufficient. We do not persist
        heartbeat rows to avoid growing an unbounded table.
        """
        from src.common.telemetry import record_active_device
        record_active_device(app_version[:32])

    async def count_pending_for_device(self, device_id_hash: str) -> int:
        """Number of crash reports from this device in the last 24h.

        Used by the client to decide whether to upload (dedup guard).
        """
        cutoff = datetime.now(UTC).timestamp() - 86400
        stmt = (
            select(func.count())
            .select_from(CrashReport)
            .where(CrashReport.device_id_hash == device_id_hash)
            .where(CrashReport.reported_at >= datetime.fromtimestamp(cutoff, UTC))
        )
        result = await self.db.execute(stmt)
        return result.scalar_one()
