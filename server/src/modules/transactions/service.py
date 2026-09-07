"""T-M2-04: Transaction business logic."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.errors import AppException
from src.common.telemetry import record_transaction
from src.models.ledger import Book, Category, Transaction

from .repository import TransactionRepository
from .schemas import TransactionCreate, TransactionUpdate


class TransactionService:
    """Business logic for Transaction operations."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = TransactionRepository(db)

    async def _verify_book_ownership(self, book_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Verify the user owns the book and it has not been soft-deleted.

        N-01/N-12: filter ``deleted_at`` so a soft-deleted book behaves as
        gone — writes and list lookups return 404 instead of succeeding
        silently against a zombie book.
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

    async def _verify_category_ownership(self, category_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Verify the user owns the category."""
        result = await self.db.execute(
            select(Category).where(Category.id == category_id, Category.owner_id == user_id)
        )
        if result.scalar_one_or_none() is None:
            raise AppException(404, "not_found", "Category not found")

    async def list_transactions(
        self,
        book_id: uuid.UUID,
        user_id: uuid.UUID,
        biz_date_from: date | None = None,
        biz_date_to: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Transaction], int]:
        # Verify book ownership first
        await self._verify_book_ownership(book_id, user_id)
        return await self.repo.list_by_book(book_id, user_id, biz_date_from, biz_date_to, limit, offset)

    async def get_transaction(self, tx_id: uuid.UUID, user_id: uuid.UUID) -> Transaction:
        tx = await self.repo.get_by_id(tx_id, user_id)
        if tx is None:
            raise AppException(404, "not_found", "Transaction not found")
        return tx

    async def create_transaction(self, user_id: uuid.UUID, data: TransactionCreate) -> Transaction:
        # Verify ownership of related entities
        await self._verify_book_ownership(data.book_id, user_id)
        await self._verify_category_ownership(data.category_id, user_id)

        tx = await self.repo.create(
            book_id=data.book_id,
            tx_type=data.type,
            amount_cents=data.amount_cents,
            category_id=data.category_id,
            account_id=data.account_id,
            note=data.note,
            biz_date=data.biz_date,
            occurred_at=data.occurred_at,
        )
        record_transaction()
        return tx

    async def create_transaction_batch(
        self,
        user_id: uuid.UUID,
        book_id: uuid.UUID,
        items: list[dict],
        source: str = "ai",
    ) -> list[Transaction]:
        """Batch create transactions atomically.

        All transactions must belong to the same book. Ownership of the book
        and all categories is verified before any writes. If any validation
        fails, the entire batch is rolled back (atomic).

        ``items`` is a list of dicts with keys: type, amount_cents, category_id,
        account_id (optional), note, biz_date.
        """

        # Verify book ownership once
        await self._verify_book_ownership(book_id, user_id)

        # Verify all category IDs upfront
        category_ids = {item["category_id"] for item in items if item.get("category_id")}
        for cat_id in category_ids:
            await self._verify_category_ownership(cat_id, user_id)

        # Build transaction dicts
        now = datetime.now(UTC)
        tx_data_list = []
        for item in items:
            tx_data_list.append({
                "book_id": book_id,
                "type": item["type"],
                "amount_cents": item["amount_cents"],
                "category_id": item.get("category_id"),
                "account_id": item.get("account_id"),
                "note": item.get("note", ""),
                "biz_date": item["biz_date"],
                "occurred_at": now,
                "source": source,
            })

        created = await self.repo.create_batch(tx_data_list)
        for _ in created:
            record_transaction()
        return created

    async def update_transaction(self, tx_id: uuid.UUID, user_id: uuid.UUID, data: TransactionUpdate) -> Transaction:
        tx = await self.get_transaction(tx_id, user_id)

        # Optimistic concurrency check
        if tx.version != data.version:
            raise AppException(
                409, "version_conflict",
                f"Transaction version mismatch: expected {data.version}, current {tx.version}",
            )

        # Verify category ownership if changing
        if data.category_id is not None:
            await self._verify_category_ownership(data.category_id, user_id)

        return await self.repo.update(
            tx,
            tx_type=data.type,
            amount_cents=data.amount_cents,
            category_id=data.category_id,
            account_id=data.account_id,
            note=data.note,
            biz_date=data.biz_date,
        )

    async def delete_transaction(self, tx_id: uuid.UUID, user_id: uuid.UUID) -> None:
        tx = await self.get_transaction(tx_id, user_id)
        await self.repo.soft_delete(tx)

    async def undo_delete_transaction(self, tx_id: uuid.UUID, user_id: uuid.UUID) -> Transaction:
        """Restore a soft-deleted transaction."""
        tx = await self.repo.get_deleted_by_id(tx_id, user_id)
        if tx is None:
            raise AppException(404, "not_found", "Deleted transaction not found")
        await self.repo.restore(tx)
        await self.db.refresh(tx)
        return tx
