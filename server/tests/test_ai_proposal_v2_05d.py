"""T-M7-05d: 分析之外四域的 v2 执行面（``/ai/validate`` + ``/ai/execute``）。

验收映射（P4 计划 §三 05d）：
- 导出 / 反馈 / 昵称 / 注销四条**服务端真的执行得了** → 各测试类
- **删号类工具强制确认且不绕过既有账号删除流程** → ``TestDeletionExecution``
  （走完两阶段、账号只到 FROZEN、撤销入口原样可用、7 天后的物理删除仍归外部脚本）
- 设备类 op（同意 / 同步触发）服务端不执行、有自己的拒绝码 → ``TestDeviceOps``
- 伪造 payload 一律拒（跨账本 404、别人的账本 403、形状不对 400）→ 各测试类
- **05d 的 op 都是副作用型，不登 sync change** → ``TestNoSyncChange``

用例全部走真实端点 + 真实 SQLite（临时文件库，不用 ``tmp_path``——本机 basetemp
权限已坏）。提案是手工构造的 v2 信封（客户端可达的那一面）。
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.testclient import TestClient


@pytest.fixture()
def v2_env():
    """TestClient + 隔离库 + **隔离的导出目录**（与 ``test_ai_proposal_v2_05c.py`` 同形）。

    导出目录必须隔离：``export_create`` 会真的往磁盘写文件，落到默认的系统临时
    目录会污染本机（那份文件要等下一次启动的 cleanup 才可能被清掉）。
    """
    from src.common.config import get_config
    from src.main import app
    from src.modules.ai.service import _ai_rate_limits
    from src.modules.auth.dependencies import _rate_limits

    _rate_limits.clear()
    _ai_rate_limits.clear()

    db_dir = Path(tempfile.mkdtemp(prefix="aozora_v2_05d_db_"))
    export_dir = Path(tempfile.mkdtemp(prefix="aozora_v2_05d_exports_"))
    config = get_config()
    original_export_dir = config.export_dir
    config.export_dir = str(export_dir)

    engine = create_async_engine(f"sqlite+aiosqlite:///{(db_dir / 'test.db').as_posix()}")
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _create_tables():
        from src.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_tables())

    async def _override_get_db_session():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides.clear()
    from src.common.database import get_db_session
    app.dependency_overrides[get_db_session] = _override_get_db_session

    with TestClient(app) as c:
        yield c, factory

    config.export_dir = original_export_dir
    asyncio.run(engine.dispose())
    shutil.rmtree(db_dir, ignore_errors=True)
    shutil.rmtree(export_dir, ignore_errors=True)
    app.dependency_overrides.clear()
    _rate_limits.clear()
    _ai_rate_limits.clear()


# ── 客户端与实体 ───────────────────────────────────────────────────────────


def _guest(env, device_id="v2-05d-device") -> dict:
    c, _ = env
    # 隐私同意先于一切同步：``/sync/push`` 对未同意的设备回 403 consent_required。
    consented = c.post("/consent", json={
        "device_id": device_id,
        "policy_version": "2026-01-01",
        "consented_at": "2026-01-01T00:00:00+00:00",
    })
    assert consented.status_code == 201, consented.text
    resp = c.post("/auth/guest", json={"device_id": device_id, "nickname": "05d-tester"})
    assert resp.status_code == 200
    data = resp.json()
    return {"id": data["user_id"], "token": data["tokens"]["access_token"]}


def _auth(user: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {user['token']}"}


def _v2(op: str, payload: dict, *, proposal_id: str | None = None):
    return {
        "contractVersion": 2,
        "proposalId": proposal_id or f"v2-{uuid.uuid4().hex[:12]}",
        "action": op,
        "confidence": 0.95,
        "requiresConfirmation": True,
        "message": "（测试提案）",
        "warnings": [],
        "payload": payload,
    }


def _validate(env, user, book_id, proposal):
    c, _ = env
    return c.post("/ai/validate", json={
        "proposal": proposal, "book_id": str(book_id),
    }, headers=_auth(user))


def _execute(env, user, book_id, proposal, token="confirm"):
    c, _ = env
    return c.post("/ai/execute", json={
        "proposal": proposal, "book_id": str(book_id), "confirmation_token": token,
    }, headers=_auth(user))


def _mk_book(c, user, name="日常") -> uuid.UUID:
    resp = c.post("/books", json={"name": name}, headers=_auth(user))
    assert resp.status_code == 201
    return uuid.UUID(resp.json()["id"])


def _rows(factory, model, **filters):
    async def _run():
        async with factory() as session:
            stmt = select(model)
            for key, value in filters.items():
                stmt = stmt.where(getattr(model, key) == value)
            return list((await session.execute(stmt)).scalars().all())
    return asyncio.run(_run())


def _uid(user: dict) -> uuid.UUID:
    return uuid.UUID(user["id"])


# ── 导出：真的建任务、真的生成文件 ─────────────────────────────────────────


class TestExportExecution:
    def test_creating_an_export_writes_the_row_and_the_file(self, v2_env):
        from src.models.export import Export, ExportStatus

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        resp = _execute(v2_env, user, book_id, _v2(
            "export_create", {"bookId": str(book_id), "format": "csv"},
        ))
        assert resp.status_code == 200, resp.text

        rows = _rows(factory, Export, user_id=_uid(user))
        assert len(rows) == 1
        assert rows[0].status == ExportStatus.COMPLETED
        assert rows[0].file_size and rows[0].file_size > 0
        assert Path(rows[0].file_path).exists(), "文件要真的落在磁盘上"
        # 回执带的是这次 op 影响的实体 id（05b 起的口径）
        assert resp.json()["transaction_ids"] == [str(rows[0].id)]

    def test_a_foreign_book_id_is_refused_by_the_authorizer(self, v2_env):
        """**结构通过不是执行许可**：别人的账本 id 在授权面就被拒。"""
        from src.models.export import Export

        c, factory = v2_env
        user = _guest(v2_env, device_id="v2-05d-owner")
        book_id = _mk_book(c, user)
        intruder = _guest(v2_env, device_id="v2-05d-intruder")

        resp = _execute(v2_env, intruder, book_id, _v2(
            "export_create", {"bookId": str(book_id), "format": "json"},
        ))
        assert resp.status_code == 403
        assert resp.json()["code"] == "ai_proposal_rejected"
        assert _rows(factory, Export, user_id=_uid(intruder)) == []

    def test_exporting_another_of_my_books_is_refused(self, v2_env):
        """授权只校验「这本书属于本人」，跨账本的收窄在执行体里（与交易同口径）。"""
        from src.models.export import Export

        c, factory = v2_env
        user = _guest(v2_env)
        book_a = _mk_book(c, user, name="日常")
        book_b = _mk_book(c, user, name="旅行")

        resp = _execute(v2_env, user, book_a, _v2(
            "export_create", {"bookId": str(book_b), "format": "json"},
        ))
        assert resp.status_code == 404
        assert _rows(factory, Export, user_id=_uid(user)) == []

    def test_an_unknown_format_is_a_400(self, v2_env):
        c, _ = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        resp = _execute(v2_env, user, book_id, _v2(
            "export_create", {"bookId": str(book_id), "format": "xlsx"},
        ))
        assert resp.status_code == 400
        assert resp.json()["code"] == "ai_invalid_payload"


# ── 反馈：落库、设备信息留空 ───────────────────────────────────────────────


class TestFeedbackExecution:
    def test_feedback_is_persisted_with_a_hashed_user_id(self, v2_env):
        from src.models.feedback import Feedback

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        resp = _execute(v2_env, user, book_id, _v2("feedback_submit", {
            "category": "bug", "content": "预算页点进去是白屏", "contact": "a@b.c",
        }))
        assert resp.status_code == 200, resp.text

        rows = _rows(factory, Feedback)
        assert len(rows) == 1
        row = rows[0]
        assert row.content == "预算页点进去是白屏"
        assert row.category == "bug"
        assert row.contact == "a@b.c"
        # 与 REST 路径同口径：只存 sha256(user_id)[:16]，原始 id 绝不落库
        expected = hashlib.sha256(str(_uid(user)).encode()).hexdigest()[:16]
        assert row.user_id_hash == expected

    def test_device_metadata_is_left_empty_not_faked(self, v2_env):
        """``/ai/agent`` 的请求里没有设备标识——留空是诚实的缺失，编一个才是掺假。"""
        from src.models.feedback import Feedback

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        _execute(v2_env, user, book_id, _v2("feedback_submit", {
            "category": "other", "content": "随便说一句",
        }))
        row = _rows(factory, Feedback)[0]
        assert row.app_version == ""
        assert row.android_version == ""
        assert row.device_model == ""

    def test_a_missing_content_is_a_400(self, v2_env):
        c, _ = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        resp = _execute(v2_env, user, book_id, _v2("feedback_submit", {"category": "bug"}))
        assert resp.status_code == 400
        assert resp.json()["code"] == "ai_invalid_payload"


# ── 昵称 ───────────────────────────────────────────────────────────────────


class TestProfileExecution:
    def test_the_nickname_is_actually_changed(self, v2_env):
        c, _ = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        resp = _execute(v2_env, user, book_id, _v2(
            "auth_update_profile", {"nickname": "小王"},
        ))
        assert resp.status_code == 200, resp.text
        assert c.get("/me", headers=_auth(user)).json()["nickname"] == "小王"

    def test_a_blank_nickname_is_a_400(self, v2_env):
        """形状校验走 auth 模块自己那份 ``UpdateProfileRequest``（strip 后非空）。"""
        c, _ = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        for bad in ("   ", ""):
            resp = _execute(v2_env, user, book_id, _v2(
                "auth_update_profile", {"nickname": bad},
            ))
            assert resp.status_code == 400, bad
            assert resp.json()["code"] == "ai_invalid_payload", bad


# ── 注销：最高危那条路 ─────────────────────────────────────────────────────


class TestDeletionExecution:
    def test_deletion_freezes_the_account_through_both_steps(self, v2_env):
        """**不绕过既有流程**：服务端内部走完「申请令牌 → 用令牌确认」两步。

        落点是 FROZEN（7 天冷静期），**不是**物理删除——真正的删除仍归
        ``infra/cleanup/account-deletion.py``（冻结满 7 天后由外部调度执行）。
        """
        from src.models.deletion import DeactivationToken
        from src.models.user import User, UserStatus

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        resp = _execute(v2_env, user, book_id, _v2("account_deletion_request", {}))
        assert resp.status_code == 200, resp.text

        row = _rows(factory, User, id=_uid(user))[0]
        assert row.status == UserStatus.FROZEN
        assert row.frozen_at is not None
        assert row.deletion_requested_at is not None

        # 两阶段都真的走过：令牌存在且**已被消费**（不是跳过申请直接冻结）
        tokens = _rows(factory, DeactivationToken, user_id=_uid(user))
        assert len(tokens) == 1
        assert tokens[0].used_at is not None

    def test_the_cancel_path_stays_available(self, v2_env):
        """撤销入口是「7 天冷静期」的另一半——AI 注销之后它必须原样可用。"""
        from src.models.user import User, UserStatus

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        _execute(v2_env, user, book_id, _v2("account_deletion_request", {}))

        assert c.post("/me/deletion-cancel", headers=_auth(user)).status_code == 204
        row = _rows(factory, User, id=_uid(user))[0]
        assert row.status == UserStatus.ACTIVE
        assert row.frozen_at is None

    def test_deleting_twice_is_refused_at_the_endpoint(self, v2_env):
        """已经在冷静期里的账号再申请一次 → **端点层 403**（``_require_active``）。

        比服务层的 409 ``deletion_pending`` 更早：冻结态的账号在 ``/ai/*`` 的每个
        端点上都被挡下，根本走不到 ``request_deletion_token``。这条用例钉的是
        「注销之后 AI 通道整体不可用」这个更大的事实。
        """
        c, _ = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        first = _execute(v2_env, user, book_id, _v2("account_deletion_request", {}))
        assert first.status_code == 200
        second = _execute(v2_env, user, book_id, _v2("account_deletion_request", {}))
        assert second.status_code == 403

    def test_account_delete_can_never_be_executed_through_ai(self, v2_env):
        """``account_delete`` 的 payload 要一次性 ``deletionToken``——凭据不进
        模型上下文，所以这个 op 在 AI 路径上**刻意不接线**（两道门都拒）。"""
        c, _ = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        resp = _execute(v2_env, user, book_id, _v2(
            "account_delete", {"deletionToken": "tok-1"},
        ))
        assert resp.status_code == 403
        assert resp.json()["code"] == "ai_proposal_rejected"


# ── 设备类 op：服务端不执行 ────────────────────────────────────────────────


class TestDeviceOps:
    def test_consent_and_sync_trigger_are_refused_on_the_server(self, v2_env):
        """执行位置在设备：服务端没有设备身份（同意按 device_id 存），也没有主动
        触发同步的通道。两者各有自己的拒绝码，与「不支持」区分开。"""
        c, _ = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        for op, payload in (("consent_record", {}), ("sync_trigger", {})):
            resp = _execute(v2_env, user, book_id, _v2(op, payload))
            assert resp.status_code == 400, op
            assert resp.json()["code"] == "ai_device_op_not_executable", op


# ── 不登 sync change ───────────────────────────────────────────────────────


class TestNoSyncChange:
    def test_none_of_the_05d_ops_writes_a_sync_change(self, v2_env):
        """05d 的 op 动的都不是可同步实体（``sync/schemas.py`` 的八类白名单之外），
        登一条 sync change 只会让设备 pull 到一个它不认识的分派键。"""
        from src.models.sync import SyncChange

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)
        before = len(_rows(factory, SyncChange, user_id=_uid(user)))

        for op, payload in (
            ("export_create", {"bookId": str(book_id), "format": "json"}),
            ("feedback_submit", {"category": "bug", "content": "白屏"}),
            ("auth_update_profile", {"nickname": "小王"}),
        ):
            resp = _execute(v2_env, user, book_id, _v2(op, payload))
            assert resp.status_code == 200, (op, resp.text)

        assert len(_rows(factory, SyncChange, user_id=_uid(user))) == before
