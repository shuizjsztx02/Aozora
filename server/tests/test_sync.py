"""T-M3-01: Tests for sync push / pull / bootstrap API."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.models.base import Base
from src.models.ledger import Book, Category, Account, CategoryKind
from src.models.user import User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _token(user_id: uuid.UUID) -> str:
    """Generate a valid access token for testing."""
    from src.modules.auth.utils import create_access_token
    return create_access_token(str(user_id))


def _auth_header(user_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(user_id)}"}


@pytest_asyncio.fixture()
async def db_session():
    """Provide an async session with all tables created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture()
async def auth_client(client, db_session):
    """Provide a test client with two authenticated users and base data."""

    # Create users
    user_a = User(id=uuid.uuid4(), nickname="alice", status="active")
    user_b = User(id=uuid.uuid4(), nickname="bob", status="active")
    db_session.add_all([user_a, user_b])
    await db_session.flush()

    # Create book for user A
    book_a = Book(id=uuid.uuid4(), owner_id=user_a.id, name="Alice Book")
    db_session.add(book_a)
    await db_session.flush()

    # Create categories for user A
    cat_expense_a = Category(
        id=uuid.uuid4(), owner_id=user_a.id, book_id=book_a.id,
        kind=CategoryKind.EXPENSE, name="餐饮",
    )
    cat_income_a = Category(
        id=uuid.uuid4(), owner_id=user_a.id, book_id=book_a.id,
        kind=CategoryKind.INCOME, name="工资",
    )
    db_session.add_all([cat_expense_a, cat_income_a])
    await db_session.flush()

    # Create account for user A
    acc_a = Account(id=uuid.uuid4(), owner_id=user_a.id, name="现金")
    db_session.add(acc_a)
    await db_session.commit()

    # Override the DB session dependency
    from src.common.database import get_db_session

    async def _override():
        yield db_session

    client.app.dependency_overrides[get_db_session] = _override

    yield {
        "client": client,
        "user_a": user_a,
        "user_b": user_b,
        "book_a": book_a,
        "cat_expense_a": cat_expense_a,
        "cat_income_a": cat_income_a,
        "acc_a": acc_a,
    }

    client.app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Push: Insert
# ---------------------------------------------------------------------------

class TestPushInsert:

    @pytest.mark.asyncio
    async def test_insert_transaction(self, auth_client):
        ctx = auth_client
        mid = uuid.uuid4()
        resp = ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(mid),
                        "entity_type": "transaction",
                        "operation": "insert",
                        "entity_id": str(uuid.uuid4()),
                        "payload": {
                            "book_id": str(ctx["book_a"].id),
                            "type": "expense",
                            "amount_cents": 5000,
                            "category_id": str(ctx["cat_expense_a"].id),
                            "account_id": str(ctx["acc_a"].id),
                            "note": "午餐",
                            "biz_date": "2026-09-04",
                            "occurred_at": "2026-09-04T12:00:00+08:00",
                        },
                    }
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 1
        r = data["results"][0]
        assert r["status"] == "applied"
        assert r["cursor"] is not None and r["cursor"] > 0
        assert r["version"] == 1

    @pytest.mark.asyncio
    async def test_insert_book(self, auth_client):
        ctx = auth_client
        new_book_id = uuid.uuid4()
        mid = uuid.uuid4()
        resp = ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(mid),
                        "entity_type": "book",
                        "operation": "insert",
                        "entity_id": str(new_book_id),
                        "payload": {"name": "新账本", "icon": "📒"},
                    }
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"][0]["status"] == "applied"

    @pytest.mark.asyncio
    async def test_insert_category(self, auth_client):
        ctx = auth_client
        mid = uuid.uuid4()
        resp = ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(mid),
                        "entity_type": "category",
                        "operation": "insert",
                        "entity_id": str(uuid.uuid4()),
                        "payload": {
                            "name": "交通",
                            "kind": "expense",
                            "book_id": str(ctx["book_a"].id),
                        },
                    }
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )
        assert resp.status_code == 200
        assert resp.json()["results"][0]["status"] == "applied"

    @pytest.mark.asyncio
    async def test_insert_account(self, auth_client):
        ctx = auth_client
        mid = uuid.uuid4()
        resp = ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(mid),
                        "entity_type": "account",
                        "operation": "insert",
                        "entity_id": str(uuid.uuid4()),
                        "payload": {"name": "银行卡"},
                    }
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )
        assert resp.status_code == 200
        assert resp.json()["results"][0]["status"] == "applied"

    @pytest.mark.asyncio
    async def test_insert_budget(self, auth_client):
        ctx = auth_client
        mid = uuid.uuid4()
        resp = ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(mid),
                        "entity_type": "budget",
                        "operation": "insert",
                        "entity_id": str(uuid.uuid4()),
                        "payload": {
                            "book_id": str(ctx["book_a"].id),
                            "budget_type": "monthly",
                            "amount_cents": 300000,
                            "category_id": str(ctx["cat_expense_a"].id),
                            "period": "2026-09",
                        },
                    }
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )
        assert resp.status_code == 200
        assert resp.json()["results"][0]["status"] == "applied"


# ---------------------------------------------------------------------------
# Push: Update / Delete
# ---------------------------------------------------------------------------

class TestPushUpdateDelete:

    @pytest.mark.asyncio
    async def test_update_transaction(self, auth_client, db_session):
        ctx = auth_client
        # First insert a transaction
        tx_id = uuid.uuid4()
        mid_insert = uuid.uuid4()
        resp = ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(mid_insert),
                        "entity_type": "transaction",
                        "operation": "insert",
                        "entity_id": str(tx_id),
                        "payload": {
                            "book_id": str(ctx["book_a"].id),
                            "type": "expense",
                            "amount_cents": 5000,
                            "category_id": str(ctx["cat_expense_a"].id),
                            "note": "午餐",
                            "biz_date": "2026-09-04",
                            "occurred_at": "2026-09-04T12:00:00+08:00",
                        },
                    }
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )
        assert resp.status_code == 200
        v1 = resp.json()["results"][0]["version"]
        assert v1 == 1

        # Now update it
        mid_update = uuid.uuid4()
        resp2 = ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(mid_update),
                        "entity_type": "transaction",
                        "operation": "update",
                        "entity_id": str(tx_id),
                        "version": 1,
                        "payload": {"amount_cents": 6000, "note": "晚餐"},
                    }
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )
        assert resp2.status_code == 200
        r = resp2.json()["results"][0]
        assert r["status"] == "applied"
        assert r["version"] == 2

    @pytest.mark.asyncio
    async def test_delete_transaction(self, auth_client):
        ctx = auth_client
        tx_id = uuid.uuid4()
        # Insert
        resp = ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "transaction",
                        "operation": "insert",
                        "entity_id": str(tx_id),
                        "payload": {
                            "book_id": str(ctx["book_a"].id),
                            "type": "expense",
                            "amount_cents": 3000,
                            "category_id": str(ctx["cat_expense_a"].id),
                            "biz_date": "2026-09-04",
                            "occurred_at": "2026-09-04T10:00:00+08:00",
                        },
                    }
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )
        assert resp.status_code == 200

        # Delete
        resp2 = ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "transaction",
                        "operation": "delete",
                        "entity_id": str(tx_id),
                        "version": 1,
                        "payload": {},
                    }
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )
        assert resp2.status_code == 200
        r = resp2.json()["results"][0]
        assert r["status"] == "applied"
        assert r["version"] == 2


# ---------------------------------------------------------------------------
# Push: Idempotency
# ---------------------------------------------------------------------------

class TestPushIdempotency:

    @pytest.mark.asyncio
    async def test_duplicate_mutation_id(self, auth_client):
        ctx = auth_client
        mid = uuid.uuid4()
        tx_id = uuid.uuid4()
        payload = {
            "mutations": [
                {
                    "mutation_id": str(mid),
                    "entity_type": "transaction",
                    "operation": "insert",
                    "entity_id": str(tx_id),
                    "payload": {
                        "book_id": str(ctx["book_a"].id),
                        "type": "expense",
                        "amount_cents": 1000,
                        "category_id": str(ctx["cat_expense_a"].id),
                        "biz_date": "2026-09-04",
                        "occurred_at": "2026-09-04T12:00:00+08:00",
                    },
                }
            ],
        }

        # First push
        resp1 = ctx["client"].post(
            "/sync/push", json=payload,
            headers=_auth_header(ctx["user_a"].id),
        )
        assert resp1.status_code == 200
        r1 = resp1.json()["results"][0]
        assert r1["status"] == "applied"

        # Second push with same mutation_id
        resp2 = ctx["client"].post(
            "/sync/push", json=payload,
            headers=_auth_header(ctx["user_a"].id),
        )
        assert resp2.status_code == 200
        r2 = resp2.json()["results"][0]
        assert r2["status"] == "duplicate"
        assert r2["cursor"] == r1["cursor"]
        assert r2["version"] == r1["version"]

    @pytest.mark.asyncio
    async def test_different_mutation_ids_are_independent(self, auth_client):
        ctx = auth_client
        resp = ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "transaction",
                        "operation": "insert",
                        "entity_id": str(uuid.uuid4()),
                        "payload": {
                            "book_id": str(ctx["book_a"].id),
                            "type": "expense",
                            "amount_cents": 1000,
                            "category_id": str(ctx["cat_expense_a"].id),
                            "biz_date": "2026-09-04",
                            "occurred_at": "2026-09-04T12:00:00+08:00",
                        },
                    },
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "transaction",
                        "operation": "insert",
                        "entity_id": str(uuid.uuid4()),
                        "payload": {
                            "book_id": str(ctx["book_a"].id),
                            "type": "income",
                            "amount_cents": 50000,
                            "category_id": str(ctx["cat_income_a"].id),
                            "biz_date": "2026-09-04",
                            "occurred_at": "2026-09-04T09:00:00+08:00",
                        },
                    },
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert len(results) == 2
        assert all(r["status"] == "applied" for r in results)
        # Cursors should be different (ascending)
        assert results[0]["cursor"] < results[1]["cursor"]


# ---------------------------------------------------------------------------
# Push: Version Conflict
# ---------------------------------------------------------------------------

class TestPushConflict:

    @pytest.mark.asyncio
    async def test_version_conflict_stops_processing(self, auth_client):
        ctx = auth_client
        tx_id = uuid.uuid4()
        # Insert a transaction
        resp1 = ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "transaction",
                        "operation": "insert",
                        "entity_id": str(tx_id),
                        "payload": {
                            "book_id": str(ctx["book_a"].id),
                            "type": "expense",
                            "amount_cents": 1000,
                            "category_id": str(ctx["cat_expense_a"].id),
                            "biz_date": "2026-09-04",
                            "occurred_at": "2026-09-04T12:00:00+08:00",
                        },
                    }
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )
        assert resp1.status_code == 200

        # Update to version 2
        resp2 = ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "transaction",
                        "operation": "update",
                        "entity_id": str(tx_id),
                        "version": 1,
                        "payload": {"note": "updated"},
                    }
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )
        assert resp2.status_code == 200
        assert resp2.json()["results"][0]["version"] == 2

        # Now try to update with stale version=1, followed by another mutation
        tx_id2 = uuid.uuid4()
        resp3 = ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "transaction",
                        "operation": "update",
                        "entity_id": str(tx_id),
                        "version": 1,  # stale! current is 2
                        "payload": {"note": "conflict"},
                    },
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "transaction",
                        "operation": "insert",
                        "entity_id": str(tx_id2),
                        "payload": {
                            "book_id": str(ctx["book_a"].id),
                            "type": "expense",
                            "amount_cents": 2000,
                            "category_id": str(ctx["cat_expense_a"].id),
                            "biz_date": "2026-09-04",
                            "occurred_at": "2026-09-04T14:00:00+08:00",
                        },
                    },
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )
        assert resp3.status_code == 200
        results = resp3.json()["results"]
        assert len(results) == 1  # second mutation was NOT processed
        assert results[0]["status"] == "conflict"
        assert results[0]["current_version"] == 2

    @pytest.mark.asyncio
    async def test_conflict_does_not_rollback_prior_mutations(self, auth_client):
        """Mutations before the conflict should still be committed."""
        ctx = auth_client
        tx_id = uuid.uuid4()
        # Insert first
        ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "transaction",
                        "operation": "insert",
                        "entity_id": str(tx_id),
                        "payload": {
                            "book_id": str(ctx["book_a"].id),
                            "type": "expense",
                            "amount_cents": 1000,
                            "category_id": str(ctx["cat_expense_a"].id),
                            "biz_date": "2026-09-04",
                            "occurred_at": "2026-09-04T12:00:00+08:00",
                        },
                    }
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )

        # Push: [successful insert, conflicting update]
        new_tx_id = uuid.uuid4()
        resp = ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "transaction",
                        "operation": "insert",
                        "entity_id": str(new_tx_id),
                        "payload": {
                            "book_id": str(ctx["book_a"].id),
                            "type": "income",
                            "amount_cents": 50000,
                            "category_id": str(ctx["cat_income_a"].id),
                            "biz_date": "2026-09-04",
                            "occurred_at": "2026-09-04T09:00:00+08:00",
                        },
                    },
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "transaction",
                        "operation": "update",
                        "entity_id": str(tx_id),
                        "version": 99,  # stale!
                        "payload": {"note": "conflict"},
                    },
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )
        results = resp.json()["results"]
        assert results[0]["status"] == "applied"  # first mutation committed
        assert results[1]["status"] == "conflict"  # second stopped


# ---------------------------------------------------------------------------
# Push: Validation
# ---------------------------------------------------------------------------

class TestPushValidation:

    @pytest.mark.asyncio
    async def test_empty_mutations_rejected(self, auth_client):
        resp = auth_client["client"].post(
            "/sync/push",
            json={"mutations": []},
            headers=_auth_header(auth_client["user_a"].id),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_batch_too_large(self, auth_client):
        mutations = [
            {
                "mutation_id": str(uuid.uuid4()),
                "entity_type": "account",
                "operation": "insert",
                "entity_id": str(uuid.uuid4()),
                "payload": {"name": f"acc-{i}"},
            }
            for i in range(101)
        ]
        resp = auth_client["client"].post(
            "/sync/push",
            json={"mutations": mutations},
            headers=_auth_header(auth_client["user_a"].id),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_entity_type_rejected(self, auth_client):
        resp = auth_client["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "invalid",
                        "operation": "insert",
                        "entity_id": str(uuid.uuid4()),
                        "payload": {},
                    }
                ],
            },
            headers=_auth_header(auth_client["user_a"].id),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_update_without_version_rejected(self, auth_client):
        resp = auth_client["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "book",
                        "operation": "update",
                        "entity_id": str(uuid.uuid4()),
                        "version": None,
                        "payload": {"name": "test"},
                    }
                ],
            },
            headers=_auth_header(auth_client["user_a"].id),
        )
        # Should get 422 (validation) or 422 from service
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_cross_user_entity_not_found(self, auth_client):
        """User B cannot operate on User A's entities — gets 404."""
        ctx = auth_client
        resp = ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "book",
                        "operation": "update",
                        "entity_id": str(ctx["book_a"].id),
                        "version": 1,
                        "payload": {"name": "hacked"},
                    }
                ],
            },
            headers=_auth_header(ctx["user_b"].id),
        )
        # Cross-user access returns 404 (AppException propagates)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Push: Unauthenticated
# ---------------------------------------------------------------------------

class TestPushAuth:

    def test_push_without_token(self, client):
        resp = client.post(
            "/sync/push",
            json={"mutations": [
                {
                    "mutation_id": str(uuid.uuid4()),
                    "entity_type": "book",
                    "operation": "insert",
                    "entity_id": str(uuid.uuid4()),
                    "payload": {"name": "test"},
                }
            ]},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Pull
# ---------------------------------------------------------------------------

class TestPull:

    @pytest.mark.asyncio
    async def test_pull_empty(self, auth_client):
        resp = auth_client["client"].get(
            "/sync/pull?cursor=0",
            headers=_auth_header(auth_client["user_a"].id),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["changes"] == []
        assert data["cursor"] == 0
        assert data["has_more"] is False

    @pytest.mark.asyncio
    async def test_pull_returns_changes(self, auth_client):
        ctx = auth_client
        # Push a mutation
        ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "transaction",
                        "operation": "insert",
                        "entity_id": str(uuid.uuid4()),
                        "payload": {
                            "book_id": str(ctx["book_a"].id),
                            "type": "expense",
                            "amount_cents": 1000,
                            "category_id": str(ctx["cat_expense_a"].id),
                            "biz_date": "2026-09-04",
                            "occurred_at": "2026-09-04T12:00:00+08:00",
                        },
                    }
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )

        # Pull all changes
        resp = ctx["client"].get(
            "/sync/pull?cursor=0",
            headers=_auth_header(ctx["user_a"].id),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["changes"]) == 1
        assert data["changes"][0]["entity_type"] == "transaction"
        assert data["changes"][0]["operation"] == "insert"
        assert data["changes"][0]["entity"] is not None
        assert data["changes"][0]["entity"]["amount_cents"] == 1000
        assert data["has_more"] is False

    @pytest.mark.asyncio
    async def test_pull_pagination(self, auth_client):
        ctx = auth_client
        # Push 3 mutations
        for i in range(3):
            ctx["client"].post(
                "/sync/push",
                json={
                    "mutations": [
                        {
                            "mutation_id": str(uuid.uuid4()),
                            "entity_type": "transaction",
                            "operation": "insert",
                            "entity_id": str(uuid.uuid4()),
                            "payload": {
                                "book_id": str(ctx["book_a"].id),
                                "type": "expense",
                                "amount_cents": (i + 1) * 1000,
                                "category_id": str(ctx["cat_expense_a"].id),
                                "biz_date": "2026-09-04",
                                "occurred_at": "2026-09-04T12:00:00+08:00",
                            },
                        }
                    ],
                },
                headers=_auth_header(ctx["user_a"].id),
            )

        # Pull with limit=2
        resp = ctx["client"].get(
            "/sync/pull?cursor=0&limit=2",
            headers=_auth_header(ctx["user_a"].id),
        )
        data = resp.json()
        assert len(data["changes"]) == 2
        assert data["has_more"] is True
        last_cursor = data["cursor"]

        # Pull remaining
        resp2 = ctx["client"].get(
            f"/sync/pull?cursor={last_cursor}&limit=2",
            headers=_auth_header(ctx["user_a"].id),
        )
        data2 = resp2.json()
        assert len(data2["changes"]) == 1
        assert data2["has_more"] is False

    @pytest.mark.asyncio
    async def test_pull_delete_has_null_entity(self, auth_client):
        ctx = auth_client
        tx_id = uuid.uuid4()
        # Insert
        ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "transaction",
                        "operation": "insert",
                        "entity_id": str(tx_id),
                        "payload": {
                            "book_id": str(ctx["book_a"].id),
                            "type": "expense",
                            "amount_cents": 1000,
                            "category_id": str(ctx["cat_expense_a"].id),
                            "biz_date": "2026-09-04",
                            "occurred_at": "2026-09-04T12:00:00+08:00",
                        },
                    }
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )
        # Delete
        ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "transaction",
                        "operation": "delete",
                        "entity_id": str(tx_id),
                        "version": 1,
                        "payload": {},
                    }
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )

        # Pull all
        resp = ctx["client"].get(
            "/sync/pull?cursor=0",
            headers=_auth_header(ctx["user_a"].id),
        )
        data = resp.json()
        assert len(data["changes"]) == 2
        # First change: insert (entity present)
        assert data["changes"][0]["operation"] == "insert"
        assert data["changes"][0]["entity"] is not None
        # Second change: delete (entity null)
        assert data["changes"][1]["operation"] == "delete"
        assert data["changes"][1]["entity"] is None

    @pytest.mark.asyncio
    async def test_pull_cross_user_isolation(self, auth_client):
        ctx = auth_client
        # User A pushes a mutation
        ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "account",
                        "operation": "insert",
                        "entity_id": str(uuid.uuid4()),
                        "payload": {"name": "A's account"},
                    }
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )

        # User B pulls — should see no changes
        resp = ctx["client"].get(
            "/sync/pull?cursor=0",
            headers=_auth_header(ctx["user_b"].id),
        )
        assert resp.status_code == 200
        assert resp.json()["changes"] == []

    @pytest.mark.asyncio
    async def test_pull_resume_after_disconnect(self, auth_client):
        """Pulling from the same cursor twice returns the same results."""
        ctx = auth_client
        # Push 2 mutations
        for _ in range(2):
            ctx["client"].post(
                "/sync/push",
                json={
                    "mutations": [
                        {
                            "mutation_id": str(uuid.uuid4()),
                            "entity_type": "account",
                            "operation": "insert",
                            "entity_id": str(uuid.uuid4()),
                            "payload": {"name": "acc"},
                        }
                    ],
                },
                headers=_auth_header(ctx["user_a"].id),
            )

        # Pull from 0
        resp1 = ctx["client"].get(
            "/sync/pull?cursor=0",
            headers=_auth_header(ctx["user_a"].id),
        )
        data1 = resp1.json()
        assert len(data1["changes"]) == 2

        # Pull from 0 again (simulating reconnect)
        resp2 = ctx["client"].get(
            "/sync/pull?cursor=0",
            headers=_auth_header(ctx["user_a"].id),
        )
        data2 = resp2.json()
        assert data1 == data2


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

class TestBootstrap:

    @pytest.mark.asyncio
    async def test_bootstrap_empty(self, auth_client, db_session):
        """A user with no data gets empty bootstrap."""
        ctx = auth_client
        # Create a new user with no data
        empty_user = User(id=uuid.uuid4(), nickname="empty", status="active")
        db_session.add(empty_user)
        await db_session.flush()

        resp = ctx["client"].post(
            "/sync/bootstrap",
            headers=_auth_header(empty_user.id),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["books"] == []
        assert data["transactions"] == []
        assert data["categories"] == []
        assert data["accounts"] == []
        assert data["budgets"] == []
        assert data["cursor"] == 0

    @pytest.mark.asyncio
    async def test_bootstrap_returns_all_data(self, auth_client):
        ctx = auth_client
        # Push some data
        ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "transaction",
                        "operation": "insert",
                        "entity_id": str(uuid.uuid4()),
                        "payload": {
                            "book_id": str(ctx["book_a"].id),
                            "type": "expense",
                            "amount_cents": 5000,
                            "category_id": str(ctx["cat_expense_a"].id),
                            "biz_date": "2026-09-04",
                            "occurred_at": "2026-09-04T12:00:00+08:00",
                        },
                    }
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )

        resp = ctx["client"].post(
            "/sync/bootstrap",
            headers=_auth_header(ctx["user_a"].id),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["books"]) == 1
        assert data["books"][0]["name"] == "Alice Book"
        assert len(data["categories"]) == 2
        assert len(data["accounts"]) == 1
        assert len(data["transactions"]) == 1
        assert data["transactions"][0]["amount_cents"] == 5000
        assert data["cursor"] > 0

    @pytest.mark.asyncio
    async def test_bootstrap_excludes_deleted(self, auth_client):
        ctx = auth_client
        tx_id = uuid.uuid4()
        # Insert
        ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "transaction",
                        "operation": "insert",
                        "entity_id": str(tx_id),
                        "payload": {
                            "book_id": str(ctx["book_a"].id),
                            "type": "expense",
                            "amount_cents": 1000,
                            "category_id": str(ctx["cat_expense_a"].id),
                            "biz_date": "2026-09-04",
                            "occurred_at": "2026-09-04T12:00:00+08:00",
                        },
                    }
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )
        # Delete
        ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "transaction",
                        "operation": "delete",
                        "entity_id": str(tx_id),
                        "version": 1,
                        "payload": {},
                    }
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )

        # Bootstrap should not include the deleted transaction
        resp = ctx["client"].post(
            "/sync/bootstrap",
            headers=_auth_header(ctx["user_a"].id),
        )
        data = resp.json()
        assert len(data["transactions"]) == 0

    @pytest.mark.asyncio
    async def test_bootstrap_cross_user_isolation(self, auth_client):
        ctx = auth_client
        # User A has data
        resp = ctx["client"].post(
            "/sync/bootstrap",
            headers=_auth_header(ctx["user_b"].id),
        )
        data = resp.json()
        # User B should see nothing (book_a belongs to A)
        assert data["books"] == []
        assert data["transactions"] == []


# ---------------------------------------------------------------------------
# Integration: Idempotent retry after conflict
# ---------------------------------------------------------------------------

class TestIntegration:

    @pytest.mark.asyncio
    async def test_push_retry_after_conflict(self, auth_client):
        """Push [A, B] → B conflicts → fix → push [A, B'] → A duplicate, B' applied."""
        ctx = auth_client
        tx_id = uuid.uuid4()
        mid_a = uuid.uuid4()
        mid_b = uuid.uuid4()

        # Insert a transaction first
        ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "transaction",
                        "operation": "insert",
                        "entity_id": str(tx_id),
                        "payload": {
                            "book_id": str(ctx["book_a"].id),
                            "type": "expense",
                            "amount_cents": 1000,
                            "category_id": str(ctx["cat_expense_a"].id),
                            "biz_date": "2026-09-04",
                            "occurred_at": "2026-09-04T12:00:00+08:00",
                        },
                    }
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )

        # Push [update with v99 (conflict), insert new]
        new_tx_id = uuid.uuid4()
        resp1 = ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(mid_a),
                        "entity_type": "transaction",
                        "operation": "update",
                        "entity_id": str(tx_id),
                        "version": 99,  # stale
                        "payload": {"note": "will conflict"},
                    },
                    {
                        "mutation_id": str(mid_b),
                        "entity_type": "transaction",
                        "operation": "insert",
                        "entity_id": str(new_tx_id),
                        "payload": {
                            "book_id": str(ctx["book_a"].id),
                            "type": "expense",
                            "amount_cents": 2000,
                            "category_id": str(ctx["cat_expense_a"].id),
                            "biz_date": "2026-09-04",
                            "occurred_at": "2026-09-04T14:00:00+08:00",
                        },
                    },
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )
        r1 = resp1.json()["results"]
        assert r1[0]["status"] == "conflict"
        assert len(r1) == 1  # second not processed

        # Client fixes: re-push [A (now correct version), B]
        resp2 = ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(mid_a),
                        "entity_type": "transaction",
                        "operation": "update",
                        "entity_id": str(tx_id),
                        "version": 1,  # correct!
                        "payload": {"note": "fixed"},
                    },
                    {
                        "mutation_id": str(mid_b),
                        "entity_type": "transaction",
                        "operation": "insert",
                        "entity_id": str(new_tx_id),
                        "payload": {
                            "book_id": str(ctx["book_a"].id),
                            "type": "expense",
                            "amount_cents": 2000,
                            "category_id": str(ctx["cat_expense_a"].id),
                            "biz_date": "2026-09-04",
                            "occurred_at": "2026-09-04T14:00:00+08:00",
                        },
                    },
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )
        r2 = resp2.json()["results"]
        assert r2[0]["status"] == "applied"
        assert r2[1]["status"] == "applied"

    @pytest.mark.asyncio
    async def test_push_then_pull_consistency(self, auth_client):
        """After push, pull returns the same changes."""
        ctx = auth_client
        # Push 2 mutations
        resp_push = ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "account",
                        "operation": "insert",
                        "entity_id": str(uuid.uuid4()),
                        "payload": {"name": "card"},
                    },
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "account",
                        "operation": "insert",
                        "entity_id": str(uuid.uuid4()),
                        "payload": {"name": "wallet"},
                    },
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )
        push_cursor = resp_push.json()["cursor"]

        # Pull from 0
        resp_pull = ctx["client"].get(
            "/sync/pull?cursor=0",
            headers=_auth_header(ctx["user_a"].id),
        )
        data = resp_pull.json()
        assert len(data["changes"]) == 2
        assert data["cursor"] == push_cursor

    @pytest.mark.asyncio
    async def test_full_cycle_insert_update_delete_pull(self, auth_client):
        """Complete lifecycle: insert → update → delete → pull all."""
        ctx = auth_client
        tx_id = uuid.uuid4()

        # Insert
        r1 = ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "transaction",
                        "operation": "insert",
                        "entity_id": str(tx_id),
                        "payload": {
                            "book_id": str(ctx["book_a"].id),
                            "type": "expense",
                            "amount_cents": 1000,
                            "category_id": str(ctx["cat_expense_a"].id),
                            "biz_date": "2026-09-04",
                            "occurred_at": "2026-09-04T12:00:00+08:00",
                        },
                    }
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )
        assert r1.json()["results"][0]["version"] == 1

        # Update
        r2 = ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "transaction",
                        "operation": "update",
                        "entity_id": str(tx_id),
                        "version": 1,
                        "payload": {"amount_cents": 2000},
                    }
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )
        assert r2.json()["results"][0]["version"] == 2

        # Delete
        r3 = ctx["client"].post(
            "/sync/push",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "transaction",
                        "operation": "delete",
                        "entity_id": str(tx_id),
                        "version": 2,
                        "payload": {},
                    }
                ],
            },
            headers=_auth_header(ctx["user_a"].id),
        )
        assert r3.json()["results"][0]["version"] == 3

        # Pull all
        r4 = ctx["client"].get(
            "/sync/pull?cursor=0",
            headers=_auth_header(ctx["user_a"].id),
        )
        changes = r4.json()["changes"]
        assert len(changes) == 3
        assert changes[0]["operation"] == "insert"
        assert changes[1]["operation"] == "update"
        assert changes[2]["operation"] == "delete"
        assert changes[2]["entity"] is None

