"""Shared test fixtures for T-M2-01."""

from __future__ import annotations

import os
import warnings

# R-M4-38: starlette ≥1.6.0 emits StarletteDeprecationWarning at TestClient
# import time (httpx → httpx2 migration). Filter before the import to ensure
# deterministic ordering on CI (ubuntu-latest exits with code 4 otherwise).
warnings.filterwarnings(
    "ignore",
    message=r".*httpx.*",
    category=DeprecationWarning,
)
warnings.filterwarnings(
    "ignore",
    category=DeprecationWarning,
    module=r"starlette\..*",
)

import pytest
from starlette.testclient import TestClient

from src.common.config import Settings, reset_config, set_config
from src.common.database import reset_engine

# R-M2-01: set API_PREFIX before ``src.main`` is imported so routers
# register at natural paths (``/health``, ``/auth/guest``, …). Without
# this, main.py's module-level ``cfg = get_config()`` would bake in the
# production default ``/api/v1`` and all tests would have to use that
# prefix. Tests that specifically verify the default can monkeypatch
# this env var away.
os.environ.setdefault("API_PREFIX", "")


@pytest.fixture(autouse=True)
def _test_config():
    """Provide an in-memory SQLite config for every test."""
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        secret_key="test-secret",
        env="test",
        api_prefix=os.environ.get("API_PREFIX", ""),
    )
    set_config(settings)
    yield
    reset_config()
    reset_engine()


@pytest.fixture()
async def client():
    """Return a ``TestClient`` wired to the test config."""
    from src.main import app
    from src.models.base import Base
    from src.common.database import get_engine

    # Create all tables before starting the app to avoid "no such table" errors
    # during lifespan startup (e.g., attachment cleanup)
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    with TestClient(app) as c:
        yield c
