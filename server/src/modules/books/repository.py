"""T-M2-04: Book data access layer."""

from __future__ import annotations

import uuid
from datetime import UTC

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.ledger import Book


class BookRepository:
    """Data access for Book entities."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_by_owner(
        self,
        owner_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Book], int]:
        """List non-archived books for a user with pagination."""
        base = select(Book).where(
            Book.owner_id == owner_id,
            Book.deleted_at.is_(None),
        )

        # Count
        count_q = select(func.count()).select_from(base.subquery())
        total = (await self.db.execute(count_q)).scalar_one()

        # Fetch
        q = base.order_by(Book.created_at.desc()).offset(offset).limit(limit)
        result = await self.db.execute(q)
        items = list(result.scalars().all())

        return items, total

    async def get_by_id(self, book_id: uuid.UUID, owner_id: uuid.UUID) -> Book | None:
        """Get a book by ID, ensuring it belongs to the owner."""
        result = await self.db.execute(
            select(Book).where(
                Book.id == book_id,
                Book.owner_id == owner_id,
                Book.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def create(self, owner_id: uuid.UUID, name: str, icon: str | None) -> Book:
        book = Book(owner_id=owner_id, name=name, icon=icon)
        self.db.add(book)
        await self.db.flush()
        await self.db.refresh(book)
        return book

    async def update(self, book: Book, name: str | None, icon: str | None) -> Book:
        if name is not None:
            book.name = name
        if icon is not None:
            book.icon = icon
        book.version += 1
        await self.db.flush()
        await self.db.refresh(book)
        return book

    async def soft_delete(self, book: Book) -> None:
        from datetime import datetime
        book.deleted_at = datetime.now(UTC)
        book.version += 1
        await self.db.flush()
