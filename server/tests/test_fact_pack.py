"""T-M4-03a: Fact Pack tests — deterministic generation, caching, hash stability."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.testclient import TestClient

from src.modules.ai.fact_pack import (
    BudgetFact,
    CategoryFact,
    _cache_clear,
    _compute_data_hash,
)

# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture()
def fp_env():
    """TestClient with seeded data for fact pack queries."""
    from src.main import app
    from src.modules.auth.dependencies import _rate_limits

    _rate_limits.clear()
    _cache_clear()

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
    _cache_clear()


def _seed_data(factory, book_id, user_id, category_ids, txs, budget=None):
    """Seed the database with test data (book must already exist)."""
    from datetime import date

    async def _run():
        async with factory() as session:
            from src.models.budget import Budget, BudgetType
            from src.models.ledger import Category, Transaction

            for cid, cname in category_ids.items():
                session.add(Category(id=cid, owner_id=user_id, name=cname, kind="expense"))

            for tx in txs:
                biz_date = tx["biz_date"]
                if isinstance(biz_date, str):
                    parts = biz_date.split("-")
                    biz_date = date(int(parts[0]), int(parts[1]), int(parts[2]))
                session.add(Transaction(
                    book_id=book_id,
                    type=tx["type"],
                    amount_cents=tx["amount_cents"],
                    category_id=tx["category_id"],
                    note=tx.get("note", ""),
                    biz_date=biz_date,
                    occurred_at=biz_date,
                ))

            if budget:
                session.add(Budget(
                    book_id=book_id,
                    budget_type=BudgetType.MONTHLY,
                    period=budget["period"],
                    amount_cents=budget["amount_cents"],
                    category_id=None,
                ))

            await session.commit()

    asyncio.run(_run())


def _guest(fp_env) -> dict:
    c, _ = fp_env
    resp = c.post("/auth/guest", json={"device_id": "fp-device", "nickname": "fp-tester"})
    assert resp.status_code == 200
    data = resp.json()
    return {"id": data["user_id"], "token": data["tokens"]["access_token"]}


def _auth(user: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {user['token']}"}


# ── Hash stability ────────────────────────────────────────────────────────


class TestHashStability:
    def test_deterministic_hash(self):
        cats = [CategoryFact("a", "餐饮", 5000, 3)]
        h1 = _compute_data_hash(10000, 5000, cats, [], None, None, None, [])
        h2 = _compute_data_hash(10000, 5000, cats, [], None, None, None, [])
        assert h1 == h2

    def test_different_data_different_hash(self):
        h1 = _compute_data_hash(10000, 5000, [], [], None, None, None, [])
        h2 = _compute_data_hash(10000, 6000, [], [], None, None, None, [])
        assert h1 != h2

    def test_hash_stable_with_category_order(self):
        cats_a = [
            CategoryFact("b", "交通", 3000, 2),
            CategoryFact("a", "餐饮", 5000, 3),
        ]
        cats_b = [
            CategoryFact("a", "餐饮", 5000, 3),
            CategoryFact("b", "交通", 3000, 2),
        ]
        h1 = _compute_data_hash(10000, 8000, cats_a, [], None, None, None, [])
        h2 = _compute_data_hash(10000, 8000, cats_b, [], None, None, None, [])
        assert h1 == h2

    def test_hash_includes_budget(self):
        cats = [CategoryFact("a", "餐饮", 5000, 3)]
        budget = BudgetFact(100000, 50000, 50000, 50.0)
        h1 = _compute_data_hash(10000, 5000, cats, [], None, None, None, [])
        h2 = _compute_data_hash(10000, 5000, cats, [], budget, None, None, [])
        assert h1 != h2


# ── FactPack generation ───────────────────────────────────────────────────


class TestFactPackGeneration:
    def test_empty_book_returns_zero_facts(self, fp_env):
        c, factory = fp_env
        user = _guest(fp_env)
        # Create a book
        resp = c.post("/books", json={"name": "Empty Book"}, headers=_auth(user))
        book_id = resp.json()["id"]

        # Request fact pack
        resp = c.post("/ai/report", json={
            "book_id": book_id,
            "period": "2026-09",
        }, headers=_auth(user))
        # Should succeed because the endpoint returns fact pack data
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_income"] == 0
        assert data["total_expense"] == 0
        assert data["balance"] == 0
        assert data["transaction_count"] == 0
        assert data["expense_by_category"] == []
        assert data["income_by_category"] == []
        assert data["budget"] is None
        assert data["largest_expense"] is None
        assert data["largest_income"] is None
        assert len(data["trend"]) == 6

    def test_single_month_returns_correct_totals(self, fp_env):
        c, factory = fp_env
        user = _guest(fp_env)

        resp = c.post("/books", json={"name": "My Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])

        cat_id = uuid.uuid4()
        _seed_data(factory, book_id, uuid.UUID(user["id"]), {cat_id: "餐饮"},
                   txs=[
                       {"type": "expense", "amount_cents": 2500, "category_id": cat_id,
                        "note": "午饭", "biz_date": "2026-09-01"},
                       {"type": "expense", "amount_cents": 1500, "category_id": cat_id,
                        "note": "咖啡", "biz_date": "2026-09-02"},
                       {"type": "income", "amount_cents": 500000, "category_id": cat_id,
                        "note": "工资", "biz_date": "2026-09-01"},
                   ])

        resp = c.post("/ai/report", json={
            "book_id": str(book_id),
            "period": "2026-09",
        }, headers=_auth(user))
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_expense"] == 4000
        assert data["total_income"] == 500000
        assert data["balance"] == 496000
        assert data["transaction_count"] == 3
        assert len(data["expense_by_category"]) == 1
        assert data["expense_by_category"][0]["total_cents"] == 4000

    def test_largest_transaction_correct(self, fp_env):
        c, factory = fp_env
        user = _guest(fp_env)

        resp = c.post("/books", json={"name": "My Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])

        cat_a = uuid.uuid4()
        cat_b = uuid.uuid4()
        _seed_data(factory, book_id, uuid.UUID(user["id"]),
                   {cat_a: "餐饮", cat_b: "购物"},
                   txs=[
                       {"type": "expense", "amount_cents": 500, "category_id": cat_a,
                        "note": "水", "biz_date": "2026-09-01"},
                       {"type": "expense", "amount_cents": 50000, "category_id": cat_b,
                        "note": "大件", "biz_date": "2026-09-05"},
                   ])

        resp = c.post("/ai/report", json={
            "book_id": str(book_id),
            "period": "2026-09",
        }, headers=_auth(user))
        assert resp.status_code == 200
        data = resp.json()
        assert data["largest_expense"]["amount_cents"] == 50000
        assert data["largest_expense"]["category_name"] == "购物"

    def test_budget_execution(self, fp_env):
        c, factory = fp_env
        user = _guest(fp_env)

        resp = c.post("/books", json={"name": "My Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])

        cat_id = uuid.uuid4()
        _seed_data(factory, book_id, uuid.UUID(user["id"]), {cat_id: "餐饮"},
                   txs=[
                       {"type": "expense", "amount_cents": 30000, "category_id": cat_id,
                        "biz_date": "2026-09-01"},
                   ],
                   budget={"period": "2026-09", "amount_cents": 100000})

        resp = c.post("/ai/report", json={
            "book_id": str(book_id),
            "period": "2026-09",
        }, headers=_auth(user))
        assert resp.status_code == 200
        data = resp.json()
        assert data["budget"] is not None
        assert data["budget"]["amount_cents"] == 100000
        assert data["budget"]["spent_cents"] == 30000
        assert data["budget"]["remaining_cents"] == 70000
        assert data["budget"]["pct_used"] == 30.0

    def test_cross_user_isolation(self, fp_env):
        c, factory = fp_env
        user_a = _guest(fp_env)
        user_b = _guest(fp_env)

        resp = c.post("/books", json={"name": "A's Book"}, headers=_auth(user_a))
        book_id_a = resp.json()["id"]

        # User B cannot access A's book
        resp = c.post("/ai/report", json={
            "book_id": book_id_a,
            "period": "2026-09",
        }, headers=_auth(user_b))
        assert resp.status_code == 404


# ── Cache behavior ────────────────────────────────────────────────────────


class TestFactPackCache:
    def test_same_data_returns_cached(self, fp_env):
        c, factory = fp_env
        user = _guest(fp_env)

        resp = c.post("/books", json={"name": "Cache Book"}, headers=_auth(user))
        book_id = resp.json()["id"]

        # First call
        resp1 = c.post("/ai/report", json={
            "book_id": book_id,
            "period": "2026-09",
        }, headers=_auth(user))
        assert resp1.status_code == 200
        hash1 = resp1.json()["data_hash"]

        # Second call with same data
        resp2 = c.post("/ai/report", json={
            "book_id": book_id,
            "period": "2026-09",
        }, headers=_auth(user))
        assert resp2.status_code == 200
        hash2 = resp2.json()["data_hash"]

        # Same hash means same fact pack (cached or deterministic)
        assert hash1 == hash2
        # request_id differs per request; everything else is identical
        d1 = {k: v for k, v in resp1.json().items() if k != "request_id"}
        d2 = {k: v for k, v in resp2.json().items() if k != "request_id"}
        assert d1 == d2

    def test_different_period_different_hash(self, fp_env):
        c, factory = fp_env
        user = _guest(fp_env)

        resp = c.post("/books", json={"name": "Period Book"}, headers=_auth(user))
        book_id = resp.json()["id"]

        resp1 = c.post("/ai/report", json={
            "book_id": book_id,
            "period": "2026-08",
        }, headers=_auth(user))
        resp2 = c.post("/ai/report", json={
            "book_id": book_id,
            "period": "2026-09",
        }, headers=_auth(user))
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        # Different periods, different hashes
        assert resp1.json()["data_hash"] != resp2.json()["data_hash"]


# ── Trend ─────────────────────────────────────────────────────────────────


class TestTrend:
    def test_trend_has_6_months(self, fp_env):
        c, factory = fp_env
        user = _guest(fp_env)

        resp = c.post("/books", json={"name": "Trend Book"}, headers=_auth(user))
        book_id = resp.json()["id"]

        resp = c.post("/ai/report", json={
            "book_id": book_id,
            "period": "2026-09",
        }, headers=_auth(user))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["trend"]) == 6
        # Periods should be 2026-04 through 2026-09
        periods = [t["period"] for t in data["trend"]]
        assert periods == ["2026-04", "2026-05", "2026-06",
                           "2026-07", "2026-08", "2026-09"]

    def test_trend_includes_cross_year_boundary(self, fp_env):
        c, factory = fp_env
        user = _guest(fp_env)

        resp = c.post("/books", json={"name": "Jan Book"}, headers=_auth(user))
        book_id = resp.json()["id"]

        # Request January 2026 — trend should include Aug-Dec 2025
        resp = c.post("/ai/report", json={
            "book_id": book_id,
            "period": "2026-01",
        }, headers=_auth(user))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["trend"]) == 6
        periods = [t["period"] for t in data["trend"]]
        assert periods == ["2025-08", "2025-09", "2025-10",
                           "2025-11", "2025-12", "2026-01"]


# ── Parity with StatisticsService (R-M4-11) ─────────────────────────────


class TestStatisticsParity:
    """Assert fact pack numbers match /statistics output for the same data.

    R-M4-11 fix: the on-account invariant "fact pack digits == stats API
    output" previously had no automated guard. These tests ensure that any
    future drift between the two aggregation implementations is caught.
    """

    def test_monthly_totals_parity(self, fp_env):
        """/ai/report and /stats/monthly must agree on totals + balance."""
        c, factory = fp_env
        user = _guest(fp_env)

        resp = c.post("/books", json={"name": "Parity Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])

        cat_food = uuid.uuid4()
        cat_salary = uuid.uuid4()
        _seed_data(
            factory, book_id, uuid.UUID(user["id"]),
            {cat_food: "餐饮", cat_salary: "工资"},
            txs=[
                {"type": "expense", "amount_cents": 30000, "category_id": cat_food,
                 "note": "聚餐", "biz_date": "2026-09-10"},
                {"type": "expense", "amount_cents": 15000, "category_id": cat_food,
                 "note": "午饭", "biz_date": "2026-09-15"},
                {"type": "income", "amount_cents": 200000, "category_id": cat_salary,
                 "note": "工资", "biz_date": "2026-09-01"},
            ],
        )

        # Fact pack
        resp_fp = c.post("/ai/report", json={
            "book_id": str(book_id),
            "period": "2026-09",
        }, headers=_auth(user))
        assert resp_fp.status_code == 200
        fp = resp_fp.json()

        # Statistics monthly
        resp_stats = c.get("/stats/monthly", params={
            "book_id": str(book_id),
            "period": "2026-09",
        }, headers=_auth(user))
        assert resp_stats.status_code == 200
        stats = resp_stats.json()

        # Core invariant: same numbers
        assert fp["total_income"] == stats["total_income"]
        assert fp["total_expense"] == stats["total_expense"]
        assert fp["balance"] == stats["balance"]
        assert fp["transaction_count"] == stats["transaction_count"]

    def test_deficit_balance_parity(self, fp_env):
        """When expense > income, both APIs must report the same negative balance."""
        c, factory = fp_env
        user = _guest(fp_env)

        resp = c.post("/books", json={"name": "Deficit Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])

        cat_id = uuid.uuid4()
        _seed_data(
            factory, book_id, uuid.UUID(user["id"]),
            {cat_id: "餐饮"},
            txs=[
                {"type": "expense", "amount_cents": 500000, "category_id": cat_id,
                 "note": "大件", "biz_date": "2026-09-10"},
                {"type": "income", "amount_cents": 100000, "category_id": cat_id,
                 "note": "零工", "biz_date": "2026-09-01"},
            ],
        )

        resp_fp = c.post("/ai/report", json={
            "book_id": str(book_id),
            "period": "2026-09",
        }, headers=_auth(user))
        fp = resp_fp.json()

        resp_stats = c.get("/stats/monthly", params={
            "book_id": str(book_id),
            "period": "2026-09",
        }, headers=_auth(user))
        stats = resp_stats.json()

        # Both must show the same deficit
        assert fp["balance"] == -400000
        assert stats["balance"] == -400000
        assert fp["balance"] == stats["balance"]

    def test_trend_parity(self, fp_env):
        """Trend points in /ai/report must match /stats/trends for the same period."""
        c, factory = fp_env
        user = _guest(fp_env)

        resp = c.post("/books", json={"name": "Trend Parity Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])

        cat_id = uuid.uuid4()
        _seed_data(
            factory, book_id, uuid.UUID(user["id"]),
            {cat_id: "餐饮"},
            txs=[
                {"type": "expense", "amount_cents": 10000, "category_id": cat_id,
                 "biz_date": "2026-07-10"},
                {"type": "expense", "amount_cents": 20000, "category_id": cat_id,
                 "biz_date": "2026-08-15"},
                {"type": "income", "amount_cents": 300000, "category_id": cat_id,
                 "biz_date": "2026-09-01"},
            ],
        )

        # Fact pack trend (6 months ending 2026-09)
        resp_fp = c.post("/ai/report", json={
            "book_id": str(book_id),
            "period": "2026-09",
        }, headers=_auth(user))
        fp_trend = {t["period"]: t for t in resp_fp.json()["trend"]}

        # Statistics trend (same 6-month range)
        resp_stats = c.get("/stats/trends", params={
            "book_id": str(book_id),
            "period_from": "2026-04",
            "period_to": "2026-09",
        }, headers=_auth(user))
        stats_trend = {p["period"]: p for p in resp_stats.json()["points"]}

        # For each month in the fact pack trend, the corresponding stats
        # point must agree on income, expense, and balance
        for period, fp_point in fp_trend.items():
            assert period in stats_trend, f"Missing trend period {period}"
            sp = stats_trend[period]
            assert fp_point["total_income"] == sp["total_income"]
            assert fp_point["total_expense"] == sp["total_expense"]
            assert fp_point["balance"] == sp["balance"]

    def test_category_totals_sum(self, fp_env):
        """Sum of category totals must equal total_expense / total_income."""
        c, factory = fp_env
        user = _guest(fp_env)

        resp = c.post("/books", json={"name": "Category Sum Book"}, headers=_auth(user))
        book_id = uuid.UUID(resp.json()["id"])

        cat_a = uuid.uuid4()
        cat_b = uuid.uuid4()
        _seed_data(
            factory, book_id, uuid.UUID(user["id"]),
            {cat_a: "餐饮", cat_b: "工资"},
            txs=[
                {"type": "expense", "amount_cents": 10000, "category_id": cat_a,
                 "biz_date": "2026-09-01"},
                {"type": "expense", "amount_cents": 20000, "category_id": cat_a,
                 "biz_date": "2026-09-05"},
                {"type": "income", "amount_cents": 300000, "category_id": cat_b,
                 "biz_date": "2026-09-01"},
            ],
        )

        resp = c.post("/ai/report", json={
            "book_id": str(book_id),
            "period": "2026-09",
        }, headers=_auth(user))
        data = resp.json()

        expense_sum = sum(c["total_cents"] for c in data["expense_by_category"])
        income_sum = sum(c["total_cents"] for c in data["income_by_category"])
        assert expense_sum == data["total_expense"]
        assert income_sum == data["total_income"]
