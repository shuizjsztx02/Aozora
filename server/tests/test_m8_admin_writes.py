"""T-M8-05：管理写操作与写审计。

验收映射（`docs/milestones/T-M8-admin-console-plan.md` §三 8-05）：

- 暂停后写请求与同步 push 被拒（403 ``account_suspended``），读可用   → :class:`TestSuspend`
- 恢复后一切正常                                                     → :class:`TestSuspend`
- **反向验证**：暂停 8 天后跑删除扫描，用户数据仍在（不复用 FROZEN）  → :class:`TestDeletionSweep`
- 配额覆盖对 guest 生效、清除后回落，ACTIVE 不受影响                 → :class:`TestQuotaOverride`
- 强制登出后 ``device_sessions`` 为空、refresh token 失效            → :class:`TestRevokeSessions`
- 三个写操作各产生一条 ``admin_audit_log(action="write")``           → :class:`TestWriteAudit`
- 越权 / 不存在的用户 → 401 / 404                                    → :class:`TestAuthBoundary`

临时库用文件 SQLite + ``tempfile.mkdtemp``（本机 pytest basetemp 权限已坏的
仓库约定），与 ``test_m8_admin_api.py`` 同构。
"""

from __future__ import annotations

import importlib.util
import shutil
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select

from src.common.config import Settings, reset_config, set_config
from src.models.admin import AdminAccount, AdminAuditLog
from src.models.ai import AIUsage
from src.models.consent import UserConsent
from src.models.ledger import Book
from src.models.user import DeviceSession, User, UserStatus
from src.modules.admin.dependencies import create_admin_token, hash_admin_password
from src.modules.ai.service import check_daily_quota
from src.modules.auth.utils import (
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
)

PASSWORD = "correct horse battery staple"
ADMIN_NAME = "ops"
DEVICE_ID = "device-1"


# ---------------------------------------------------------------------------
# 环境与播种
# ---------------------------------------------------------------------------

class WriteEnv:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)

    # -- HTTP ----------------------------------------------------------

    def admin_headers(self, admin_id: uuid.UUID) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_admin_token(admin_id)}"}

    def user_headers(self, user_id: uuid.UUID) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(str(user_id))}"}

    # -- 播种 ----------------------------------------------------------

    async def seed_admin(self, username: str = ADMIN_NAME) -> uuid.UUID:
        async with self.factory() as session:
            admin = AdminAccount(
                username=username,
                password_hash=hash_admin_password(PASSWORD),
                status="active",
            )
            session.add(admin)
            await session.commit()
            return admin.id

    async def seed_user(
        self,
        *,
        status: UserStatus = UserStatus.GUEST,
        frozen_at: datetime | None = None,
    ) -> uuid.UUID:
        async with self.factory() as session:
            user = User(status=status, frozen_at=frozen_at)
            session.add(user)
            await session.commit()
            return user.id

    async def seed_device(
        self,
        user_id: uuid.UUID,
        *,
        device_id: str = DEVICE_ID,
        refresh_token: str | None = None,
    ) -> str:
        """Seed a device session; returns the raw refresh token."""
        raw = refresh_token or generate_refresh_token()
        async with self.factory() as session:
            session.add(DeviceSession(
                user_id=user_id,
                device_id=device_id,
                refresh_token_hash=hash_refresh_token(raw),
                expires_at=datetime.now(UTC) + timedelta(days=30),
            ))
            await session.commit()
        return raw

    async def seed_consent(self, device_id: str = DEVICE_ID) -> None:
        async with self.factory() as session:
            session.add(UserConsent(
                device_id=device_id,
                policy_version="v1",
                consented_at=datetime.now(UTC),
            ))
            await session.commit()

    async def seed_usage(self, user_id: uuid.UUID, count: int) -> None:
        async with self.factory() as session:
            for _ in range(count):
                session.add(AIUsage(
                    user_id=user_id,
                    provider="test-provider",
                    model="test-model",
                    tokens=1,
                    cost=0,
                    request_id=uuid.uuid4().hex,
                ))
            await session.commit()

    async def seed_book(self, user_id: uuid.UUID, name: str = "账本") -> uuid.UUID:
        async with self.factory() as session:
            book = Book(owner_id=user_id, name=name)
            session.add(book)
            await session.commit()
            return book.id

    # -- 读取 ----------------------------------------------------------

    async def get_user(self, user_id: uuid.UUID) -> User | None:
        async with self.factory() as session:
            return await session.get(User, user_id)

    async def device_count(self, user_id: uuid.UUID) -> int:
        async with self.factory() as session:
            return int(await session.scalar(
                select(func.count(DeviceSession.id)).where(
                    DeviceSession.user_id == user_id,
                )
            ) or 0)

    async def audit_rows(self) -> list[AdminAuditLog]:
        async with self.factory() as session:
            return list((await session.scalars(
                select(AdminAuditLog).order_by(AdminAuditLog.created_at)
            )).all())

    async def write_audits(self) -> list[AdminAuditLog]:
        return [row for row in await self.audit_rows() if row.action == "write"]

    # -- 管理写端点 ----------------------------------------------------

    async def suspend(
        self, admin_id: uuid.UUID, user_id: uuid.UUID, reason: str | None = None,
    ) -> httpx.Response:
        return await self.client.post(
            f"/admin/users/{user_id}/suspend",
            json={"reason": reason},
            headers=self.admin_headers(admin_id),
        )

    async def resume(
        self, admin_id: uuid.UUID, user_id: uuid.UUID,
    ) -> httpx.Response:
        return await self.client.post(
            f"/admin/users/{user_id}/resume",
            headers=self.admin_headers(admin_id),
        )

    async def set_quota(
        self, admin_id: uuid.UUID, user_id: uuid.UUID, quota: int | None,
    ) -> httpx.Response:
        return await self.client.put(
            f"/admin/users/{user_id}/ai-quota",
            json={"quota": quota},
            headers=self.admin_headers(admin_id),
        )

    async def revoke_sessions(
        self, admin_id: uuid.UUID, user_id: uuid.UUID,
    ) -> httpx.Response:
        return await self.client.delete(
            f"/admin/users/{user_id}/sessions",
            headers=self.admin_headers(admin_id),
        )


@pytest_asyncio.fixture()
async def env():
    from src.common.database import get_engine, get_session_factory, reset_engine
    from src.models.base import Base
    from src.modules.auth.dependencies import _rate_limits

    reset_config()
    reset_engine()
    _rate_limits.clear()

    db_dir = Path(tempfile.mkdtemp(prefix="aozora_test_m8_admin_writes_"))
    cfg = Settings(
        database_url=f"sqlite+aiosqlite:///{(db_dir / 'test.db').as_posix()}",
        secret_key="test-secret",
        admin_jwt_secret="test-admin-jwt-secret",
        env="test",
        api_prefix="",
        # 配额用例按这个值 seed 用量；钉死避免 ambient .env 污染。
        ai_daily_quota_guest=20,
        ai_agent_enabled=False,
        ai_device_channel_enabled=False,
    )
    set_config(cfg)
    reset_engine()

    from src.main import app

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield WriteEnv(
            app=app, client=client, cfg=cfg, engine=engine,
            factory=get_session_factory(), db_dir=db_dir,
        )

    await engine.dispose()
    reset_config()
    reset_engine()
    _rate_limits.clear()
    shutil.rmtree(db_dir, ignore_errors=True)


def _load_cleanup_module(name: str):
    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / "infra" / "cleanup" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _push_body() -> dict[str, Any]:
    return {
        "mutations": [{
            "mutation_id": str(uuid.uuid4()),
            "entity_type": "book",
            "operation": "insert",
            "entity_id": str(uuid.uuid4()),
            "payload": {"name": "同步账本", "icon": "📒"},
        }],
    }


# ---------------------------------------------------------------------------
# 1. 暂停 / 恢复
# ---------------------------------------------------------------------------

class TestSuspend:
    async def test_suspend_blocks_writes_allows_reads_and_resume_restores(self, env):
        admin_id = await env.seed_admin()
        user_id = await env.seed_user()
        await env.seed_device(user_id)
        await env.seed_consent()
        headers = env.user_headers(user_id)

        # 基线：写与 push 都通。
        assert (await env.client.post(
            "/books", json={"name": "书一"}, headers=headers,
        )).status_code == 201
        assert (await env.client.post(
            "/sync/push", json=_push_body(), headers=headers,
        )).status_code == 200

        suspended = await env.suspend(admin_id, user_id, reason="涉嫌滥用")
        assert suspended.status_code == 200, suspended.text
        body = suspended.json()
        assert body["admin_suspended_at"] is not None
        assert body["admin_suspended_reason"] == "涉嫌滥用"

        # 写：全部 403 account_suspended。
        for resp in (
            await env.client.post("/books", json={"name": "书二"}, headers=headers),
            await env.client.post("/accounts", json={"name": "现金"}, headers=headers),
            await env.client.post("/sync/push", json=_push_body(), headers=headers),
            await env.client.patch("/me", json={"nickname": "改名"}, headers=headers),
        ):
            assert resp.status_code == 403, resp.text
            assert resp.json()["code"] == "account_suspended"

        # 读：照常。
        assert (await env.client.get("/me", headers=headers)).status_code == 200
        assert (await env.client.get("/books", headers=headers)).status_code == 200
        assert (await env.client.post(
            "/sync/pull", json={"cursor": 0}, headers=headers,
        )).status_code == 200
        assert (await env.client.get(
            "/ai/conversations", headers=headers,
        )).status_code == 200

        # 恢复：写与 push 重新可用。
        resumed = await env.resume(admin_id, user_id)
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["admin_suspended_at"] is None
        assert resumed.json()["admin_suspended_reason"] is None
        assert (await env.client.post(
            "/books", json={"name": "书三"}, headers=headers,
        )).status_code == 201
        assert (await env.client.post(
            "/sync/push", json=_push_body(), headers=headers,
        )).status_code == 200

    async def test_suspend_does_not_touch_status_or_frozen_fields(self, env):
        admin_id = await env.seed_admin()
        user_id = await env.seed_user(status=UserStatus.GUEST)

        assert (await env.suspend(admin_id, user_id)).status_code == 200

        user = await env.get_user(user_id)
        assert user is not None
        assert user.status == UserStatus.GUEST
        assert user.frozen_at is None
        assert user.deletion_requested_at is None
        assert user.admin_suspended_at is not None

    async def test_suspend_without_body_is_accepted(self, env):
        admin_id = await env.seed_admin()
        user_id = await env.seed_user()

        response = await env.client.post(
            f"/admin/users/{user_id}/suspend",
            headers=env.admin_headers(admin_id),
        )
        assert response.status_code == 200, response.text
        assert response.json()["admin_suspended_at"] is not None
        assert response.json()["admin_suspended_reason"] is None

    async def test_resume_is_idempotent_for_unsuspended_user(self, env):
        admin_id = await env.seed_admin()
        user_id = await env.seed_user()

        response = await env.resume(admin_id, user_id)
        assert response.status_code == 200
        assert response.json()["admin_suspended_at"] is None


# ---------------------------------------------------------------------------
# 2. 反向验证：暂停 8 天后删除扫描不碰该用户
# ---------------------------------------------------------------------------

class TestDeletionSweep:
    async def test_suspended_user_survives_deletion_sweep(self, env):
        cleanup = _load_cleanup_module("account-deletion")
        admin_id = await env.seed_admin()
        user_id = await env.seed_user()
        book_id = await env.seed_book(user_id, name="兜底账本")

        assert (await env.suspend(admin_id, user_id)).status_code == 200

        # 把暂停时间提前 8 天：如果实现偷偷复用了 FROZEN + frozen_at，
        # 这个用户就会落进删除窗口。
        async with env.factory() as session:
            user = await session.get(User, user_id)
            user.admin_suspended_at = datetime.now(UTC) - timedelta(days=8)
            await session.commit()

        # 阳性对照：真正的 FROZEN 用户（冻结 8 天）必须被删掉，证明扫描
        # 本身在这个测试环境里是生效的。
        frozen_id = await env.seed_user(
            status=UserStatus.FROZEN,
            frozen_at=datetime.now(UTC) - timedelta(days=8),
        )

        async with env.factory() as session:
            deleted = await cleanup.delete_frozen_users(session)
        assert deleted == 1  # 只有阳性对照

        assert await env.get_user(user_id) is not None
        assert await env.get_user(frozen_id) is None
        async with env.factory() as session:
            assert await session.get(Book, book_id) is not None


# ---------------------------------------------------------------------------
# 3. 配额覆盖
# ---------------------------------------------------------------------------

async def _quota_check(env, user_id: uuid.UUID) -> None:
    """以新 session 载入用户后跑真实的 ``check_daily_quota``。"""
    async with env.factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        await check_daily_quota(session, user, env.cfg)


class TestQuotaOverride:
    async def test_override_applies_to_guest_and_clears(self, env):
        from src.common.errors import AppException

        admin_id = await env.seed_admin()
        user_id = await env.seed_user(status=UserStatus.GUEST)
        await env.seed_usage(user_id, env.cfg.ai_daily_quota_guest)  # 20 = 已用满

        with pytest.raises(AppException) as excinfo:
            await _quota_check(env, user_id)
        assert excinfo.value.status_code == 429
        assert excinfo.value.code == "ai_quota_exceeded"

        response = await env.set_quota(admin_id, user_id, 100)
        assert response.status_code == 200, response.text
        assert response.json()["ai_daily_quota_override"] == 100
        await _quota_check(env, user_id)  # 覆盖生效，不再 429

        cleared = await env.set_quota(admin_id, user_id, None)
        assert cleared.status_code == 200
        assert cleared.json()["ai_daily_quota_override"] is None

        with pytest.raises(AppException) as excinfo:
            await _quota_check(env, user_id)
        assert excinfo.value.code == "ai_quota_exceeded"

    async def test_active_user_is_not_capped_by_override(self, env):
        admin_id = await env.seed_admin()
        user_id = await env.seed_user(status=UserStatus.ACTIVE)
        await env.seed_usage(user_id, env.cfg.ai_daily_quota_guest + 10)

        response = await env.set_quota(admin_id, user_id, 1)
        assert response.status_code == 200
        await _quota_check(env, user_id)  # 不抛——ACTIVE 不吃日配额

    async def test_negative_quota_rejected(self, env):
        admin_id = await env.seed_admin()
        user_id = await env.seed_user()

        response = await env.set_quota(admin_id, user_id, -1)
        assert response.status_code == 422
        assert (await env.write_audits()) == []


# ---------------------------------------------------------------------------
# 4. 强制登出
# ---------------------------------------------------------------------------

class TestRevokeSessions:
    async def test_revoke_sessions_invalidates_refresh_token(self, env):
        admin_id = await env.seed_admin()
        user_id = await env.seed_user()
        raw_a = await env.seed_device(user_id, device_id="device-a")
        raw_b = await env.seed_device(user_id, device_id="device-b")
        assert await env.device_count(user_id) == 2

        response = await env.revoke_sessions(admin_id, user_id)
        assert response.status_code == 200, response.text
        assert response.json()["deleted_sessions"] == 2
        assert await env.device_count(user_id) == 0

        for raw, device_id in ((raw_a, "device-a"), (raw_b, "device-b")):
            refreshed = await env.client.post(
                "/auth/refresh",
                json={"refresh_token": raw, "device_id": device_id},
            )
            assert refreshed.status_code == 401
            assert refreshed.json()["code"] == "invalid_token"


# ---------------------------------------------------------------------------
# 5. 写审计
# ---------------------------------------------------------------------------

class TestWriteAudit:
    async def test_each_write_operation_records_action_write(self, env):
        admin_id = await env.seed_admin()
        user_id = await env.seed_user()
        await env.seed_device(user_id)

        assert (await env.suspend(admin_id, user_id, "理由")).status_code == 200
        assert (await env.resume(admin_id, user_id)).status_code == 200
        assert (await env.set_quota(admin_id, user_id, 50)).status_code == 200
        assert (await env.revoke_sessions(admin_id, user_id)).status_code == 200

        rows = await env.write_audits()
        assert len(rows) == 4
        by_operation = {row.detail["operation"]: row for row in rows}
        assert set(by_operation) == {
            "suspend", "resume", "set_ai_quota", "revoke_sessions",
        }

        for row in rows:
            assert row.admin_id == admin_id
            assert row.target_user_id == user_id
            assert row.detail["target_user_id"] == str(user_id)
            assert set(row.detail) >= {
                "operation", "target_user_id", "before", "after",
            }
            assert row.ip is not None

        suspend_row = by_operation["suspend"]
        assert suspend_row.detail["before"]["admin_suspended_at"] is None
        assert suspend_row.detail["after"]["admin_suspended_at"] is not None
        assert suspend_row.detail["after"]["admin_suspended_reason"] == "理由"

        quota_row = by_operation["set_ai_quota"]
        assert quota_row.detail["before"]["ai_daily_quota_override"] is None
        assert quota_row.detail["after"]["ai_daily_quota_override"] == 50

        sessions_row = by_operation["revoke_sessions"]
        assert sessions_row.detail["before"]["device_sessions"] == 1
        assert sessions_row.detail["after"]["device_sessions"] == 0


# ---------------------------------------------------------------------------
# 6. 鉴权边界与 404
# ---------------------------------------------------------------------------

class TestAuthBoundary:
    async def test_user_token_cannot_use_admin_write(self, env):
        user_id = await env.seed_user()
        response = await env.client.post(
            f"/admin/users/{user_id}/suspend",
            json={"reason": None},
            headers=env.user_headers(user_id),
        )
        assert response.status_code == 401
        assert response.json()["code"] == "invalid_admin_token"

    async def test_missing_admin_token_is_401(self, env):
        user_id = await env.seed_user()
        response = await env.client.post(f"/admin/users/{user_id}/resume")
        assert response.status_code == 401
        assert response.json()["code"] == "missing_admin_token"

    async def test_unknown_user_is_404_for_every_write(self, env):
        admin_id = await env.seed_admin()
        missing = uuid.uuid4()

        assert (await env.suspend(admin_id, missing)).status_code == 404
        assert (await env.resume(admin_id, missing)).status_code == 404
        assert (await env.set_quota(admin_id, missing, 10)).status_code == 404
        assert (await env.revoke_sessions(admin_id, missing)).status_code == 404

        for response in (
            await env.suspend(admin_id, missing),
            await env.resume(admin_id, missing),
            await env.set_quota(admin_id, missing, 10),
            await env.revoke_sessions(admin_id, missing),
        ):
            assert response.json()["code"] == "user_not_found"
        assert (await env.write_audits()) == []
