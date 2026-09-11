"""T-M4-02b: ActionProposal execution tests — batch, undo, idempotency.

Acceptance mapping (docs/APP-CONTEXT.md T-M4-02b):
- 批量 N 笔全成或全回滚 → TestBatchExecution
- 重复确认不重复入账    → TestIdempotency
- 撤销窗口内恢复        → TestUndoWindow
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from src.models.ledger import Transaction

# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture()
def exec_env():
    """TestClient with isolated DB for execution tests."""
    from src.main import app
    from src.modules.auth.dependencies import _rate_limits

    _rate_limits.clear()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
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

    app.dependency_overrides.clear()
    _rate_limits.clear()


def _guest(exec_env) -> dict:
    c, _ = exec_env
    resp = c.post("/auth/guest", json={"device_id": "exec-device", "nickname": "exec-tester"})
    assert resp.status_code == 200
    data = resp.json()
    return {"id": data["user_id"], "token": data["tokens"]["access_token"]}


def _auth(user: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {user['token']}"}


def _seed_categories(factory, user_id, book_id, category_ids):
    """Seed categories for the given user+book."""
    async def _run():
        async with factory() as session:
            from src.models.ledger import Category
            for cid, cname in category_ids.items():
                session.add(Category(
                    id=cid, owner_id=user_id, book_id=book_id,
                    name=cname, kind="expense",
                ))
            await session.commit()
    asyncio.run(_run())


def _execute(exec_env, user, book_id, proposal, confirmation_token="confirm"):
    c, _ = exec_env
    return c.post("/ai/execute", json={
        "proposal": proposal,
        "book_id": str(book_id),
        "confirmation_token": confirmation_token,
    }, headers=_auth(user))


def _count_transactions(factory, book_id) -> int:
    """S-M1 fix: 使用 count() 返回真实计数，非 scalar() 的 0/1。"""
    async def _run():
        async with factory() as session:
            from sqlalchemy import func
            result = await session.scalar(
                select(func.count()).select_from(Transaction).where(
                    Transaction.book_id == book_id,
                    Transaction.deleted_at.is_(None),
                )
            )
            return result or 0
    return asyncio.run(_run())


# ── Batch execution ──────────────────────────────────────────────────────


class TestBatchExecution:
    def test_single_transaction_executed(self, exec_env):
        user = _guest(exec_env)
        c, factory = exec_env

        resp = c.post("/books", json={"name": "Test Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])
        cat_id = uuid.uuid4()
        _seed_categories(factory, uuid.UUID(user["id"]), book_id, {cat_id: "餐饮"})

        proposal = {
            "proposalId": "prop-001",
            "action": "add_tx",
            "confidence": 0.95,
            "requiresConfirmation": False,
            "transactions": [{
                "type": "expense",
                "amountCents": 3500,
                "categoryId": str(cat_id),
                "bizDate": "2026-09-06",
                "note": "午餐",
            }],
            "message": "已记录午餐",
            "warnings": [],
        }

        resp = _execute(exec_env, user, book_id, proposal)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["transaction_ids"]) == 1
        assert data["proposal_id"] == "prop-001"
        assert data["undo_window_seconds"] == 5

    def test_batch_transactions_all_created(self, exec_env):
        user = _guest(exec_env)
        c, factory = exec_env

        resp = c.post("/books", json={"name": "Test Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])
        cat_1 = uuid.uuid4()
        cat_2 = uuid.uuid4()
        _seed_categories(factory, uuid.UUID(user["id"]), book_id, {
            cat_1: "餐饮", cat_2: "交通",
        })

        proposal = {
            "proposalId": "prop-002",
            "action": "add_tx_batch",
            "confidence": 0.95,
            "requiresConfirmation": True,
            "transactions": [
                {
                    "type": "expense",
                    "amountCents": 3500,
                    "categoryId": str(cat_1),
                    "bizDate": "2026-09-06",
                    "note": "午餐",
                },
                {
                    "type": "expense",
                    "amountCents": 1500,
                    "categoryId": str(cat_2),
                    "bizDate": "2026-09-06",
                    "note": "地铁",
                },
                {
                    "type": "income",
                    "amountCents": 500,
                    "categoryId": str(cat_1),
                    "bizDate": "2026-09-06",
                    "note": "退款",
                },
            ],
            "message": "检测到 3 笔交易",
            "warnings": [],
        }

        resp = _execute(exec_env, user, book_id, proposal)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["transaction_ids"]) == 3

    def test_batch_atomic_all_or_nothing(self, exec_env):
        """If any transaction in batch is invalid, entire batch fails."""
        user = _guest(exec_env)
        c, factory = exec_env

        resp = c.post("/books", json={"name": "Test Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])
        legit_cat = uuid.uuid4()
        _seed_categories(factory, uuid.UUID(user["id"]), book_id, {legit_cat: "餐饮"})

        # Second transaction has hallucinated category
        hallucinated_cat = uuid.uuid4()
        proposal = {
            "proposalId": "prop-003",
            "action": "add_tx_batch",
            "confidence": 0.95,
            "requiresConfirmation": True,
            "transactions": [
                {
                    "type": "expense",
                    "amountCents": 3500,
                    "categoryId": str(legit_cat),
                    "bizDate": "2026-09-06",
                    "note": "午餐",
                },
                {
                    "type": "expense",
                    "amountCents": 1500,
                    "categoryId": str(hallucinated_cat),  # Invalid
                    "bizDate": "2026-09-06",
                    "note": "地铁",
                },
            ],
            "message": "检测到 2 笔交易",
            "warnings": [],
        }

        resp = _execute(exec_env, user, book_id, proposal)
        # Should fail validation
        assert resp.status_code == 403
        assert resp.json()["code"] == "ai_proposal_rejected"

    def test_unvalidated_proposal_rejected(self, exec_env):
        """Proposal with schema errors cannot be executed."""
        user = _guest(exec_env)
        c, factory = exec_env

        resp = c.post("/books", json={"name": "Test Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])

        # Invalid proposal (missing required fields)
        proposal = {
            "proposalId": "prop-004",
            "action": "add_tx",
            # Missing: confidence, requiresConfirmation, etc.
        }

        resp = _execute(exec_env, user, book_id, proposal)
        assert resp.status_code == 403
        assert resp.json()["code"] == "ai_proposal_rejected"


# ── T14-3（REQ-40）: missing category → 400 before insert ────────────────


class TestMissingCategoryGuard:
    def test_execute_missing_category_400(self, exec_env):
        """零分类账本 + 缺 categoryId 行（REQ-40 旧 500 IntegrityError 路径）
        → items 构建后 400 ai_missing_category（中文 message），不落库。

        注：账本有分类时缺 categoryId 在重校验即 403 rejected_hallucination
        （既有 R-M4-64 行为，不回归）；本卡 400 拦截覆盖 validator 放行的
        零分类场景——正是线上「确认 → Internal database error」的路径。
        """
        user = _guest(exec_env)
        c, factory = exec_env

        resp = c.post("/books", json={"name": "Test Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])
        # 故意不建任何分类 → ctx.category_ids 为空，validator 放行

        proposal = {
            "proposalId": "prop-no-cat-001",
            "action": "add_tx_batch",
            "confidence": 0.95,
            "requiresConfirmation": True,
            "transactions": [
                {
                    "type": "expense",
                    "amountCents": 3500,
                    "bizDate": "2026-09-06",
                    "note": "午餐",
                },
                {
                    "type": "expense",
                    "amountCents": 1500,
                    "bizDate": "2026-09-06",
                    "note": "购物",
                },
            ],
            "message": "检测到 2 笔交易",
            "warnings": [],
        }

        resp = _execute(exec_env, user, book_id, proposal)
        assert resp.status_code == 400
        data = resp.json()
        assert data["code"] == "ai_missing_category"
        assert "分类" in data["message"]  # 中文可映射，不透英文 IntegrityError
        # 拦截发生在落库前：0 笔写入
        assert _count_transactions(factory, book_id) == 0

    def test_execute_with_categories_still_ok(self, exec_env):
        """回归：全行有分类 → 正常 200（拦截不误伤主链路）。"""
        user = _guest(exec_env)
        c, factory = exec_env

        resp = c.post("/books", json={"name": "Test Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])
        cat_id = uuid.uuid4()
        _seed_categories(factory, uuid.UUID(user["id"]), book_id, {cat_id: "餐饮"})

        proposal = {
            "proposalId": "prop-no-cat-002",
            "action": "add_tx",
            "confidence": 0.95,
            "requiresConfirmation": False,
            "transactions": [{
                "type": "expense",
                "amountCents": 3500,
                "categoryId": str(cat_id),
                "bizDate": "2026-09-06",
                "note": "午餐",
            }],
            "message": "已记录午餐",
            "warnings": [],
        }

        resp = _execute(exec_env, user, book_id, proposal)
        assert resp.status_code == 200
        assert len(resp.json()["transaction_ids"]) == 1


# ── Confidence gate ──────────────────────────────────────────────────────


class TestConfidenceGate:
    def test_low_confidence_with_confirmation_token_accepted(self, exec_env):
        """Low-confidence proposal with confirmation token is accepted."""
        user = _guest(exec_env)
        c, factory = exec_env

        resp = c.post("/books", json={"name": "Test Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])
        cat_id = uuid.uuid4()
        _seed_categories(factory, uuid.UUID(user["id"]), book_id, {cat_id: "餐饮"})

        proposal = {
            "proposalId": "prop-005",
            "action": "add_tx",
            "confidence": 0.75,  # Low
            "requiresConfirmation": False,
            "transactions": [{
                "type": "expense",
                "amountCents": 3500,
                "categoryId": str(cat_id),
                "bizDate": "2026-09-06",
                "note": "午餐",
            }],
            "message": "可能是午餐",
            "warnings": ["置信度较低"],
        }

        # With confirmation token → accepted
        resp = _execute(exec_env, user, book_id, proposal, confirmation_token="user-confirmed-123")
        assert resp.status_code == 200
        assert len(resp.json()["transaction_ids"]) == 1

    def test_high_confidence_auto_executes(self, exec_env):
        """High-confidence proposal executes without confirmation token."""
        user = _guest(exec_env)
        c, factory = exec_env

        resp = c.post("/books", json={"name": "Test Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])
        cat_id = uuid.uuid4()
        _seed_categories(factory, uuid.UUID(user["id"]), book_id, {cat_id: "餐饮"})

        proposal = {
            "proposalId": "prop-006",
            "action": "add_tx",
            "confidence": 0.95,
            "requiresConfirmation": False,
            "transactions": [{
                "type": "expense",
                "amountCents": 3500,
                "categoryId": str(cat_id),
                "bizDate": "2026-09-06",
                "note": "午餐",
            }],
            "message": "已记录午餐",
            "warnings": [],
        }

        # Any token works for high confidence
        resp = _execute(exec_env, user, book_id, proposal, confirmation_token="auto")
        assert resp.status_code == 200
        assert len(resp.json()["transaction_ids"]) == 1


# ── Security ─────────────────────────────────────────────────────────────


class TestExecutionSecurity:
    def test_hallucinated_category_rejected(self, exec_env):
        """Proposal with hallucinated category ID is rejected."""
        user = _guest(exec_env)
        c, factory = exec_env

        resp = c.post("/books", json={"name": "Test Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])
        cat_id = uuid.uuid4()
        _seed_categories(factory, uuid.UUID(user["id"]), book_id, {cat_id: "餐饮"})

        hallucinated_cat = uuid.uuid4()
        proposal = {
            "proposalId": "prop-007",
            "action": "add_tx",
            "confidence": 0.95,
            "requiresConfirmation": False,
            "transactions": [{
                "type": "expense",
                "amountCents": 3500,
                "categoryId": str(hallucinated_cat),  # Not in DB
                "bizDate": "2026-09-06",
                "note": "午餐",
            }],
            "message": "已记录",
            "warnings": [],
        }

        resp = _execute(exec_env, user, book_id, proposal)
        assert resp.status_code == 403
        assert resp.json()["code"] == "ai_proposal_rejected"

    def test_cross_book_category_rejected(self, exec_env):
        """Category from another book is rejected."""
        user = _guest(exec_env)
        c, factory = exec_env

        # Create two books
        resp = c.post("/books", json={"name": "Book 1"}, headers=_auth(user))
        book_1 = uuid.UUID(resp.json()["id"])
        resp = c.post("/books", json={"name": "Book 2"}, headers=_auth(user))
        book_2 = uuid.UUID(resp.json()["id"])

        # Category in Book 1
        cat_1 = uuid.uuid4()
        _seed_categories(factory, uuid.UUID(user["id"]), book_1, {cat_1: "餐饮"})

        # Try to execute in Book 2 with Book 1's category
        proposal = {
            "proposalId": "prop-008",
            "action": "add_tx",
            "confidence": 0.95,
            "requiresConfirmation": False,
            "transactions": [{
                "type": "expense",
                "amountCents": 3500,
                "categoryId": str(cat_1),
                "bizDate": "2026-09-06",
                "note": "午餐",
            }],
            "message": "已记录",
            "warnings": [],
        }

        resp = _execute(exec_env, user, book_2, proposal)
        assert resp.status_code == 403
        assert resp.json()["code"] == "ai_proposal_rejected"

    def test_unsupported_action_rejected(self, exec_env):
        """Non-executable actions (query, report, chat) are rejected."""
        user = _guest(exec_env)
        c, factory = exec_env

        resp = c.post("/books", json={"name": "Test Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])

        proposal = {
            "proposalId": "prop-009",
            "action": "query",  # Not executable
            "confidence": 0.95,
            "requiresConfirmation": False,
            "message": "查询结果",
            "warnings": [],
        }

        resp = _execute(exec_env, user, book_id, proposal)
        assert resp.status_code == 400
        assert resp.json()["code"] == "ai_unsupported_action"


# ── Idempotency (T-M4-02b) ───────────────────────────────────────────────


class TestIdempotency:
    def test_repeat_same_proposal_id_returns_same_transaction_ids(self, exec_env):
        """Re-executing the same proposalId returns cached transaction_ids."""
        user = _guest(exec_env)
        c, factory = exec_env

        resp = c.post("/books", json={"name": "Test Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])
        cat_id = uuid.uuid4()
        _seed_categories(factory, uuid.UUID(user["id"]), book_id, {cat_id: "餐饮"})

        proposal = {
            "proposalId": "prop-idem-001",
            "action": "add_tx",
            "confidence": 0.95,
            "requiresConfirmation": False,
            "transactions": [{
                "type": "expense",
                "amountCents": 3500,
                "categoryId": str(cat_id),
                "bizDate": "2026-09-06",
                "note": "午餐",
            }],
            "message": "已记录午餐",
            "warnings": [],
        }

        # First execution
        resp1 = _execute(exec_env, user, book_id, proposal)
        assert resp1.status_code == 200
        ids1 = resp1.json()["transaction_ids"]
        assert len(ids1) == 1

        # Second execution (same proposalId) — must return cached IDs
        resp2 = _execute(exec_env, user, book_id, proposal)
        assert resp2.status_code == 200
        ids2 = resp2.json()["transaction_ids"]
        assert ids1 == ids2

        # Only one transaction in DB (no duplicate)
        async def _count():
            async with factory() as session:
                result = await session.execute(
                    select(Transaction).where(Transaction.book_id == book_id)
                )
                return len(result.scalars().all())
        assert asyncio.run(_count()) == 1

    def test_repeat_does_not_create_duplicate_transactions(self, exec_env):
        """Multiple executions of same proposalId create only one set of transactions."""
        user = _guest(exec_env)
        c, factory = exec_env

        resp = c.post("/books", json={"name": "Test Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])
        cat_id = uuid.uuid4()
        _seed_categories(factory, uuid.UUID(user["id"]), book_id, {cat_id: "餐饮"})

        proposal = {
            "proposalId": "prop-idem-002",
            "action": "add_tx_batch",
            "confidence": 0.95,
            "requiresConfirmation": True,
            "transactions": [
                {
                    "type": "expense",
                    "amountCents": 3500,
                    "categoryId": str(cat_id),
                    "bizDate": "2026-09-06",
                    "note": "午餐",
                },
                {
                    "type": "expense",
                    "amountCents": 1500,
                    "categoryId": str(cat_id),
                    "bizDate": "2026-09-06",
                    "note": "地铁",
                },
            ],
            "message": "检测到 2 笔交易",
            "warnings": [],
        }

        # Execute 3 times — all must return same IDs
        first_ids = None
        for _ in range(3):
            resp = _execute(exec_env, user, book_id, proposal)
            assert resp.status_code == 200
            ids = resp.json()["transaction_ids"]
            assert len(ids) == 2
            if first_ids is None:
                first_ids = ids
            else:
                assert ids == first_ids

        # Only 2 transactions in DB (not 6)
        async def _count():
            async with factory() as session:
                result = await session.execute(
                    select(Transaction).where(Transaction.book_id == book_id)
                )
                return len(result.scalars().all())
        assert asyncio.run(_count()) == 2

    def test_different_user_same_proposal_id_independent(self, exec_env):
        """Same proposalId from different users are independent (no cross-user dedup)."""
        user1 = _guest(exec_env)
        c, factory = exec_env

        # Create second guest user
        resp = c.post("/auth/guest", json={"device_id": "exec-device-2", "nickname": "exec-tester-2"})
        assert resp.status_code == 200
        data = resp.json()
        user2 = {"id": data["user_id"], "token": data["tokens"]["access_token"]}

        # Each user creates their own book
        resp1 = c.post("/books", json={"name": "Book 1"}, headers=_auth(user1))
        book1 = uuid.UUID(resp1.json()["id"])
        resp2 = c.post("/books", json={"name": "Book 2"}, headers=_auth(user2))
        book2 = uuid.UUID(resp2.json()["id"])

        cat1 = uuid.uuid4()
        cat2 = uuid.uuid4()
        _seed_categories(factory, uuid.UUID(user1["id"]), book1, {cat1: "餐饮"})
        _seed_categories(factory, uuid.UUID(user2["id"]), book2, {cat2: "交通"})

        # Same proposalId, different users + different categories
        proposal1 = {
            "proposalId": "prop-idem-003",
            "action": "add_tx",
            "confidence": 0.95,
            "requiresConfirmation": False,
            "transactions": [{
                "type": "expense",
                "amountCents": 3500,
                "categoryId": str(cat1),
                "bizDate": "2026-09-06",
                "note": "午餐",
            }],
            "message": "已记录午餐",
            "warnings": [],
        }
        proposal2 = {
            "proposalId": "prop-idem-003",  # Same ID
            "action": "add_tx",
            "confidence": 0.95,
            "requiresConfirmation": False,
            "transactions": [{
                "type": "expense",
                "amountCents": 1500,
                "categoryId": str(cat2),
                "bizDate": "2026-09-06",
                "note": "地铁",
            }],
            "message": "已记录地铁",
            "warnings": [],
        }

        resp1 = _execute(exec_env, user1, book1, proposal1)
        assert resp1.status_code == 200
        ids1 = resp1.json()["transaction_ids"]

        resp2 = _execute(exec_env, user2, book2, proposal2)
        assert resp2.status_code == 200
        ids2 = resp2.json()["transaction_ids"]

        # Different transaction IDs (independent executions)
        assert ids1 != ids2

        # Each user has exactly 1 transaction in their own book
        async def _count():
            async with factory() as session:
                r1 = await session.execute(
                    select(Transaction).where(Transaction.book_id == book1)
                )
                r2 = await session.execute(
                    select(Transaction).where(Transaction.book_id == book2)
                )
                return len(r1.scalars().all()), len(r2.scalars().all())
        c1, c2 = asyncio.run(_count())
        assert c1 == 1
        assert c2 == 1


# ── T14-8 回归：/ai/execute 写入必须进 sync 变更日志（pull 可见）──────────


class TestSyncVisibility:
    """T14-8：图片批量确认走 /ai/execute「服务端权威」，但 TransactionService
    直写不产生 SyncChange → 任何设备 pull 都拿不到（模拟器实测：已落库两笔、
    本机永远查不到）。execute 成功后必须逐笔写 insert 变更。"""

    def test_execute_transactions_are_pullable(self, exec_env):
        user = _guest(exec_env)
        c, factory = exec_env

        resp = c.post("/books", json={"name": "Sync Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])
        cat_a, cat_b = uuid.uuid4(), uuid.uuid4()
        _seed_categories(factory, uuid.UUID(user["id"]), book_id, {
            cat_a: "交通", cat_b: "餐饮",
        })

        proposal = {
            "proposalId": "prop-sync-001",
            "action": "add_tx_batch",
            "confidence": 0.95,
            "requiresConfirmation": False,
            "transactions": [
                {
                    "type": "expense", "amountCents": 3000,
                    "categoryId": str(cat_a), "bizDate": "2026-09-08",
                    "note": "打车",
                },
                {
                    "type": "expense", "amountCents": 67800,
                    "categoryId": str(cat_b), "bizDate": "2026-09-08",
                    "note": "吃饭",
                },
            ],
            "message": "识别到 2 笔交易",
            "warnings": [],
        }
        r = _execute(exec_env, user, book_id, proposal)
        assert r.status_code == 200
        tx_ids = {str(x) for x in r.json()["transaction_ids"]}

        pull = c.post("/sync/pull", json={"cursor": 0}, headers=_auth(user))
        assert pull.status_code == 200
        tx_changes = [
            ch for ch in pull.json()["changes"]
            if ch["entity_type"] == "transaction"
        ]
        assert {str(ch["entity_id"]) for ch in tx_changes} == tx_ids
        assert all(ch["operation"] == "insert" for ch in tx_changes)
        assert pull.json()["cursor"] > 0

    def test_idempotent_replay_does_not_duplicate_changes(self, exec_env):
        """重放同 proposalId 命中幂等缓存，不得再写一遍 SyncChange。"""
        user = _guest(exec_env)
        c, factory = exec_env

        resp = c.post("/books", json={"name": "Sync Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])
        cat_id = uuid.uuid4()
        _seed_categories(factory, uuid.UUID(user["id"]), book_id, {cat_id: "餐饮"})

        proposal = {
            "proposalId": "prop-sync-002",
            "action": "add_tx",
            "confidence": 0.95,
            "requiresConfirmation": False,
            "transactions": [{
                "type": "expense", "amountCents": 1500,
                "categoryId": str(cat_id), "bizDate": "2026-09-08",
                "note": "午饭",
            }],
            "message": "已记录午饭",
            "warnings": [],
        }
        assert _execute(exec_env, user, book_id, proposal).status_code == 200
        assert _execute(exec_env, user, book_id, proposal).status_code == 200

        pull = c.post("/sync/pull", json={"cursor": 0}, headers=_auth(user))
        tx_changes = [
            ch for ch in pull.json()["changes"]
            if ch["entity_type"] == "transaction"
        ]
        assert len(tx_changes) == 1
