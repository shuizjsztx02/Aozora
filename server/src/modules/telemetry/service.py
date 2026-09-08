"""T-M5-05b + T-M5-05c: Telemetry service — crash reports + feedback.

Storage strategy:
- Crash reports are written directly to ``crash_reports``. There is no
  per-user ownership because reports are device-scoped and arrive
  already redacted of PII — the authenticated user is only used for
  rate limiting (not stored).
- Feedback is written to ``feedbacks``. The submitting user is hashed
  (``user_id_hash``) so the table can be deduped in Grafana without
  exposing the real user id.

Defenses:
- ``MAX_STACKTRACE_CHARS`` caps crash row size (16 KB). Longer
  stacktraces are truncated with a marker suffix so they remain
  symbolizable.
- ``MAX_REPORTS_PER_DEVICE_PER_HOUR`` bounds write amplification from a
  single misbehaving device (crash loop protection).
- ``MAX_FEEDBACK_PER_USER_PER_HOUR`` bounds feedback spam from a single
  authenticated user; per-IP rate limit is enforced at the route layer.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.crash_report import CrashReport
from src.models.feedback import Feedback
from src.modules.telemetry.schemas import CrashReportRequest, FeedbackRequest

logger = logging.getLogger(__name__)

MAX_STACKTRACE_CHARS = 16_000
MAX_MESSAGE_CHARS = 2_000
MAX_CONTENT_CHARS = 4_000
MAX_CONTACT_CHARS = 128
TRUNCATION_MARKER = "\n…[truncated by server]"

ALLOWED_FEEDBACK_CATEGORIES = frozenset({"bug", "suggestion", "other"})

# In-memory rate limiter: {device_id_hash: [timestamps]}
# Bounded by _MAX_DEVICES; stale entries are evicted periodically.
_recent_reports: dict[str, list[float]] = defaultdict(list)
_last_cleanup: float = 0.0
_MAX_DEVICES = 5_000
MAX_REPORTS_PER_DEVICE_PER_HOUR = 20
RATE_WINDOW_SECONDS = 3600

# Feedback rate limiter: {user_id_hash: [timestamps]}
_recent_feedback: dict[str, list[float]] = defaultdict(list)
_feedback_last_cleanup: float = 0.0
_FEEDBACK_MAX_USERS = 5_000
MAX_FEEDBACK_PER_USER_PER_HOUR = 5


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


def _check_feedback_rate_limit(user_id_hash: str) -> bool:
    """Return True if the user is within the hourly feedback quota.

    Beta program has 20–50 users, but the limiter must remain correct if
    the program scales. Structure mirrors ``_check_rate_limit`` for
    crash reports.
    """
    now = datetime.now(UTC).timestamp()
    global _feedback_last_cleanup

    if now - _feedback_last_cleanup > RATE_WINDOW_SECONDS:
        cutoff = now - RATE_WINDOW_SECONDS
        for key in list(_recent_feedback):
            _recent_feedback[key] = [t for t in _recent_feedback[key] if t > cutoff]
            if not _recent_feedback[key]:
                del _recent_feedback[key]
        _feedback_last_cleanup = now

    if len(_recent_feedback) >= _FEEDBACK_MAX_USERS and user_id_hash not in _recent_feedback:
        oldest = min(_recent_feedback, key=lambda k: _recent_feedback[k][0])
        del _recent_feedback[oldest]

    recent = _recent_feedback[user_id_hash]
    cutoff = now - RATE_WINDOW_SECONDS
    _recent_feedback[user_id_hash] = [t for t in recent if t > cutoff]
    if len(_recent_feedback[user_id_hash]) >= MAX_FEEDBACK_PER_USER_PER_HOUR:
        return False
    _recent_feedback[user_id_hash].append(now)
    return True


class TelemetryService:
    # T-M5-05ab fix (S2-6): heartbeat is DB-free — accept db=None explicitly
    # so type checkers stop flagging the routes.py call site, and DB-requiring
    # methods guard on self.db to surface misuse early instead of AttributeError.
    def __init__(self, db: AsyncSession | None) -> None:
        self.db = db

    async def record_crash(self, body: CrashReportRequest) -> CrashReport:
        """Persist a sanitized crash report.

        Re-applies server-side truncation in case a client bypassed
        client-side redaction. Updates the ``crash_reports_total``
        counter for Grafana.
        """
        if self.db is None:
            raise RuntimeError("record_crash requires a DB session")
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
            device_id_hash=body.device_id_hash[:64] if body.device_id_hash else None,
        )
        self.db.add(report)
        await self.db.commit()
        await self.db.refresh(report)
        return report

    async def heartbeat(self, app_version: str, device_id_hash: str | None) -> None:
        """Record a device heartbeat → active_devices_total gauge.

        T-M5-05ab fix (S1-4): the metric is now an ObservableGauge driven by
        per-device last-seen timestamps. We record the hash so unique-device
        count over the last 24h can be computed on scrape.
        """
        from src.common.telemetry import record_active_device
        record_active_device(app_version[:32], device_id_hash=device_id_hash)

    async def submit_feedback(
        self,
        body: FeedbackRequest,
        user_id_hash: str,
    ) -> Feedback:
        """Persist sanitized feedback from a beta user.

        ``user_id_hash`` is the SHA-256 of the user id (caller's
        responsibility to hash); the raw user id is never stored so the
        feedback table cannot be reverse-joined to ``users``.
        """
        if self.db is None:
            raise RuntimeError("submit_feedback requires a DB session")
        from src.common.telemetry import record_feedback
        record_feedback()

        if body.category not in ALLOWED_FEEDBACK_CATEGORIES:
            raise ValueError(
                f"category must be one of {sorted(ALLOWED_FEEDBACK_CATEGORIES)}",
            )

        feedback = Feedback(
            id=uuid.uuid4(),
            user_id_hash=user_id_hash[:16],
            device_id_hash=body.device_id_hash if body.device_id_hash else None,
            category=body.category,
            content=_truncate(body.content, MAX_CONTENT_CHARS),
            contact=(
                _truncate(body.contact, MAX_CONTACT_CHARS)
                if body.contact else None
            ),
            app_version=body.app_version[:32],
            android_version=body.android_version[:16],
            device_model=body.device_model[:64],
            submitted_at=datetime.now(UTC),
        )
        self.db.add(feedback)
        await self.db.commit()
        await self.db.refresh(feedback)
        return feedback
