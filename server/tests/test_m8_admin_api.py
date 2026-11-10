"""T-M8-04：admin 鉴权与只读 API 测试。

验收映射（`docs/milestones/T-M8-admin-console-plan.md` §三 8-04）：

- 双向 401（用户 token ⇄ 管理员 token）            → :class:`TestAuthBoundary`
- 登录成功 / 失败 + 审计行                          → :class:`TestLogin`
- 用户检索的筛选组合                                → :class:`TestUserSearch`
- 会话检索（关键词 / 用户 / 时间 / 归档）            → :class:`TestConversationSearch`
- keyset 分页无重复无遗漏                            → :class:`TestKeysetPagination`
- 列表不返回事件正文                                → :class:`TestNoEventBodyInLists`
- 默认脱敏与 ``reveal=true`` 的审计行               → :class:`TestMasking`
- ``request_id`` 反查命中正确的会话 / 轮次           → :class:`TestRequestLookup`
- 只读事务的等价实现                                → :class:`TestReadOnlyTransaction`

全部用例走真实 DB（临时文件 SQLite）+ 真实 ASGI 应用，无网络、无模型。
临时目录用 ``tempfile.mkdtemp``（本机 pytest basetemp 权限已坏的仓库约定）。
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest_asyncio
from sqlalchemy import select

from src.common.config import Settings, reset_config, set_config
from src.models.admin import AdminAccount, AdminAuditLog
from src.models.ai import AIConversation, AIConversationEvent, AIUsage
from src.models.user import DeviceSession, User, UserStatus
from src.modules.admin.dependencies import (
    begin_read_only,
    create_admin_token,
    hash_admin_password,
)
from src.modules.admin.schemas import mask_text
from src.modules.auth.utils import create_access_token

PASSWORD = "correct horse battery staple"
ADMIN_NAME = "ops"


# ---------------------------------------------------------------------------
# 环境与播种
# ---------------------------------------------------------------------------

class AdminEnv:
    """临时库 + ASGI client + 直接造数的种子函数。"""

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)

    # -- HTTP ----------------------------------------------------------

    async def login(
        self, username: str = ADMIN_NAME, password: str = PASSWORD,
    ) -> httpx.Response:
        return await self.client.post(
            "/admin/auth/login",
            json={"username": username, "password": password},
        )

    def admin_headers(self, admin_id: uuid.UUID) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_admin_token(admin_id)}"}

    def user_headers(self, user_id: uuid.UUID) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(str(user_id))}"}

    async def get(
        self, path: str, headers: dict[str, str], **params: Any,
    ) -> httpx.Response:
        return await self.client.get(path, params=params, headers=headers)

    # -- 播种 ----------------------------------------------------------

    async def seed_admin(
        self,
        *,
        username: str = ADMIN_NAME,
        password: str = PASSWORD,
        status: str = "active",
    ) -> uuid.UUID:
        async with self.factory() as session:
            admin = AdminAccount(
                username=username,
                password_hash=hash_admin_password(password),
                status=status,
            )
            session.add(admin)
            await session.commit()
            return admin.id

    async def seed_user(
        self,
        *,
        phone: str | None = None,
        nickname: str | None = None,
        status: UserStatus = UserStatus.GUEST,
        created_at: datetime | None = None,
    ) -> uuid.UUID:
        async with self.factory() as session:
            values: dict[str, Any] = {
                "id": uuid.uuid4(),
                "phone": phone,
                "nickname": nickname,
                "status": status,
            }
            if created_at is not None:
                values["created_at"] = created_at
            user = User(**values)
            session.add(user)
            await session.commit()
            return user.id

    async def seed_device(
        self, user_id: uuid.UUID, device_id: str = "device-1",
    ) -> None:
        async with self.factory() as session:
            session.add(DeviceSession(
                user_id=user_id,
                device_id=device_id,
                refresh_token_hash="hash-" + device_id,
                expires_at=datetime.now(UTC) + timedelta(days=30),
            ))
            await session.commit()

    async def seed_conversation(
        self,
        user_id: uuid.UUID,
        *,
        title: str = "",
        search_text: str = "",
        last_active_at: datetime | None = None,
        archived_at: datetime | None = None,
        created_at: datetime | None = None,
        summary: str = "",
    ) -> uuid.UUID:
        async with self.factory() as session:
            values: dict[str, Any] = {
                "id": uuid.uuid4(),
                "user_id": user_id,
                "title": title,
                "search_text": search_text,
                "last_active_at": last_active_at,
                "archived_at": archived_at,
                "summary": summary,
            }
            if created_at is not None:
                values["created_at"] = created_at
            conversation = AIConversation(**values)
            session.add(conversation)
            await session.commit()
            return conversation.id

    async def seed_event(
        self,
        conversation_id: uuid.UUID,
        *,
        seq: int,
        turn: int,
        event_type: str = "user",
        data: dict[str, Any] | None = None,
        time: datetime | None = None,
        request_id: str | None = None,
    ) -> None:
        async with self.factory() as session:
            session.add(AIConversationEvent(
                conversation_id=conversation_id,
                seq=seq,
                turn=turn,
                type=event_type,
                data=data or {},
                time=time or datetime.now(UTC),
                ignorable=event_type not in {"user", "assistant", "tool"},
                request_id=request_id,
            ))
            await session.commit()

    async def seed_usage(
        self,
        user_id: uuid.UUID,
        *,
        model: str,
        tokens: int,
        cost: int = 0,
        conversation_id: uuid.UUID | None = None,
        request_id: str | None = None,
        created_at: datetime | None = None,
    ) -> None:
        async with self.factory() as session:
            values: dict[str, Any] = {
                "user_id": user_id,
                "conversation_id": conversation_id,
                "provider": "test-provider",
                "model": model,
                "tokens": tokens,
                "cost": cost,
                "request_id": request_id or uuid.uuid4().hex,
            }
            if created_at is not None:
                values["created_at"] = created_at
            session.add(AIUsage(**values))
            await session.commit()

    async def audit_rows(self) -> list[AdminAuditLog]:
        async with self.factory() as session:
            return list((await session.scalars(
                select(AdminAuditLog).order_by(AdminAuditLog.created_at)
            )).all())

    async def refresh_admin(self, admin_id: uuid.UUID) -> AdminAccount:
        async with self.factory() as session:
            return await session.get(AdminAccount, admin_id)


@pytest_asyncio.fixture()
async def env():
    from src.common.database import get_engine, get_session_factory, reset_engine
    from src.models.base import Base
    from src.modules.auth.dependencies import _rate_limits

    reset_config()
    reset_engine()
    _rate_limits.clear()

    db_dir = Path(tempfile.mkdtemp(prefix="aozora_test_m8_admin_api_"))
    cfg = Settings(
        database_url=f"sqlite+aiosqlite:///{(db_dir / 'test.db').as_posix()}",
        secret_key="test-secret",
        admin_jwt_secret="test-admin-jwt-secret",
        env="test",
        api_prefix="",
    )
    set_config(cfg)
    reset_engine()

    from src.main import app

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield AdminEnv(
            app=app,
            client=client,
            cfg=cfg,
            engine=engine,
            factory=get_session_factory(),
            db_dir=db_dir,
        )

    await engine.dispose()
    reset_config()
    reset_engine()
    _rate_limits.clear()
    shutil.rmtree(db_dir, ignore_errors=True)


async def _walk_pages(
    env: AdminEnv,
    headers: dict[str, str],
    path: str,
    **params: Any,
) -> list[dict[str, Any]]:
    """按 ``next_cursor`` 走完所有页，返回拼接后的 items。"""
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(20):
        query = dict(params)
        if cursor is not None:
            query["cursor"] = cursor
        response = await env.get(path, headers, **query)
        assert response.status_code == 200, response.text
        body = response.json()
        items.extend(body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            return items
    raise AssertionError("pagination did not terminate")


# ---------------------------------------------------------------------------
# 1. 双向 401
# ---------------------------------------------------------------------------

class TestAuthBoundary:
    async def test_missing_token_is_401(self, env):
        response = await env.client.get("/admin/users")
        assert response.status_code == 401
        assert response.json()["code"] == "missing_admin_token"

    async def test_user_token_cannot_use_admin_api(self, env):
        user_id = await env.seed_user(nickname="u1")
        response = await env.client.get(
            "/admin/users", headers=env.user_headers(user_id),
        )
        assert response.status_code == 401
        assert response.json()["code"] == "invalid_admin_token"

    async def test_admin_token_cannot_use_client_api(self, env):
        admin_id = await env.seed_admin()
        response = await env.client.get(
            "/me", headers=env.admin_headers(admin_id),
        )
        assert response.status_code == 401

    async def test_admin_token_with_user_secret_is_rejected(self, env):
        """即便 audience 对，secret 不对也必须 401——两边独立签名。"""
        import jwt as pyjwt

        admin_id = await env.seed_admin()
        now = datetime.now(UTC)
        forged = pyjwt.encode(
            {
                "sub": str(admin_id),
                "aud": "aozora-admin",
                "exp": now + timedelta(minutes=10),
            },
            "test-secret",  # 用户端 secret，而非 admin secret
            algorithm="HS256",
        )
        response = await env.client.get(
            "/admin/users", headers={"Authorization": f"Bearer {forged}"},
        )
        assert response.status_code == 401

    async def test_admin_token_works(self, env):
        admin_id = await env.seed_admin()
        response = await env.client.get(
            "/admin/users", headers=env.admin_headers(admin_id),
        )
        assert response.status_code == 200
        assert response.json()["items"] == []


# ---------------------------------------------------------------------------
# 2. 登录
# ---------------------------------------------------------------------------

class TestLogin:
    async def test_login_success_issues_usable_token(self, env):
        admin_id = await env.seed_admin()

        response = await env.login()
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["admin_id"] == str(admin_id)
        assert body["username"] == ADMIN_NAME
        assert body["token_type"] == "bearer"
        assert body["expires_in"] == env.cfg.admin_token_expire_minutes * 60

        protected = await env.client.get(
            "/admin/users",
            headers={"Authorization": f"Bearer {body['access_token']}"},
        )
        assert protected.status_code == 200

    async def test_login_writes_audit_with_ip(self, env):
        admin_id = await env.seed_admin()
        await env.login()

        rows = await env.audit_rows()
        assert len(rows) == 1
        assert rows[0].action == "login"
        assert rows[0].admin_id == admin_id
        assert rows[0].ip == "127.0.0.1"
        assert rows[0].detail == {"username": ADMIN_NAME}

    async def test_login_updates_last_login(self, env):
        admin_id = await env.seed_admin()
        assert (await env.refresh_admin(admin_id)).last_login_at is None

        await env.login()

        assert (await env.refresh_admin(admin_id)).last_login_at is not None

    async def test_wrong_password_is_401_and_not_audited(self, env):
        await env.seed_admin()
        response = await env.login(password="wrong-password")

        assert response.status_code == 401
        assert response.json()["code"] == "invalid_credentials"
        assert await env.audit_rows() == []

    async def test_unknown_username_is_401(self, env):
        response = await env.login(username="ghost")
        assert response.status_code == 401
        assert response.json()["code"] == "invalid_credentials"

    async def test_inactive_admin_cannot_login(self, env):
        await env.seed_admin(status="disabled")
        response = await env.login()
        assert response.status_code == 401
        assert await env.audit_rows() == []

    async def test_inactive_admin_token_is_rejected(self, env):
        admin_id = await env.seed_admin(status="disabled")
        response = await env.client.get(
            "/admin/users", headers=env.admin_headers(admin_id),
        )
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# 3. 用户检索
# ---------------------------------------------------------------------------

class TestUserSearch:
    async def test_query_by_phone_is_masked(self, env):
        admin_id = await env.seed_admin()
        await env.seed_user(phone="13800001234", nickname="小王")
        await env.seed_user(phone="13900005678", nickname="小李")

        response = await env.get(
            "/admin/users", env.admin_headers(admin_id), q="138000",
        )
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["nickname"] == "小王"
        assert items[0]["phone"] == "138****1234"

    async def test_query_by_nickname(self, env):
        admin_id = await env.seed_admin()
        await env.seed_user(nickname="小王")
        await env.seed_user(nickname="小李")

        response = await env.get(
            "/admin/users", env.admin_headers(admin_id), q="小",
        )
        assert {item["nickname"] for item in response.json()["items"]} == {"小王", "小李"}

    async def test_query_by_uuid(self, env):
        admin_id = await env.seed_admin()
        target = await env.seed_user(nickname="目标用户")
        await env.seed_user(nickname="其他人")

        response = await env.get(
            "/admin/users", env.admin_headers(admin_id), q=str(target),
        )
        items = response.json()["items"]
        assert [item["id"] for item in items] == [str(target)]

    async def test_status_and_time_filters(self, env):
        admin_id = await env.seed_admin()
        guest = await env.seed_user(
            nickname="游客", created_at=datetime(2026, 1, 1, 12, tzinfo=UTC),
        )
        active = await env.seed_user(
            nickname="正式",
            status=UserStatus.ACTIVE,
            created_at=datetime(2026, 2, 1, 12, tzinfo=UTC),
        )

        by_status = await env.get(
            "/admin/users", env.admin_headers(admin_id), status="active",
        )
        assert [item["id"] for item in by_status.json()["items"]] == [str(active)]

        by_time = await env.get(
            "/admin/users",
            env.admin_headers(admin_id),
            created_from="2026-01-15T00:00:00+00:00",
            created_to="2026-02-15T00:00:00+00:00",
        )
        assert [item["id"] for item in by_time.json()["items"]] == [str(active)]

        # 组合：关键词 + 状态
        combined = await env.get(
            "/admin/users", env.admin_headers(admin_id), q="游", status="guest",
        )
        assert [item["id"] for item in combined.json()["items"]] == [str(guest)]

    async def test_like_wildcards_are_escaped(self, env):
        """``%`` 是字面量而不是通配符——否则 q 会命中全部用户。"""
        admin_id = await env.seed_admin()
        await env.seed_user(nickname="普通用户")

        response = await env.get(
            "/admin/users", env.admin_headers(admin_id), q="%",
        )
        assert response.json()["items"] == []

    async def test_user_detail_counts(self, env):
        admin_id = await env.seed_admin()
        user_id = await env.seed_user(phone="13800001234", nickname="统计")
        await env.seed_device(user_id, "device-a")
        await env.seed_device(user_id, "device-b")
        await env.seed_conversation(
            user_id, last_active_at=datetime(2026, 3, 1, tzinfo=UTC),
        )
        await env.seed_conversation(
            user_id, last_active_at=datetime(2026, 3, 5, tzinfo=UTC),
        )

        response = await env.get(
            f"/admin/users/{user_id}", env.admin_headers(admin_id),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["device_count"] == 2
        assert body["conversation_count"] == 2
        assert body["phone"] == "138****1234"
        assert body["status"] == "guest"
        assert body["last_activity_at"].startswith("2026-03-05")

    async def test_unknown_user_detail_is_404(self, env):
        admin_id = await env.seed_admin()
        response = await env.get(
            f"/admin/users/{uuid.uuid4()}", env.admin_headers(admin_id),
        )
        assert response.status_code == 404
        assert response.json()["code"] == "user_not_found"


# ---------------------------------------------------------------------------
# 4. 会话检索
# ---------------------------------------------------------------------------

class TestConversationSearch:
    async def _seed(self, env: AdminEnv) -> dict[str, uuid.UUID]:
        user_a = await env.seed_user(nickname="A")
        user_b = await env.seed_user(nickname="B")
        takeout_a = await env.seed_conversation(
            user_a,
            search_text="今天中午吃外卖花了35元",
            last_active_at=datetime(2026, 1, 10, tzinfo=UTC),
        )
        grocery_a = await env.seed_conversation(
            user_a,
            search_text="周末去超市买菜",
            last_active_at=datetime(2026, 2, 10, tzinfo=UTC),
        )
        archived_a = await env.seed_conversation(
            user_a,
            search_text="外卖退单",
            last_active_at=datetime(2026, 3, 10, tzinfo=UTC),
            archived_at=datetime(2026, 3, 11, tzinfo=UTC),
        )
        takeout_b = await env.seed_conversation(
            user_b,
            search_text="外卖到货了",
            last_active_at=datetime(2026, 4, 10, tzinfo=UTC),
        )
        return {
            "user_a": user_a,
            "user_b": user_b,
            "takeout_a": takeout_a,
            "grocery_a": grocery_a,
            "archived_a": archived_a,
            "takeout_b": takeout_b,
        }

    async def test_global_keyword_search_excludes_archived(self, env):
        admin_id = await env.seed_admin()
        seeded = await self._seed(env)

        response = await env.get(
            "/admin/conversations", env.admin_headers(admin_id), q="外卖",
        )
        assert response.status_code == 200, response.text
        ids = [item["id"] for item in response.json()["items"]]
        # 按 last_active_at 倒序：B 在前；已归档的 A 不在默认结果里
        assert ids == [str(seeded["takeout_b"]), str(seeded["takeout_a"])]

    async def test_user_filter(self, env):
        admin_id = await env.seed_admin()
        seeded = await self._seed(env)

        response = await env.get(
            "/admin/conversations",
            env.admin_headers(admin_id),
            q="外卖",
            user_id=str(seeded["user_a"]),
        )
        ids = [item["id"] for item in response.json()["items"]]
        assert ids == [str(seeded["takeout_a"])]

    async def test_include_archived(self, env):
        admin_id = await env.seed_admin()
        seeded = await self._seed(env)

        response = await env.get(
            "/admin/conversations",
            env.admin_headers(admin_id),
            user_id=str(seeded["user_a"]),
            include_archived=True,
        )
        ids = [item["id"] for item in response.json()["items"]]
        assert ids == [
            str(seeded["archived_a"]),
            str(seeded["grocery_a"]),
            str(seeded["takeout_a"]),
        ]

    async def test_time_range_filter(self, env):
        admin_id = await env.seed_admin()
        seeded = await self._seed(env)

        response = await env.get(
            "/admin/conversations",
            env.admin_headers(admin_id),
            user_id=str(seeded["user_a"]),
            time_from="2026-02-01T00:00:00+00:00",
        )
        ids = [item["id"] for item in response.json()["items"]]
        assert ids == [str(seeded["grocery_a"])]

    async def test_short_query_is_rejected(self, env):
        admin_id = await env.seed_admin()
        response = await env.get(
            "/admin/conversations", env.admin_headers(admin_id), q="外",
        )
        assert response.status_code == 422
        assert response.json()["code"] == "query_too_short"

    async def test_search_is_case_insensitive(self, env):
        admin_id = await env.seed_admin()
        user_id = await env.seed_user(nickname="C")
        target = await env.seed_conversation(
            user_id,
            search_text="Lunch takeaway order",
            last_active_at=datetime(2026, 5, 1, tzinfo=UTC),
        )
        response = await env.get(
            "/admin/conversations", env.admin_headers(admin_id), q="TAKEAWAY",
        )
        assert [item["id"] for item in response.json()["items"]] == [str(target)]

    async def test_user_conversations_endpoint(self, env):
        admin_id = await env.seed_admin()
        seeded = await self._seed(env)

        response = await env.get(
            f"/admin/users/{seeded['user_a']}/conversations",
            env.admin_headers(admin_id),
        )
        ids = [item["id"] for item in response.json()["items"]]
        assert ids == [str(seeded["grocery_a"]), str(seeded["takeout_a"])]

    async def test_conversation_detail_aggregates(self, env):
        admin_id = await env.seed_admin()
        user_id = await env.seed_user(nickname="D")
        conversation_id = await env.seed_conversation(
            user_id,
            summary="一段摘要",
            last_active_at=datetime(2026, 6, 1, tzinfo=UTC),
        )
        await env.seed_event(
            conversation_id, seq=1, turn=1, event_type="user",
            data={"role": "user", "content": "你好"},
        )
        await env.seed_event(
            conversation_id, seq=2, turn=1, event_type="quota",
            data={"model": "m1", "tokens": 10},
        )
        await env.seed_event(
            conversation_id, seq=3, turn=2, event_type="error",
            data={"code": "provider_timeout"},
        )
        await env.seed_usage(
            user_id, model="m1", tokens=10, cost=1, conversation_id=conversation_id,
        )

        response = await env.get(
            f"/admin/conversations/{conversation_id}", env.admin_headers(admin_id),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["turn_count"] == 2
        assert body["event_count"] == 3
        assert body["max_turn"] == 2
        assert body["models"] == ["m1"]
        assert body["tokens"] == 10
        assert body["has_error"] is True
        assert body["summary_present"] is True
        assert "一段摘要" not in response.text

    async def test_unknown_conversation_is_404(self, env):
        admin_id = await env.seed_admin()
        response = await env.get(
            f"/admin/conversations/{uuid.uuid4()}", env.admin_headers(admin_id),
        )
        assert response.status_code == 404
        assert response.json()["code"] == "conversation_not_found"


# ---------------------------------------------------------------------------
# 5. keyset 分页
# ---------------------------------------------------------------------------

class TestKeysetPagination:
    async def test_users_pagination_is_complete_and_unique(self, env):
        admin_id = await env.seed_admin()
        seeded = [
            await env.seed_user(
                nickname=f"u{index}",
                created_at=datetime(2026, 1, 1, 12, index, tzinfo=UTC),
            )
            for index in range(5)
        ]
        expected = [str(user_id) for user_id in reversed(seeded)]

        items = await _walk_pages(
            env, env.admin_headers(admin_id), "/admin/users", limit=2,
        )
        ids = [item["id"] for item in items]
        assert ids == expected
        assert len(ids) == len(set(ids))

    async def test_conversations_pagination_is_complete_and_unique(self, env):
        admin_id = await env.seed_admin()
        user_id = await env.seed_user(nickname="pager")
        seeded = [
            await env.seed_conversation(
                user_id,
                title=f"c{index}",
                last_active_at=datetime(2026, 2, 1, 12, index, tzinfo=UTC),
            )
            for index in range(5)
        ]
        expected = [str(conversation_id) for conversation_id in reversed(seeded)]

        items = await _walk_pages(
            env, env.admin_headers(admin_id), "/admin/conversations", limit=2,
        )
        ids = [item["id"] for item in items]
        assert ids == expected
        assert len(ids) == len(set(ids))

    async def test_invalid_cursor_is_422(self, env):
        admin_id = await env.seed_admin()
        response = await env.get(
            "/admin/users", env.admin_headers(admin_id), cursor="not-a-cursor",
        )
        assert response.status_code == 422
        assert response.json()["code"] == "invalid_cursor"


# ---------------------------------------------------------------------------
# 6. 列表不返回事件正文
# ---------------------------------------------------------------------------

class TestNoEventBodyInLists:
    async def test_lists_do_not_leak_event_bodies(self, env):
        admin_id = await env.seed_admin()
        user_id = await env.seed_user(nickname="leak-check")
        conversation_id = await env.seed_conversation(
            user_id,
            title="会话",
            search_text="SECRET_SEARCH_TEXT",
            last_active_at=datetime(2026, 7, 1, tzinfo=UTC),
        )
        long_input = "前缀" * 80 + "SECRET_TAIL"
        await env.seed_event(
            conversation_id,
            seq=1,
            turn=1,
            data={"role": "user", "content": long_input},
        )
        headers = env.admin_headers(admin_id)

        for path in (
            f"/admin/users/{user_id}/conversations",
            "/admin/conversations",
            f"/admin/conversations/{conversation_id}",
        ):
            response = await env.get(path, headers)
            assert response.status_code == 200, response.text
            assert "SECRET_TAIL" not in response.text
            assert "SECRET_SEARCH_TEXT" not in response.text
            assert '"data"' not in response.text

    async def test_turn_list_returns_masked_truncated_preview(self, env):
        admin_id = await env.seed_admin()
        user_id = await env.seed_user(nickname="turn-preview")
        conversation_id = await env.seed_conversation(
            user_id, last_active_at=datetime(2026, 7, 2, tzinfo=UTC),
        )
        await env.seed_event(
            conversation_id,
            seq=1,
            turn=1,
            data={
                "role": "user",
                "content": "电话13812345678 " + "长" * 200 + "SECRET_TAIL",
            },
        )
        response = await env.get(
            f"/admin/conversations/{conversation_id}/turns",
            env.admin_headers(admin_id),
        )
        assert response.status_code == 200, response.text
        assert "SECRET_TAIL" not in response.text
        item = response.json()["items"][0]
        assert item["turn"] == 1
        assert "138****5678" in item["first_user_input"]
        assert "13812345678" not in item["first_user_input"]
        assert len(item["first_user_input"]) <= 120


# ---------------------------------------------------------------------------
# 7. 脱敏与 reveal 审计
# ---------------------------------------------------------------------------

class TestMasking:
    PII = (
        "手机13812345678 邮箱zhangsan@example.com "
        "身份证110101199001011234 银行卡6222021234567890123"
    )

    async def _seed(self, env: AdminEnv) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        admin_id = await env.seed_admin()
        user_id = await env.seed_user(nickname="pii")
        conversation_id = await env.seed_conversation(
            user_id, last_active_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        await env.seed_event(
            conversation_id,
            seq=1,
            turn=1,
            data={"role": "user", "content": self.PII},
            request_id="req-pii",
        )
        return admin_id, user_id, conversation_id

    async def test_default_response_is_masked(self, env):
        admin_id, _, conversation_id = await self._seed(env)
        response = await env.get(
            f"/admin/conversations/{conversation_id}/events",
            env.admin_headers(admin_id),
        )
        assert response.status_code == 200, response.text
        content = response.json()["items"][0]["data"]["content"]
        assert "138****5678" in content
        assert "z***@example.com" in content
        assert "110101********1234" in content
        assert "6222***********0123" in content
        for raw in ("13812345678", "zhangsan@example.com", "110101199001011234",
                    "6222021234567890123"):
            assert raw not in content

    async def test_reveal_returns_plaintext_and_writes_audit(self, env):
        admin_id, user_id, conversation_id = await self._seed(env)
        response = await env.get(
            f"/admin/conversations/{conversation_id}/events",
            env.admin_headers(admin_id),
            reveal="true",
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["reveal"] is True
        assert body["items"][0]["data"]["content"] == self.PII

        rows = await env.audit_rows()
        assert len(rows) == 1
        assert rows[0].action == "reveal"
        assert rows[0].admin_id == admin_id
        assert rows[0].target_user_id == user_id
        assert rows[0].conversation_id == conversation_id
        assert rows[0].detail["event_count"] == 1

    async def test_masked_read_does_not_audit(self, env):
        admin_id, _, conversation_id = await self._seed(env)
        await env.get(
            f"/admin/conversations/{conversation_id}/events",
            env.admin_headers(admin_id),
        )
        assert await env.audit_rows() == []

    async def test_event_filters(self, env):
        admin_id = await env.seed_admin()
        user_id = await env.seed_user(nickname="filters")
        conversation_id = await env.seed_conversation(user_id)
        await env.seed_event(
            conversation_id, seq=1, turn=1, data={"role": "user", "content": "一"},
        )
        await env.seed_event(
            conversation_id, seq=2, turn=2, data={"role": "user", "content": "二"},
        )

        headers = env.admin_headers(admin_id)
        after = await env.get(
            f"/admin/conversations/{conversation_id}/events", headers, after_seq=1,
        )
        assert [item["seq"] for item in after.json()["items"]] == [2]

        by_turn = await env.get(
            f"/admin/conversations/{conversation_id}/events", headers, turn=1,
        )
        assert [item["seq"] for item in by_turn.json()["items"]] == [1]


class TestMaskTextUnit:
    def test_phone(self):
        assert mask_text("联系我 13812345678 谢谢") == "联系我 138****5678 谢谢"

    def test_email(self):
        assert mask_text("zhangsan@example.com") == "z***@example.com"

    def test_id_card(self):
        assert mask_text("110101199001011234") == "110101********1234"

    def test_bank_card(self):
        assert mask_text("6222021234567890") == "6222********7890"

    def test_plain_text_untouched(self):
        assert mask_text("今天花了三十五元") == "今天花了三十五元"


# ---------------------------------------------------------------------------
# 8. request_id 反查
# ---------------------------------------------------------------------------

class TestRequestLookup:
    async def test_hits_the_right_conversation_and_turn(self, env):
        admin_id = await env.seed_admin()
        user_id = await env.seed_user(nickname="req")
        first = await env.seed_conversation(user_id, title="one")
        second = await env.seed_conversation(user_id, title="two")
        await env.seed_event(
            first, seq=1, turn=1, data={"role": "user", "content": "a"},
            request_id="req-hit",
        )
        await env.seed_event(
            first, seq=2, turn=1, event_type="assistant",
            data={"role": "assistant", "content": "b", "reasoning_content": ""},
            request_id="req-hit",
        )
        await env.seed_event(
            second, seq=1, turn=1, data={"role": "user", "content": "c"},
            request_id="req-other",
        )
        await env.seed_usage(
            user_id, model="m1", tokens=7, conversation_id=first,
            request_id="req-hit",
        )

        response = await env.get(
            "/admin/requests/req-hit", env.admin_headers(admin_id),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["request_id"] == "req-hit"
        assert len(body["conversations"]) == 1
        hit = body["conversations"][0]
        assert hit["conversation_id"] == str(first)
        assert hit["user_id"] == str(user_id)
        assert hit["turns"] == [1]
        assert hit["event_count"] == 2
        assert hit["first_seq"] == 1
        assert hit["last_seq"] == 2
        assert hit["event_types"] == ["assistant", "user"]
        assert [row["model"] for row in body["usage"]] == ["m1"]

    async def test_usage_only_request_still_resolves(self, env):
        """``ai_usage.request_id`` 兼容：事件列还没有值时也能反查到使用量。"""
        admin_id = await env.seed_admin()
        user_id = await env.seed_user(nickname="usage-only")
        conversation_id = await env.seed_conversation(user_id)
        await env.seed_usage(
            user_id, model="m2", tokens=3, conversation_id=conversation_id,
            request_id="req-usage-only",
        )

        response = await env.get(
            "/admin/requests/req-usage-only", env.admin_headers(admin_id),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["conversations"] == []
        assert body["usage"][0]["conversation_id"] == str(conversation_id)

    async def test_unknown_request_is_404(self, env):
        admin_id = await env.seed_admin()
        response = await env.get(
            "/admin/requests/req-missing", env.admin_headers(admin_id),
        )
        assert response.status_code == 404
        assert response.json()["code"] == "request_not_found"


# ---------------------------------------------------------------------------
# 9. 聚合统计
# ---------------------------------------------------------------------------

class TestUsageSummary:
    async def _seed(self, env: AdminEnv) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        admin_id = await env.seed_admin()
        user_a = await env.seed_user(nickname="ua")
        user_b = await env.seed_user(nickname="ub")
        await env.seed_usage(
            user_a, model="m1", tokens=100, cost=5,
            created_at=datetime(2026, 3, 1, 10, tzinfo=UTC),
        )
        await env.seed_usage(
            user_a, model="m2", tokens=200, cost=7,
            created_at=datetime(2026, 3, 2, 10, tzinfo=UTC),
        )
        await env.seed_usage(
            user_b, model="m1", tokens=50, cost=1,
            created_at=datetime(2026, 3, 2, 11, tzinfo=UTC),
        )
        return admin_id, user_a, user_b

    async def test_group_by_model(self, env):
        admin_id, _, _ = await self._seed(env)
        response = await env.get(
            "/admin/ai-usage/summary",
            env.admin_headers(admin_id),
            group_by="model",
        )
        assert response.status_code == 200, response.text
        items = {item["key"]: item for item in response.json()["items"]}
        assert items["m1"] == {
            "key": "m1", "requests": 2, "tokens": 150, "cost": 6, "users": 2,
        }
        assert items["m2"] == {
            "key": "m2", "requests": 1, "tokens": 200, "cost": 7, "users": 1,
        }

    async def test_group_by_user(self, env):
        admin_id, user_a, user_b = await self._seed(env)
        response = await env.get(
            "/admin/ai-usage/summary",
            env.admin_headers(admin_id),
            group_by="user",
        )
        items = {item["key"]: item for item in response.json()["items"]}
        assert items[str(user_a)]["requests"] == 2
        assert items[str(user_a)]["tokens"] == 300
        assert items[str(user_b)]["tokens"] == 50

    async def test_group_by_day_and_time_range(self, env):
        admin_id, _, _ = await self._seed(env)
        headers = env.admin_headers(admin_id)

        all_days = await env.get("/admin/ai-usage/summary", headers, group_by="day")
        keys = [item["key"] for item in all_days.json()["items"]]
        assert keys == ["2026-03-01", "2026-03-02"]

        ranged = await env.get(
            "/admin/ai-usage/summary",
            headers,
            group_by="day",
            time_from="2026-03-02T00:00:00+00:00",
        )
        items = ranged.json()["items"]
        assert len(items) == 1
        assert items[0]["key"] == "2026-03-02"
        assert items[0]["requests"] == 2
        assert items[0]["tokens"] == 250

    async def test_requires_admin(self, env):
        response = await env.client.get("/admin/ai-usage/summary")
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# 10. 只读事务
# ---------------------------------------------------------------------------

class _FakeSession:
    """只记录 ``execute`` 的最小替身——只测「是否发出了只读语句」。"""

    def __init__(self, dialect_name: str) -> None:
        dialect = type("Dialect", (), {"name": dialect_name})()
        bind = type("Bind", (), {"dialect": dialect})()
        self.bind = bind
        self.statements: list[str] = []

    async def execute(self, statement: Any) -> None:
        self.statements.append(str(statement))


class TestReadOnlyTransaction:
    async def test_postgres_sets_transaction_read_only(self):
        session = _FakeSession("postgresql")
        await begin_read_only(session)  # type: ignore[arg-type]
        assert session.statements == ["SET TRANSACTION READ ONLY"]

    async def test_sqlite_has_no_equivalent_statement(self):
        session = _FakeSession("sqlite")
        await begin_read_only(session)  # type: ignore[arg-type]
        assert session.statements == []
