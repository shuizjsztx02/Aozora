"""T-M2-04: Budget business logic."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.errors import AppException
from src.models.budget import Budget
from src.models.ledger import Book, Category

from .repository import BudgetRepository
from .schemas import BudgetUpsert


class BudgetService:
    """Business logic for Budget operations."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = BudgetRepository(db)

    async def _verify_book_ownership(self, book_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Verify the user owns the book."""
        result = await self.db.execute(
            select(Book).where(Book.id == book_id, Book.owner_id == user_id)
        )
        if result.scalar_one_or_none() is None:
            raise AppException(404, "not_found", "Book not found")

    async def _verify_category_ownership(self, category_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Verify the user owns the category."""
        result = await self.db.execute(
            select(Category).where(Category.id == category_id, Category.owner_id == user_id)
        )
        if result.scalar_one_or_none() is None:
            raise AppException(404, "not_found", "Category not found")

    async def list_budgets(
        self,
        book_id: uuid.UUID,
        user_id: uuid.UUID,
        period: str | None = None,
    ) -> list[Budget]:
        await self._verify_book_ownership(book_id, user_id)
        return await self.repo.list_by_book(book_id, user_id, period)

    async def upsert_budget(self, user_id: uuid.UUID, period: str, data: BudgetUpsert) -> Budget:
        """Create or update a budget.

        PUT semantics: if budget exists for (book_id, budget_type, period, category_id),
        update it; otherwise create new.
        """
        # Verify ownership
        await self._verify_book_ownership(data.book_id, user_id)
        if data.category_id is not None:
            await self._verify_category_ownership(data.category_id, user_id)

        existing = await self.repo.get_by_period_and_category(
            data.book_id, data.budget_type, period, data.category_id,
        )

        if existing is None:
            # Create new
            return await self.repo.create(
                data.book_id, data.budget_type, period, data.amount_cents, data.category_id,
            )

        # Update existing
        if data.version is not None and existing.version != data.version:
            raise AppException(
                409, "version_conflict",
                f"Budget version mismatch: expected {data.version}, current {existing.version}",
            )

        return await self.repo.update(existing, data.amount_cents)
