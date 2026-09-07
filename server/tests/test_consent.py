"""T-M5-04a: Consent module tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from src.modules.consent.service import ConsentService
from tests.fixtures_db import db_session  # noqa: F401

# ── Service layer ──────────────────────────────────────────────────────

class TestConsentService:
    @pytest.mark.asyncio
    async def test_record_consent(self, db_session):
        service = ConsentService(db_session)
        now = datetime.now(UTC)
        consent = await service.record_consent(
            device_id="device-123",
            policy_version="v1",
            consented_at=now,
        )
        assert consent.id is not None
        assert consent.device_id == "device-123"
        assert consent.policy_version == "v1"

    @pytest.mark.asyncio
    async def test_get_latest_consent(self, db_session):
        service = ConsentService(db_session)
        t1 = datetime(2026, 1, 1, tzinfo=UTC)
        t2 = datetime(2026, 6, 1, tzinfo=UTC)
        await service.record_consent("device-abc", "v1", t1)
        await service.record_consent("device-abc", "v2", t2)

        latest = await service.get_latest_consent("device-abc")
        assert latest is not None
        assert latest.policy_version == "v2"

    @pytest.mark.asyncio
    async def test_get_latest_consent_none(self, db_session):
        service = ConsentService(db_session)
        result = await service.get_latest_consent("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_consent_per_device(self, db_session):
        """Consent records are per-device, not per-user."""
        service = ConsentService(db_session)
        now = datetime.now(UTC)
        await service.record_consent("device-A", "v1", now)
        await service.record_consent("device-B", "v1", now)

        a = await service.get_latest_consent("device-A")
        b = await service.get_latest_consent("device-B")
        assert a is not None
        assert b is not None
        assert a.id != b.id


# ── API layer ──────────────────────────────────────────────────────────

@pytest.fixture()
def consent_client(db_session):
    """Create a test client with consent routes mounted."""
    from fastapi import FastAPI

    from src.common.database import get_db_session
    from src.modules.consent.routes import router as consent_router

    app = FastAPI()
    app.include_router(consent_router, prefix="/api/v1")

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db_session] = override_db
    return TestClient(app)


class TestConsentAPI:
    def test_record_consent(self, consent_client):
        resp = consent_client.post("/api/v1/consent", json={
            "device_id": "test-device",
            "policy_version": "v1",
            "consented_at": "2026-09-07T10:00:00Z",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["device_id"] == "test-device"
        assert data["policy_version"] == "v1"

    def test_consent_status_no_consent(self, consent_client):
        resp = consent_client.get("/api/v1/consent/status", params={
            "device_id": "unknown-device",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_consented"] is False

    def test_consent_status_after_record(self, consent_client):
        # Record consent
        consent_client.post("/api/v1/consent", json={
            "device_id": "status-device",
            "policy_version": "v1",
            "consented_at": "2026-09-07T10:00:00Z",
        })
        # Check status
        resp = consent_client.get("/api/v1/consent/status", params={
            "device_id": "status-device",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_consented"] is True
        assert data["policy_version"] == "v1"

    def test_record_consent_missing_device_id(self, consent_client):
        resp = consent_client.post("/api/v1/consent", json={
            "policy_version": "v1",
            "consented_at": "2026-09-07T10:00:00Z",
        })
        assert resp.status_code == 422

    def test_record_consent_no_auth_required(self, consent_client):
        """Consent recording must not require authentication."""
        resp = consent_client.post("/api/v1/consent", json={
            "device_id": "no-auth-device",
            "policy_version": "v1",
            "consented_at": "2026-09-07T10:00:00Z",
        })
        # Should succeed without any Authorization header
        assert resp.status_code == 201
