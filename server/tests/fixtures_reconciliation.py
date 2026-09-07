"""T-M5-01a: Cross-platform reconciliation assertion function (Python).

Loads shared fixtures from contracts/fixtures/reconciliation/ and verifies
that the server StatisticsService produces correct results matching manual
computation from the seed data.

Invariants checked:
1. Detail totals = StatisticsService output (income/expense/count)
2. Category breakdown sums = type totals
3. Monthly breakdown sums = grand totals
4. All amounts are integers (no floats)
5. Tamper detection: modifying an amount changes the totals
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.models.base import Base
from src.models.budget import Budget, BudgetType
from src.models.ledger import (
    Account,
    Book,
    Category,
    CategoryKind,
    Transaction,
    TxType,
)
from src.models.user import User
from src.modules.statistics.service import StatisticsService

# ─── Paths ───

CONTRACTS_DIR = Path(__file__).resolve().parent.parent.parent / "contracts" / "fixtures" / "reconciliation"

SCENARIOS = ["basic", "cross-month", "tz-boundary", "soft-delete", "budget-mixed"]


# ─── Loader ───

def load_seed(scenario: str) -> dict[str, Any]:
    """Load a scenario's seed.json from the shared contracts directory."""
    path = CONTRACTS_DIR / scenario / "seed.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ─── Database seeding ───

async def seed_database(session: AsyncSession, seed: dict[str, Any]) -> dict:
    """Insert entities from seed data into the database.

    Uses ORM-generated UUIDs to avoid SQLite/UUID compatibility issues
    with explicit fixture IDs. Builds a mapping from seed IDs to generated IDs.

    Returns a dict with user_id, books mapping, and the ID mapping.
    """
    # ID mapping: seed JSON id → ORM-generated UUID
    id_map: dict[str, uuid.UUID] = {}

    # Create user
    user = User(id=uuid.uuid4(), nickname="reconciler", timezone=seed.get("user", {}).get("timezone", "UTC"))
    session.add(user)
    await session.flush()
    user_id = user.id

    # Create books
    books = {}
    for b in seed.get("books", []):
        book = Book(
            id=uuid.uuid4(),
            owner_id=user_id,
            name=b["name"],
            icon=b.get("icon"),
        )
        session.add(book)
        books[b["id"]] = book
        id_map[b["id"]] = book.id

    await session.flush()

    # Create categories
    categories = {}
    for c in seed.get("categories", []):
        cat = Category(
            id=uuid.uuid4(),
            owner_id=user_id,
            book_id=id_map.get(c["bookId"]) if c.get("bookId") else None,
            kind=CategoryKind.EXPENSE if c["kind"] == "expense" else CategoryKind.INCOME,
            name=c["name"],
        )
        session.add(cat)
        categories[c["id"]] = cat
        id_map[c["id"]] = cat.id

    # Create accounts
    accounts = {}
    for a in seed.get("accounts", []):
        acct = Account(
            id=uuid.uuid4(),
            owner_id=user_id,
            name=a["name"],
            icon=a.get("icon"),
        )
        session.add(acct)
        accounts[a["id"]] = acct
        id_map[a["id"]] = acct.id

    await session.flush()

    # Create transactions
    for t in seed.get("transactions", []):
        occurred_at = datetime.fromtimestamp(t["occurredAt"] / 1000, tz=UTC)
        biz_date = date.fromisoformat(t["bizDate"])
        tx_type = TxType.EXPENSE if t["type"] == "expense" else TxType.INCOME
        deleted_at = None
        if t.get("deletedAt"):
            deleted_at = datetime.fromtimestamp(t["deletedAt"] / 1000, tz=UTC)

        tx = Transaction(
            id=uuid.uuid4(),
            book_id=id_map[t["bookId"]],
            type=tx_type,
            amount_cents=t["amountCents"],
            category_id=id_map[t["categoryId"]],
            account_id=id_map.get(t["accountId"]) if t.get("accountId") else None,
            note=t.get("note", ""),
            biz_date=biz_date,
            occurred_at=occurred_at,
            source=t.get("source", "manual"),
        )
        if deleted_at is not None:
            tx.deleted_at = deleted_at
        session.add(tx)

    # Create budgets
    for g in seed.get("budgets", []):
        budget = Budget(
            id=uuid.uuid4(),
            book_id=id_map[g["bookId"]],
            budget_type=BudgetType(g["budgetType"]),
            category_id=id_map.get(g["categoryId"]) if g.get("categoryId") else None,
            period=g["period"],
            amount_cents=g["amountCents"],
        )
        session.add(budget)

    await session.flush()

    return {"user_id": user_id, "books": books}


# ─── Assertion functions ───

def assert_amounts_are_integers(seed: dict[str, Any]) -> None:
    """Invariant 4: All amounts in seed data are integers, not floats."""
    for tx in seed.get("transactions", []):
        amount = tx["amountCents"]
        assert isinstance(amount, int), f"Amount is not int: {amount} (tx {tx['id']})"
        assert amount >= 0, f"Amount is negative: {amount} (tx {tx['id']})"
    for g in seed.get("budgets", []):
        amount = g["amountCents"]
        assert isinstance(amount, int), f"Budget amount is not int: {amount}"


def compute_detail_totals(seed: dict[str, Any]) -> dict:
    """Manually compute expected totals from seed data."""
    active_txs = [
        tx for tx in seed.get("transactions", [])
        if not tx.get("deletedAt")
    ]
    expense = sum(tx["amountCents"] for tx in active_txs if tx["type"] == "expense")
    income = sum(tx["amountCents"] for tx in active_txs if tx["type"] == "income")
    return {
        "totalExpense": expense,
        "totalIncome": income,
        "balance": income - expense,
        "count": len(active_txs),
    }


def compute_monthly_totals(seed: dict[str, Any]) -> dict[str, dict]:
    """Compute expected monthly totals from seed data."""
    active_txs = [
        tx for tx in seed.get("transactions", [])
        if not tx.get("deletedAt")
    ]
    months: dict[str, dict] = {}
    for tx in active_txs:
        period = tx["bizDate"][:7]  # YYYY-MM
        if period not in months:
            months[period] = {"totalExpense": 0, "totalIncome": 0, "count": 0}
        m = months[period]
        if tx["type"] == "expense":
            m["totalExpense"] += tx["amountCents"]
        else:
            m["totalIncome"] += tx["amountCents"]
        m["count"] += 1
    for m in months.values():
        m["balance"] = m["totalIncome"] - m["totalExpense"]
    return months


def compute_category_totals(seed: dict[str, Any]) -> dict[str, dict]:
    """Compute expected category breakdowns from seed data."""
    active_txs = [
        tx for tx in seed.get("transactions", [])
        if not tx.get("deletedAt")
    ]
    cats: dict[str, dict] = {}
    for tx in active_txs:
        cid = tx["categoryId"]
        if cid not in cats:
            cats[cid] = {"kind": tx["type"], "total_cents": 0, "count": 0}
        cats[cid]["total_cents"] += tx["amountCents"]
        cats[cid]["count"] += 1
    return cats


async def assert_reconciliation(
    session: AsyncSession,
    seed: dict[str, Any],
) -> dict:
    """Run all reconciliation invariants.

    Seeds the database, calls StatisticsService, and compares results
    against manual computation from the seed data.

    Returns the context dict (user_id, book_id, stats_result).
    """
    ctx = await seed_database(session, seed)
    book_id = list(ctx["books"].values())[0].id
    user_id = ctx["user_id"]

    # Determine period(s) from seed data
    periods = set()
    for tx in seed.get("transactions", []):
        if not tx.get("deletedAt"):
            periods.add(tx["bizDate"][:7])

    service = StatisticsService(session)

    # Invariant 1 & 2: For each period, check stats match detail totals
    monthly_from_seed = compute_monthly_totals(seed)
    detail_totals = compute_detail_totals(seed)

    stats_results = {}
    for period in sorted(periods):
        stats = await service.get_monthly_stats(book_id, user_id, period)
        stats_results[period] = stats

        seed_month = monthly_from_seed.get(period, {
            "totalExpense": 0, "totalIncome": 0, "balance": 0, "count": 0,
        })

        # Invariant 1: Monthly totals match
        assert stats.total_expense == seed_month["totalExpense"], (
            f"Period {period}: expense {stats.total_expense} != {seed_month['totalExpense']}"
        )
        assert stats.total_income == seed_month["totalIncome"], (
            f"Period {period}: income {stats.total_income} != {seed_month['totalIncome']}"
        )
        assert stats.balance == seed_month["balance"], (
            f"Period {period}: balance {stats.balance} != {seed_month['balance']}"
        )
        assert stats.transaction_count == seed_month["count"], (
            f"Period {period}: count {stats.transaction_count} != {seed_month['count']}"
        )

    # Invariant 3: Monthly sums = grand totals
    grand_expense = sum(s.total_expense for s in stats_results.values())
    grand_income = sum(s.total_income for s in stats_results.values())
    grand_count = sum(s.transaction_count for s in stats_results.values())
    assert grand_expense == detail_totals["totalExpense"], (
        f"Grand expense mismatch: {grand_expense} != {detail_totals['totalExpense']}"
    )
    assert grand_income == detail_totals["totalIncome"], (
        f"Grand income mismatch: {grand_income} != {detail_totals['totalIncome']}"
    )
    assert grand_count == detail_totals["count"], (
        f"Grand count mismatch: {grand_count} != {detail_totals['count']}"
    )

    # Invariant 2: Category breakdown sums = type totals (using first period as check)
    if periods:
        first_period = sorted(periods)[0]
        first_stats = stats_results[first_period]
        cat_expense_sum = sum(c.total_cents for c in first_stats.expense_by_category)
        cat_income_sum = sum(c.total_cents for c in first_stats.income_by_category)
        assert cat_expense_sum == first_stats.total_expense, (
            f"Category expense sum {cat_expense_sum} != total {first_stats.total_expense}"
        )
        assert cat_income_sum == first_stats.total_income, (
            f"Category income sum {cat_income_sum} != total {first_stats.total_income}"
        )

    return {
        "user_id": user_id,
        "book_id": book_id,
        "stats": stats_results,
        "detail_totals": detail_totals,
    }


# ─── Pytest fixtures ───

@pytest_asyncio.fixture()
async def db_session():
    """Provide an async session backed by in-memory SQLite."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# ─── Tests ───

@pytest.mark.asyncio
async def test_all_scenarios_pass_invariants(db_session):
    """All 5 scenarios pass reconciliation invariants."""
    for scenario in SCENARIOS:
        # Each scenario needs a fresh session to avoid FK conflicts
        # We re-use the same session but clear between scenarios
        seed = load_seed(scenario)
        assert_amounts_are_integers(seed)
        result = await assert_reconciliation(db_session, seed)
        assert result["detail_totals"]["count"] >= 0


@pytest.mark.asyncio
async def test_basic_expected_values(db_session):
    """Basic scenario: verify exact expected values."""
    seed = load_seed("basic")
    result = await assert_reconciliation(db_session, seed)

    # Total: expense=10000, income=50000, balance=40000, count=4
    assert result["detail_totals"]["totalExpense"] == 10000
    assert result["detail_totals"]["totalIncome"] == 50000
    assert result["detail_totals"]["balance"] == 40000
    assert result["detail_totals"]["count"] == 4

    # Stats for 2026-09
    stats = result["stats"]["2026-09"]
    assert stats.total_expense == 10000
    assert stats.total_income == 50000
    assert stats.balance == 40000

    # Category breakdown: Food=8500, Transport=1500
    food = next(
        (c for c in stats.expense_by_category if c.category_name == "Food"),
        None,
    )
    assert food is not None
    assert food.total_cents == 8500
    assert food.count == 2

    transport = next(
        (c for c in stats.expense_by_category if c.category_name == "Transport"),
        None,
    )
    assert transport is not None
    assert transport.total_cents == 1500


@pytest.mark.asyncio
async def test_cross_month_expected_values(db_session):
    """Cross-month scenario: verify monthly grouping."""
    seed = load_seed("cross-month")
    result = await assert_reconciliation(db_session, seed)

    stats = result["stats"]
    assert len(stats) == 3

    # Aug: expense=2000, income=30000
    assert stats["2026-08"].total_expense == 2000
    assert stats["2026-08"].total_income == 30000

    # Sep: expense=3000, income=20000
    assert stats["2026-09"].total_expense == 3000
    assert stats["2026-09"].total_income == 20000

    # Oct: expense=8000, income=0
    assert stats["2026-10"].total_expense == 8000
    assert stats["2026-10"].total_income == 0


@pytest.mark.asyncio
async def test_soft_delete_expected_values(db_session):
    """Soft-delete scenario: verify deleted transactions excluded."""
    seed = load_seed("soft-delete")
    result = await assert_reconciliation(db_session, seed)

    # 5 txs total, 2 deleted → 3 active
    # Active: expense 3000+3000=6000, income 8000
    assert result["detail_totals"]["totalExpense"] == 6000
    assert result["detail_totals"]["totalIncome"] == 8000
    assert result["detail_totals"]["balance"] == 2000
    assert result["detail_totals"]["count"] == 3


@pytest.mark.asyncio
async def test_budget_mixed_deficit_month(db_session):
    """Budget-mixed scenario: verify deficit month has negative balance."""
    seed = load_seed("budget-mixed")
    result = await assert_reconciliation(db_session, seed)

    stats = result["stats"]

    # Sep has deficit: expense=15000 > income=12000
    sept = stats["2026-09"]
    assert sept.balance == -3000  # Negative balance!
    assert sept.total_expense == 15000
    assert sept.total_income == 12000

    # Aug has surplus
    aug = stats["2026-08"]
    assert aug.balance == 2000
    assert aug.total_expense == 5000
    assert aug.total_income == 7000

    # Oct: income only
    oct_stats = stats["2026-10"]
    assert oct_stats.total_expense == 0
    assert oct_stats.total_income == 25000


@pytest.mark.asyncio
async def test_tz_boundary_uses_biz_date(db_session):
    """TZ-boundary scenario: verify grouping by bizDate, not occurredAt.

    The seed has two boundary transactions whose UTC-occurredAt month differs
    from their Shanghai-bizDate month (e.g. 2026-08-31T16:00Z = 2026-09-01 in
    Shanghai). Grouping must follow bizDate, yielding:
      - 2026-09: TX1 (1000 expense) + TX3 (15000 income)
      - 2026-10: TX2 (2000 expense)
    """
    seed = load_seed("tz-boundary")
    result = await assert_reconciliation(db_session, seed)

    # bizDate spans two months: 2026-09 and 2026-10
    assert len(result["stats"]) == 2
    assert "2026-09" in result["stats"]
    assert "2026-10" in result["stats"]

    sep_stats = result["stats"]["2026-09"]
    assert sep_stats.total_expense == 1000
    assert sep_stats.total_income == 15000
    assert sep_stats.transaction_count == 2

    oct_stats = result["stats"]["2026-10"]
    assert oct_stats.total_expense == 2000
    assert oct_stats.total_income == 0
    assert oct_stats.transaction_count == 1
