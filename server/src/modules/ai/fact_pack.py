"""T-M4-03a: Fact Pack — deterministic data bundle for AI report generation.

The fact pack is the *only* source of financial data the LLM sees during
report generation.  Every number in the generated narrative must be traceable
to a specific field in the fact pack (T-M4-03b number verification).

Design rules:
- All numbers come from the authoritative StatisticsService (T-M2-05) or
  direct DB queries — never from the LLM.
- A SHA-256 hash of the numerical content enables caching: when the same
  data is requested again, the cached fact pack is returned, avoiding a
  redundant and billable LLM call.
- The cache is in-memory with a configurable TTL — suitable for the
  single-instance first release.  Multi-instance deployments should
  replace with Redis.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.errors import AppException
from src.models.budget import Budget, BudgetType
from src.models.ledger import Book, Transaction, TxType

# ---------------------------------------------------------------------------
# Schema (plain dataclasses — Pydantic is not needed for internal transfer)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CategoryFact:
    category_id: str
    category_name: str
    total_cents: int
    count: int


@dataclass(frozen=True, slots=True)
class BudgetFact:
    amount_cents: int
    spent_cents: int
    remaining_cents: int
    pct_used: float  # 0.0 – 100.0


@dataclass(frozen=True, slots=True)
class TransactionFact:
    id: str
    amount_cents: int
    category_name: str
    note: str
    biz_date: str


@dataclass(frozen=True, slots=True)
class TrendFact:
    period: str  # YYYY-MM
    total_income: int
    total_expense: int
    balance: int
    transaction_count: int


@dataclass(frozen=True, slots=True)
class FactPack:
    book_id: str
    period: str
    total_income: int
    total_expense: int
    balance: int
    transaction_count: int
    expense_by_category: list[CategoryFact]
    income_by_category: list[CategoryFact]
    budget: BudgetFact | None
    largest_expense: TransactionFact | None
    largest_income: TransactionFact | None
    trend: list[TrendFact]
    data_hash: str


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

# In-memory cache: key = f"{book_id}:{period}:{data_hash}" → FactPack
_cache: dict[str, tuple[float, FactPack]] = {}
_CACHE_TTL_SECONDS = 300  # 5 minutes
_MAX_CACHE_ENTRIES = 500


def _cache_key(book_id: uuid.UUID, period: str, data_hash: str) -> str:
    return f"{book_id}:{period}:{data_hash}"


def _cache_get(key: str) -> FactPack | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    inserted, pack = entry
    if time.time() - inserted > _CACHE_TTL_SECONDS:
        _cache.pop(key, None)
        return None
    return pack


def _cache_set(key: str, pack: FactPack) -> None:
    if len(_cache) >= _MAX_CACHE_ENTRIES:
        # Evict oldest
        oldest = min(_cache.items(), key=lambda kv: kv[1][0])[0]
        _cache.pop(oldest, None)
    _cache[key] = (time.time(), pack)


def _cache_clear() -> None:
    """Test helper."""
    _cache.clear()


# ---------------------------------------------------------------------------
# Hash computation
# ---------------------------------------------------------------------------


def _compute_data_hash(
    total_income: int,
    total_expense: int,
    expense_cats: list[CategoryFact],
    income_cats: list[CategoryFact],
    budget: BudgetFact | None,
    largest_expense: TransactionFact | None,
    largest_income: TransactionFact | None,
    trend: list[TrendFact],
) -> str:
    """Deterministic SHA-256 of the numerical content.

    The hash covers all financial numbers so that an identical data snapshot
    produces the same hash — enabling the LLM-call cache.
    """
    canonical: list[str] = [
        str(total_income),
        str(total_expense),
    ]
    for c in sorted(expense_cats, key=lambda x: x.category_id):
        canonical.append(f"{c.category_id}:{c.total_cents}:{c.count}")
    for c in sorted(income_cats, key=lambda x: x.category_id):
        canonical.append(f"{c.category_id}:{c.total_cents}:{c.count}")
    if budget:
        canonical.append(f"budget:{budget.amount_cents}:{budget.spent_cents}")
    if largest_expense:
        canonical.append(f"maxexp:{largest_expense.amount_cents}")
    if largest_income:
        canonical.append(f"maxinc:{largest_income.amount_cents}")
    for t in sorted(trend, key=lambda x: x.period):
        canonical.append(f"{t.period}:{t.total_income}:{t.total_expense}:{t.balance}")
    raw = "\n".join(canonical).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]  # 16 hex chars is enough for cache


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@dataclass
class FactPackService:
    """Build a FactPack for a given book and period.

    Reuses the StatisticsService for monthly aggregation and trend data.
    The fact pack is purely deterministic — no LLM involved.
    """

    db: AsyncSession

    async def build(self, book_id: uuid.UUID, user_id: uuid.UUID, period: str) -> FactPack:
        """Build (or return cached) fact pack for *period* (YYYY-MM)."""
        # Verify ownership first
        result = await self.db.execute(
            select(Book).where(
                Book.id == book_id,
                Book.owner_id == user_id,
                Book.deleted_at.is_(None),
            )
        )
        if result.scalar_one_or_none() is None:
            raise AppException(404, "not_found", "Book not found")

        year, month = self._parse_period(period)
        start_date = date(year, month, 1)
        end_date = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)

        # 1. Monthly totals
        totals = await self._query_totals(book_id, start_date, end_date)
        total_income = totals["total_income"]
        total_expense = totals["total_expense"]
        # Allow negative balance (deficit) so LLM can generate insights about overspending
        balance = total_income - total_expense
        tx_count = totals["tx_count"]

        # 2. Category breakdown
        expense_cats, income_cats = await self._query_category_breakdown(
            book_id, start_date, end_date,
        )

        # 3. Budget execution
        budget = await self._query_budget(book_id, period)

        # 4. Largest transactions
        largest_expense = await self._query_largest_tx(
            book_id, start_date, end_date, TxType.EXPENSE,
        )
        largest_income = await self._query_largest_tx(
            book_id, start_date, end_date, TxType.INCOME,
        )

        # 5. Trend (last 6 months including current)
        trend = await self._query_trend(book_id, year, month)

        # 6. Hash
        data_hash = _compute_data_hash(
            total_income, total_expense,
            expense_cats, income_cats,
            budget, largest_expense, largest_income,
            trend,
        )

        # 7. Check cache
        key = _cache_key(book_id, period, data_hash)
        cached = _cache_get(key)
        if cached is not None:
            return cached

        pack = FactPack(
            book_id=str(book_id),
            period=period,
            total_income=total_income,
            total_expense=total_expense,
            balance=balance,
            transaction_count=tx_count,
            expense_by_category=expense_cats,
            income_by_category=income_cats,
            budget=budget,
            largest_expense=largest_expense,
            largest_income=largest_income,
            trend=trend,
            data_hash=data_hash,
        )
        _cache_set(key, pack)
        return pack

    # -- private helpers -------------------------------------------------------

    @staticmethod
    def _parse_period(period: str) -> tuple[int, int]:
        try:
            year, month = period.split("-")
            y, m = int(year), int(month)
            date(y, m, 1)  # validate
            return y, m
        except ValueError:
            raise AppException(422, "invalid_period", f"Invalid period: {period!r}")

    async def _query_totals(
        self, book_id: uuid.UUID, start: date, end: date,
    ) -> dict:
        result = await self.db.execute(
            select(
                func.coalesce(
                    func.sum(
                        case((Transaction.type == TxType.INCOME, Transaction.amount_cents), else_=0)
                    ), 0
                ).label("total_income"),
                func.coalesce(
                    func.sum(
                        case((Transaction.type == TxType.EXPENSE, Transaction.amount_cents), else_=0)
                    ), 0
                ).label("total_expense"),
                func.count().label("tx_count"),
            ).where(
                Transaction.book_id == book_id,
                Transaction.deleted_at.is_(None),
                Transaction.biz_date >= start,
                Transaction.biz_date < end,
            )
        )
        row = result.one()
        return {
            "total_income": int(row.total_income),
            "total_expense": int(row.total_expense),
            "tx_count": int(row.tx_count),
        }

    async def _query_category_breakdown(
        self, book_id: uuid.UUID, start: date, end: date,
    ) -> tuple[list[CategoryFact], list[CategoryFact]]:
        from src.models.ledger import Category

        q = (
            select(
                Transaction.category_id,
                Category.name,
                Transaction.type,
                func.sum(Transaction.amount_cents).label("total_cents"),
                func.count().label("count"),
            )
            .join(Category, Transaction.category_id == Category.id)
            .where(
                Transaction.book_id == book_id,
                Transaction.deleted_at.is_(None),
                Transaction.biz_date >= start,
                Transaction.biz_date < end,
            )
            .group_by(Transaction.category_id, Category.name, Transaction.type)
        )
        rows = (await self.db.execute(q)).all()

        expense: list[CategoryFact] = []
        income: list[CategoryFact] = []
        for row in rows:
            cf = CategoryFact(
                category_id=str(row.category_id),
                category_name=row.name,
                total_cents=int(row.total_cents),
                count=int(row.count),
            )
            if row.type == TxType.EXPENSE:
                expense.append(cf)
            else:
                income.append(cf)
        expense.sort(key=lambda x: x.total_cents, reverse=True)
        income.sort(key=lambda x: x.total_cents, reverse=True)
        return expense, income

    async def _query_budget(
        self, book_id: uuid.UUID, period: str,
    ) -> BudgetFact | None:
        """Find the overall monthly budget for this period."""

        budget_row = (
            await self.db.execute(
                select(Budget).where(
                    Budget.book_id == book_id,
                    Budget.budget_type == BudgetType.MONTHLY,
                    Budget.period == period,
                    Budget.category_id.is_(None),
                    Budget.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

        if budget_row is None:
            return None

        # Compute spent amount for this period
        year, month = self._parse_period(period)
        start = date(year, month, 1)
        end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)

        spent = await self.db.scalar(
            select(func.coalesce(func.sum(Transaction.amount_cents), 0))
            .where(
                Transaction.book_id == book_id,
                Transaction.deleted_at.is_(None),
                Transaction.type == TxType.EXPENSE,
                Transaction.biz_date >= start,
                Transaction.biz_date < end,
            )
        )
        spent_cents = int(spent) if spent else 0
        budget_cents = budget_row.amount_cents
        remaining = max(0, budget_cents - spent_cents)
        pct = round(spent_cents / budget_cents * 100, 1) if budget_cents > 0 else 0.0

        return BudgetFact(
            amount_cents=budget_cents,
            spent_cents=spent_cents,
            remaining_cents=remaining,
            pct_used=pct,
        )

    async def _query_largest_tx(
        self, book_id: uuid.UUID, start: date, end: date, tx_type: TxType,
    ) -> TransactionFact | None:
        from src.models.ledger import Category

        row = (
            await self.db.execute(
                select(
                    Transaction.id,
                    Transaction.amount_cents,
                    Category.name,
                    Transaction.note,
                    Transaction.biz_date,
                )
                .join(Category, Transaction.category_id == Category.id)
                .where(
                    Transaction.book_id == book_id,
                    Transaction.deleted_at.is_(None),
                    Transaction.type == tx_type,
                    Transaction.biz_date >= start,
                    Transaction.biz_date < end,
                )
                .order_by(Transaction.amount_cents.desc())
                .limit(1)
            )
        ).one_or_none()

        if row is None:
            return None
        return TransactionFact(
            id=str(row.id),
            amount_cents=int(row.amount_cents),
            category_name=row.name,
            note=row.note or "",
            biz_date=str(row.biz_date),
        )

    async def _query_trend(
        self, book_id: uuid.UUID, year: int, month: int,
    ) -> list[TrendFact]:
        """Return trend for the last 6 months ending at *year-month*."""
        from src.modules.statistics.service import YearMonth

        # Compute start: 6 months back
        total_months = year * 12 + month - 5  # 6 months including current
        sy = total_months // 12
        sm = total_months % 12
        if sm == 0:
            sm = 12
            sy -= 1
        start_date = date(sy, sm, 1)
        em = month + 1
        ey = year
        if em > 12:
            em = 1
            ey += 1
        end_date = date(ey, em, 1)

        ym = YearMonth(Transaction.biz_date).label("period")
        rows = (
            await self.db.execute(
                select(
                    ym.label("period"),
                    func.coalesce(
                        func.sum(
                            case((Transaction.type == TxType.INCOME, Transaction.amount_cents), else_=0)
                        ), 0
                    ).label("total_income"),
                    func.coalesce(
                        func.sum(
                            case((Transaction.type == TxType.EXPENSE, Transaction.amount_cents), else_=0)
                        ), 0
                    ).label("total_expense"),
                    func.count().label("tx_count"),
                )
                .where(
                    Transaction.book_id == book_id,
                    Transaction.deleted_at.is_(None),
                    Transaction.biz_date >= start_date,
                    Transaction.biz_date < end_date,
                )
                .group_by(ym)
                .order_by(ym)
            )
        ).all()

        data = {
            r.period: {
                "total_income": int(r.total_income),
                "total_expense": int(r.total_expense),
                "tx_count": int(r.tx_count),
            }
            for r in rows
        }

        result: list[TrendFact] = []
        cy, cm = sy, sm
        for _ in range(6):
            p = f"{cy:04d}-{cm:02d}"
            d = data.get(p, {"total_income": 0, "total_expense": 0, "tx_count": 0})
            income = d["total_income"]
            expense = d["total_expense"]
            result.append(TrendFact(
                period=p,
                total_income=income,
                total_expense=expense,
                # P1#3: allow negative balance (deficit), consistent with the
                # current-month balance in build() — truncating trend months to
                # 0 while the current month goes negative feeds the LLM
                # self-contradictory data.
                balance=income - expense,
                transaction_count=d["tx_count"],
            ))
            cm += 1
            if cm > 12:
                cm = 1
                cy += 1
        return result
