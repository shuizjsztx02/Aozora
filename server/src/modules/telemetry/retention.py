"""T-M5-05b follow-up: nightly D1/D7 retention computation.

Closes the last gap in the business funnel: ``set_d1_retention`` /
``set_d7_retention`` in ``src.common.telemetry`` previously had no writer,
so the Grafana D1/D7 panels stayed empty.

Definitions (all UTC, evaluated against the current calendar day "today"):
- **D1 retained**: user registered on *yesterday* (today − 1) with at least
  one activity row written *today*.
- **D7 retained**: user registered on *today − 7* with activity *today*.
- **Activity today**: any of —
  (a) a ``sync_changes`` row (client push touched the server),
  (b) a ``transactions`` row on a book owned by the user (direct API write),
  (c) a ``device_sessions`` row (token rotation implies the app opened).

The job runs inside the API process because the OTel instruments are
in-process; a standalone cron script cannot write them. Results are
published as Gauges (overwritten, not incremented), so a same-day restart
re-computes the same value — the metric is restart-idempotent.

Single-instance assumption (same caveat as the in-memory auth rate
limiter): with multiple API workers each instance reports its own view;
Prometheus keeps the last-scraped series per instance. Staging runs one
worker; revisit before a multi-instance production rollout.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, union
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.common.telemetry import set_d1_retention, set_d7_retention
from src.models.ledger import Book, Transaction
from src.models.sync import SyncChange
from src.models.user import DeviceSession, User

logger = logging.getLogger(__name__)

# How often the loop checks whether the daily run is due.
CHECK_INTERVAL_SECONDS = 3600

def _activity_days():
    # Resolved at call time so tests can monkeypatch the setters.
    return (("d1", 1, set_d1_retention), ("d7", 7, set_d7_retention))


async def compute_retention(
    db: AsyncSession, now: datetime | None = None
) -> dict[str, dict[str, float]]:
    """Compute and record D1/D7 retention for ``now``'s UTC day.

    Returns ``{label: {"retained": n, "total": m, "rate_pct": p}}`` for
    observability in logs/tests. Cohorts with zero signups are recorded as
    rate 0.0 (the dashboard divides by its own window; a 0-row cohort must
    not keep yesterday's value alive).
    """
    if now is None:
        now = datetime.now(UTC)
    today_start = datetime.combine(now.date(), datetime.min.time(), tzinfo=UTC)
    today_end = today_start + timedelta(days=1)

    results: dict[str, dict[str, float]] = {}
    for label, days_ago, setter in _activity_days():
        cohort_start = today_start - timedelta(days=days_ago)
        cohort_end = cohort_start + timedelta(days=1)

        cohort_filter = (User.created_at >= cohort_start, User.created_at < cohort_end)

        total = (
            await db.execute(
                select(func.count()).select_from(User).where(*cohort_filter)
            )
        ).scalar_one()

        active_ids = (
            union(
                select(SyncChange.user_id.label("uid")).where(
                    SyncChange.changed_at >= today_start,
                    SyncChange.changed_at < today_end,
                ),
                select(Book.owner_id.label("uid"))
                .join(Transaction, Transaction.book_id == Book.id)
                .where(
                    Transaction.created_at >= today_start,
                    Transaction.created_at < today_end,
                ),
                select(DeviceSession.user_id.label("uid")).where(
                    DeviceSession.created_at >= today_start,
                    DeviceSession.created_at < today_end,
                ),
            )
            .subquery()
        )

        retained = (
            await db.execute(
                select(func.count())
                .select_from(User)
                .where(*cohort_filter, User.id.in_(select(active_ids.c.uid)))
            )
        ).scalar_one()

        rate_pct = round(100.0 * retained / total, 2) if total else 0.0
        setter(retained, rate_pct)
        results[label] = {"retained": retained, "total": total, "rate_pct": rate_pct}

    logger.info("Retention computed: %s", results)
    return results


async def retention_loop(
    factory: async_sessionmaker[AsyncSession],
    check_interval_seconds: int = CHECK_INTERVAL_SECONDS,
) -> None:
    """Daily driver: refresh the retention gauges once per UTC calendar day.

    Runs one check immediately after startup, then every
    ``check_interval_seconds``. The ``last_run`` marker is in-process only —
    safe because publishing is an idempotent gauge ``set``.
    """
    last_run = None
    while True:
        today = datetime.now(UTC).date()
        if last_run != today:
            try:
                async with factory() as session:
                    await compute_retention(session)
                last_run = today
            except asyncio.CancelledError:
                raise
            except Exception:
                # Never kill the loop over one failed day; retry next tick.
                logger.exception("Retention computation failed")
        await asyncio.sleep(check_interval_seconds)
