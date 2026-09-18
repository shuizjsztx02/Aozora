"""T-M4-02a: ActionProposal validation tests — hallucination, confidence, schema.

Acceptance mapping (docs/APP-CONTEXT.md T-M4-02):
- 非法 JSON 零写库        → TestSchemaValidation
- 幻觉数字/无权限分类 零写库 → TestHallucinationRejection
- 低置信动作需确认        → TestConfidenceGate
- 上下文取可信 DB 数据    → TestContextFromDB
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.testclient import TestClient

from src.modules.ai.context import load_validation_context

# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture()
def val_env():
    """TestClient with isolated DB for validation tests."""
    from src.main import app
    from src.modules.auth.dependencies import _rate_limits

    _rate_limits.clear()

    # 用**临时文件库**而不是 `:memory:` + StaticPool：内存库依附唯一一条连接，
    # 而本 fixture 里有多个生命周期不同的 asyncio 循环（`asyncio.run` 建表、
    # TestClient 的 portal loop、用例里 ad-hoc 的 `asyncio.run`）。连接跨 loop 复用
    # 又从不释放，aiosqlite 的工作线程就会在旧 loop 关闭后回投结果 → 线程里抛
    # `RuntimeError: Event loop is closed`，被 filterwarnings=["error"] 提升成 error，
    # 且**记在哪个用例上纯看时机**（2026-09-18 定位，服务端作业偶发 1 error 的真因）。
    # 文件库让每个 loop 各用各的连接，teardown 里也就能干净释放（实测线程归零）。
    # 临时目录用 mkdtemp 而不是 pytest 的 tmp_path：与本仓库其它测试
    # （test_attachments / test_image_parse / test_stats_exports）一致。
    db_dir = Path(tempfile.mkdtemp(prefix="aozora_test_db_"))
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(db_dir / 'test.db').as_posix()}"
    )
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


def _guest(val_env) -> dict:
    c, _ = val_env
    resp = c.post("/auth/guest", json={"device_id": "val-device", "nickname": "val-tester"})
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


def _seed_accounts(factory, user_id, account_ids):
    """Seed accounts for the given user."""
    async def _run():
        async with factory() as session:
            from src.models.ledger import Account
            for aid, aname in account_ids.items():
                session.add(Account(id=aid, owner_id=user_id, name=aname))
            await session.commit()
    asyncio.run(_run())


def _validate(val_env, user, book_id, proposal):
    c, _ = val_env
    return c.post("/ai/validate", json={
        "proposal": proposal,
        "book_id": str(book_id),
    }, headers=_auth(user))


# ── Schema validation ────────────────────────────────────────────────────


class TestSchemaValidation:
    def test_valid_proposal_accepted(self, val_env):
        user = _guest(val_env)
        book_id = uuid.uuid4()
        cat_id = uuid.uuid4()
        acc_id = uuid.uuid4()
        c, factory = val_env

        # Create book via API
        resp = c.post("/books", json={"name": "Test Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])

        _seed_categories(factory, uuid.UUID(user["id"]), book_id, {cat_id: "餐饮"})
        _seed_accounts(factory, uuid.UUID(user["id"]), {acc_id: "现金"})

        proposal = {
            "proposalId": "prop-001",
            "action": "add_tx",
            "confidence": 0.95,
            "requiresConfirmation": False,
            "transactions": [{
                "type": "expense",
                "amountCents": 3500,
                "categoryId": str(cat_id),
                "accountId": str(acc_id),
                "bizDate": "2026-09-06",
                "note": "午餐",
            }],
            "message": "已记录午餐支出",
            "warnings": [],
        }

        resp = _validate(val_env, user, book_id, proposal)
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["verdict"] == "accepted"
        assert data["errors"] == []

    def test_invalid_schema_rejected(self, val_env):
        user = _guest(val_env)
        c, _ = val_env

        resp = c.post("/books", json={"name": "Test Book"}, headers=_auth(user))
        book_id = resp.json()["id"]

        # Missing required fields
        proposal = {
            "proposalId": "prop-002",
            "action": "add_tx",
            # Missing: confidence, requiresConfirmation, message, warnings, transactions
        }

        resp = _validate(val_env, user, book_id, proposal)
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert data["verdict"] == "rejected_schema"
        assert len(data["errors"]) > 0

    def test_invalid_date_format_rejected(self, val_env):
        user = _guest(val_env)
        c, factory = val_env

        resp = c.post("/books", json={"name": "Test Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])
        cat_id = uuid.uuid4()
        _seed_categories(factory, uuid.UUID(user["id"]), book_id, {cat_id: "餐饮"})

        proposal = {
            "proposalId": "prop-003",
            "action": "add_tx",
            "confidence": 0.95,
            "requiresConfirmation": False,
            "transactions": [{
                "type": "expense",
                "amountCents": 3500,
                "categoryId": str(cat_id),
                "bizDate": "2026-02-30",  # Invalid date
                "note": "午餐",
            }],
            "message": "已记录",
            "warnings": [],
        }

        resp = _validate(val_env, user, book_id, proposal)
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert data["verdict"] == "rejected_schema"

    def test_negative_amount_rejected(self, val_env):
        user = _guest(val_env)
        c, factory = val_env

        resp = c.post("/books", json={"name": "Test Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])
        cat_id = uuid.uuid4()
        _seed_categories(factory, uuid.UUID(user["id"]), book_id, {cat_id: "餐饮"})

        proposal = {
            "proposalId": "prop-004",
            "action": "add_tx",
            "confidence": 0.95,
            "requiresConfirmation": False,
            "transactions": [{
                "type": "expense",
                "amountCents": -100,  # Negative
                "categoryId": str(cat_id),
                "bizDate": "2026-09-06",
                "note": "退款",
            }],
            "message": "已记录",
            "warnings": [],
        }

        resp = _validate(val_env, user, book_id, proposal)
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert data["verdict"] == "rejected_schema"


# ── Hallucination rejection ──────────────────────────────────────────────


class TestHallucinationRejection:
    def test_unauthorized_category_rejected(self, val_env):
        user = _guest(val_env)
        c, factory = val_env

        resp = c.post("/books", json={"name": "Test Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])

        # Seed one legitimate category
        legit_cat = uuid.uuid4()
        _seed_categories(factory, uuid.UUID(user["id"]), book_id, {legit_cat: "餐饮"})

        # Proposal references a hallucinated category ID
        hallucinated_cat = uuid.uuid4()
        proposal = {
            "proposalId": "prop-005",
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

        resp = _validate(val_env, user, book_id, proposal)
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert data["verdict"] == "rejected_hallucination"
        assert any("categoryId" in e["path"] for e in data["errors"])

    def test_unauthorized_account_rejected(self, val_env):
        user = _guest(val_env)
        c, factory = val_env

        resp = c.post("/books", json={"name": "Test Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])
        cat_id = uuid.uuid4()
        _seed_categories(factory, uuid.UUID(user["id"]), book_id, {cat_id: "餐饮"})

        # Proposal references a hallucinated account ID
        hallucinated_acc = uuid.uuid4()
        proposal = {
            "proposalId": "prop-006",
            "action": "add_tx",
            "confidence": 0.95,
            "requiresConfirmation": False,
            "transactions": [{
                "type": "expense",
                "amountCents": 3500,
                "categoryId": str(cat_id),
                "accountId": str(hallucinated_acc),  # Not in DB
                "bizDate": "2026-09-06",
                "note": "午餐",
            }],
            "message": "已记录",
            "warnings": [],
        }

        resp = _validate(val_env, user, book_id, proposal)
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert data["verdict"] == "rejected_hallucination"
        assert any("accountId" in e["path"] for e in data["errors"])

    def test_cross_user_category_rejected(self, val_env):
        """User A's category ID is not valid for User B."""
        user_a = _guest(val_env)
        user_b = _guest(val_env)
        c, factory = val_env

        # User A creates a book and category
        resp = c.post("/books", json={"name": "A's Book"}, headers=_auth(user_a))
        book_a = uuid.UUID(resp.json()["id"])
        cat_a = uuid.uuid4()
        _seed_categories(factory, uuid.UUID(user_a["id"]), book_a, {cat_a: "餐饮"})

        # User B creates their own book
        resp = c.post("/books", json={"name": "B's Book"}, headers=_auth(user_b))
        book_b = uuid.UUID(resp.json()["id"])

        # User B's proposal references User A's category (hallucination)
        proposal = {
            "proposalId": "prop-007",
            "action": "add_tx",
            "confidence": 0.95,
            "requiresConfirmation": False,
            "transactions": [{
                "type": "expense",
                "amountCents": 3500,
                "categoryId": str(cat_a),  # Belongs to User A
                "bizDate": "2026-09-06",
                "note": "午餐",
            }],
            "message": "已记录",
            "warnings": [],
        }

        resp = _validate(val_env, user_b, book_b, proposal)
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert data["verdict"] == "rejected_hallucination"

    def test_wrong_book_category_rejected(self, val_env):
        """Category from User's other book is not valid in this book."""
        user = _guest(val_env)
        c, factory = val_env

        # User creates two books
        resp = c.post("/books", json={"name": "Book 1"}, headers=_auth(user))
        book_1 = uuid.UUID(resp.json()["id"])
        resp = c.post("/books", json={"name": "Book 2"}, headers=_auth(user))
        book_2 = uuid.UUID(resp.json()["id"])

        # Category in Book 1
        cat_1 = uuid.uuid4()
        _seed_categories(factory, uuid.UUID(user["id"]), book_1, {cat_1: "餐饮"})

        # Proposal in Book 2 references Book 1's category
        proposal = {
            "proposalId": "prop-008",
            "action": "add_tx",
            "confidence": 0.95,
            "requiresConfirmation": False,
            "transactions": [{
                "type": "expense",
                "amountCents": 3500,
                "categoryId": str(cat_1),  # Belongs to Book 1
                "bizDate": "2026-09-06",
                "note": "午餐",
            }],
            "message": "已记录",
            "warnings": [],
        }

        resp = _validate(val_env, user, book_2, proposal)
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert data["verdict"] == "rejected_hallucination"


# ── Confidence gate ──────────────────────────────────────────────────────


class TestConfidenceGate:
    def test_low_confidence_requires_confirmation(self, val_env):
        user = _guest(val_env)
        c, factory = val_env

        resp = c.post("/books", json={"name": "Test Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])
        cat_id = uuid.uuid4()
        _seed_categories(factory, uuid.UUID(user["id"]), book_id, {cat_id: "餐饮"})

        # Valid proposal but low confidence
        proposal = {
            "proposalId": "prop-009",
            "action": "add_tx",
            "confidence": 0.75,  # Below 0.9 threshold
            "requiresConfirmation": False,  # Client says no, but server overrides
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

        resp = _validate(val_env, user, book_id, proposal)
        assert resp.status_code == 200
        data = resp.json()
        # Schema and context pass, but confidence is low
        assert data["valid"] is True
        assert data["verdict"] == "rejected_low_confidence"
        assert data["errors"] == []

    def test_high_confidence_accepted(self, val_env):
        user = _guest(val_env)
        c, factory = val_env

        resp = c.post("/books", json={"name": "Test Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])
        cat_id = uuid.uuid4()
        _seed_categories(factory, uuid.UUID(user["id"]), book_id, {cat_id: "餐饮"})

        proposal = {
            "proposalId": "prop-010",
            "action": "add_tx",
            "confidence": 0.95,  # Above 0.9 threshold
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

        resp = _validate(val_env, user, book_id, proposal)
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["verdict"] == "accepted"

    def test_exact_threshold_accepted(self, val_env):
        """Confidence exactly at threshold (0.9) is accepted."""
        user = _guest(val_env)
        c, factory = val_env

        resp = c.post("/books", json={"name": "Test Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])
        cat_id = uuid.uuid4()
        _seed_categories(factory, uuid.UUID(user["id"]), book_id, {cat_id: "餐饮"})

        proposal = {
            "proposalId": "prop-011",
            "action": "add_tx",
            "confidence": 0.9,  # Exactly at threshold
            "requiresConfirmation": False,
            "transactions": [{
                "type": "expense",
                "amountCents": 3500,
                "categoryId": str(cat_id),
                "bizDate": "2026-09-06",
                "note": "午餐",
            }],
            "message": "已记录",
            "warnings": [],
        }

        resp = _validate(val_env, user, book_id, proposal)
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["verdict"] == "accepted"


# ── Context from DB ──────────────────────────────────────────────────────


class TestContextFromDB:
    def test_context_loads_from_db_not_model(self, val_env):
        """Verify that validation context is loaded from DB, not from proposal."""
        user = _guest(val_env)
        c, factory = val_env

        resp = c.post("/books", json={"name": "Test Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])

        # Seed categories and accounts
        cat_1 = uuid.uuid4()
        cat_2 = uuid.uuid4()
        acc_1 = uuid.uuid4()
        _seed_categories(factory, uuid.UUID(user["id"]), book_id, {
            cat_1: "餐饮", cat_2: "交通",
        })
        _seed_accounts(factory, uuid.UUID(user["id"]), {acc_1: "现金"})

        # Load context directly
        async def _load():
            async with factory() as session:
                return await load_validation_context(
                    session, uuid.UUID(user["id"]), book_id,
                )
        ctx = asyncio.run(_load())

        # Verify context contains only DB-backed IDs
        assert str(cat_1) in ctx.category_ids
        assert str(cat_2) in ctx.category_ids
        assert str(acc_1) in ctx.account_ids
        assert len(ctx.category_ids) == 2
        assert len(ctx.account_ids) == 1

    def test_empty_context_rejects_all_ids(self, val_env):
        """When no categories/accounts exist, any ID is rejected."""
        user = _guest(val_env)
        c, _ = val_env

        resp = c.post("/books", json={"name": "Empty Book"}, headers=_auth(user))
        book_id = resp.json()["id"]

        # Proposal with any ID should be rejected
        proposal = {
            "proposalId": "prop-012",
            "action": "add_tx",
            "confidence": 0.95,
            "requiresConfirmation": False,
            "transactions": [{
                "type": "expense",
                "amountCents": 3500,
                "categoryId": str(uuid.uuid4()),  # Any ID
                "bizDate": "2026-09-06",
                "note": "午餐",
            }],
            "message": "已记录",
            "warnings": [],
        }

        resp = _validate(val_env, user, book_id, proposal)
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert data["verdict"] == "rejected_hallucination"

    def test_invalid_book_id_rejected(self, val_env):
        user = _guest(val_env)
        c, _ = val_env

        proposal = {
            "proposalId": "prop-013",
            "action": "add_tx",
            "confidence": 0.95,
            "requiresConfirmation": False,
            "transactions": [{
                "type": "expense",
                "amountCents": 3500,
                "bizDate": "2026-09-06",
                "note": "午餐",
            }],
            "message": "已记录",
            "warnings": [],
        }

        resp = c.post("/ai/validate", json={
            "proposal": proposal,
            "book_id": "not-a-uuid",
        }, headers=_auth(user))
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert data["verdict"] == "rejected_schema"


# ── Batch validation ─────────────────────────────────────────────────────


class TestBatchValidation:
    def test_batch_all_valid_accepted(self, val_env):
        user = _guest(val_env)
        c, factory = val_env

        resp = c.post("/books", json={"name": "Test Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])
        cat_1 = uuid.uuid4()
        cat_2 = uuid.uuid4()
        _seed_categories(factory, uuid.UUID(user["id"]), book_id, {
            cat_1: "餐饮", cat_2: "交通",
        })

        proposal = {
            "proposalId": "prop-014",
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
            ],
            "message": "检测到 2 笔交易",
            "warnings": [],
        }

        resp = _validate(val_env, user, book_id, proposal)
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["verdict"] == "accepted"

    def test_batch_one_hallucinated_rejected(self, val_env):
        """If any transaction in batch has hallucinated ID, entire batch rejected."""
        user = _guest(val_env)
        c, factory = val_env

        resp = c.post("/books", json={"name": "Test Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])
        legit_cat = uuid.uuid4()
        _seed_categories(factory, uuid.UUID(user["id"]), book_id, {legit_cat: "餐饮"})

        proposal = {
            "proposalId": "prop-015",
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
                    "categoryId": str(uuid.uuid4()),  # Hallucinated
                    "bizDate": "2026-09-06",
                    "note": "地铁",
                },
            ],
            "message": "检测到 2 笔交易",
            "warnings": [],
        }

        resp = _validate(val_env, user, book_id, proposal)
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert data["verdict"] == "rejected_hallucination"
        # Error should point to the second transaction
        assert any("[1]" in e["path"] for e in data["errors"])
