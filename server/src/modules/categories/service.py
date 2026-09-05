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

    # Guard against pathological hierarchies — no legitimate use needs
    # more than 16 levels of nesting.
    _MAX_DEPTH = 16

    def __init__(self, db: AsyncSession):
        self.repo = CategoryRepository(db)

    async def _validate_parent(
        self,
        category_id: uuid.UUID,
        parent_id: uuid.UUID,
        owner_id: uuid.UUID,
    ) -> None:
        """Reject self-references and cycles in the parent chain.

        N-09: a category cannot be its own parent, and the ancestor walk
        from ``parent_id`` must not reach ``category_id`` (which would
        create a loop).
        """
        if parent_id == category_id:
            raise AppException(
                400, "self_reference", "A category cannot be its own parent",
            )

        # Walk the ancestor chain from parent_id; if we hit category_id,
        # there is a cycle.
        current_id: uuid.UUID | None = parent_id
        visited: set[uuid.UUID] = {category_id}
        depth = 0
        while current_id is not None:
            if current_id in visited:
                raise AppException(
                    400, "cycle_detected",
                    "Category hierarchy would contain a cycle",
                )
            visited.add(current_id)
            depth += 1
            if depth > self._MAX_DEPTH:
                raise AppException(
                    400, "depth_exceeded",
                    f"Category hierarchy exceeds maximum depth of {self._MAX_DEPTH}",
                )
            ancestor = await self.repo.get_by_id_including_archived(current_id, owner_id)
            if ancestor is None:
                # Parent doesn't exist / doesn't belong to owner — will be
                # caught by FK or ownership check elsewhere.
                break
            current_id = ancestor.parent_id

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
        # N-09 note: no cycle check needed at creation — the id is
        # server-generated, so the new category cannot yet be part of
        # any parent chain.
        return await self.repo.create(user_id, data.name, data.kind, data.book_id, data.parent_id)

    async def update_category(self, category_id: uuid.UUID, user_id: uuid.UUID, data: CategoryUpdate) -> Category:
        category = await self.get_category(category_id, user_id)

        # Optimistic concurrency check
        if category.version != data.version:
            raise AppException(
                409, "version_conflict",
                f"Category version mismatch: expected {data.version}, current {category.version}",
            )

        # N-09: cycle detection — only when the parent actually changes
        # (in CategoryUpdate, parent_id=None means "not provided").
        if data.parent_id is not None:
            await self._validate_parent(category_id, data.parent_id, user_id)

        return await self.repo.update(category, data.name, data.parent_id)

    async def archive_category(self, category_id: uuid.UUID, user_id: uuid.UUID) -> None:
        category = await self.get_category(category_id, user_id)
        await self.repo.archive(category)
