"""T-M5 E2E: Full user journey against a real HTTP server.

Single guest session贯穿所有场景，避免触发 per-IP 频率限制。
Exercises: auth → ledger → AI → sync → stats → export → attachments → consent.

NOTE: These tests require a live server at http://127.0.0.1:8000.
In CI, skip unless E2E_LIVE=1 is set.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

BASE = "http://127.0.0.1:8000/api/v1"
TODAY = date.today()
PERIOD = TODAY.strftime("%Y-%m")
TODAY_ISO = TODAY.isoformat()
TZ8 = timezone(timedelta(hours=8))
OCCURRED = datetime.combine(TODAY, datetime.min.time()).replace(tzinfo=TZ8).isoformat()


# ─── Skip entire module if live server is unavailable ───────────────────────

def _server_reachable() -> bool:
    try:
        r = httpx.get(f"{BASE}/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    os.environ.get("E2E_LIVE") != "1" and not _server_reachable(),
    reason="Live server not available (set E2E_LIVE=1 to enable)",
)


# ─── Shared session (created once, reused by all classes) ──────────────────

@pytest.fixture(scope="session")
def e2e_client():
    """One guest session shared across the entire test module."""
    c = httpx.Client(base_url=BASE, timeout=15)
    r = c.post("/auth/guest", json={"device_id": "e2e-session"})
    assert r.status_code == 200, f"Guest failed: {r.status_code} {r.text}"
    body = r.json()
    c.headers["Authorization"] = f"Bearer {body['tokens']['access_token']}"
    c._e2e_user_id = body["user_id"]
    c._e2e_refresh = body["tokens"]["refresh_token"]

    # F-M5-04a-F1: server-side sync push now requires a recorded consent
    # for one of the user's devices. Record it up front so all subsequent
    # push-based tests can proceed without per-test ceremony.
    now = datetime.now(timezone.utc).isoformat()
    r = c.post("/consent", json={
        "device_id": "e2e-session", "policy_version": "v1",
        "consented_at": now,
    })
    assert r.status_code == 201, f"Consent seed failed: {r.status_code} {r.text}"

    yield c
    c.close()


@pytest.fixture(scope="session")
def shared_data(e2e_client):
    """Create shared book/category/account once, reuse across all tests."""
    c = e2e_client
    book = post_book(c, "E2E Master")
    cat_exp = post_category(c, book["id"], "expense", "Food")
    cat_inc = post_category(c, book["id"], "income", "Salary")
    acct = post_account(c, book["id"])

    # Pre-create some transactions for stats tests
    txs = []
    for i in range(3):
        txs.append(post_tx(c, book["id"], cat_exp["id"], acct["id"], (i + 1) * 1000))
    txs.append(post_tx(c, book["id"], cat_inc["id"], acct["id"], 50000, kind="income"))

    return {
        "book": book, "expense_cat": cat_exp, "income_cat": cat_inc,
        "account": acct, "transactions": txs,
    }


# ── Helpers ───────────────────────────────────────────────────────────────

def post_book(c, name="E2E", icon=None):
    payload = {"name": name}
    if icon is not None:
        payload["icon"] = icon
    r = c.post("/books", json=payload)
    assert r.status_code == 201
    return r.json()


def post_category(c, book_id, kind, name, parent_id=None):
    payload = {"book_id": book_id, "kind": kind, "name": name}
    if parent_id:
        payload["parent_id"] = parent_id
    r = c.post("/categories", json=payload)
    assert r.status_code == 201
    return r.json()


def post_account(c, book_id, name="Cash", icon=None):
    payload = {"book_id": book_id, "name": name}
    if icon is not None:
        payload["icon"] = icon
    r = c.post("/accounts", json=payload)
    assert r.status_code == 201
    return r.json()


def post_tx(c, book_id, cat_id, acct_id, amount=1000, kind="expense",
            note="", biz_date=TODAY_ISO):
    r = c.post("/transactions", json={
        "book_id": book_id, "type": kind, "amount_cents": amount,
        "category_id": cat_id, "account_id": acct_id,
        "note": note, "biz_date": biz_date, "occurred_at": OCCURRED,
    })
    assert r.status_code == 201
    return r.json()


# ─── Auth E2E ──────────────────────────────────────────────────────────────

class TestAuthE2E:
    def test_me_endpoint(self, e2e_client):
        r = e2e_client.get("/me")
        assert r.status_code == 200
        me = r.json()
        assert me["id"] == e2e_client._e2e_user_id
        assert me["status"] == "guest"

    def test_unauthenticated_rejected(self):
        c = httpx.Client(base_url=BASE, timeout=10)
        r = c.get("/me")
        assert r.status_code in (401, 403)
        c.close()

    def test_health(self):
        c = httpx.Client(base_url=BASE, timeout=10)
        r = c.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        r2 = c.get("/health/ready")
        assert r2.status_code == 200
        assert r2.json()["checks"]["database"] == "ok"
        c.close()


# ─── Ledger CRUD E2E ───────────────────────────────────────────────────────

class TestBookE2E:
    def test_full_lifecycle(self, e2e_client):
        c = e2e_client
        b = post_book(c, "Lifecycle Book")
        bid = b["id"]
        assert b["version"] == 1

        r = c.get("/books")
        assert any(x["id"] == bid for x in r.json()["items"])

        r = c.patch(f"/books/{bid}", json={"name": "Lifecycle v2", "version": 1})
        assert r.status_code == 200
        assert r.json()["version"] == 2

        r = c.patch(f"/books/{bid}", json={"name": "conflict", "version": 1})
        assert r.status_code == 409

        r = c.delete(f"/books/{bid}")
        assert r.status_code in (200, 204)

        r = c.get("/books")
        assert not any(x["id"] == bid for x in r.json()["items"])


class TestCategoryE2E:
    def test_hierarchy_and_validation(self, e2e_client):
        c = e2e_client
        b = post_book(c, "Cat Book")
        bid = b["id"]

        parent = post_category(c, bid, "expense", "Food")
        pid = parent["id"]
        child = post_category(c, bid, "expense", "Lunch", parent_id=pid)
        assert child["parent_id"] == pid

        r = c.get(f"/categories?book_id={bid}&kind=expense")
        names = [x["name"] for x in r.json()["items"]]
        assert "Food" in names and "Lunch" in names

        # Self-parent rejected
        r = c.patch(f"/categories/{pid}", json={"parent_id": pid, "version": 1})
        assert r.status_code in (400, 422)

        # Cycle rejected
        r = c.patch(f"/categories/{pid}", json={"parent_id": child["id"], "version": 1})
        assert r.status_code in (400, 422)

        # Duplicate name
        r = c.post("/categories", json={"book_id": bid, "kind": "expense", "name": "Food"})
        assert r.status_code == 409


class TestAccountE2E:
    def test_crud(self, e2e_client):
        c = e2e_client
        b = post_book(c, "Acct Book")
        a = post_account(c, b["id"], "WeChat", "💳")
        aid = a["id"]

        r = c.get(f"/accounts?book_id={b['id']}")
        assert any(x["id"] == aid for x in r.json()["items"])

        r = c.delete(f"/accounts/{aid}")
        assert r.status_code in (200, 204)


class TestTransactionE2E:
    def test_full_flow(self, e2e_client):
        c = e2e_client
        b = post_book(c, "Tx Book")
        cat = post_category(c, b["id"], "expense", "Transport")
        acct = post_account(c, b["id"])

        t = post_tx(c, b["id"], cat["id"], acct["id"], amount=2500, note="Subway")
        tid, ver = t["id"], t["version"]
        assert t["amount_cents"] == 2500 and t["source"] == "manual"

        r = c.get(f"/transactions/{tid}")
        assert r.status_code == 200 and r.json()["amount_cents"] == 2500

        r = c.patch(f"/transactions/{tid}", json={"amount_cents": 3000, "version": ver})
        assert r.status_code == 200

        r = c.patch(f"/transactions/{tid}", json={"amount_cents": 9999, "version": ver})
        assert r.status_code == 409

        r = c.get(f"/transactions?book_id={b['id']}&period={PERIOD}")
        assert any(x["id"] == tid for x in r.json()["items"])

        r = c.delete(f"/transactions/{tid}")
        assert r.status_code in (200, 204)

        r = c.get(f"/transactions?book_id={b['id']}&period={PERIOD}")
        assert not any(x["id"] == tid for x in r.json()["items"])

        # Undo restores
        r = c.post(f"/transactions/{tid}/undo")
        assert r.status_code in (200, 204)

    def test_ownership_guard(self, e2e_client):
        """Create a second guest and verify cross-user isolation."""
        c2 = httpx.Client(base_url=BASE, timeout=10)
        r = c2.post("/auth/guest", json={"device_id": "e2e-tx-guard"})
        assert r.status_code == 200
        c2.headers["Authorization"] = f"Bearer {r.json()['tokens']['access_token']}"

        c = e2e_client
        b = post_book(c, "Guard Book")
        cat = post_category(c, b["id"], "expense", "Food")
        acct = post_account(c, b["id"])
        t = post_tx(c, b["id"], cat["id"], acct["id"], 500)

        # Other user cannot access
        r = c2.get(f"/transactions/{t['id']}")
        assert r.status_code == 404

        r = c2.get(f"/transactions?book_id={b['id']}")
        assert r.status_code in (200, 403, 404)

        c2.close()


# ─── Statistics E2E ────────────────────────────────────────────────────────

class TestStatsE2E:
    def test_monthly_accuracy(self, e2e_client, shared_data):
        # shared_data already has 3 expense txs (1000, 2000, 3000) and 1 income (50000)
        r = e2e_client.get(f"/stats/monthly?book_id={shared_data['book']['id']}&period={PERIOD}")
        s = r.json()
        assert s["total_expense"] == 6000  # 1000 + 2000 + 3000
        assert s["total_income"] == 50000
        assert s["balance"] == 44000
        assert s["transaction_count"] == 4

    def test_category_breakdown_sums(self, e2e_client, shared_data):
        r = e2e_client.get(f"/stats/monthly?book_id={shared_data['book']['id']}&period={PERIOD}")
        s = r.json()
        cat_sum = sum(x["total_cents"] for x in s["expense_by_category"])
        assert cat_sum == s["total_expense"]

    def test_empty_period(self, e2e_client, shared_data):
        r = e2e_client.get(f"/stats/monthly?book_id={shared_data['book']['id']}&period=2020-01")
        assert r.status_code == 200
        s = r.json()
        assert s["total_expense"] == 0 and s["total_income"] == 0


# ─── Trends E2E ───────────────────────────────────────────────────────────

class TestTrendsE2E:
    def test_trends_endpoint(self, e2e_client, shared_data):
        period_from = TODAY.replace(day=1).strftime("%Y-%m")
        period_to = TODAY.strftime("%Y-%m")
        r = e2e_client.get(f"/stats/trends?book_id={shared_data['book']['id']}&period_from={period_from}&period_to={period_to}")
        assert r.status_code == 200


# ─── Budget E2E ────────────────────────────────────────────────────────────

class TestBudgetE2E:
    def test_monthly_budget(self, e2e_client, shared_data):
        c = e2e_client
        b = shared_data["book"]
        cat = shared_data["expense_cat"]
        acct = shared_data["account"]

        r = c.put(f"/budgets/{PERIOD}/category/{cat['id']}", json={
            "book_id": b["id"], "budget_type": "monthly", "amount_cents": 50000,
        })
        assert r.status_code in (200, 201)

        post_tx(c, b["id"], cat["id"], acct["id"], 20000)
        post_tx(c, b["id"], cat["id"], acct["id"], 35000)

        r = c.get(f"/budgets?book_id={b['id']}&period={PERIOD}")
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        assert len(r.json()) >= 1


# ─── Sync E2E ──────────────────────────────────────────────────────────────

class TestSyncE2E:
    def _payload(self, book_id, cat_id, acct_id, eid=None, amount=1234):
        return {
            "mutations": [{
                "mutation_id": str(uuid.uuid4()),
                "entity_type": "transaction",
                "operation": "insert",
                "entity_id": eid or str(uuid.uuid4()),
                "payload": {
                    "book_id": book_id, "type": "expense",
                    "amount_cents": amount, "category_id": cat_id,
                    "account_id": acct_id,
                    "biz_date": TODAY_ISO, "occurred_at": OCCURRED,
                },
            }],
        }

    def test_push_and_pull(self, e2e_client, shared_data):
        b = shared_data["book"]
        cat = shared_data["expense_cat"]
        acct = shared_data["account"]
        r = e2e_client.post("/sync/push", json=self._payload(b["id"], cat["id"], acct["id"]))
        assert r.status_code == 200
        assert r.json()["results"][0]["status"] == "applied"

        r = e2e_client.post("/sync/pull", json={"cursor": 0, "limit": 50})
        assert r.status_code == 200 and len(r.json()["changes"]) >= 1

    def test_idempotency(self, e2e_client, shared_data):
        b = shared_data["book"]
        cat = shared_data["expense_cat"]
        acct = shared_data["account"]
        payload = self._payload(b["id"], cat["id"], acct["id"])

        r1 = e2e_client.post("/sync/push", json=payload)
        assert r1.json()["results"][0]["status"] == "applied"

        # Replay same mutation_id
        r2 = e2e_client.post("/sync/push", json=payload)
        statuses = [x["status"] for x in r2.json()["results"]]
        assert "applied" not in statuses  # duplicate skipped

    def test_version_conflict(self, e2e_client, shared_data):
        b = shared_data["book"]
        cat = shared_data["expense_cat"]
        acct = shared_data["account"]
        t = post_tx(e2e_client, b["id"], cat["id"], acct["id"], 100)
        r = e2e_client.post("/sync/push", json={
            "mutations": [{
                "mutation_id": str(uuid.uuid4()),
                "entity_type": "transaction",
                "operation": "update",
                "entity_id": t["id"],
                "version": max(0, t["version"] - 1),
                "payload": {"amount_cents": 999},
            }],
        })
        statuses = [x["status"] for x in r.json()["results"]]
        assert any(s in ("conflict", "skipped", "error") for s in statuses)


# ─── Attachments E2E ───────────────────────────────────────────────────────

class TestAttachmentsE2E:
    def test_upload_download_delete(self, e2e_client):
        import io
        c = e2e_client
        jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"

        r = c.post("/attachments/upload",
                   files={"file": ("r.jpg", io.BytesIO(jpeg), "image/jpeg")})
        assert r.status_code == 201
        aid = r.json()["id"]

        r = c.get(f"/attachments/{aid}/download")
        assert r.status_code == 200 and r.content == jpeg

        r = c.get("/attachments")
        assert any(x["id"] == aid for x in r.json())

        r = c.delete(f"/attachments/{aid}")
        assert r.status_code in (200, 204)

        r = c.get(f"/attachments/{aid}/download")
        assert r.status_code in (404, 410)

    def test_ownership_isolation(self, e2e_client):
        import io
        c = e2e_client
        jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"

        r = c.post("/attachments/upload",
                   files={"file": ("r.jpg", io.BytesIO(jpeg), "image/jpeg")})
        aid = r.json()["id"]

        c2 = httpx.Client(base_url=BASE, timeout=10)
        r2 = c2.post("/auth/guest", json={"device_id": "e2e-att-iso"})
        c2.headers["Authorization"] = f"Bearer {r2.json()['tokens']['access_token']}"

        r = c2.get(f"/attachments/{aid}/download")
        assert r.status_code == 404
        c2.close()

    def test_size_limit(self, e2e_client):
        import io
        r = e2e_client.post("/attachments/upload",
                            files={"file": ("big.jpg", io.BytesIO(b"\x00" * (11 * 1024 * 1024)), "image/jpeg")})
        assert r.status_code == 413

    def test_mime_rejection(self, e2e_client):
        import io
        r = e2e_client.post("/attachments/upload",
                            files={"file": ("d.pdf", io.BytesIO(b"%PDF fake"), "application/pdf")})
        assert r.status_code == 415


# ── AI E2E ────────────────────────────────────────────────────────────────

class TestAIE2E:
    def test_interpret_503_without_provider(self, e2e_client):
        r = e2e_client.post("/ai/interpret", json={"input": "午饭三十块"})
        assert r.status_code == 503
        assert r.json()["code"] == "ai_unavailable"

    def test_validate_bad_proposal(self, e2e_client):
        r = e2e_client.post("/ai/validate", json={
            "proposal": {"intent": "add_transaction", "params": {
                "book_id": "00000000-0000-0000-0000-000000000000",
                "type": "expense", "amount_cents": -100,
            }},
        })
        assert r.status_code in (400, 422)


# ─── Exports E2E ──────────────────────────────────────────────────────────

class TestExportsE2E:
    def test_json_export(self, e2e_client, shared_data):
        c = e2e_client
        b = shared_data["book"]
        # Create export
        r = c.post("/exports", json={"book_id": b["id"], "format": "json"})
        assert r.status_code == 201
        export_id = r.json()["id"]

        # Download
        r = c.get(f"/exports/{export_id}/download")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert len(data) >= 4  # shared_data has 4 transactions

    def test_csv_export(self, e2e_client, shared_data):
        c = e2e_client
        b = shared_data["book"]
        r = c.post("/exports", json={"book_id": b["id"], "format": "csv"})
        assert r.status_code == 201
        export_id = r.json()["id"]

        r = c.get(f"/exports/{export_id}/download")
        assert r.status_code == 200
        assert "text/csv" in r.headers.get("content-type", "")
        assert len(r.text.strip().split("\n")) >= 5  # header + 4 txs


# ── Consent E2E ───────────────────────────────────────────────────────────

class TestConsentE2E:
    def test_record_consent(self, e2e_client):
        c = e2e_client
        now = datetime.now(TZ8).isoformat()

        # Record consent
        r = c.post("/consent", json={
            "device_id": "e2e-session", "policy_version": "1.0",
            "consented_at": now,
        })
        assert r.status_code == 201
        assert r.json()["policy_version"] == "1.0"

        # Record again (idempotent / new version)
        r = c.post("/consent", json={
            "device_id": "e2e-session", "policy_version": "2.0",
            "consented_at": now,
        })
        assert r.status_code == 201


# ─── Cross-Module Invariants ──────────────────────────────────────────────

class TestInvariants:
    def test_stats_match_detail(self, e2e_client, shared_data):
        b = shared_data["book"]
        r = e2e_client.get(f"/stats/monthly?book_id={b['id']}&period={PERIOD}")
        stats_total = r.json()["total_expense"]

        r = e2e_client.get(f"/transactions?book_id={b['id']}&period={PERIOD}")
        detail_total = sum(t["amount_cents"] for t in r.json()["items"] if t["type"] == "expense")
        assert stats_total == detail_total

    def test_all_amounts_are_integers(self, e2e_client, shared_data):
        b = shared_data["book"]
        r = e2e_client.get(f"/transactions?book_id={b['id']}&period={PERIOD}")
        for t in r.json()["items"]:
            assert isinstance(t["amount_cents"], int) and t["amount_cents"] >= 0
