"""T-M2-04: Category data access layer."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.ledger import Category


class CategoryRepository:
    """Data access for Category entities."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_by_owner(
        self,
        owner_id: uuid.UUID,
        kind: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Category], int]:
        """List non-archived categories for a user with pagination."""
        conditions = [
            Category.owner_id == owner_id,
            Category.archived_at.is_(None),
        ]
        if kind:
            conditions.append(Category.kind == kind)

        base = select(Category).where(*conditions)

        # Count
        count_q = select(func.count()).select_from(base.subquery())
        total = (await self.db.execute(count_q)).scalar_one()

        # Fetch
        q = base.order_by(Category.created_at.desc()).offset(offset).limit(limit)
        result = await self.db.execute(q)
        items = list(result.scalars().all())

        return items, total

    async def get_by_id(self, category_id: uuid.UUID, owner_id: uuid.UUID) -> Category | None:
        """Get a category by ID, ensuring it belongs to the owner."""
        result = await self.db.execute(
            select(Category).where(
                Category.id == category_id,
                Category.owner_id == owner_id,
                Category.archived_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        owner_id: uuid.UUID,
        name: str,
        kind: str,
        book_id: uuid.UUID | None,
        parent_id: uuid.UUID | None,
    ) -> Category:
        category = Category(
            owner_id=owner_id,
            name=name,
            kind=kind,
            book_id=book_id,
            parent_id=parent_id,
        )
        self.db.add(category)
        await self.db.flush()
        await self.db.refresh(category)
        return category

    async def update(self, category: Category, name: str | None, parent_id: uuid.UUID | None) -> Category:
        if name is not None:
            category.name = name
        if parent_id is not None:
            category.parent_id = parent_id
        category.version += 1
        await self.db.flush()
        await self.db.refresh(category)
        return category

    async def archive(self, category: Category) -> None:
        category.archived_at = datetime.now(timezone.utc)
        category.version += 1
        await self.db.flush()
