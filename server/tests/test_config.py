"""Tests for config loading and validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.common.config import Settings, get_config, load_config, reset_config


class TestRequiredFields:
    """The app must refuse to start without critical environment variables."""

    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    def test_missing_database_url_raises(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        with pytest.raises(ValidationError, match="DATABASE_URL"):
            load_config()

    def test_valid_env_loads(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
        settings = load_config()
        assert settings.database_url == "postgresql+asyncpg://u:p@localhost/db"


class TestDefaults:
    def test_default_env_is_development(self):
        s = Settings(database_url="sqlite+aiosqlite:///test.db")
        assert s.env == "development"

    def test_default_api_prefix(self, monkeypatch):
        # The autouse conftest sets API_PREFIX="" so routers register at
        # natural paths in tests; clear it here to verify the Settings
        # default (``/api/v1``) is still applied when no env override exists.
        monkeypatch.delenv("API_PREFIX", raising=False)
        s = Settings(database_url="sqlite+aiosqlite:///test.db")
        assert s.api_prefix == "/api/v1"

    def test_default_cors(self):
        s = Settings(database_url="sqlite+aiosqlite:///test.db")
        assert s.cors_origins == "*"

    def test_default_log_level(self):
        s = Settings(database_url="sqlite+aiosqlite:///test.db")
        assert s.log_level == "INFO"

    def test_redis_url_default_none(self):
        s = Settings(database_url="sqlite+aiosqlite:///test.db")
        assert s.redis_url is None

    def test_secret_key_default(self):
        s = Settings(database_url="sqlite+aiosqlite:///test.db")
        assert s.secret_key == "dev-secret-do-not-use-in-production"


class TestProperties:
    def test_is_production_true(self):
        # Production config must also pass the safety guards (R-M2-02/03/09
        # + T-M4-01a AI key).
        s = Settings(
            database_url="x",
            env="production",
            secret_key="super-secret-key-that-is-not-default",
            dev_auth_code="",
            cors_origins="https://app.aozora.app",
            ai_provider_api_key="sk-test-not-a-real-key",
        )
        assert s.is_production is True

    def test_is_production_false(self):
        s = Settings(database_url="x", env="development")
        assert s.is_production is False

    def test_cors_origin_list_wildcard(self):
        s = Settings(database_url="x", cors_origins="*")
        assert s.cors_origin_list == ["*"]

    def test_cors_origin_list_comma_separated(self):
        s = Settings(
            database_url="x",
            cors_origins="http://a.com, http://b.com",
        )
        assert s.cors_origin_list == ["http://a.com", "http://b.com"]


class TestSingleton:
    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    def test_get_config_returns_cached(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///test.db")
        a = get_config()
        b = get_config()
        assert a is b

    def test_reset_clears_cache(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///test.db")
        get_config()
        reset_config()
        # After reset, a new call creates a fresh instance
        b = get_config()
        assert b is not None
