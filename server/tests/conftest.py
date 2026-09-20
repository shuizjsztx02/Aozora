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
        # 密封：init kwarg 优先级高于 ``.env``。真实 DashScope key 配入 .env 后
        # （第十二轮云端验收前置），不能让"无 key → 降级"类断言被 ambient .env 污染。
        ai_provider_api_key=None,
        # T-M7-02b：agent 路径 provider 同理——本地 .env 一旦配入 DeepSeek key，
        # "无 key → 快路径不受影响"类断言会失真。
        ai_agent_provider_api_key=None,
        # 配额测试按 get_config().ai_daily_quota_guest 逐行 seed 用量，生产默认值
        # 临时提额期间（2026-09-11 联测）测试面必须钉回 20，勿删。
        ai_daily_quota_guest=20,
        # 两个 AI 闸门同理必须钉死：``Settings`` 对**未显式赋值**的字段仍会读
        # ambient ``.env``，所以本机把 ``AI_AGENT_ENABLED`` / ``AI_DEVICE_CHANNEL_ENABLED``
        # 打开做真机联调时，闸门与契约车道两类断言（``test_agent_stream``）会集体
        # 变红——那是测试被环境污染，不是回归。默认形态＝关闭，用例要打开时自己
        # 显式覆盖（见 ``stream_env`` 与 ``TestDeviceChannel``）。
        ai_agent_enabled=False,
        ai_device_channel_enabled=False,
    )
    set_config(settings)
    yield
    reset_config()
    reset_engine()


@pytest.fixture()
async def client():
    """Return a ``TestClient`` wired to the test config."""
    from src.common.database import get_engine
    from src.main import app
    from src.models.base import Base

    # Create all tables before starting the app to avoid "no such table" errors
    # during lifespan startup (e.g., attachment cleanup)
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    with TestClient(app) as c:
        yield c
