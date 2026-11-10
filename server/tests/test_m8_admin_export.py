"""T-M8-09：导出（三种范围 × 两种内容）与导出审计。

验收映射（`docs/milestones/T-M8-admin-console-plan.md` §三 8-09）：

- 三种范围（筛选结果 / 全库 / 单用户）× 两种内容（明文 / 打码）各走通
  → :class:`TestScopeFormatMatrix`
- ``masked`` 输出手机号 / 邮箱 / 身份证 / 银行卡已脱敏，``plain`` 为原文
  → :class:`TestMasking`
- 每次导出产生一条 ``admin_audit_log(action="export")``，detail 字段完整
  → :class:`TestAudit`
- 超过 100,000 事件上限 → 422 ``export_too_large``          → :class:`TestLimit`
- 非管理员 token → 401                                      → :class:`TestAuthBoundary`
- 流式响应可被完整读取，按行 JSON 可解析                     → :class:`TestStreaming`

与 ``test_m8_admin_api.py`` 同构：文件 SQLite + 真实 ASGI 应用，无网络、无模型。
临时目录用 ``tempfile.mkdtemp``（本机 pytest basetemp 权限已坏的仓库约定）。
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from src.common.config import Settings, reset_config, set_config
from src.models.admin import AdminAccount, AdminAuditLog
from src.models.ai import AIConversation, AIConversationEvent
from src.models.user import User, UserStatus
from src.modules.admin import routes as admin_routes
from src.modules.admin.dependencies import create_admin_token, hash_admin_password
from src.modules.auth.utils import create_access_token

PASSWORD = "correct horse battery staple"
ADMIN_NAME = "ops"

#: 造数用的四类结构化 PII（脱敏口径见 ``src/modules/admin/schemas.py``）。
PHONE = "13812341234"
EMAIL = "alice@example.com"
ID_CARD = "110101199001011234"
BANK_CARD = "6222021234567890"
PII_TEXT = f"手机 {PHONE} 邮箱 {EMAIL} 身份证 {ID_CARD} 银行卡 {BANK_CARD}"

MASKED_PHONE = "138****1234"
MASKED_EMAIL = "a***@example.com"
MASKED_ID_CARD = "110101********1234"
MASKED_BANK_CARD = "6222********7890"

USER_SCOPE_EVENTS = 3      # user A：conv_a1（2）+ conv_a2（1）
USER_SCOPE_CONVERSATIONS = 2
ALL_EVENTS = 5             # + conv_b1（2）
ALL_CONVERSATIONS = 3
FILTERED_EVENTS = 2        # q="外卖" 只命中 conv_a1
FILTERED_CONVERSATIONS = 1


# ---------------------------------------------------------------------------
# 环境与播种
# ---------------------------------------------------------------------------

class ExportEnv:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)

    # -- HTTP ----------------------------------------------------------

    def admin_headers(self, admin_id: uuid.UUID) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_admin_token(admin_id)}"}

    def user_headers(self, user_id: uuid.UUID) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(str(user_id))}"}

    async def export(
        self, body: dict[str, Any], headers: dict[str, str],
    ) -> httpx.Response:
        return await self.client.post("/admin/export", json=body, headers=headers)

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

    async def seed_user(self, *, nickname: str) -> uuid.UUID:
        async with self.factory() as session:
            user = User(
                id=uuid.uuid4(),
                nickname=nickname,
                status=UserStatus.GUEST,
            )
            session.add(user)
            await session.commit()
            return user.id

    async def seed_conversation(
        self,
        user_id: uuid.UUID,
        *,
        title: str,
        search_text: str,
        last_active_at: datetime,
        archived_at: datetime | None = None,
    ) -> uuid.UUID:
        async with self.factory() as session:
            conversation = AIConversation(
                id=uuid.uuid4(),
                user_id=user_id,
                title=title,
                search_text=search_text,
                last_active_at=last_active_at,
                archived_at=archived_at,
            )
            session.add(conversation)
            await session.commit()
            return conversation.id

    async def seed_event(
        self,
        conversation_id: uuid.UUID,
        *,
        seq: int,
        turn: int,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        async with self.factory() as session:
            session.add(AIConversationEvent(
                conversation_id=conversation_id,
                seq=seq,
                turn=turn,
                type=event_type,
                data=data,
                time=datetime.now(UTC),
                ignorable=event_type not in {"user", "assistant", "tool"},
            ))
            await session.commit()

    async def audit_rows(self) -> list[AdminAuditLog]:
        async with self.factory() as session:
            return list((await session.scalars(
                select(AdminAuditLog)
                .where(AdminAuditLog.action == "export")
                .order_by(AdminAuditLog.created_at)
            )).all())


def _lines(response: httpx.Response) -> list[dict[str, Any]]:
    """按行解析 NDJSON。"""
    return [
        json.loads(line)
        for line in response.text.splitlines()
        if line.strip()
    ]


@pytest_asyncio.fixture()
async def env():
    from src.common.database import get_engine, get_session_factory, reset_engine
    from src.models.base import Base
    from src.modules.auth.dependencies import _rate_limits

    reset_config()
    reset_engine()
    _rate_limits.clear()

    db_dir = Path(tempfile.mkdtemp(prefix="aozora_test_m8_admin_export_"))
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
        yield ExportEnv(
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


@pytest_asyncio.fixture()
async def seeded(env: ExportEnv):
    """一个小型数据集：两个用户、三个会话（含一个归档）、五条事件。"""
    admin_id = await env.seed_admin()
    user_a = await env.seed_user(nickname="用户A")
    user_b = await env.seed_user(nickname="用户B")

    base = datetime.now(UTC) - timedelta(days=3)
    conv_a1 = await env.seed_conversation(
        user_a,
        title=f"外卖会话 {PHONE}",
        search_text="帮我记一笔外卖 30 元",
        last_active_at=base,
    )
    conv_a2 = await env.seed_conversation(
        user_a,
        title="归档会话",
        search_text="存档的历史对话",
        last_active_at=base + timedelta(hours=1),
        archived_at=base + timedelta(hours=2),
    )
    conv_b1 = await env.seed_conversation(
        user_b,
        title="打车会话",
        search_text="打车花了 50",
        last_active_at=base + timedelta(days=1),
    )

    await env.seed_event(
        conv_a1, seq=1, turn=1, event_type="user", data={"content": PII_TEXT},
    )
    await env.seed_event(
        conv_a1, seq=2, turn=1, event_type="assistant",
        data={"content": "已记下", "reasoning_content": ""},
    )
    await env.seed_event(
        conv_a2, seq=1, turn=1, event_type="user", data={"content": "旧会话"},
    )
    await env.seed_event(
        conv_b1, seq=1, turn=1, event_type="user", data={"content": PII_TEXT},
    )
    await env.seed_event(
        conv_b1, seq=2, turn=1, event_type="error",
        data={"code": "provider_error", "message": "模型暂不可用"},
    )

    return {
        "admin_id": admin_id,
        "headers": env.admin_headers(admin_id),
        "user_a": user_a,
        "user_b": user_b,
        "conversations": {"a1": conv_a1, "a2": conv_a2, "b1": conv_b1},
    }


# ---------------------------------------------------------------------------
# 1. 三种范围 × 两种内容（事件流）
# ---------------------------------------------------------------------------

class TestScopeFormatMatrix:
    @pytest.mark.parametrize(
        ("name", "scope_body", "expected_events", "expected_conversations"),
        [
            (
                "all-plain",
                {"scope": "all", "format": "plain"},
                ALL_EVENTS, ALL_CONVERSATIONS,
            ),
            (
                "all-masked",
                {"scope": "all", "format": "masked"},
                ALL_EVENTS, ALL_CONVERSATIONS,
            ),
            (
                "user-plain",
                {"scope": "user", "format": "plain", "user_id": "USER_A"},
                USER_SCOPE_EVENTS, USER_SCOPE_CONVERSATIONS,
            ),
            (
                "user-masked",
                {"scope": "user", "format": "masked", "user_id": "USER_A"},
                USER_SCOPE_EVENTS, USER_SCOPE_CONVERSATIONS,
            ),
            (
                "filtered-plain",
                {"scope": "filtered", "format": "plain", "q": "外卖"},
                FILTERED_EVENTS, FILTERED_CONVERSATIONS,
            ),
            (
                "filtered-masked",
                {"scope": "filtered", "format": "masked", "q": "外卖"},
                FILTERED_EVENTS, FILTERED_CONVERSATIONS,
            ),
        ],
        ids=[
            "all-plain", "all-masked", "user-plain",
            "user-masked", "filtered-plain", "filtered-masked",
        ],
    )
    async def test_combination(
        self,
        env: ExportEnv,
        seeded: dict[str, Any],
        name: str,
        scope_body: dict[str, Any],
        expected_events: int,
        expected_conversations: int,
    ):
        body = {"content": "events", **scope_body}
        if body.get("user_id") == "USER_A":
            body["user_id"] = str(seeded["user_a"])

        response = await env.export(body, seeded["headers"])
        assert response.status_code == 200, response.text
        items = _lines(response)

        assert len(items) == expected_events
        assert {item["kind"] for item in items} == {"event"}
        assert len({item["conversation_id"] for item in items}) == expected_conversations

        conversation_ids = set(seeded["conversations"].values())
        for item in items:
            assert uuid.UUID(item["conversation_id"]) in conversation_ids
            assert set(item) == {
                "kind", "conversation_id", "seq", "turn", "type", "time",
                "request_id", "data",
            }

        # 每个组合都恰好写一条导出审计，条数与实际写出的一致。
        audits = await env.audit_rows()
        assert len(audits) == 1
        detail = audits[0].detail
        assert detail["scope"] == body["scope"]
        assert detail["format"] == body["format"]
        assert detail["content"] == "events"
        assert detail["events"] == expected_events
        assert detail["conversations"] == expected_conversations
        assert detail["completed"] is True

    async def test_archived_excluded_from_filtered_by_default(
        self, env: ExportEnv, seeded: dict[str, Any],
    ):
        body = {"scope": "filtered", "format": "masked", "content": "events"}
        items = _lines(await env.export(body, seeded["headers"]))
        exported = {item["conversation_id"] for item in items}
        assert str(seeded["conversations"]["a2"]) not in exported

        body["include_archived"] = True
        items = _lines(await env.export(body, seeded["headers"]))
        exported = {item["conversation_id"] for item in items}
        assert str(seeded["conversations"]["a2"]) in exported

    async def test_single_conversation_filter(self, env: ExportEnv, seeded: dict[str, Any]):
        """回放页的「导出本会话」：scope=filtered + conversation_id。"""
        body = {
            "scope": "filtered",
            "format": "masked",
            "content": "events",
            "conversation_id": str(seeded["conversations"]["b1"]),
        }
        items = _lines(await env.export(body, seeded["headers"]))
        assert len(items) == 2
        assert {item["conversation_id"] for item in items} == {
            str(seeded["conversations"]["b1"]),
        }

        audits = await env.audit_rows()
        assert len(audits) == 1
        assert audits[0].conversation_id == seeded["conversations"]["b1"]
        assert audits[0].detail["filters"]["conversation_id"] == str(
            seeded["conversations"]["b1"],
        )


# ---------------------------------------------------------------------------
# 2. 脱敏
# ---------------------------------------------------------------------------

class TestMasking:
    async def test_plain_returns_original(self, env: ExportEnv, seeded: dict[str, Any]):
        response = await env.export(
            {"scope": "all", "format": "plain", "content": "events"},
            seeded["headers"],
        )
        text = response.text
        for raw in (PHONE, EMAIL, ID_CARD, BANK_CARD):
            assert raw in text

    async def test_masked_redacts_all_four_pii_kinds(
        self, env: ExportEnv, seeded: dict[str, Any],
    ):
        response = await env.export(
            {"scope": "all", "format": "masked", "content": "events"},
            seeded["headers"],
        )
        text = response.text
        for raw in (PHONE, EMAIL, ID_CARD, BANK_CARD):
            assert raw not in text
        for masked in (MASKED_PHONE, MASKED_EMAIL, MASKED_ID_CARD, MASKED_BANK_CARD):
            assert masked in text


# ---------------------------------------------------------------------------
# 3. 审计
# ---------------------------------------------------------------------------

class TestAudit:
    async def test_one_audit_per_export_with_full_detail(
        self, env: ExportEnv, seeded: dict[str, Any],
    ):
        await env.export(
            {"scope": "all", "format": "plain", "content": "events"},
            seeded["headers"],
        )
        await env.export(
            {
                "scope": "user",
                "format": "masked",
                "content": "events",
                "user_id": str(seeded["user_a"]),
            },
            seeded["headers"],
        )

        audits = await env.audit_rows()
        assert len(audits) == 2

        all_row, user_row = audits
        for row in audits:
            assert row.admin_id == seeded["admin_id"]
            assert row.action == "export"
            assert set(row.detail) >= {
                "scope", "format", "content", "events", "conversations",
                "duration_ms", "filters",
            }
            assert isinstance(row.detail["duration_ms"], int)
            assert row.detail["duration_ms"] >= 0

        assert all_row.target_user_id is None
        assert all_row.detail["filters"]["user_id"] is None
        assert user_row.target_user_id == seeded["user_a"]
        assert user_row.detail["filters"]["user_id"] == str(seeded["user_a"])

    async def test_no_audit_when_request_rejected(
        self, env: ExportEnv, seeded: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setattr(admin_routes, "MAX_EXPORT_EVENTS", 2)
        response = await env.export(
            {"scope": "all", "format": "plain", "content": "events"},
            seeded["headers"],
        )
        assert response.status_code == 422
        assert await env.audit_rows() == []


# ---------------------------------------------------------------------------
# 4. 上限
# ---------------------------------------------------------------------------

class TestLimit:
    async def test_over_limit_is_422_export_too_large(
        self, env: ExportEnv, seeded: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setattr(admin_routes, "MAX_EXPORT_EVENTS", 4)
        response = await env.export(
            {"scope": "all", "format": "masked", "content": "events"},
            seeded["headers"],
        )
        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "export_too_large"
        assert "batches" in body["message"]

    async def test_at_limit_is_allowed(
        self, env: ExportEnv, seeded: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setattr(admin_routes, "MAX_EXPORT_EVENTS", ALL_EVENTS)
        response = await env.export(
            {"scope": "all", "format": "masked", "content": "events"},
            seeded["headers"],
        )
        assert response.status_code == 200
        assert len(_lines(response)) == ALL_EVENTS


# ---------------------------------------------------------------------------
# 5. 鉴权与参数校验
# ---------------------------------------------------------------------------

class TestAuthBoundary:
    async def test_user_token_is_401(self, env: ExportEnv, seeded: dict[str, Any]):
        response = await env.export(
            {"scope": "all", "format": "masked", "content": "events"},
            env.user_headers(seeded["user_a"]),
        )
        assert response.status_code == 401
        assert response.json()["code"] == "invalid_admin_token"
        assert await env.audit_rows() == []

    async def test_missing_token_is_401(self, env: ExportEnv, seeded: dict[str, Any]):
        response = await env.client.post(
            "/admin/export",
            json={"scope": "all", "format": "masked", "content": "events"},
        )
        assert response.status_code == 401
        assert response.json()["code"] == "missing_admin_token"

    async def test_user_scope_requires_user_id(
        self, env: ExportEnv, seeded: dict[str, Any],
    ):
        response = await env.export(
            {"scope": "user", "format": "masked", "content": "events"},
            seeded["headers"],
        )
        assert response.status_code == 422
        assert response.json()["code"] == "user_required"

    async def test_short_q_is_rejected(self, env: ExportEnv, seeded: dict[str, Any]):
        response = await env.export(
            {"scope": "filtered", "format": "masked", "content": "events", "q": "外"},
            seeded["headers"],
        )
        assert response.status_code == 422
        assert response.json()["code"] == "query_too_short"


# ---------------------------------------------------------------------------
# 6. 流式响应可完整读取
# ---------------------------------------------------------------------------

class TestStreaming:
    async def test_full_body_is_valid_ndjson(self, env: ExportEnv, seeded: dict[str, Any]):
        async with env.client.stream(
            "POST",
            "/admin/export",
            json={"scope": "all", "format": "masked", "content": "events"},
            headers=seeded["headers"],
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"] == "application/x-ndjson"
            disposition = response.headers["content-disposition"]
            assert re.fullmatch(
                r'attachment; filename="aozora-export-\d{8}T\d{6}Z\.jsonl"',
                disposition,
            )
            chunks = [chunk async for chunk in response.aiter_bytes()]

        body = b"".join(chunks).decode("utf-8")
        lines = [line for line in body.splitlines() if line.strip()]
        assert len(lines) == ALL_EVENTS
        for line in lines:
            parsed = json.loads(line)
            assert parsed["kind"] == "event"

    async def test_keyset_batches_cover_all_rows(
        self, env: ExportEnv, seeded: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
    ):
        """批量小于数据集时（多批拼接）无重复、无遗漏——导出靠这个分批避免爆内存。"""
        monkeypatch.setattr(admin_routes, "EXPORT_BATCH_SIZE", 2)
        response = await env.export(
            {"scope": "all", "format": "masked", "content": "events"},
            seeded["headers"],
        )
        items = _lines(response)
        assert len(items) == ALL_EVENTS
        assert len({(item["conversation_id"], item["seq"]) for item in items}) == ALL_EVENTS
        assert len({item["conversation_id"] for item in items}) == ALL_CONVERSATIONS

        audits = await env.audit_rows()
        assert audits[0].detail["events"] == ALL_EVENTS
        assert audits[0].detail["conversations"] == ALL_CONVERSATIONS

    async def test_conversations_content(self, env: ExportEnv, seeded: dict[str, Any]):
        """``content=conversations``：导出会话元信息，标题同样按 PII 打码。"""
        response = await env.export(
            {"scope": "all", "format": "masked", "content": "conversations"},
            seeded["headers"],
        )
        assert response.status_code == 200
        items = _lines(response)
        assert len(items) == ALL_CONVERSATIONS
        assert {item["kind"] for item in items} == {"conversation"}
        assert all(PHONE not in item["title"] for item in items)
        assert any(MASKED_PHONE in item["title"] for item in items)

        audits = await env.audit_rows()
        assert len(audits) == 1
        assert audits[0].detail["content"] == "conversations"
        assert audits[0].detail["events"] == 0
        assert audits[0].detail["conversations"] == ALL_CONVERSATIONS
