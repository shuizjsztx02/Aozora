"""T-M2-05: Statistics business logic — authoritative aggregation."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import FunctionElement, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.compiler import compiles

from src.common.errors import AppException
from src.models.ledger import Book, Category, Transaction, TxType

from .schemas import (
    CategoryStats,
    MonthlyStats,
    TrendPoint,
    TrendStats,
)


class _YearMonth(FunctionElement):
    """Cross-dialect YYYY-MM extraction: strftime for SQLite, TO_CHAR for PG."""
    name = "year_month"
    inherit_cache = True


@compiles(_YearMonth)
def _ym_default(element, compiler, **kw):
    return f"TO_CHAR({compiler.process(element.clauses, **kw)}, 'YYYY-MM')"


@compiles(_YearMonth, "sqlite")
def _ym_sqlite(element, compiler, **kw):
    return f"strftime('%Y-%m', {compiler.process(element.clauses, **kw)})"


class StatisticsService:
    """Authoritative aggregation from Transaction table.

    Statistics口径：只统计 deleted_at IS NULL 的交易。
    balance = max(0, totalIncome - totalExpense)。
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _verify_book_ownership(self, book_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Verify the user owns the book and it has not been soft-deleted.

        N-01: filter ``deleted_at`` so statistics queries cannot be run
        against soft-deleted books.
        """
        result = await self.db.execute(
            select(Book).where(
                Book.id == book_id,
                Book.owner_id == user_id,
                Book.deleted_at.is_(None),
            )
        )
        if result.scalar_one_or_none() is None:
            raise AppException(404, "not_found", "Book not found")

    @staticmethod
    def _parse_period(period: str) -> tuple[int, int]:
        """Parse 'YYYY-MM' and validate month range; raise 422 on invalid."""
        try:
            year, month = period.split("-")
            y, m = int(year), int(month)
            date(y, m, 1)  # raises ValueError if month out of range
            return y, m
        except ValueError:
            raise AppException(422, "invalid_period", f"Invalid period: {period!r}")

    async def get_monthly_stats(self, book_id: uuid.UUID, user_id: uuid.UUID, period: str) -> MonthlyStats:
        """Aggregate monthly statistics for a book.

        period: YYYY-MM format.
        """
        await self._verify_book_ownership(book_id, user_id)

        # Parse period to date range
        year, month = self._parse_period(period)
        start_date = date(year, month, 1)
        if month == 12:  # N-33: compare int with int, not str
            end_date = date(year + 1, 1, 1)
        else:
            end_date = date(year, month + 1, 1)

        # Base filter: non-deleted transactions in the period for this book
        base_filter = [
            Transaction.book_id == book_id,
            Transaction.deleted_at.is_(None),
            Transaction.biz_date >= start_date,
            Transaction.biz_date < end_date,
        ]

        # Totals
        totals_q = select(
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
            func.count().label("transaction_count"),
        ).where(*base_filter)

        totals = (await self.db.execute(totals_q)).one()
        total_income = int(totals.total_income)
        total_expense = int(totals.total_expense)

        # Per-category breakdown
        cat_stats_q = (
            select(
                Transaction.category_id,
                Category.name,
                Transaction.type,
                func.sum(Transaction.amount_cents).label("total_cents"),
                func.count().label("count"),
            )
            .join(Category, Transaction.category_id == Category.id)
            .where(*base_filter)
            .group_by(Transaction.category_id, Category.name, Transaction.type)
        )
        cat_rows = (await self.db.execute(cat_stats_q)).all()

        expense_by_category: list[CategoryStats] = []
        income_by_category: list[CategoryStats] = []
        for row in cat_rows:
            cs = CategoryStats(
                category_id=row.category_id,
                category_name=row.name,
                total_cents=int(row.total_cents),
                count=int(row.count),
            )
            if row.type == TxType.EXPENSE:
                expense_by_category.append(cs)
            else:
                income_by_category.append(cs)

        # Sort by total_cents descending
        expense_by_category.sort(key=lambda x: x.total_cents, reverse=True)
        income_by_category.sort(key=lambda x: x.total_cents, reverse=True)

        return MonthlyStats(
            book_id=book_id,
            period=period,
            total_income=total_income,
            total_expense=total_expense,
            balance=max(0, total_income - total_expense),
            transaction_count=int(totals.transaction_count),
            expense_by_category=expense_by_category,
            income_by_category=income_by_category,
        )

    async def get_trend_stats(
        self,
        book_id: uuid.UUID,
        user_id: uuid.UUID,
        period_from: str,
        period_to: str,
    ) -> TrendStats:
        """Aggregate monthly trend from period_from to period_to (inclusive, YYYY-MM)."""
        await self._verify_book_ownership(book_id, user_id)

        # Parse period range
        fy, fm = self._parse_period(period_from)
        ty, tm = self._parse_period(period_to)

        # Validate period_from <= period_to
        if (fy, fm) > (ty, tm):
            raise AppException(
                422, "invalid_period_range",
                f"period_from ({period_from}) must be <= period_to ({period_to})",
            )

        start_date = date(fy, fm, 1)
        if tm == 12:  # N-33: compare int with int, not str
            end_date = date(ty + 1, 1, 1)
        else:
            end_date = date(ty, tm + 1, 1)

        base_filter = [
            Transaction.book_id == book_id,
            Transaction.deleted_at.is_(None),
            Transaction.biz_date >= start_date,
            Transaction.biz_date < end_date,
        ]

        # Group by year-month
        ym = _YearMonth(Transaction.biz_date).label("period")
        q = (
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
                func.count().label("transaction_count"),
            )
            .where(*base_filter)
            .group_by(ym)
            .order_by(ym)
        )

        rows = (await self.db.execute(q)).all()

        # Build a map of existing months
        data_map = {
            row.period: TrendPoint(
                period=row.period,
                total_income=int(row.total_income),
                total_expense=int(row.total_expense),
                balance=max(0, int(row.total_income) - int(row.total_expense)),
                transaction_count=int(row.transaction_count),
            )
            for row in rows
        }

        # Fill in all months in range (even empty ones)
        points: list[TrendPoint] = []
        cy, cm = int(fy), int(fm)
        ey, em = int(ty), int(tm)
        while (cy, cm) <= (ey, em):
            p = f"{cy:04d}-{cm:02d}"
            points.append(data_map.get(p, TrendPoint(
                period=p, total_income=0, total_expense=0, balance=0, transaction_count=0,
            )))
            cm += 1
            if cm > 12:
                cm = 1
                cy += 1

        return TrendStats(book_id=book_id, points=points)
