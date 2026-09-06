"""Tests for error handlers."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.common.config import Settings, set_config
from src.common.errors import AppException, register_exception_handlers


@pytest.fixture()
def error_client():
    """A minimal app with only exception handlers registered."""
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/http-err")
    async def http_err():
        raise StarletteHTTPException(status_code=404, detail="not found")

    @app.get("/app-err")
    async def app_err():
        raise AppException(
            status_code=409,
            code="conflict",
            message="version mismatch",
        )

    @app.get("/unhandled")
    async def unhandled():
        raise RuntimeError("unexpected")

    @app.get("/validation")
    async def validation(q: int):
        return {"q": q}

    set_config(Settings(database_url="sqlite+aiosqlite:///test.db"))
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestExceptionHandlers:
    def test_http_exception(self, error_client):
        resp = error_client.get("/http-err")
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "http_error"
        assert body["message"] == "not found"

    def test_app_exception(self, error_client):
        resp = error_client.get("/app-err")
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "conflict"
        assert body["message"] == "version mismatch"

    def test_unhandled_exception(self, error_client):
        resp = error_client.get("/unhandled")
        assert resp.status_code == 500
        body = resp.json()
        assert body["code"] == "internal_error"

    def test_validation_error(self, error_client):
        resp = error_client.get("/validation?q=abc")
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "validation_error"
        assert "errors" in body
