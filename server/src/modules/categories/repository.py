"""T-M2-04: Category data access layer."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.errors import AppException
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

    async def get_by_id_including_archived(
        self, category_id: uuid.UUID, owner_id: uuid.UUID,
    ) -> Category | None:
        """Get a category by ID regardless of archive status.

        Used by the cycle-detection walk, which needs to traverse through
        ancestors that may themselves be archived.
        """
        result = await self.db.execute(
            select(Category).where(
                Category.id == category_id,
                Category.owner_id == owner_id,
            )
        )
        return result.scalar_one_or_none()

    async def _name_conflict_exists(
        self,
        owner_id: uuid.UUID,
        name: str,
        kind: str,
        book_id: uuid.UUID | None,
        parent_id: uuid.UUID | None,
        exclude_id: uuid.UUID | None = None,
    ) -> bool:
        """Return True if a category with the same scope+name already
        exists for this owner.

        Mirrors ``uq_categories_scope_name`` exactly — that constraint
        spans (book_id, parent_id, kind, name, owner_id) and does NOT
        exclude archived rows, so an archived category still occupies
        its name slot. Filtering archived here would let a conflict
        pass the pre-check and resurface as an IntegrityError.
        """
        conditions = [
            Category.owner_id == owner_id,
            Category.name == name,
            Category.kind == kind,
        ]
        if book_id is not None:
            conditions.append(Category.book_id == book_id)
        else:
            conditions.append(Category.book_id.is_(None))
        if parent_id is not None:
            conditions.append(Category.parent_id == parent_id)
        else:
            conditions.append(Category.parent_id.is_(None))
        if exclude_id is not None:
            conditions.append(Category.id != exclude_id)

        result = await self.db.execute(select(Category).where(*conditions))
        return result.scalar_one_or_none() is not None

    async def _check_name_conflict(
        self,
        owner_id: uuid.UUID,
        name: str,
        kind: str,
        book_id: uuid.UUID | None,
        parent_id: uuid.UUID | None,
        exclude_id: uuid.UUID | None = None,
    ) -> None:
        """Raise ``AppException(409)`` if the name is taken in this scope.

        Pre-check so the client gets a friendly 409 instead of the 500
        that the unique-constraint violation would otherwise surface as.
        """
        if await self._name_conflict_exists(
            owner_id, name, kind, book_id, parent_id, exclude_id,
        ):
            raise AppException(
                409, "duplicate_name",
                "A category with this name already exists in the same scope",
            )

    async def _flush_or_duplicate_conflict(
        self,
        owner_id: uuid.UUID,
        name: str,
        kind: str,
        book_id: uuid.UUID | None,
        parent_id: uuid.UUID | None,
        exclude_id: uuid.UUID | None = None,
    ) -> None:
        """Flush pending changes; translate a lost unique-constraint race
        into ``AppException(409)`` (N-08).

        The pre-check in ``_check_name_conflict`` covers the common path,
        but a concurrent request can still insert the same name between
        check and flush. On ``IntegrityError`` we roll back, re-check,
        and raise 409 only if a duplicate is actually visible — any
        other IntegrityError cause (e.g. FK violation) is re-raised.
        """
        try:
            await self.db.flush()
        except IntegrityError:
            await self.db.rollback()
            if await self._name_conflict_exists(
                owner_id, name, kind, book_id, parent_id, exclude_id,
            ):
                raise AppException(
                    409, "duplicate_name",
                    "A category with this name already exists in the same scope",
                )
            raise

    async def create(
        self,
        owner_id: uuid.UUID,
        name: str,
        kind: str,
        book_id: uuid.UUID | None,
        parent_id: uuid.UUID | None,
    ) -> Category:
        # N-08: check name conflict before insert so the client gets 409
        # instead of 500 from the unique constraint violation.
        await self._check_name_conflict(owner_id, name, kind, book_id, parent_id)

        category = Category(
            owner_id=owner_id,
            name=name,
            kind=kind,
            book_id=book_id,
            parent_id=parent_id,
        )
        self.db.add(category)
        await self._flush_or_duplicate_conflict(owner_id, name, kind, book_id, parent_id)
        await self.db.refresh(category)
        return category

    async def update(self, category: Category, name: str | None, parent_id: uuid.UUID | None) -> Category:
        # N-08: any change to name or parent moves the row within the
        # unique (owner, book, parent, kind, name) space — check the
        # destination before writing so the client gets 409 instead of
        # a 500 from the unique constraint violation.
        new_name = name if name is not None else category.name
        new_parent = parent_id if parent_id is not None else category.parent_id
        if new_name != category.name or new_parent != category.parent_id:
            await self._check_name_conflict(
                category.owner_id, new_name, category.kind,
                category.book_id, new_parent, exclude_id=category.id,
            )
        if name is not None:
            category.name = name
        if parent_id is not None:
            category.parent_id = parent_id
        category.version += 1
        await self._flush_or_duplicate_conflict(
            category.owner_id, new_name, category.kind,
            category.book_id, new_parent, exclude_id=category.id,
        )
        await self.db.refresh(category)
        return category

    async def archive(self, category: Category) -> None:
        category.archived_at = datetime.now(UTC)
        category.version += 1
        await self.db.flush()
