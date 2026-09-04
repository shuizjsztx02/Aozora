"""T-M2-04: Book business logic."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.common.errors import AppException
from src.models.ledger import Book

from .repository import BookRepository
from .schemas import BookCreate, BookUpdate


class BookService:
    """Business logic for Book operations."""

    def __init__(self, db: AsyncSession):
        self.repo = BookRepository(db)

    async def list_books(
        self,
        user_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Book], int]:
        return await self.repo.list_by_owner(user_id, limit, offset)

    async def get_book(self, book_id: uuid.UUID, user_id: uuid.UUID) -> Book:
        book = await self.repo.get_by_id(book_id, user_id)
        if book is None:
            raise AppException(404, "not_found", "Book not found")
        return book

    async def create_book(self, user_id: uuid.UUID, data: BookCreate) -> Book:
        return await self.repo.create(user_id, data.name, data.icon)

    async def update_book(self, book_id: uuid.UUID, user_id: uuid.UUID, data: BookUpdate) -> Book:
        book = await self.get_book(book_id, user_id)

        # Optimistic concurrency check
        if book.version != data.version:
            raise AppException(
                409, "version_conflict",
                f"Book version mismatch: expected {data.version}, current {book.version}",
            )

        return await self.repo.update(book, data.name, data.icon)

    async def delete_book(self, book_id: uuid.UUID, user_id: uuid.UUID) -> None:
        book = await self.get_book(book_id, user_id)
        await self.repo.soft_delete(book)
