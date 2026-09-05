"""T-M2-04: Budget data access layer."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.budget import Budget


class BudgetRepository:
    """Data access for Budget entities."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_by_book(
        self,
        book_id: uuid.UUID,
        owner_id: uuid.UUID,
        period: str | None = None,
    ) -> list[Budget]:
        """List budgets for a book, optionally filtered by period."""
        from src.models.ledger import Book

        # Verify book ownership (not deleted) — N-35: defensive soft-delete guard
        book_result = await self.db.execute(
            select(Book).where(
                Book.id == book_id,
                Book.owner_id == owner_id,
                Book.deleted_at.is_(None),
            )
        )
        if book_result.scalar_one_or_none() is None:
            return []

        conditions = [Budget.book_id == book_id]
        if period:
            conditions.append(Budget.period == period)

        result = await self.db.execute(
            select(Budget).where(*conditions).order_by(Budget.period.desc())
        )
        return list(result.scalars().all())

    async def get_by_period_and_category(
        self,
        book_id: uuid.UUID,
        budget_type: str,
        period: str,
        category_id: uuid.UUID | None,
    ) -> Budget | None:
        """Get a budget by unique key."""
        conditions = [
            Budget.book_id == book_id,
            Budget.budget_type == budget_type,
            Budget.period == period,
        ]
        if category_id is None:
            conditions.append(Budget.category_id.is_(None))
        else:
            conditions.append(Budget.category_id == category_id)

        result = await self.db.execute(select(Budget).where(*conditions))
        return result.scalar_one_or_none()

    async def create(
        self,
        book_id: uuid.UUID,
        budget_type: str,
        period: str,
        amount_cents: int,
        category_id: uuid.UUID | None,
    ) -> Budget:
        budget = Budget(
            book_id=book_id,
            budget_type=budget_type,
            period=period,
            amount_cents=amount_cents,
            category_id=category_id,
        )
        self.db.add(budget)
        await self.db.flush()
        await self.db.refresh(budget)
        return budget

    async def update(self, budget: Budget, amount_cents: int) -> Budget:
        budget.amount_cents = amount_cents
        budget.version += 1
        await self.db.flush()
        await self.db.refresh(budget)
        return budget
