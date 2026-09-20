"""T-M7-05b: ActionProposal v2 的服务端执行面（``/ai/validate`` + ``/ai/execute``）。

05a 留下的三件事在这里闭环（其交接记录 §六 条目 ①）：
- **校验器分流** —— ``AIService.validate_proposal`` 按 ``contractVersion`` 走两条车道；
- **execute 放行** —— v2 的 op 由 ``proposal_ops.EXECUTORS`` 执行，未登记的 op 一律 400；
- **payload 级授权** —— ``authorization.authorize_payload`` 在组装、``/ai/validate``、
  ``/ai/execute`` 三处同一个函数（结构通过不是执行许可）。

验收映射（P4 计划 §三 05b）：写工具一律走审批门（02e 路径不放开）、写操作的
sync 登记（T14-8 口径：服务端权威直写必须自己登 sync change）、最高危 op
（``account_delete``）**不在执行面里**。

用例全部走真实端点 + 真实 SQLite（临时文件库，不用 ``tmp_path``——本机 basetemp
权限已坏）。提案是**手工构造的 v2 信封**（客户端可达的那一面）：这些用例拷问的
正是「一份客户端伪造的 v2 提案能做什么」。
"""

from __future__ import annotations

import asyncio
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
    """TestClient + 隔离库（与 ``test_ai_proposal_execution.py`` 同形）。"""
    from src.main import app
    from src.modules.ai.service import _ai_rate_limits
    from src.modules.auth.dependencies import _rate_limits

    _rate_limits.clear()
    _ai_rate_limits.clear()

    db_dir = Path(tempfile.mkdtemp(prefix="aozora_v2_db_"))
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

    asyncio.run(engine.dispose())
    shutil.rmtree(db_dir, ignore_errors=True)
    app.dependency_overrides.clear()
    _rate_limits.clear()
    _ai_rate_limits.clear()


def _guest(env) -> dict:
    c, _ = env
    resp = c.post("/auth/guest", json={"device_id": "v2-device", "nickname": "v2-tester"})
    assert resp.status_code == 200
    data = resp.json()
    return {"id": data["user_id"], "token": data["tokens"]["access_token"]}


def _auth(user: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {user['token']}"}


def _v2(op: str, payload: dict, *, proposal_id: str | None = None, confidence: float = 0.95):
    """一份 v2 信封（``contractVersion: 2`` 是在场即判定车道的那个键）。"""
    return {
        "contractVersion": 2,
        "proposalId": proposal_id or f"v2-{uuid.uuid4().hex[:12]}",
        "action": op,
        "confidence": confidence,
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


def _mk_category(c, user, kind="expense", name="餐饮") -> uuid.UUID:
    resp = c.post("/categories", json={"name": name, "kind": kind}, headers=_auth(user))
    assert resp.status_code == 201
    return uuid.UUID(resp.json()["id"])


def _mk_account(c, user, name="招行卡") -> uuid.UUID:
    resp = c.post("/accounts", json={"name": name}, headers=_auth(user))
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


# ── 执行面：记账域 ─────────────────────────────────────────────────────────


class TestV2TransactionOps:
    def test_transaction_update_changes_the_row_and_registers_a_sync_change(self, v2_env):
        from src.models.ledger import Transaction
        from src.models.sync import SyncChange

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)
        category_id = _mk_category(c, user)

        created = c.post("/transactions", json={
            "book_id": str(book_id), "type": "expense", "amount_cents": 1250,
            "category_id": str(category_id), "note": "午饭", "biz_date": "2026-09-18",
            "occurred_at": "2026-09-18T12:00:00+00:00",
        }, headers=_auth(user))
        assert created.status_code == 201
        tx = created.json()

        proposal = _v2("transaction_update", {
            "transactionId": tx["id"], "version": tx["version"],
            "amountCents": 1500, "note": "午饭（改）",
        })
        assert _validate(v2_env, user, book_id, proposal).json()["valid"] is True

        resp = _execute(v2_env, user, book_id, proposal)
        assert resp.status_code == 200
        assert resp.json()["transaction_ids"] == [tx["id"]]

        rows = _rows(factory, Transaction, id=uuid.UUID(tx["id"]))
        assert rows[0].amount_cents == 1500 and rows[0].note == "午饭（改）"
        assert rows[0].version == tx["version"] + 1

        changes = _rows(factory, SyncChange, entity_id=uuid.UUID(tx["id"]))
        assert [(str(ch.operation), ch.version) for ch in changes] == [
            ("update", tx["version"] + 1),
        ], "服务端权威直写必须自己登 sync change（T14-8）"

    def test_transaction_update_rejects_a_stale_version(self, v2_env):
        """乐观并发：提案产出后目标被改过 → 409，而不是把那次改动覆盖掉。"""
        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)
        category_id = _mk_category(c, user)
        created = c.post("/transactions", json={
            "book_id": str(book_id), "type": "expense", "amount_cents": 1250,
            "category_id": str(category_id), "note": "午饭", "biz_date": "2026-09-18",
            "occurred_at": "2026-09-18T12:00:00+00:00",
        }, headers=_auth(user)).json()

        stale = _v2("transaction_update", {
            "transactionId": created["id"], "version": created["version"] - 1 if created["version"] > 1 else 99,
            "amountCents": 1500,
        })
        resp = _execute(v2_env, user, book_id, stale)
        assert resp.status_code == 409

    def test_transaction_delete_soft_deletes_and_registers_a_sync_change(self, v2_env):
        from src.models.ledger import Transaction
        from src.models.sync import SyncChange

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)
        category_id = _mk_category(c, user)
        since = _rows(factory, SyncChange)
        created = c.post("/transactions", json={
            "book_id": str(book_id), "type": "expense", "amount_cents": 3000,
            "category_id": str(category_id), "note": "打车", "biz_date": "2026-09-17",
            "occurred_at": "2026-09-17T12:00:00+00:00",
        }, headers=_auth(user)).json()

        resp = _execute(v2_env, user, book_id, _v2(
            "transaction_delete", {"transactionId": created["id"]},
        ))
        assert resp.status_code == 200

        row = _rows(factory, Transaction, id=uuid.UUID(created["id"]))[0]
        assert row.deleted_at is not None
        changes = _rows(factory, SyncChange, entity_id=uuid.UUID(created["id"]))
        assert str(changes[-1].operation) in ("delete", "SyncOperation.DELETE")
        assert len(changes) > len([ch for ch in since if ch.entity_id == uuid.UUID(created["id"])])

    def test_transaction_add_rides_the_shared_transaction_path(self, v2_env):
        """v2 的 ``transaction_add`` 与 v1 的 ``add_tx`` 是同一条写路径。"""
        from src.models.ledger import Transaction

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)
        category_id = _mk_category(c, user)

        proposal = _v2("transaction_add", {"transactions": [{
            "type": "expense", "amountCents": 3500, "categoryId": str(category_id),
            "bizDate": "2026-09-06", "note": "午餐",
        }]})
        resp = _execute(v2_env, user, book_id, proposal)
        assert resp.status_code == 200
        assert len(resp.json()["transaction_ids"]) == 1
        assert len(_rows(factory, Transaction, book_id=book_id)) == 1


# ── 执行面：账户账本域 ─────────────────────────────────────────────────────


class TestV2LedgerOps:
    def test_category_create_update_archive(self, v2_env):
        from src.models.ledger import Category

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        create = _execute(v2_env, user, book_id, _v2("category_create", {
            "name": "夜宵", "kind": "expense", "bookId": str(book_id), "parentId": None,
        }))
        assert create.status_code == 200
        new_id = uuid.UUID(create.json()["transaction_ids"][0])
        row = _rows(factory, Category, id=new_id)[0]
        assert (row.name, row.kind, row.book_id) == ("夜宵", "expense", book_id)

        update = _execute(v2_env, user, book_id, _v2("category_update", {
            "categoryId": str(new_id), "version": row.version, "name": "宵夜",
        }))
        assert update.status_code == 200
        assert _rows(factory, Category, id=new_id)[0].name == "宵夜"

        archive = _execute(v2_env, user, book_id, _v2(
            "category_archive", {"categoryId": str(new_id)},
        ))
        assert archive.status_code == 200
        archived = _rows(factory, Category, id=new_id)[0]
        assert archived.archived_at is not None
        assert archived.version > row.version

    def test_account_create_update_archive(self, v2_env):
        from src.models.ledger import Account

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        create = _execute(v2_env, user, book_id, _v2(
            "account_create", {"name": "微信零钱"},
        ))
        assert create.status_code == 200
        acc_id = uuid.UUID(create.json()["transaction_ids"][0])

        account = _rows(factory, Account, id=acc_id)[0]
        update = _execute(v2_env, user, book_id, _v2("account_update", {
            "accountId": str(acc_id), "version": account.version, "name": "零钱",
        }))
        assert update.status_code == 200
        assert _rows(factory, Account, id=acc_id)[0].name == "零钱"

        archive = _execute(v2_env, user, book_id, _v2(
            "account_archive", {"accountId": str(acc_id)},
        ))
        assert archive.status_code == 200
        assert _rows(factory, Account, id=acc_id)[0].archived_at is not None

    def test_book_create_update_delete(self, v2_env):
        from src.models.ledger import Book

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        create = _execute(v2_env, user, book_id, _v2("book_create", {"name": "旅行"}))
        assert create.status_code == 200
        new_book = uuid.UUID(create.json()["transaction_ids"][0])
        assert _rows(factory, Book, id=new_book)[0].name == "旅行"

        version = _rows(factory, Book, id=new_book)[0].version
        update = _execute(v2_env, user, book_id, _v2("book_update", {
            "bookId": str(new_book), "version": version, "name": "蜜月",
        }))
        assert update.status_code == 200
        assert _rows(factory, Book, id=new_book)[0].name == "蜜月"

        delete = _execute(v2_env, user, book_id, _v2(
            "book_delete", {"bookId": str(new_book)},
        ))
        assert delete.status_code == 200
        assert _rows(factory, Book, id=new_book)[0].deleted_at is not None

    def test_a_write_registers_exactly_one_sync_change(self, v2_env):
        """每个写 op 都要能被别的设备 pull 到（T14-8 的教训：不登 = 看不见）。"""
        from src.models.sync import SyncChange

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        before = len(_rows(factory, SyncChange))
        resp = _execute(v2_env, user, book_id, _v2("book_create", {"name": "旅行"}))
        assert resp.status_code == 200
        changes = _rows(factory, SyncChange)
        assert len(changes) == before + 1
        assert changes[-1].entity_type == "book"
        assert str(changes[-1].operation) in ("insert", "SyncOperation.INSERT")


# ── payload 级授权与执行面边界 ─────────────────────────────────────────────


class TestV2PayloadAuthorization:
    def test_another_users_category_is_refused_at_validate_and_execute(self, v2_env):
        """别人的 id 打不进这个端点——校验判幻觉、执行 403（TOCTOU 重跑同一个函数）。"""
        c, _ = v2_env
        alice = _guest(v2_env)
        alice_book = _mk_book(c, alice)
        alice_category = _mk_category(c, alice)

        bob = c.post("/auth/guest", json={
            "device_id": "v2-device-bob", "nickname": "bob",
        }).json()
        bob = {"id": bob["user_id"], "token": bob["tokens"]["access_token"]}
        bob_book = _mk_book(c, bob, name="Bob 的账本")

        proposal = _v2("category_create", {
            "name": "偷来的", "kind": "expense",
            "bookId": str(alice_book), "parentId": str(alice_category),
        })
        verdict = _validate(v2_env, bob, bob_book, proposal).json()
        assert verdict["valid"] is False
        assert verdict["verdict"] == "rejected_hallucination"

        resp = _execute(v2_env, bob, bob_book, proposal)
        assert resp.status_code == 403
        assert resp.json()["code"] == "ai_proposal_rejected"

    def test_transaction_add_authorizes_its_account_id_too(self, v2_env):
        """**记账 op 也过 payload 授权**——共用写路径只校验账本与分类的归属，
        账户 id 全靠这道门（漏掉它 = 放行「把别人的账户写进自己的流水」）。"""
        from src.models.ledger import Transaction

        c, factory = v2_env
        alice = _guest(v2_env)
        _mk_book(c, alice)
        alice_account = _mk_account(c, alice, name="Alice 的卡")

        bob = c.post("/auth/guest", json={
            "device_id": "v2-device-bob", "nickname": "bob",
        }).json()
        bob = {"id": bob["user_id"], "token": bob["tokens"]["access_token"]}
        bob_book = _mk_book(c, bob, name="Bob 的账本")
        bob_category = _mk_category(c, bob, name="Bob 的餐饮")

        def _proposal(account_id: str) -> dict:
            return _v2("transaction_add", {"transactions": [{
                "type": "expense", "amountCents": 1250,
                "categoryId": str(bob_category), "accountId": account_id,
                "bizDate": "2026-09-18", "note": "午饭",
            }]})

        # 别人的账户 → 403，且一行都没落
        foreign = _execute(v2_env, bob, bob_book, _proposal(str(alice_account)))
        assert foreign.status_code == 403
        assert _rows(factory, Transaction, book_id=bob_book) == []

        # 自己的账户 → 正常写入（覆盖率哨兵：拒绝的理由是归属，不是别的）
        own = _mk_account(c, bob, name="Bob 的卡")
        assert _execute(v2_env, bob, bob_book, _proposal(str(own))).status_code == 200
        assert len(_rows(factory, Transaction, book_id=bob_book)) == 1

    def test_transaction_add_refuses_a_foreign_category(self, v2_env):
        """分类那条走共用写路径自己的归属校验（``_verify_category_ownership``），
        与账户那条互为补充——两条路都不放行别人的 id。"""
        c, factory = v2_env
        alice = _guest(v2_env)
        _mk_book(c, alice)
        alice_category = _mk_category(c, alice, name="Alice 的餐饮")
        alice_account = _mk_account(c, alice, name="Alice 的卡")

        bob = c.post("/auth/guest", json={
            "device_id": "v2-device-bob", "nickname": "bob",
        }).json()
        bob = {"id": bob["user_id"], "token": bob["tokens"]["access_token"]}
        bob_book = _mk_book(c, bob, name="Bob 的账本")

        resp = _execute(v2_env, bob, bob_book, _v2("transaction_add", {"transactions": [{
            "type": "expense", "amountCents": 1250, "categoryId": str(alice_category),
            "accountId": str(alice_account), "bizDate": "2026-09-18", "note": "",
        }]}))
        assert resp.status_code == 403
        from src.models.ledger import Transaction

        assert _rows(factory, Transaction, book_id=bob_book) == []

    def test_another_books_transaction_is_not_updatable(self, v2_env):
        c, factory = v2_env
        user = _guest(v2_env)
        book_a = _mk_book(c, user, name="账本A")
        book_b = _mk_book(c, user, name="账本B")
        category_id = _mk_category(c, user)
        created = c.post("/transactions", json={
            "book_id": str(book_a), "type": "expense", "amount_cents": 1250,
            "category_id": str(category_id), "note": "午饭", "biz_date": "2026-09-18",
            "occurred_at": "2026-09-18T12:00:00+00:00",
        }, headers=_auth(user)).json()

        # 同一个用户的另一本账本：交易 id 真实存在，但不在本轮的账本里
        proposal = _v2("transaction_update", {
            "transactionId": created["id"], "version": created["version"],
            "amountCents": 1,
        })
        verdict = _validate(v2_env, user, book_b, proposal).json()
        assert verdict["verdict"] == "rejected_hallucination"
        assert _execute(v2_env, user, book_b, proposal).status_code == 403

    def test_an_unregistered_op_fails_closed(self, v2_env):
        """授权表里没表态的 op 一律拒绝——默认放行会让「忘了登记」变成越权面。"""
        c, _ = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        # reminder_set 是注册表成员（提醒行），但它既没有授权规则也没有执行体：
        # 执行位置在设备（桥 reminder.set），工具体归 05f 的设备执行器——
        # 在此之前它对服务端是不存在的（见计划 §五 挂账项）。
        proposal = _v2("reminder_set", {})
        verdict = _validate(v2_env, user, book_id, proposal).json()
        assert verdict["valid"] is False
        assert verdict["verdict"] == "rejected_hallucination"

    def test_an_unregistered_op_is_refused_before_it_can_reach_the_executor(self, v2_env):
        """**最高危的 op 到不了执行面**：``account_delete`` 在授权表里就没表态
        ——fail-closed 判 403。05d 的取舍是**不接**它（它的 payload 要一次性
        ``deletionToken``，凭据不进模型上下文，AI 路径合并进
        ``account_deletion_request``），所以这条断言在 05d 之后依然成立。

        这是**两道门的第一道**：授权面先拒（``authorization`` 未登记 → 拒），
        执行面还有第二道（``execute_op`` 的 ``ai_unsupported_action``，见
        :class:`TestExecutorRegistry`）。
        """
        c, _ = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        for op, payload in (
            ("account_delete", {"confirm": True}),
            ("reminder_set", {}),
        ):
            resp = _execute(v2_env, user, book_id, _v2(op, payload))
            assert resp.status_code == 403, op
            assert resp.json()["code"] == "ai_proposal_rejected", op

    def test_a_device_op_is_not_executable_on_the_server(self, v2_env):
        """``sync_trigger`` 的执行位置在设备（T-M7-05j 后 ``meta_set`` 已迁回
        服务端执行，不再是设备 op）——服务端没有落点，客户端应当在设备上执行
        它，而不是打这个端点。"""
        c, _ = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        resp = _execute(v2_env, user, book_id, _v2(
            "sync_trigger", {},
        ))
        assert resp.status_code == 400
        assert resp.json()["code"] == "ai_device_op_not_executable"

    def test_a_malformed_payload_is_a_400_not_a_500(self, v2_env):
        c, _ = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        resp = _execute(v2_env, user, book_id, _v2("book_create", {}))
        assert resp.status_code == 400
        assert resp.json()["code"] == "ai_invalid_payload"


class TestExecutorRegistry:
    """``proposal_ops.execute_op`` 自己的两道闸（端点上的顺序见上一条用例）。"""

    async def test_an_op_without_an_executor_is_a_400(self):
        """有授权规则但没执行体（将来接线漏了半边）→ 400，不是 500。"""
        from src.common.errors import AppException
        from src.modules.ai.proposal_ops import execute_op

        with pytest.raises(AppException) as exc:
            await execute_op(
                None, user_id=uuid.uuid4(), book_id=uuid.uuid4(),
                op="account_delete", payload={},
            )
        assert exc.value.code == "ai_unsupported_action"

    async def test_a_device_op_is_refused_with_its_own_code(self):
        """设备类 op 的拒绝理由与「不支持」不同：它在设备上执行，不是没人实现。

        T-M7-05j：``meta_set`` 已迁回服务端执行，不再是设备 op；本用例改用
        ``sync_trigger`` 作设备 op 的探针。"""
        from src.common.errors import AppException
        from src.modules.ai.proposal_ops import execute_op

        with pytest.raises(AppException) as exc:
            await execute_op(
                None, user_id=uuid.uuid4(), book_id=uuid.uuid4(),
                op="sync_trigger", payload={},
            )
        assert exc.value.code == "ai_device_op_not_executable"

    def test_every_server_executed_op_is_also_authorized(self):
        """两份表必须**同时**覆盖每个服务端可执行的 op——只在一处表态就是半截接线。"""
        from src.modules.ai.authorization import has_authorization_rule
        from src.modules.ai.proposal_ops import SERVER_EXECUTED_OPS

        missing = [op for op in SERVER_EXECUTED_OPS if not has_authorization_rule(op)]
        assert missing == [], f"这些 op 有执行体但没授权规则：{missing}"


# ── 幂等：v2 与 v1 同口径 ──────────────────────────────────────────────────


class TestV2Idempotency:
    def test_the_same_proposal_id_executes_once(self, v2_env):
        from src.models.ledger import Book

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        proposal = _v2("book_create", {"name": "旅行"}, proposal_id="v2-idem-1")
        first = _execute(v2_env, user, book_id, proposal)
        second = _execute(v2_env, user, book_id, proposal)

        assert first.status_code == second.status_code == 200
        assert first.json()["transaction_ids"] == second.json()["transaction_ids"]
        assert len(_rows(factory, Book, name="旅行")) == 1, "重复确认不得重复建账本"


# ---------------------------------------------------------------------------
# T-M7-05j：meta_set 服务端执行（偏好项上云）
# ---------------------------------------------------------------------------


class TestV2MetaSet:
    """meta_set 的执行体现在是服务端——``proposal_ops._execute_meta_set``。"""

    def test_meta_set_upserts_a_user_preference_and_registers_sync_change(
        self, v2_env,
    ):
        """meta_set 写进 user_preferences 表、登一条 sync change。"""
        from src.models.sync import SyncChange
        from src.models.user_preference import UserPreference

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        before = len(_rows(factory, SyncChange))
        resp = _execute(v2_env, user, book_id, _v2(
            "meta_set", {"key": "default_book", "bookId": str(book_id)},
        ))
        assert resp.status_code == 200, resp.text

        prefs = _rows(factory, UserPreference, user_id=uuid.UUID(user["id"]))
        assert len(prefs) == 1
        # 工具 key ``default_book`` 落到表里变成 sync_meta 的本地键名
        # ``default_book_id``——与客户端 ``MetaWhitelist`` 对齐。
        assert prefs[0].key == "default_book_id"
        assert prefs[0].value == str(book_id)

        changes = _rows(factory, SyncChange)
        assert len(changes) == before + 1
        assert changes[-1].entity_type == "user_preference"
        assert str(changes[-1].operation) in ("insert", "SyncOperation.INSERT")

    def test_meta_set_updates_existing_row_bumps_version(self, v2_env):
        """同键第二次 meta_set → 更新 value、version++。"""
        from src.models.user_preference import UserPreference

        c, factory = v2_env
        user = _guest(v2_env)
        book_a = _mk_book(c, user, name="A")
        book_b = _mk_book(c, user, name="B")

        _execute(v2_env, user, book_a, _v2(
            "meta_set", {"key": "default_book", "bookId": str(book_a)},
        ))
        _execute(v2_env, user, book_b, _v2(
            "meta_set", {"key": "default_book", "bookId": str(book_b)},
        ))

        prefs = _rows(
            factory, UserPreference,
            user_id=uuid.UUID(user["id"]), key="default_book_id",
        )
        assert len(prefs) == 1
        assert prefs[0].value == str(book_b)
        assert prefs[0].version == 2

    def test_meta_set_widget_config_accepts_a_json_string(self, v2_env):
        """widget_config 接受 JSON 字符串（value 字段）。"""
        from src.models.user_preference import UserPreference

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        resp = _execute(v2_env, user, book_id, _v2(
            "meta_set", {
                "key": "widget_config",
                "value": '{"showBalance": true, "theme": "dark"}',
            },
        ))
        assert resp.status_code == 200, resp.text
        prefs = _rows(factory, UserPreference, user_id=uuid.UUID(user["id"]))
        assert len(prefs) == 1
        assert prefs[0].key == "widget_config"
        assert prefs[0].value == '{"showBalance": true, "theme": "dark"}'

    def test_meta_set_rejects_unknown_key(self, v2_env):
        """未在工具 key 枚举里的 key → 400。"""
        c, _ = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        resp = _execute(v2_env, user, book_id, _v2(
            "meta_set", {"key": "sync_cursor", "value": "42"},
        ))
        assert resp.status_code == 400
        assert resp.json()["code"] == "ai_invalid_payload"

    def test_meta_set_authorizes_foreign_book_id(self, v2_env):
        """别人账本的 id 打到本端点 → 403（payload 授权把 bookId 过归属校验）。"""
        c, _ = v2_env
        alice = _guest(v2_env)
        alice_book = _mk_book(c, alice)

        bob_resp = c.post("/auth/guest", json={
            "device_id": "v2-device-bob", "nickname": "bob",
        }).json()
        bob = {"id": bob_resp["user_id"], "token": bob_resp["tokens"]["access_token"]}
        bob_book = _mk_book(c, bob, name="Bob 的账本")

        resp = c.post(
            "/ai/execute",
            json={"proposal": _v2(
                "meta_set", {"key": "default_book", "bookId": str(alice_book)},
            ), "book_id": str(bob_book), "confirmation_token": "confirm"},
            headers=_auth(bob),
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ai_proposal_rejected"
