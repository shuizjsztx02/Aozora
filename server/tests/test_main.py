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


class TestSharedProxyPrefix:
    """A 机共享 Nginx 把 /aozora-api/ 原样透传，由入口剥离（见 main.py）。

    测试环境 API_PREFIX=""（conftest R-M2-01），/aozora-api/health 即生产
    /aozora-api/api/v1/health 的同构替身。
    """

    def test_prefixed_request_matches_unprefixed(self, client):
        resp = client.get("/aozora-api/health")
        assert resp.status_code == 200
        assert resp.json() == client.get("/health").json()

    def test_bare_prefix_serves_root(self, client):
        resp = client.get("/aozora-api")
        assert resp.status_code == 200
        assert resp.json()["message"] == "Aozora API"

    def test_prefixed_404_keeps_error_contract_and_security_headers(self, client):
        resp = client.get("/aozora-api/nonexistent")
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "http_error"
        assert "request_id" in body
        # 剥离发生在全部中间件之外：安全头/请求 ID 链路照常生效
        assert resp.headers["x-frame-options"] == "DENY"

    def test_unprefixed_requests_unaffected(self, client):
        assert client.get("/health").status_code == 200

    def test_prefix_boundary_only_matches_at_path_start(self, client):
        # "/aozora-apixxx" 不是带前缀请求，须按原路径 404（不能被剥成 "/xxx"）
        resp = client.get("/aozora-apixxx")
        assert resp.status_code == 404
