"""T-M5-05c: feedback endpoint tests.

Covers:
- Happy path: authenticated POST /telemetry/feedback stores a row in
  ``feedbacks`` and returns 201 with a UUID id.
- Validation: invalid ``category`` → 422; empty ``content`` → 422 (Pydantic).
- Rate limit: 6th feedback in one hour from the same user → 429.
- Auth gate: unauthenticated request → 401.
- Truncation: content longer than MAX_CONTENT_CHARS is capped server-side.
- Defense: ``user_id_hash`` stored is the SHA-256[:16] of the user id — the
  raw id is never written.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from src.common.database import get_db_session
from src.models.base import Base
from src.models.feedback import Feedback
from src.models.user import User, UserStatus
from src.modules.auth.dependencies import _rate_limits
from src.modules.auth.utils import create_access_token
from src.modules.telemetry import service as telemetry_service

# ── Fixtures ────────────────────────────────────────────────────────────

@pytest_asyncio.fixture()
async def client():
    """TestClient with all tables + a single active user."""
    from src.main import app

    _rate_limits.clear()
    telemetry_service._recent_feedback.clear()
    telemetry_service._feedback_last_cleanup = 0.0
    telemetry_service._recent_reports.clear()
    telemetry_service._last_cleanup = 0.0

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
    )
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def _override():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db_session] = _override

    with TestClient(app) as c:
        c.db_factory = factory
        yield c

    app.dependency_overrides.clear()
    _rate_limits.clear()
    telemetry_service._recent_feedback.clear()
    await engine.dispose()


@pytest.fixture()
def user(client):
    import asyncio

    async def _seed():
        async with client.db_factory() as session:
            u = User(id=uuid.uuid4(), nickname="beta-1", status=UserStatus.ACTIVE)
            session.add(u)
            await session.commit()
            return u

    return asyncio.run(_seed())


def _auth_header(user_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user_id))}"}


def _feedback_payload(**overrides) -> dict:
    base = {
        "category": "bug",
        "content": "记账按钮点击后无响应",
        "contact": "beta-1@example.com",
        "app_version": "0.1.0",
        "android_version": "14",
        "device_model": "Pixel 8",
    }
    base.update(overrides)
    return base


# ── Tests ───────────────────────────────────────────────────────────────

def test_feedback_happy_path_persists_row(client, user):
    resp = client.post(
        "/telemetry/feedback",
        json=_feedback_payload(),
        headers=_auth_header(user.id),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "id" in body
    assert body["received"] is True
    # UUID shape (36 chars with hyphens).
    uuid.UUID(body["id"])

    import asyncio

    async def _read():
        async with client.db_factory() as session:
            rows = (await session.execute(select(Feedback))).scalars().all()
            return rows

    rows = asyncio.run(_read())
    assert len(rows) == 1
    row = rows[0]
    assert row.category == "bug"
    assert row.content == "记账按钮点击后无响应"
    assert row.contact == "beta-1@example.com"
    assert row.app_version == "0.1.0"
    # user_id_hash is the SHA-256[:16] of the user id, NOT the raw id.
    expected_hash = hashlib.sha256(str(user.id).encode()).hexdigest()[:16]
    assert row.user_id_hash == expected_hash
    assert row.user_id_hash != str(user.id)


def test_feedback_invalid_category_rejected(client, user):
    resp = client.post(
        "/telemetry/feedback",
        json=_feedback_payload(category="rude"),
        headers=_auth_header(user.id),
    )
    assert resp.status_code == 422, resp.text
    assert "category" in resp.text


def test_feedback_empty_content_rejected_by_pydantic(client, user):
    resp = client.post(
        "/telemetry/feedback",
        json=_feedback_payload(content=""),
        headers=_auth_header(user.id),
    )
    assert resp.status_code == 422


def test_feedback_unauthenticated_rejected(client):
    resp = client.post(
        "/telemetry/feedback",
        json=_feedback_payload(),
    )
    assert resp.status_code in (401, 403)


def test_feedback_content_length_cap(client, user):
    # Exactly at the cap — accepted.
    at_cap = "x" * telemetry_service.MAX_CONTENT_CHARS
    resp = client.post(
        "/telemetry/feedback",
        json=_feedback_payload(content=at_cap),
        headers=_auth_header(user.id),
    )
    assert resp.status_code == 201, resp.text

    # Past the cap — Pydantic rejects before the route handler runs
    # (defense-in-depth; service-side truncation is the second layer).
    over_cap = "x" * (telemetry_service.MAX_CONTENT_CHARS + 1)
    resp = client.post(
        "/telemetry/feedback",
        json=_feedback_payload(content=over_cap),
        headers=_auth_header(user.id),
    )
    assert resp.status_code == 422


def test_feedback_rate_limit_kicks_in_after_five(client, user):
    headers = _auth_header(user.id)
    for i in range(5):
        resp = client.post(
            "/telemetry/feedback",
            json=_feedback_payload(content=f"feedback #{i}"),
            headers=headers,
        )
        assert resp.status_code == 201, f"iteration {i}: {resp.text}"

    # 6th submission within the same window should be rate-limited.
    resp = client.post(
        "/telemetry/feedback",
        json=_feedback_payload(content="one too many"),
        headers=headers,
    )
    assert resp.status_code == 429


def test_feedback_different_user_has_separate_quota(client):
    import asyncio

    async def _seed_two_users():
        async with client.db_factory() as session:
            u1 = User(id=uuid.uuid4(), nickname="beta-1", status=UserStatus.ACTIVE)
            u2 = User(id=uuid.uuid4(), nickname="beta-2", status=UserStatus.ACTIVE)
            session.add_all([u1, u2])
            await session.commit()
            return u1, u2

    u1, u2 = asyncio.run(_seed_two_users())

    # Exhaust u1's quota.
    for i in range(5):
        assert client.post(
            "/telemetry/feedback",
            json=_feedback_payload(content=f"u1 #{i}"),
            headers=_auth_header(u1.id),
        ).status_code == 201

    # u2 should still have its own full quota.
    resp = client.post(
        "/telemetry/feedback",
        json=_feedback_payload(content="u2 still works"),
        headers=_auth_header(u2.id),
    )
    assert resp.status_code == 201, resp.text


# ── Frozen account guards (T-M5-05ab) ────────────────────────────────────


def _crash_payload(**overrides) -> dict:
    base = {
        "crash_signature": "java.lang.RuntimeException: test",
        "app_version": "0.1.0",
        "android_version": "14",
        "device_model": "Pixel 8",
        "exception_class": "java.lang.RuntimeException",
        "exception_message": "test crash",
        "stacktrace": "at com.aozora.app.MainActivity.onCreate(MainActivity.kt)",
        "reported_at": datetime.now(UTC).isoformat(),
        "device_id_hash": hashlib.sha256("test-device".encode()).hexdigest(),
    }
    base.update(overrides)
    return base


def test_feedback_frozen_user_rejected(client):
    """Frozen user cannot submit feedback."""
    import asyncio

    async def _seed_frozen():
        async with client.db_factory() as session:
            u = User(id=uuid.uuid4(), nickname="frozen-user", status=UserStatus.FROZEN)
            session.add(u)
            await session.commit()
            return u

    user = asyncio.run(_seed_frozen())
    resp = client.post(
        "/telemetry/feedback",
        json=_feedback_payload(),
        headers=_auth_header(user.id),
    )
    assert resp.status_code == 403, resp.text
    assert "pending deletion" in resp.text.lower()


def test_crash_report_frozen_user_rejected(client):
    """Frozen user cannot submit crash reports."""
    import asyncio

    async def _seed_frozen():
        async with client.db_factory() as session:
            u = User(id=uuid.uuid4(), nickname="frozen-crash", status=UserStatus.FROZEN)
            session.add(u)
            await session.commit()
            return u

    user = asyncio.run(_seed_frozen())
    resp = client.post(
        "/telemetry/crash",
        json=_crash_payload(),
        headers=_auth_header(user.id),
    )
    assert resp.status_code == 403, resp.text
    assert "pending deletion" in resp.text.lower()


def test_heartbeat_frozen_user_rejected(client):
    """Frozen user cannot send heartbeat."""
    import asyncio

    async def _seed_frozen():
        async with client.db_factory() as session:
            u = User(id=uuid.uuid4(), nickname="frozen-hb", status=UserStatus.FROZEN)
            session.add(u)
            await session.commit()
            return u

    user = asyncio.run(_seed_frozen())
    resp = client.post(
        "/telemetry/heartbeat",
        json={"app_version": "0.1.0"},
        headers=_auth_header(user.id),
    )
    assert resp.status_code == 403, resp.text
    assert "pending deletion" in resp.text.lower()


def test_active_user_can_access_all_endpoints(client, user):
    """Active user can still use all three telemetry endpoints."""
    headers = _auth_header(user.id)

    # Feedback
    assert client.post("/telemetry/feedback", json=_feedback_payload(), headers=headers).status_code == 201

    # Crash report
    assert client.post("/telemetry/crash", json=_crash_payload(), headers=headers).status_code == 201

    # Heartbeat
    assert client.post("/telemetry/heartbeat", json={"app_version": "0.1.0"}, headers=headers).status_code == 200


# ── S2-3: /telemetry/crash + /telemetry/heartbeat integration ───────────


def test_crash_report_persists_row(client, user):
    """T-M5-05b S2-3: happy path stores a crash_reports row with device hash."""
    from src.models.crash_report import CrashReport

    headers = _auth_header(user.id)
    resp = client.post("/telemetry/crash", json=_crash_payload(), headers=headers)
    assert resp.status_code == 201, resp.text
    report_id = resp.json()["id"]

    import asyncio

    async def _read():
        async with client.db_factory() as session:
            return await session.get(CrashReport, uuid.UUID(report_id))

    row = asyncio.run(_read())
    assert row is not None
    assert row.app_version == "0.1.0"
    assert row.device_id_hash == hashlib.sha256("test-device".encode()).hexdigest()


def test_crash_report_rate_limit_boundary(client, user):
    """T-M5-05b S2-3: the 21st report from one device within an hour → 429.

    The per-IP auth limiter (10/min) is cleared between calls so only the
    per-device quota (20/h) is exercised.
    """
    from src.modules.auth.dependencies import _rate_limits as ip_limits

    headers = _auth_header(user.id)
    for i in range(telemetry_service.MAX_REPORTS_PER_DEVICE_PER_HOUR):
        ip_limits.clear()
        resp = client.post("/telemetry/crash", json=_crash_payload(), headers=headers)
        assert resp.status_code == 201, f"report {i + 1} failed: {resp.text}"

    ip_limits.clear()
    resp = client.post("/telemetry/crash", json=_crash_payload(), headers=headers)
    assert resp.status_code == 429, resp.text
    assert "rate limit" in resp.text.lower()


def test_crash_report_different_device_separate_quota(client, user):
    """Device quota is per device_id_hash, not global."""
    from src.modules.auth.dependencies import _rate_limits as ip_limits

    headers = _auth_header(user.id)
    # Exhaust the quota for device A.
    for _ in range(telemetry_service.MAX_REPORTS_PER_DEVICE_PER_HOUR):
        ip_limits.clear()
        assert client.post("/telemetry/crash", json=_crash_payload(), headers=headers).status_code == 201
    # Device B (different hash) still within budget.
    ip_limits.clear()
    other = _crash_payload(
        device_id_hash=hashlib.sha256("other-device".encode()).hexdigest()
    )
    assert client.post("/telemetry/crash", json=other, headers=headers).status_code == 201


def test_heartbeat_records_device_hash(client, user, monkeypatch):
    """T-M5-05b S2-3: X-Device-Id is SHA-256-hashed server-side and recorded."""
    from src.common import telemetry as telemetry_mod

    calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        telemetry_mod,
        "record_active_device",
        lambda app_version, device_id_hash=None: calls.append((app_version, device_id_hash)),
    )

    resp = client.post(
        "/telemetry/heartbeat",
        json={"app_version": "0.2.0"},
        headers={**_auth_header(user.id), "X-Device-Id": "raw-device-uuid"},
    )
    assert resp.status_code == 200
    assert resp.json()["ack"] is True
    assert len(calls) == 1
    version, hashed = calls[0]
    assert version == "0.2.0"
    # Full 64-char hex (S2-2 fix), never the raw device id.
    assert hashed == hashlib.sha256("raw-device-uuid".encode()).hexdigest()
    assert len(hashed) == 64


def test_heartbeat_without_device_header_records_none(client, user, monkeypatch):
    """Missing X-Device-Id → device_id_hash None (anonymous device)."""
    from src.common import telemetry as telemetry_mod

    calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        telemetry_mod,
        "record_active_device",
        lambda app_version, device_id_hash=None: calls.append((app_version, device_id_hash)),
    )

    resp = client.post(
        "/telemetry/heartbeat",
        json={"app_version": "0.2.0"},
        headers=_auth_header(user.id),
    )
    assert resp.status_code == 200
    assert calls == [("0.2.0", None)]
