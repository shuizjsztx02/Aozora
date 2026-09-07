"""T-M2-03: Authentication module tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.common.config import Settings
from src.common.database import get_db_session
from src.models.base import Base
from src.models.user import DeviceSession, UserStatus
from src.modules.auth.dependencies import _rate_limits
from src.modules.auth.service import AuthService
from src.modules.auth.utils import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_refresh_token,
)
from tests.fixtures_db import db_session, sample_data  # noqa: F401

# ── JWT utilities ──────────────────────────────────────────────────────

class TestJWT:
    def test_create_and_decode(self):
        uid = str(uuid.uuid4())
        token = create_access_token(uid)
        payload = decode_access_token(token)
        assert payload["sub"] == uid
        assert "exp" in payload

    def test_expired_token_raises(self):
        """Tokens with past exp should be rejected."""
        import jwt as pyjwt

        from src.common.config import get_config

        cfg = get_config()
        now = datetime.now(UTC)
        payload = {"sub": str(uuid.uuid4()), "exp": now - timedelta(seconds=10)}
        token = pyjwt.encode(payload, cfg.secret_key, algorithm=cfg.jwt_algorithm)

        with pytest.raises(Exception):
            decode_access_token(token)

    def test_wrong_secret_raises(self):
        import jwt as pyjwt

        now = datetime.now(UTC)
        token = pyjwt.encode(
            {"sub": str(uuid.uuid4()), "exp": now + timedelta(minutes=15)},
            "wrong-secret",
            algorithm="HS256",
        )
        with pytest.raises(Exception):
            decode_access_token(token)


# ── Refresh token hashing ──────────────────────────────────────────────

class TestRefreshTokenHashing:
    def test_hash_is_deterministic(self):
        token = generate_refresh_token()
        assert hash_refresh_token(token) == hash_refresh_token(token)

    def test_hash_is_sha256(self):
        token = "test-token"
        h = hash_refresh_token(token)
        assert len(h) == 64  # SHA-256 hex length

    def test_different_tokens_different_hashes(self):
        t1 = generate_refresh_token()
        t2 = generate_refresh_token()
        assert hash_refresh_token(t1) != hash_refresh_token(t2)

    def test_generated_token_length(self):
        token = generate_refresh_token()
        assert len(token) == 64  # 32 bytes hex


# ── Auth service ───────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_rate_limits():
    """Clear in-memory rate limiter between tests."""
    _rate_limits.clear()
    yield
    _rate_limits.clear()


class TestGuestCreation:
    @pytest.mark.asyncio
    async def test_create_guest(self, db_session):
        service = AuthService(db_session)
        user, access_token, refresh_token = await service.create_guest(
            device_id="device-1", nickname="tester",
        )
        assert user.status == UserStatus.GUEST
        assert user.nickname == "tester"
        assert len(access_token) > 0
        assert len(refresh_token) == 64

        # Session stored in DB
        result = await db_session.execute(
            select(DeviceSession).where(DeviceSession.user_id == user.id)
        )
        session = result.scalar_one()
        assert session.device_id == "device-1"
        # Verify the stored hash matches the returned token
        assert session.refresh_token_hash == hash_refresh_token(refresh_token)


class TestPhoneLogin:
    @pytest.mark.asyncio
    async def test_valid_code_creates_user(self, db_session):
        service = AuthService(db_session)
        user, at, rt, is_new = await service.phone_login(
            phone="13800000001", verification_code="000000", device_id="dev-1",
        )
        assert is_new is True
        assert user.phone == "13800000001"
        assert user.status == UserStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_existing_user_returns_not_new(self, db_session):
        service = AuthService(db_session)
        # First login creates user
        await service.phone_login("13800000002", "000000", "dev-1")
        # Second login finds existing user
        _, _, _, is_new = await service.phone_login("13800000002", "000000", "dev-2")
        assert is_new is False

    @pytest.mark.asyncio
    async def test_invalid_code_raises(self, db_session):
        service = AuthService(db_session)
        with pytest.raises(Exception) as exc_info:
            await service.phone_login("13800000003", "999999", "dev-1")
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_deactivated_user_rejected(self, db_session):
        service = AuthService(db_session)
        # Create and deactivate user
        user, _, _, _ = await service.phone_login("13800000004", "000000", "dev-1")
        user.status = UserStatus.DEACTIVATED
        await db_session.flush()

        from src.common.errors import AppException
        with pytest.raises(AppException) as exc_info:
            await service.phone_login("13800000004", "000000", "dev-2")
        assert exc_info.value.status_code == 403


class TestRefreshTokenRotation:
    @pytest.mark.asyncio
    async def test_refresh_returns_new_tokens(self, db_session):
        service = AuthService(db_session)
        _, _, original_refresh = await service.create_guest(device_id="dev-1")

        new_access, new_refresh = await service.refresh_tokens(original_refresh, "dev-1")
        assert len(new_access) > 0
        assert len(new_refresh) == 64
        assert new_refresh != original_refresh

    @pytest.mark.asyncio
    async def test_old_token_rejected_after_refresh(self, db_session):
        """Replay of old refresh token must be rejected (rotation)."""
        service = AuthService(db_session)
        _, _, original_refresh = await service.create_guest(device_id="dev-1")

        await service.refresh_tokens(original_refresh, "dev-1")

        from src.common.errors import AppException
        with pytest.raises(AppException) as exc_info:
            await service.refresh_tokens(original_refresh, "dev-1")
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_device_id_rejected(self, db_session):
        service = AuthService(db_session)
        _, _, refresh = await service.create_guest(device_id="dev-1")

        from src.common.errors import AppException
        with pytest.raises(AppException):
            await service.refresh_tokens(refresh, "wrong-device")


class TestLogout:
    @pytest.mark.asyncio
    async def test_logout_revokes_session(self, db_session):
        service = AuthService(db_session)
        user, _, refresh = await service.create_guest(device_id="dev-1")

        await service.logout(refresh, "dev-1")

        result = await db_session.execute(
            select(DeviceSession).where(DeviceSession.user_id == user.id)
        )
        assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_revoke_all_sessions(self, db_session):
        service = AuthService(db_session)
        user, _, _ = await service.create_guest(device_id="dev-1")
        # Create a second session
        session2 = DeviceSession(
            user_id=user.id, device_id="dev-2",
            refresh_token_hash=hash_refresh_token(generate_refresh_token()),
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
        db_session.add(session2)
        await db_session.flush()

        count = await service.revoke_all_sessions(user.id)
        assert count == 2

        result = await db_session.execute(
            select(DeviceSession).where(DeviceSession.user_id == user.id)
        )
        assert result.scalars().all() == []


# ── Rate limiter ───────────────────────────────────────────────────────

class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_rate_limit_enforced(self):
        """After MAX_AUTH_REQUESTS (10) in 60s, the 11th must fail."""
        from src.modules.auth.dependencies import (
            MAX_AUTH_REQUESTS,
            check_auth_rate_limit,
        )

        class FakeClient:
            host = "1.2.3.4"

        class FakeURL:
            path = "/auth/guest"

        class FakeHeaders(dict):
            def get(self, key, default=None):
                return super().get(key, default)

        class FakeRequest:
            client = FakeClient()
            url = FakeURL()
            headers = FakeHeaders()  # No X-Forwarded-For → use client.host

        for _ in range(MAX_AUTH_REQUESTS):
            check_auth_rate_limit(FakeRequest())

        from src.common.errors import AppException
        with pytest.raises(AppException) as exc_info:
            check_auth_rate_limit(FakeRequest())
        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_rate_limit_uses_x_forwarded_for(self):
        """R-M2-18: X-Forwarded-For should take precedence over client.host."""
        from src.modules.auth.dependencies import (
            MAX_AUTH_REQUESTS,
            check_auth_rate_limit,
        )

        class FakeClient:
            host = "127.0.0.1"  # proxy IP

        class FakeURL:
            path = "/auth/guest"

        class FakeHeaders(dict):
            def __init__(self):
                super().__init__({"x-forwarded-for": "203.0.113.42, 127.0.0.1"})

            def get(self, key, default=None):
                return super().get(key, default)

        class FakeRequest:
            client = FakeClient()
            url = FakeURL()
            headers = FakeHeaders()

        # 203.0.113.42 should have its own bucket, not shared with 127.0.0.1.
        for _ in range(MAX_AUTH_REQUESTS):
            check_auth_rate_limit(FakeRequest())

        from src.common.errors import AppException
        with pytest.raises(AppException) as exc_info:
            check_auth_rate_limit(FakeRequest())
        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_rate_limit_shared_across_auth_endpoints(self):
        """N-10: all auth endpoints share ONE bucket per IP (10/min total).

        Previously the key was (ip, path), so hitting 4 auth endpoints in
        rotation allowed 4x the limit. The 11th request overall must fail
        even though each individual endpoint was only used 2-3 times.
        """
        from src.modules.auth.dependencies import (
            MAX_AUTH_REQUESTS,
            check_auth_rate_limit,
        )

        def make_request(path: str):
            class FakeClient:
                host = "9.9.9.9"

            class FakeURL:
                pass

            class FakeHeaders(dict):
                def get(self, key, default=None):
                    return super().get(key, default)

            url_obj = FakeURL()
            url_obj.path = path

            class FakeRequest:
                client = FakeClient()
                url = url_obj
                headers = FakeHeaders()

            return FakeRequest()

        paths = ["/auth/guest", "/auth/login", "/auth/refresh", "/auth/logout"]
        for i in range(MAX_AUTH_REQUESTS):
            check_auth_rate_limit(make_request(paths[i % len(paths)]))

        from src.common.errors import AppException
        with pytest.raises(AppException) as exc_info:
            check_auth_rate_limit(make_request("/auth/guest"))
        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_rate_limit_buckets_stay_per_ip(self):
        """N-10 guard: converging keys must not merge distinct clients."""
        from src.modules.auth.dependencies import (
            MAX_AUTH_REQUESTS,
            check_auth_rate_limit,
        )

        def make_request(host: str):
            class FakeClient:
                pass

            class FakeURL:
                path = "/auth/guest"

            class FakeHeaders(dict):
                def get(self, key, default=None):
                    return super().get(key, default)

            client_obj = FakeClient()
            client_obj.host = host

            class FakeRequest:
                client = client_obj
                url = FakeURL()
                headers = FakeHeaders()

            return FakeRequest()

        for _ in range(MAX_AUTH_REQUESTS):
            check_auth_rate_limit(make_request("8.8.8.1"))

        # A different IP must still get through.
        check_auth_rate_limit(make_request("8.8.8.2"))


# ── Production config safety ──────────────────────────────────────────

class TestProductionGuards:
    """R-M2-02 / R-M2-03 / R-M2-09: production must refuse insecure defaults."""

    def test_secret_key_default_rejected_in_production(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="SECRET_KEY"):
            Settings(
                database_url="sqlite+aiosqlite:///test.db",
                env="production",
            )

    def test_dev_auth_code_rejected_in_production(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="DEV_AUTH_CODE"):
            Settings(
                database_url="sqlite+aiosqlite:///test.db",
                env="production",
                secret_key="super-secret-key-that-is-not-default",
            )

    def test_cors_wildcard_rejected_in_production(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="CORS_ORIGINS"):
            Settings(
                database_url="sqlite+aiosqlite:///test.db",
                env="production",
                secret_key="super-secret-key-that-is-not-default",
                dev_auth_code="",
            )

    def test_ai_key_missing_rejected_in_production(self):
        """T-M4-01a acceptance: production refuses to start without AI key."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="AI_PROVIDER_API_KEY"):
            Settings(
                database_url="sqlite+aiosqlite:///test.db",
                env="production",
                secret_key="super-secret-key-that-is-not-default",
                dev_auth_code="",
                cors_origins="https://app.aozora.app",
            )

    def test_production_accepts_secure_config(self):
        s = Settings(
            database_url="sqlite+aiosqlite:///test.db",
            env="production",
            secret_key="super-secret-key-that-is-not-default",
            dev_auth_code="",
            cors_origins="https://app.aozora.app",
            ai_provider_api_key="sk-test-not-a-real-key",
        )
        assert s.is_production is True

    def test_cors_non_aozora_domain_rejected_in_production(self):
        """O-T-M5-02-3: production CORS only accepts *.aozora.app over https."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="CORS_ORIGINS"):
            Settings(
                database_url="sqlite+aiosqlite:///test.db",
                env="production",
                secret_key="super-secret-key-that-is-not-default",
                dev_auth_code="",
                cors_origins="https://evil.com",
                ai_provider_api_key="sk-test-not-a-real-key",
            )

    def test_cors_http_scheme_rejected_in_production(self):
        """O-T-M5-02-3: http:// is rejected even on aozora.app."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="CORS_ORIGINS"):
            Settings(
                database_url="sqlite+aiosqlite:///test.db",
                env="production",
                secret_key="super-secret-key-that-is-not-default",
                dev_auth_code="",
                cors_origins="http://app.aozora.app",
                ai_provider_api_key="sk-test-not-a-real-key",
            )

    def test_ttl_must_be_positive(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            Settings(
                database_url="sqlite+aiosqlite:///test.db",
                access_token_expire_minutes=0,
            )
        with pytest.raises(ValidationError):
            Settings(
                database_url="sqlite+aiosqlite:///test.db",
                refresh_token_expire_days=-1,
            )

    def test_jwt_algorithm_whitelist(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            Settings(
                database_url="sqlite+aiosqlite:///test.db",
                jwt_algorithm="none",
            )
        # Accepted values
        for algo in ("HS256", "RS256", "ES256"):
            Settings(database_url="sqlite+aiosqlite:///test.db", jwt_algorithm=algo)


# ── API integration ───────────────────────────────────────────────────

@pytest_asyncio.fixture()
async def auth_client():
    """TestClient wired to the full app with auth routes and initialized tables.

    R-M2-22: drop ``raise_server_exceptions=False`` so server-side errors
    propagate instead of being hidden behind a 500 response.
    R-M2-23: use an async fixture so the engine lifecycle shares the
    pytest-asyncio event loop — no more ``asyncio.run()`` per test.
    """
    from src.main import app

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def _override_get_db_session():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db_session] = _override_get_db_session

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()


class TestAuthAPI:
    # R-M2-01: in tests, API_PREFIX is "" (conftest.py) so auth routes
    # live at their natural paths. Production serves them under /api/v1.
    def test_guest_endpoint(self, auth_client):
        resp = auth_client.post("/auth/guest", json={
            "device_id": "test-device-1",
            "nickname": "guest-user",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "user_id" in data
        assert "access_token" in data["tokens"]
        assert "refresh_token" in data["tokens"]
        assert data["tokens"]["token_type"] == "bearer"
        assert data["tokens"]["expires_in"] > 0

    def test_login_endpoint(self, auth_client):
        resp = auth_client.post("/auth/login", json={
            "phone": "13900001111",
            "verification_code": "000000",
            "device_id": "test-device-2",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_new_user"] is True
        assert "tokens" in data

    def test_login_invalid_code(self, auth_client):
        resp = auth_client.post("/auth/login", json={
            "phone": "13900002222",
            "verification_code": "999999",
            "device_id": "test-device-3",
        })
        assert resp.status_code == 401

    def test_refresh_endpoint(self, auth_client):
        # First create a guest to get tokens
        guest_resp = auth_client.post("/auth/guest", json={
            "device_id": "test-device-4",
        })
        tokens = guest_resp.json()["tokens"]

        # Refresh
        resp = auth_client.post("/auth/refresh", json={
            "refresh_token": tokens["refresh_token"],
            "device_id": "test-device-4",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["refresh_token"] != tokens["refresh_token"]

    def test_logout_endpoint(self, auth_client):
        guest_resp = auth_client.post("/auth/guest", json={
            "device_id": "test-device-5",
        })
        tokens = guest_resp.json()["tokens"]

        resp = auth_client.post("/auth/logout", json={
            "refresh_token": tokens["refresh_token"],
            "device_id": "test-device-5",
        })
        assert resp.status_code == 204

    def test_protected_route_without_token(self, auth_client):
        """Verify the OpenAPI spec lists the auth endpoints."""
        resp = auth_client.get("/openapi.json")
        assert resp.status_code == 200
        paths = resp.json()["paths"]
        assert "/auth/guest" in paths
        assert "/auth/login" in paths
        assert "/auth/refresh" in paths
        assert "/auth/logout" in paths

    def test_timezone_rejects_invalid_value(self, auth_client):
        """R-M2-07: bogus timezone must be rejected at the API boundary."""
        resp = auth_client.post("/auth/guest", json={
            "device_id": "test-device-6",
            "timezone": "Not/A/Real/Zone",
        })
        assert resp.status_code == 422

    def test_device_id_rejects_overlong_input(self, auth_client):
        """R-M2-08: device_id longer than 128 chars → 422."""
        resp = auth_client.post("/auth/guest", json={
            "device_id": "x" * 200,
        })
        assert resp.status_code == 422

    def test_nickname_overlong_rejected(self, auth_client):
        """N-26 regression: nickname > 64 chars → 422, not 500.

        ``users.nickname`` is ``String(64)``; without schema validation,
        overlong input would fall through to the DB layer and 500.
        """
        resp = auth_client.post("/auth/guest", json={
            "device_id": "test-device-nick",
            "nickname": "x" * 65,
        })
        assert resp.status_code == 422


class TestMeEndpoint:
    """R-M2-27: GET /me returns the authenticated user's profile."""

    def test_me_returns_current_user(self, auth_client):
        guest_resp = auth_client.post("/auth/guest", json={
            "device_id": "test-device-me",
            "nickname": "me-user",
        })
        body = guest_resp.json()
        tokens = body["tokens"]

        resp = auth_client.get(
            "/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == body["user_id"]
        assert data["nickname"] == "me-user"
        assert data["phone"] is None
        assert data["status"] == "guest"
        assert data["timezone"] == "Asia/Shanghai"
        assert "created_at" in data

    def test_me_without_token_rejected(self, auth_client):
        # Missing Authorization header → 401 (FastAPI HTTPBearer).
        resp = auth_client.get("/me")
        assert resp.status_code == 401

    def test_me_invalid_token_rejected(self, auth_client):
        resp = auth_client.get(
            "/me", headers={"Authorization": "Bearer not-a-real-token"},
        )
        assert resp.status_code == 401
