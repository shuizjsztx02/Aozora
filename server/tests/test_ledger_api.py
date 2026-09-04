"""T-M2-04: Ledger API tests — Books, Categories, Accounts, Transactions, Budgets."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from src.common.database import get_db_session
from src.models.base import Base
from src.models.user import User, UserStatus


# ── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture()
def auth_client():
    """TestClient with tables created and dependency override."""
    from src.main import app
    from src.modules.auth.dependencies import _rate_limits

    # Clear rate limiter for tests
    _rate_limits.clear()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    import asyncio
    asyncio.run(_create_tables(engine))

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
    _rate_limits.clear()


async def _create_tables(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture()
def user(auth_client):
    """Create a test user via the auth API."""
    resp = auth_client.post("/auth/guest", json={"device_id": "test-device-1", "nickname": "tester"})
    user_id = resp.json()["user_id"]
    token = resp.json()["tokens"]["access_token"]
    return {"id": user_id, "token": token}


@pytest.fixture()
def other_user(auth_client):
    """Create another test user for ownership tests."""
    resp = auth_client.post("/auth/guest", json={"device_id": "test-device-2", "nickname": "other"})
    user_id = resp.json()["user_id"]
    token = resp.json()["tokens"]["access_token"]
    return {"id": user_id, "token": token}


def _auth_header(user: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {user['token']}"}


# ── Books ───────────────────────────────────────────────────────────────

class TestBooksAPI:
    def test_create_book(self, auth_client, user):
        """POST /books should create a new book."""
        resp = auth_client.post(
            "/books",
            json={"name": "日常账本", "icon": "wallet"},
            headers=_auth_header(user),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "日常账本"
        assert data["icon"] == "wallet"
        assert data["version"] == 1

    def test_list_books(self, auth_client, user):
        """GET /books should list user's books."""
        auth_client.post("/books", json={"name": "Book 1"}, headers=_auth_header(user))
        auth_client.post("/books", json={"name": "Book 2"}, headers=_auth_header(user))

        resp = auth_client.get("/books", headers=_auth_header(user))
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    def test_get_book(self, auth_client, user):
        """GET /books/:id should return a specific book."""
        create_resp = auth_client.post("/books", json={"name": "Test"}, headers=_auth_header(user))
        book_id = create_resp.json()["id"]

        resp = auth_client.get(f"/books/{book_id}", headers=_auth_header(user))
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test"

    def test_update_book_with_version(self, auth_client, user):
        """PATCH /books/:id should update with version check."""
        create_resp = auth_client.post("/books", json={"name": "Old"}, headers=_auth_header(user))
        data = create_resp.json()
        book_id = data["id"]
        version = data["version"]

        resp = auth_client.patch(
            f"/books/{book_id}",
            json={"name": "New", "version": version},
            headers=_auth_header(user),
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "New"
        assert resp.json()["version"] == version + 1

    def test_update_book_version_conflict(self, auth_client, user):
        """PATCH /books/:id should return 409 on version mismatch."""
        create_resp = auth_client.post("/books", json={"name": "Test"}, headers=_auth_header(user))
        data = create_resp.json()
        book_id = data["id"]

        resp = auth_client.patch(
            f"/books/{book_id}",
            json={"name": "New", "version": 999},
            headers=_auth_header(user),
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "version_conflict"

    def test_delete_book(self, auth_client, user):
        """DELETE /books/:id should soft-delete."""
        create_resp = auth_client.post("/books", json={"name": "Test"}, headers=_auth_header(user))
        book_id = create_resp.json()["id"]

        resp = auth_client.delete(f"/books/{book_id}", headers=_auth_header(user))
        assert resp.status_code == 204

        list_resp = auth_client.get("/books", headers=_auth_header(user))
        assert list_resp.json()["total"] == 0

    def test_ownership_guard(self, auth_client, user, other_user):
        """User should not be able to access another user's book."""
        create_resp = auth_client.post("/books", json={"name": "Private"}, headers=_auth_header(user))
        book_id = create_resp.json()["id"]

        resp = auth_client.get(f"/books/{book_id}", headers=_auth_header(other_user))
        assert resp.status_code == 404


# ── Categories ──────────────────────────────────────────────────────────

class TestCategoriesAPI:
    def test_create_category(self, auth_client, user):
        """POST /categories should create a new category."""
        resp = auth_client.post(
            "/categories",
            json={"name": "餐饮", "kind": "expense"},
            headers=_auth_header(user),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "餐饮"
        assert data["kind"] == "expense"

    def test_list_categories_by_kind(self, auth_client, user):
        """GET /categories?kind=expense should filter by kind."""
        auth_client.post("/categories", json={"name": "餐饮", "kind": "expense"}, headers=_auth_header(user))
        auth_client.post("/categories", json={"name": "工资", "kind": "income"}, headers=_auth_header(user))

        resp = auth_client.get("/categories?kind=expense", headers=_auth_header(user))
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["kind"] == "expense"

    def test_archive_category(self, auth_client, user):
        """DELETE /categories/:id should archive (not delete)."""
        create_resp = auth_client.post("/categories", json={"name": "Test", "kind": "expense"}, headers=_auth_header(user))
        cat_id = create_resp.json()["id"]

        resp = auth_client.delete(f"/categories/{cat_id}", headers=_auth_header(user))
        assert resp.status_code == 204


# ── Accounts ────────────────────────────────────────────────────────────

class TestAccountsAPI:
    def test_create_account(self, auth_client, user):
        """POST /accounts should create a new account."""
        resp = auth_client.post(
            "/accounts",
            json={"name": "支付宝", "icon": "alipay"},
            headers=_auth_header(user),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "支付宝"

    def test_list_accounts(self, auth_client, user):
        """GET /accounts should list user's accounts."""
        auth_client.post("/accounts", json={"name": "A1"}, headers=_auth_header(user))
        auth_client.post("/accounts", json={"name": "A2"}, headers=_auth_header(user))

        resp = auth_client.get("/accounts", headers=_auth_header(user))
        assert resp.status_code == 200
        assert resp.json()["total"] == 2


# ── Transactions ────────────────────────────────────────────────────────

class TestTransactionsAPI:
    @pytest.fixture()
    def book_and_category(self, auth_client, user):
        """Create a book and category for transaction tests."""
        book_resp = auth_client.post("/books", json={"name": "Test Book"}, headers=_auth_header(user))
        book_id = book_resp.json()["id"]

        cat_resp = auth_client.post("/categories", json={"name": "餐饮", "kind": "expense"}, headers=_auth_header(user))
        cat_id = cat_resp.json()["id"]

        return book_id, cat_id

    def test_create_transaction(self, auth_client, user, book_and_category):
        """POST /transactions should create a new transaction."""
        book_id, cat_id = book_and_category
        resp = auth_client.post(
            "/transactions",
            json={
                "book_id": book_id,
                "type": "expense",
                "amount_cents": 1000,
                "category_id": cat_id,
                "note": "午餐",
                "biz_date": "2026-09-04",
                "occurred_at": "2026-09-04T12:00:00Z",
            },
            headers=_auth_header(user),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["amount_cents"] == 1000
        assert data["type"] == "expense"

    def test_list_transactions_by_book(self, auth_client, user, book_and_category):
        """GET /transactions?book_id=... should list transactions for a book."""
        book_id, cat_id = book_and_category
        auth_client.post("/transactions", json={
            "book_id": book_id, "type": "expense", "amount_cents": 100,
            "category_id": cat_id, "biz_date": "2026-09-01", "occurred_at": "2026-09-01T10:00:00Z",
        }, headers=_auth_header(user))
        auth_client.post("/transactions", json={
            "book_id": book_id, "type": "expense", "amount_cents": 200,
            "category_id": cat_id, "biz_date": "2026-09-02", "occurred_at": "2026-09-02T10:00:00Z",
        }, headers=_auth_header(user))

        resp = auth_client.get(f"/transactions?book_id={book_id}", headers=_auth_header(user))
        assert resp.status_code == 200
        assert resp.json()["total"] == 2

    def test_soft_delete_transaction(self, auth_client, user, book_and_category):
        """DELETE /transactions/:id should soft-delete."""
        book_id, cat_id = book_and_category
        create_resp = auth_client.post("/transactions", json={
            "book_id": book_id, "type": "expense", "amount_cents": 100,
            "category_id": cat_id, "biz_date": "2026-09-04", "occurred_at": "2026-09-04T12:00:00Z",
        }, headers=_auth_header(user))
        tx_id = create_resp.json()["id"]

        resp = auth_client.delete(f"/transactions/{tx_id}", headers=_auth_header(user))
        assert resp.status_code == 204

        list_resp = auth_client.get(f"/transactions?book_id={book_id}", headers=_auth_header(user))
        assert list_resp.json()["total"] == 0

    def test_undo_delete_transaction(self, auth_client, user, book_and_category):
        """POST /transactions/:id/undo should restore soft-deleted transaction."""
        book_id, cat_id = book_and_category
        create_resp = auth_client.post("/transactions", json={
            "book_id": book_id, "type": "expense", "amount_cents": 100,
            "category_id": cat_id, "biz_date": "2026-09-04", "occurred_at": "2026-09-04T12:00:00Z",
        }, headers=_auth_header(user))
        tx_id = create_resp.json()["id"]

        auth_client.delete(f"/transactions/{tx_id}", headers=_auth_header(user))

        resp = auth_client.post(f"/transactions/{tx_id}/undo", headers=_auth_header(user))
        assert resp.status_code == 200
        assert resp.json()["deleted_at"] is None


# ── Budgets ─────────────────────────────────────────────────────────────

class TestBudgetsAPI:
    @pytest.fixture()
    def book(self, auth_client, user):
        """Create a book for budget tests."""
        book_resp = auth_client.post("/books", json={"name": "Test Book"}, headers=_auth_header(user))
        return book_resp.json()["id"]

    def test_upsert_budget(self, auth_client, user, book):
        """PUT /budgets/:period should create or update a budget."""
        resp = auth_client.put(
            "/budgets/2026-09",
            json={
                "book_id": book,
                "budget_type": "monthly",
                "amount_cents": 500000,
            },
            headers=_auth_header(user),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["period"] == "2026-09"
        assert data["amount_cents"] == 500000

    def test_list_budgets(self, auth_client, user, book):
        """GET /budgets?book_id=... should list budgets."""
        auth_client.put("/budgets/2026-09", json={
            "book_id": book, "budget_type": "monthly", "amount_cents": 500000,
        }, headers=_auth_header(user))

        resp = auth_client.get(f"/budgets?book_id={book}", headers=_auth_header(user))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["period"] == "2026-09"


# ── Auth Required ───────────────────────────────────────────────────────

class TestAuthRequired:
    def test_books_require_auth(self, auth_client):
        """Endpoints should require authentication."""
        resp = auth_client.get("/books")
        assert resp.status_code in (401, 403)

    def test_categories_require_auth(self, auth_client):
        resp = auth_client.get("/categories")
        assert resp.status_code in (401, 403)

    def test_transactions_require_auth(self, auth_client):
        resp = auth_client.get("/transactions?book_id=00000000-0000-0000-0000-000000000000")
        assert resp.status_code in (401, 403)
