"""T-M2-04: Category business logic."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.common.errors import AppException
from src.models.ledger import Category

from .repository import CategoryRepository
from .schemas import CategoryCreate, CategoryUpdate


class CategoryService:
    """Business logic for Category operations."""

    def __init__(self, db: AsyncSession):
        self.repo = CategoryRepository(db)

    async def list_categories(
        self,
        user_id: uuid.UUID,
        kind: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Category], int]:
        return await self.repo.list_by_owner(user_id, kind, limit, offset)

    async def get_category(self, category_id: uuid.UUID, user_id: uuid.UUID) -> Category:
        category = await self.repo.get_by_id(category_id, user_id)
        if category is None:
            raise AppException(404, "not_found", "Category not found")
        return category

    async def create_category(self, user_id: uuid.UUID, data: CategoryCreate) -> Category:
        return await self.repo.create(user_id, data.name, data.kind, data.book_id, data.parent_id)

    async def update_category(self, category_id: uuid.UUID, user_id: uuid.UUID, data: CategoryUpdate) -> Category:
        category = await self.get_category(category_id, user_id)

        # Optimistic concurrency check
        if category.version != data.version:
            raise AppException(
                409, "version_conflict",
                f"Category version mismatch: expected {data.version}, current {category.version}",
            )

        return await self.repo.update(category, data.name, data.parent_id)

    async def archive_category(self, category_id: uuid.UUID, user_id: uuid.UUID) -> None:
        category = await self.get_category(category_id, user_id)
        await self.repo.archive(category)
