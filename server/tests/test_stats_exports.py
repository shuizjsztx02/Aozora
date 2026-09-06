"""T-M2-05: Statistics and Exports API tests."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from src.common.database import get_db_session
from src.models.base import Base

# ── Fixtures ────────────────────────────────────────────────────────────

@pytest_asyncio.fixture()
async def auth_client():
    """TestClient with tables created and dependency override."""
    from src.main import app
    from src.modules.auth.dependencies import _rate_limits

    _rate_limits.clear()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
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
        # Expose the session factory so tests can manipulate rows that
        # the API cannot (e.g. forcing an export to be expired, N-06).
        c.db_factory = factory
        yield c

    app.dependency_overrides.clear()
    _rate_limits.clear()
    await engine.dispose()


@pytest.fixture()
def user(auth_client):
    resp = auth_client.post("/auth/guest", json={"device_id": "stats-test-1", "nickname": "stats-user"})
    return {"id": resp.json()["user_id"], "token": resp.json()["tokens"]["access_token"]}


@pytest.fixture()
def other_user(auth_client):
    resp = auth_client.post("/auth/guest", json={"device_id": "stats-test-2", "nickname": "other"})
    return {"id": resp.json()["user_id"], "token": resp.json()["tokens"]["access_token"]}


def _auth(user: dict) -> dict:
    return {"Authorization": f"Bearer {user['token']}"}


@pytest.fixture()
def book_data(auth_client, user):
    """Create a book with some transactions for statistics tests."""
    book_resp = auth_client.post("/books", json={"name": "Stats Book"}, headers=_auth(user))
    book_id = book_resp.json()["id"]

    # Create categories
    food_resp = auth_client.post("/categories", json={"name": "餐饮", "kind": "expense"}, headers=_auth(user))
    food_id = food_resp.json()["id"]

    salary_resp = auth_client.post("/categories", json={"name": "工资", "kind": "income"}, headers=_auth(user))
    salary_id = salary_resp.json()["id"]

    # Create transactions in September 2026
    auth_client.post("/transactions", json={
        "book_id": book_id, "type": "expense", "amount_cents": 3000,
        "category_id": food_id, "note": "午餐",
        "biz_date": "2026-09-01", "occurred_at": "2026-09-01T12:00:00Z",
    }, headers=_auth(user))

    auth_client.post("/transactions", json={
        "book_id": book_id, "type": "expense", "amount_cents": 2000,
        "category_id": food_id, "note": "晚餐",
        "biz_date": "2026-09-15", "occurred_at": "2026-09-15T19:00:00Z",
    }, headers=_auth(user))

    auth_client.post("/transactions", json={
        "book_id": book_id, "type": "income", "amount_cents": 50000,
        "category_id": salary_id, "note": "月薪",
        "biz_date": "2026-09-01", "occurred_at": "2026-09-01T09:00:00Z",
    }, headers=_auth(user))

    return {
        "book_id": book_id,
        "food_id": food_id,
        "salary_id": salary_id,
    }


# ── Statistics ──────────────────────────────────────────────────────────

class TestMonthlyStats:
    def test_monthly_stats_correct(self, auth_client, user, book_data):
        """GET /stats/monthly should aggregate correctly from transactions."""
        resp = auth_client.get(
            "/stats/monthly",
            params={"book_id": book_data["book_id"], "period": "2026-09"},
            headers=_auth(user),
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["period"] == "2026-09"
        assert data["total_income"] == 50000
        assert data["total_expense"] == 5000  # 3000 + 2000
        assert data["balance"] == 45000  # max(0, 50000 - 5000)
        assert data["transaction_count"] == 3

    def test_monthly_stats_category_breakdown(self, auth_client, user, book_data):
        """Monthly stats should include per-category breakdown."""
        resp = auth_client.get(
            "/stats/monthly",
            params={"book_id": book_data["book_id"], "period": "2026-09"},
            headers=_auth(user),
        )
        data = resp.json()

        # Expense breakdown
        assert len(data["expense_by_category"]) == 1
        assert data["expense_by_category"][0]["category_name"] == "餐饮"
        assert data["expense_by_category"][0]["total_cents"] == 5000
        assert data["expense_by_category"][0]["count"] == 2

        # Income breakdown
        assert len(data["income_by_category"]) == 1
        assert data["income_by_category"][0]["category_name"] == "工资"
        assert data["income_by_category"][0]["total_cents"] == 50000

    def test_monthly_stats_empty_period(self, auth_client, user, book_data):
        """Empty period should return zeros."""
        resp = auth_client.get(
            "/stats/monthly",
            params={"book_id": book_data["book_id"], "period": "2026-08"},
            headers=_auth(user),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_income"] == 0
        assert data["total_expense"] == 0
        assert data["balance"] == 0
        assert data["transaction_count"] == 0

    def test_monthly_stats_balance_non_negative(self, auth_client, user):
        """Balance = max(0, income - expense), never negative."""
        book_resp = auth_client.post("/books", json={"name": "Deficit"}, headers=_auth(user))
        book_id = book_resp.json()["id"]

        cat_resp = auth_client.post("/categories", json={"name": "支出", "kind": "expense"}, headers=_auth(user))
        cat_id = cat_resp.json()["id"]

        # Only expense, no income
        auth_client.post("/transactions", json={
            "book_id": book_id, "type": "expense", "amount_cents": 10000,
            "category_id": cat_id, "biz_date": "2026-09-01", "occurred_at": "2026-09-01T12:00:00Z",
        }, headers=_auth(user))

        resp = auth_client.get(
            "/stats/monthly",
            params={"book_id": book_id, "period": "2026-09"},
            headers=_auth(user),
        )
        data = resp.json()
        assert data["total_income"] == 0
        assert data["total_expense"] == 10000
        assert data["balance"] == 0  # max(0, 0 - 10000) = 0

    def test_stats_excludes_deleted_transactions(self, auth_client, user, book_data):
        """Deleted transactions should not be counted."""
        # Create a transaction then delete it
        tx_resp = auth_client.post("/transactions", json={
            "book_id": book_data["book_id"], "type": "expense", "amount_cents": 9999,
            "category_id": book_data["food_id"],
            "biz_date": "2026-09-20", "occurred_at": "2026-09-20T12:00:00Z",
        }, headers=_auth(user))
        tx_id = tx_resp.json()["id"]
        auth_client.delete(f"/transactions/{tx_id}", headers=_auth(user))

        resp = auth_client.get(
            "/stats/monthly",
            params={"book_id": book_data["book_id"], "period": "2026-09"},
            headers=_auth(user),
        )
        data = resp.json()
        # The deleted transaction should not be counted
        assert data["total_expense"] == 5000  # original 3000 + 2000, not +9999
        assert data["transaction_count"] == 3

    def test_stats_ownership_guard(self, auth_client, user, other_user, book_data):
        """User should not be able to access another user's stats."""
        resp = auth_client.get(
            "/stats/monthly",
            params={"book_id": book_data["book_id"], "period": "2026-09"},
            headers=_auth(other_user),
        )
        assert resp.status_code == 404


class TestTrendStats:
    def test_trend_fills_empty_months(self, auth_client, user, book_data):
        """Trend should include empty months in the range."""
        resp = auth_client.get(
            "/stats/trends",
            params={
                "book_id": book_data["book_id"],
                "period_from": "2026-07",
                "period_to": "2026-10",
            },
            headers=_auth(user),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["points"]) == 4  # Jul, Aug, Sep, Oct

        # July and August should be empty
        assert data["points"][0]["period"] == "2026-07"
        assert data["points"][0]["transaction_count"] == 0
        assert data["points"][1]["period"] == "2026-08"
        assert data["points"][1]["transaction_count"] == 0

        # September has data
        assert data["points"][2]["period"] == "2026-09"
        assert data["points"][2]["transaction_count"] == 3

        # October is empty
        assert data["points"][3]["period"] == "2026-10"
        assert data["points"][3]["transaction_count"] == 0

    def test_stats_reconciliation(self, auth_client, user, book_data):
        """Transaction list sum must equal statistics totals — 对账不变量."""
        # Get stats
        stats_resp = auth_client.get(
            "/stats/monthly",
            params={"book_id": book_data["book_id"], "period": "2026-09"},
            headers=_auth(user),
        )
        stats = stats_resp.json()

        # Get transaction list
        tx_resp = auth_client.get(
            f"/transactions?book_id={book_data['book_id']}",
            headers=_auth(user),
        )
        transactions = tx_resp.json()["items"]

        # Manual sum
        manual_income = sum(tx["amount_cents"] for tx in transactions if tx["type"] == "income")
        manual_expense = sum(tx["amount_cents"] for tx in transactions if tx["type"] == "expense")

        assert stats["total_income"] == manual_income
        assert stats["total_expense"] == manual_expense
        assert stats["transaction_count"] == len(transactions)


# ── Period validation (N-02) ───────────────────────────────────────────

class TestPeriodValidation:
    """N-02 regression: invalid ``period`` and reversed range must return 422,
    not 500 from uncaught ``ValueError``."""

    @pytest.fixture()
    def real_book(self, auth_client, user):
        """Create a real book so ownership check passes before period validation."""
        book_resp = auth_client.post("/books", json={"name": "Period Test"}, headers=_auth(user))
        return book_resp.json()["id"]

    def test_invalid_period_month_rejected(self, auth_client, user, real_book):
        resp = auth_client.get(
            f"/stats/monthly?book_id={real_book}&period=2026-13",
            headers=_auth(user),
        )
        assert resp.status_code == 422

    def test_period_zero_month_rejected(self, auth_client, user, real_book):
        resp = auth_client.get(
            f"/stats/monthly?book_id={real_book}&period=2026-00",
            headers=_auth(user),
        )
        assert resp.status_code == 422

    def test_period_range_order_rejected(self, auth_client, user, real_book):
        resp = auth_client.get(
            f"/stats/trends?book_id={real_book}&period_from=2026-12&period_to=2026-01",
            headers=_auth(user),
        )
        assert resp.status_code == 422

    def test_december_boundary_monthly_stats(self, auth_client, user, real_book):
        """N-36 regression: period=2026-12 must return 200 (not 500).

        Catches N-33: ``month == "12"`` (str) vs ``int(12)`` always False → ValueError.
        """
        resp = auth_client.get(
            f"/stats/monthly?book_id={real_book}&period=2026-12",
            headers=_auth(user),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["period"] == "2026-12"

    def test_december_boundary_trend_stats(self, auth_client, user, real_book):
        """N-36 regression: trends ending in December must not 500.

        Catches N-33 in get_trend_stats: ``tm == "12"`` (str) vs int(12).
        Bug triggers when period_to is December.
        """
        resp = auth_client.get(
            f"/stats/trends?book_id={real_book}&period_from=2026-10&period_to=2026-12",
            headers=_auth(user),
        )
        assert resp.status_code == 200
        data = resp.json()
        periods = [p["period"] for p in data["points"]]
        assert "2026-12" in periods


# ── Exports ─────────────────────────────────────────────────────────────

class TestExportsAPI:
    def test_create_json_export(self, auth_client, user, book_data):
        """POST /exports should create a JSON export."""
        resp = auth_client.post(
            "/exports",
            json={"book_id": book_data["book_id"], "format": "json"},
            headers=_auth(user),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "completed"
        assert data["format"] == "json"
        assert data["file_size"] > 0

    def test_create_csv_export(self, auth_client, user, book_data):
        """POST /exports should create a CSV export."""
        resp = auth_client.post(
            "/exports",
            json={"book_id": book_data["book_id"], "format": "csv"},
            headers=_auth(user),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "completed"
        assert data["format"] == "csv"

    def test_download_json_export(self, auth_client, user, book_data):
        """GET /exports/:id/download should return the file."""
        # Create export
        create_resp = auth_client.post(
            "/exports",
            json={"book_id": book_data["book_id"], "format": "json"},
            headers=_auth(user),
        )
        export_id = create_resp.json()["id"]

        # Download
        resp = auth_client.get(f"/exports/{export_id}/download", headers=_auth(user))
        assert resp.status_code == 200

        # Verify JSON content
        data = resp.json()
        assert len(data) == 3  # 3 transactions
        # Check first transaction
        amounts = {item["amount_cents"] for item in data}
        assert 3000 in amounts
        assert 2000 in amounts
        assert 50000 in amounts

    def test_download_csv_export(self, auth_client, user, book_data):
        """GET /exports/:id/download for CSV."""
        create_resp = auth_client.post(
            "/exports",
            json={"book_id": book_data["book_id"], "format": "csv"},
            headers=_auth(user),
        )
        export_id = create_resp.json()["id"]

        resp = auth_client.get(f"/exports/{export_id}/download", headers=_auth(user))
        assert resp.status_code == 200
        content = resp.text
        lines = content.strip().split("\n")
        assert len(lines) == 4  # header + 3 transactions

    def test_export_ownership_guard(self, auth_client, user, other_user, book_data):
        """User should not access another user's export."""
        # Create export as user
        create_resp = auth_client.post(
            "/exports",
            json={"book_id": book_data["book_id"], "format": "json"},
            headers=_auth(user),
        )
        export_id = create_resp.json()["id"]

        # Try to access as other_user
        resp = auth_client.get(f"/exports/{export_id}", headers=_auth(other_user))
        assert resp.status_code == 404

    def test_export_download_cross_user_blocked(self, auth_client, user, other_user, book_data):
        """Download link should not be accessible cross-user."""
        create_resp = auth_client.post(
            "/exports",
            json={"book_id": book_data["book_id"], "format": "json"},
            headers=_auth(user),
        )
        export_id = create_resp.json()["id"]

        resp = auth_client.get(f"/exports/{export_id}/download", headers=_auth(other_user))
        assert resp.status_code == 404

    def test_list_exports(self, auth_client, user, book_data):
        """GET /exports should list user's exports."""
        auth_client.post(
            "/exports",
            json={"book_id": book_data["book_id"], "format": "json"},
            headers=_auth(user),
        )
        auth_client.post(
            "/exports",
            json={"book_id": book_data["book_id"], "format": "csv"},
            headers=_auth(user),
        )

        resp = auth_client.get("/exports", headers=_auth(user))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_export_reconciliation(self, auth_client, user, book_data):
        """Export totals must match stats totals — 对账不变量."""
        # Get stats
        stats_resp = auth_client.get(
            "/stats/monthly",
            params={"book_id": book_data["book_id"], "period": "2026-09"},
            headers=_auth(user),
        )
        stats = stats_resp.json()

        # Create and download export
        create_resp = auth_client.post(
            "/exports",
            json={"book_id": book_data["book_id"], "format": "json"},
            headers=_auth(user),
        )
        export_id = create_resp.json()["id"]

        dl_resp = auth_client.get(f"/exports/{export_id}/download", headers=_auth(user))
        export_data = dl_resp.json()

        # Verify totals match
        export_income = sum(item["amount_cents"] for item in export_data if item["type"] == "income")
        export_expense = sum(item["amount_cents"] for item in export_data if item["type"] == "expense")

        assert export_income == stats["total_income"]
        assert export_expense == stats["total_expense"]


# ── Export expiration & file lifecycle (N-05 / N-06 / N-07) ────────────

def _force_expire(db_factory, export_id: str) -> str | None:
    """Rewrite an export's ``expires_at`` into the past; return file_path."""
    from src.models.export import Export

    async def _run():
        async with db_factory() as session:
            export = await session.get(Export, uuid.UUID(export_id))
            assert export is not None
            export.expires_at = datetime.now(UTC) - timedelta(hours=1)
            await session.commit()
            return export.file_path

    return asyncio.run(_run())


class TestExportExpiration:
    def test_expired_export_status_written_back(self, auth_client, user, book_data):
        """N-06: get_export must flip status to EXPIRED past expires_at."""
        create_resp = auth_client.post(
            "/exports",
            json={"book_id": book_data["book_id"], "format": "json"},
            headers=_auth(user),
        )
        export_id = create_resp.json()["id"]
        _force_expire(auth_client.db_factory, export_id)

        resp = auth_client.get(f"/exports/{export_id}", headers=_auth(user))
        assert resp.status_code == 200
        assert resp.json()["status"] == "expired"

    def test_expired_export_shown_expired_in_list(self, auth_client, user, book_data):
        """N-06: list_exports must also report EXPIRED."""
        create_resp = auth_client.post(
            "/exports",
            json={"book_id": book_data["book_id"], "format": "json"},
            headers=_auth(user),
        )
        export_id = create_resp.json()["id"]
        _force_expire(auth_client.db_factory, export_id)

        resp = auth_client.get("/exports", headers=_auth(user))
        assert resp.status_code == 200
        match = next(e for e in resp.json() if e["id"] == export_id)
        assert match["status"] == "expired"

    def test_expired_export_download_rejected_and_file_deleted(self, auth_client, user, book_data):
        """N-05/N-06: expired download returns 410 and the file is deleted."""
        create_resp = auth_client.post(
            "/exports",
            json={"book_id": book_data["book_id"], "format": "json"},
            headers=_auth(user),
        )
        export_id = create_resp.json()["id"]
        file_path = _force_expire(auth_client.db_factory, export_id)
        assert file_path and Path(file_path).exists()

        resp = auth_client.get(f"/exports/{export_id}/download", headers=_auth(user))
        assert resp.status_code == 410
        assert resp.json()["code"] == "expired"
        assert not Path(file_path).exists()

    def test_unexpired_export_stays_completed(self, auth_client, user, book_data):
        """Guard: the expiration flip must not touch fresh exports."""
        create_resp = auth_client.post(
            "/exports",
            json={"book_id": book_data["book_id"], "format": "json"},
            headers=_auth(user),
        )
        export_id = create_resp.json()["id"]

        resp = auth_client.get(f"/exports/{export_id}", headers=_auth(user))
        assert resp.json()["status"] == "completed"
        dl = auth_client.get(f"/exports/{export_id}/download", headers=_auth(user))
        assert dl.status_code == 200


class TestExportFileLifecycle:
    def test_startup_cleanup_removes_stale_files(self, tmp_path, monkeypatch):
        """N-05: cleanup_expired_files deletes only old export files."""
        from src.modules.exports import service as exports_service

        monkeypatch.setattr(exports_service, "_get_export_dir", lambda: tmp_path)

        stale = tmp_path / f"{uuid.uuid4()}.json"
        fresh = tmp_path / f"{uuid.uuid4()}.csv"
        unrelated = tmp_path / "notes.txt"
        for p in (stale, fresh, unrelated):
            p.write_text("x")
        past = time.time() - 26 * 3600
        os.utime(stale, (past, past))

        deleted = exports_service.cleanup_expired_files()

        assert deleted == 1
        assert not stale.exists()
        assert fresh.exists()
        assert unrelated.exists()

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits only")
    def test_export_dir_forced_owner_only(self, tmp_path, monkeypatch):
        """N-05: export dir must be 0o700 even if it pre-existed looser."""
        from src.modules.exports import service as exports_service

        target = tmp_path / "exports"
        target.mkdir(mode=0o755)
        monkeypatch.setattr(exports_service, "_get_export_dir", lambda: target)

        result = exports_service._ensure_export_dir()

        assert result == target
        assert target.stat().st_mode & 0o777 == 0o700

    def test_file_generation_runs_outside_event_loop_thread(self, auth_client, user, book_data, monkeypatch):
        """N-07: sync file generation is offloaded, not run on the loop thread."""
        import threading

        from src.modules.exports.service import ExportService

        seen: dict[str, int] = {}
        orig_fetch = ExportService._fetch_export_rows
        orig_generate = ExportService._generate_file

        async def fetch_spy(self, export):
            seen["loop"] = threading.get_ident()
            return await orig_fetch(self, export)

        def generate_spy(self, export, rows):
            seen["worker"] = threading.get_ident()
            return orig_generate(self, export, rows)

        monkeypatch.setattr(ExportService, "_fetch_export_rows", fetch_spy)
        monkeypatch.setattr(ExportService, "_generate_file", generate_spy)

        resp = auth_client.post(
            "/exports",
            json={"book_id": book_data["book_id"], "format": "csv"},
            headers=_auth(user),
        )
        assert resp.status_code == 201
        assert "loop" in seen and "worker" in seen
        assert seen["loop"] != seen["worker"]
