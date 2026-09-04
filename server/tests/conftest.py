"""Shared test fixtures for T-M2-01."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.common.config import Settings, reset_config, set_config
from src.common.database import reset_engine


@pytest.fixture(autouse=True)
def _test_config():
    """Provide an in-memory SQLite config for every test."""
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        secret_key="test-secret",
        env="test",
    )
    set_config(settings)
    yield
    reset_config()
    reset_engine()


@pytest.fixture()
def client():
    """Return a ``TestClient`` wired to the test config."""
    from src.main import app

    with TestClient(app) as c:
        yield c
