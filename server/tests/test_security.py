"""T-M5-02b: Security middleware tests — headers, body-size limit, docs gating."""

from __future__ import annotations

import os

import pytest
from starlette.testclient import TestClient

# Force non-production env so docs are available for the default test run.
os.environ.setdefault("AOZORA_ENV", "testing")


@pytest.fixture()
def client():
    """Return a TestClient for the current app.

    The import is deferred so env vars set in tests take effect.
    """
    from src.main import app
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

class TestSecurityHeaders:
    """Every response must carry the standard security headers."""

    def test_hsts(self, client):
        resp = client.get("/api/v1/health/live")
        assert resp.headers["Strict-Transport-Security"] == (
            "max-age=31536000; includeSubDomains"
        )

    def test_x_content_type_options(self, client):
        resp = client.get("/api/v1/health/live")
        assert resp.headers["X-Content-Type-Options"] == "nosniff"

    def test_x_frame_options(self, client):
        resp = client.get("/api/v1/health/live")
        assert resp.headers["X-Frame-Options"] == "DENY"

    def test_referrer_policy(self, client):
        resp = client.get("/api/v1/health/live")
        assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"

    def test_content_security_policy(self, client):
        resp = client.get("/api/v1/health/live")
        assert resp.headers["Content-Security-Policy"] == "default-src 'self'"

    def test_permissions_policy(self, client):
        resp = client.get("/api/v1/health/live")
        assert resp.headers["Permissions-Policy"] == (
            "geolocation=(), microphone=(), camera=()"
        )

    def test_all_headers_on_error_responses(self, client):
        """Security headers must be present even on 404 / error pages."""
        resp = client.get("/nonexistent")
        assert resp.status_code == 404
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"


# ---------------------------------------------------------------------------
# Request-body size limit
# ---------------------------------------------------------------------------

class TestRequestBodyLimit:
    """Oversized JSON bodies must be rejected with 413."""

    def test_content_length_exceeds_limit(self, client):
        """Explicit Content-Length over the cap → 413 without reading body."""
        body = b'{"data":"' + b"x" * (11 * 1024 * 1024) + b'"}'
        resp = client.post(
            "/api/v1/auth/verify",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 413
        data = resp.json()
        assert data["error"]["code"] == "payload_too_large"
        # R-T-M5-02-3: security headers must still attach on 413 responses
        # (SecurityHeadersMiddleware must be outermost of the body-limit check).
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert resp.headers["Strict-Transport-Security"] == (
            "max-age=31536000; includeSubDomains"
        )

    def test_normal_body_accepted(self, client):
        """A body well under the limit must pass through."""
        resp = client.post(
            "/api/v1/auth/verify",
            json={"code": "000000"},
        )
        # 200 or 401/422 depending on auth state — but NOT 413.
        assert resp.status_code != 413


# ---------------------------------------------------------------------------
# Docs gating
# ---------------------------------------------------------------------------

class TestDocsGating:
    """Swagger/ReDoc/OpenAPI must be available in non-production."""

    def test_docs_available_in_non_production(self, client):
        resp = client.get("/docs")
        assert resp.status_code == 200

    def test_openapi_json_available(self, client):
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
