"""T-M5-05b follow-up: nightly D1/D7 retention job tests.

Covers ``src.modules.telemetry.retention.compute_retention``:
- Cohort windows (UTC calendar days) — signup yesterday → D1, signup 7 days
  ago → D7; same-day signups are in neither cohort.
- Activity signals: sync push (``sync_changes``), direct transaction write
  (``transactions`` via ``books.owner_id``), token rotation
  (``device_sessions``) — each counted only for *today*.
- Rate math and empty cohorts (record 0 / 0.0, no stale value kept).
- Setter wiring (set_d1_retention / set_d7_retention called with the
  computed numbers).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.models.ledger import Book, Category, Transaction, TxType
from src.models.sync import SyncChange, SyncOperation
from src.models.user import DeviceSession, User
from src.modules.telemetry import retention as retention_mod

# Fixed clock: "today" = 2026-09-07 (UTC). D1 cohort day = 09-06,
# D7 cohort day = 08-31.
NOW = datetime(2026, 9, 7, 3, 0, tzinfo=UTC)


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    from src.models.base import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _user(created_at: datetime) -> User:
    return User(id=uuid.uuid4(), nickname=f"u-{created_at:%Y%m%d%H%M}", created_at=created_at)


def _days_ago(day: date, hour: int = 10) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=UTC)


YESTERDAY = date(2026, 9, 6)          # D1 cohort signup day
D7_DAY = date(2026, 8, 31)            # D7 cohort signup day
TODAY = date(2026, 9, 7)              # activity day


async def _seed_sync_activity(s, user: User, at: datetime):
    s.add(
        SyncChange(
            user_id=user.id,
            entity_type="transaction",
            entity_id=uuid.uuid4(),
            operation=SyncOperation.INSERT,
            version=1,
            changed_at=at,
        )
    )


async def _seed_transaction_activity(s, user: User, at: datetime):
    book = Book(id=uuid.uuid4(), owner_id=user.id, name="main", created_at=at)
    cat = Category(
        id=uuid.uuid4(), owner_id=user.id, book_id=book.id, kind="expense", name="food",
        created_at=at,
    )
    s.add_all([book, cat])
    await s.flush()
    s.add(
        Transaction(
            id=uuid.uuid4(),
            book_id=book.id,
            type=TxType.EXPENSE,
            amount_cents=1000,
            category_id=cat.id,
            biz_date=at.date(),
            occurred_at=at,
            created_at=at,
        )
    )


async def _seed_session_activity(s, user: User, at: datetime):
    s.add(
        DeviceSession(
            id=uuid.uuid4(),
            user_id=user.id,
            device_id="dev-1",
            refresh_token_hash="hash",
            expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            created_at=at,
        )
    )


@pytest.fixture()
def setters(monkeypatch):
    """Capture set_d1_retention / set_d7_retention calls."""
    calls: dict[str, list] = {"d1": [], "d7": []}
    monkeypatch.setattr(
        retention_mod, "set_d1_retention",
        lambda count, rate: calls["d1"].append((count, rate)),
    )
    monkeypatch.setattr(
        retention_mod, "set_d7_retention",
        lambda count, rate: calls["d7"].append((count, rate)),
    )
    return calls


async def test_d1_cohort_retention_via_sync_push(session, setters):
    u_active = _user(_days_ago(YESTERDAY, 10))
    u_idle = _user(_days_ago(YESTERDAY, 12))
    session.add_all([u_active, u_idle])
    # Activity today → retained; activity yesterday (cohort day) → not.
    await _seed_sync_activity(session, u_active, _days_ago(TODAY, 1))
    await _seed_sync_activity(session, u_idle, _days_ago(YESTERDAY, 23))
    await session.commit()

    result = await retention_mod.compute_retention(session, now=NOW)

    assert result["d1"] == {"retained": 1, "total": 2, "rate_pct": 50.0}
    assert setters["d1"] == [(1, 50.0)]


async def test_d7_cohort_retention_via_transaction_write(session, setters):
    u = _user(_days_ago(D7_DAY, 9))
    session.add(u)
    await _seed_transaction_activity(session, u, _days_ago(TODAY, 0))
    await session.commit()

    result = await retention_mod.compute_retention(session, now=NOW)

    assert result["d7"] == {"retained": 1, "total": 1, "rate_pct": 100.0}
    assert setters["d7"] == [(1, 100.0)]


async def test_d1_retention_via_session_rotation(session, setters):
    u = _user(_days_ago(YESTERDAY, 8))
    session.add(u)
    await _seed_session_activity(session, u, _days_ago(TODAY, 2))
    await session.commit()

    result = await retention_mod.compute_retention(session, now=NOW)

    assert result["d1"]["retained"] == 1


async def test_same_day_signup_not_in_cohorts(session, setters):
    """Users who signed up today belong to no evaluated cohort."""
    u = _user(_days_ago(TODAY, 1))
    session.add(u)
    await _seed_sync_activity(session, u, _days_ago(TODAY, 2))
    await session.commit()

    result = await retention_mod.compute_retention(session, now=NOW)

    assert result["d1"]["total"] == 0
    assert result["d7"]["total"] == 0


async def test_boundary_midnight(session, setters):
    """created_at exactly at the cohort window end is excluded (half-open)."""
    u_in = _user(datetime(2026, 9, 6, 0, 0, tzinfo=UTC))     # cohort start inclusive
    u_out = _user(datetime(2026, 9, 7, 0, 0, tzinfo=UTC))    # next day, excluded
    session.add_all([u_in, u_out])
    await _seed_sync_activity(session, u_in, _days_ago(TODAY, 5))
    await _seed_sync_activity(session, u_out, _days_ago(TODAY, 5))
    await session.commit()

    result = await retention_mod.compute_retention(session, now=NOW)

    assert result["d1"] == {"retained": 1, "total": 1, "rate_pct": 100.0}


async def test_empty_db_records_zero(session, setters):
    result = await retention_mod.compute_retention(session, now=NOW)

    assert result["d1"] == {"retained": 0, "total": 0, "rate_pct": 0.0}
    assert result["d7"] == {"retained": 0, "total": 0, "rate_pct": 0.0}
    # Setters still called with 0 so the gauge never keeps a stale value.
    assert setters["d1"] == [(0, 0.0)]
    assert setters["d7"] == [(0, 0.0)]


async def test_gauge_setters_are_noop_without_telemetry(session, monkeypatch):
    """set_d1_retention / set_d7_retention must not raise when _api_metrics
    is None (telemetry not initialized)."""
    from src.common import telemetry as telemetry_mod

    # Other suites may have run setup_telemetry via TestClient lifespan;
    # force the uninitialized state for this test.
    monkeypatch.setattr(telemetry_mod, "_api_metrics", None)
    telemetry_mod.set_d1_retention(3, 50.0)    # no-op, no exception
    telemetry_mod.set_d7_retention(1, 100.0)
