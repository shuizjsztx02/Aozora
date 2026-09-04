"""Integration-style tests for the main application."""

from __future__ import annotations


class TestRoot:
    def test_root_returns_message(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.json()["message"] == "Aozora API"


class TestOpenAPI:
    def test_openapi_json_available(self, client):
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        spec = resp.json()
        assert spec["info"]["title"] == "Aozora API"
        assert "openapi" in spec

    def test_swagger_ui_available(self, client):
        resp = client.get("/docs")
        assert resp.status_code == 200

    def test_redoc_available(self, client):
        resp = client.get("/redoc")
        assert resp.status_code == 200

    def test_health_in_openapi(self, client):
        # R-M2-01: in tests API_PREFIX is empty (see conftest.py) so
        # health endpoints register at their natural paths; production
        # would see them under /api/v1.
        spec = client.get("/openapi.json").json()
        assert "/health" in spec["paths"]
        assert "/health/ready" in spec["paths"]


class TestNotFound:
    def test_404_uses_error_format(self, client):
        resp = client.get("/nonexistent")
        assert resp.status_code == 404
        body = resp.json()
        assert "code" in body
        assert "message" in body
        assert "request_id" in body
