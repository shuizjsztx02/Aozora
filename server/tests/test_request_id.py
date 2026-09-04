"""Tests for request-ID middleware."""

from __future__ import annotations


class TestRequestId:
    def test_generated_when_absent(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert "x-request-id" in resp.headers
        rid = resp.headers["x-request-id"]
        assert len(rid) == 32  # UUID hex

    def test_client_supplied_preserved(self, client):
        rid = "client-request-id-42"
        resp = client.get("/health", headers={"X-Request-Id": rid})
        assert resp.headers["x-request-id"] == rid

    def test_unique_per_request(self, client):
        r1 = client.get("/health").headers["x-request-id"]
        r2 = client.get("/health").headers["x-request-id"]
        assert r1 != r2
