"""Tests for health and readiness endpoints."""

from __future__ import annotations

from src.common.config import Settings, set_config


class TestLiveness:
    def test_returns_200_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestReadiness:
    def test_ready_with_sqlite(self, client):
        """SQLite in-memory is always reachable → 200."""
        resp = client.get("/health/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert data["checks"]["database"] == "ok"

    def test_unhealthy_when_db_unreachable(self, client):
        """Point at a non-existent PostgreSQL → 503."""
        set_config(
            Settings(
                database_url="postgresql+asyncpg://bad:bad@127.0.0.1:1/none",
                secret_key="test",
                env="test",
                api_prefix="",
            )
        )
        # Reset the engine so it picks up the new config
        from src.common.database import reset_engine

        reset_engine()

        resp = client.get("/health/ready")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "unhealthy"
        assert data["checks"]["database"] == "unreachable"
