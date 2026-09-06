"""T-M2-04: Transaction data access layer."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.ledger import Transaction


class TransactionRepository:
    """Data access for Transaction entities."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_by_book(
        self,
        book_id: uuid.UUID,
        owner_id: uuid.UUID,
        biz_date_from: date | None = None,
        biz_date_to: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Transaction], int]:
        """List non-deleted transactions for a book with pagination."""
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
            return [], 0

        conditions = [
            Transaction.book_id == book_id,
            Transaction.deleted_at.is_(None),
        ]
        if biz_date_from:
            conditions.append(Transaction.biz_date >= biz_date_from)
        if biz_date_to:
            conditions.append(Transaction.biz_date <= biz_date_to)

        base = select(Transaction).where(*conditions)

        # Count
        count_q = select(func.count()).select_from(base.subquery())
        total = (await self.db.execute(count_q)).scalar_one()

        # Fetch
        q = base.order_by(Transaction.biz_date.desc(), Transaction.created_at.desc()).offset(offset).limit(limit)
        result = await self.db.execute(q)
        items = list(result.scalars().all())

        return items, total

    async def get_by_id(self, tx_id: uuid.UUID, owner_id: uuid.UUID) -> Transaction | None:
        """Get a transaction by ID, ensuring it belongs to the owner via book."""
        from src.models.ledger import Book

        result = await self.db.execute(
            select(Transaction)
            .join(Book, Transaction.book_id == Book.id)
            .where(
                Transaction.id == tx_id,
                Book.owner_id == owner_id,
                Transaction.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        book_id: uuid.UUID,
        tx_type: str,
        amount_cents: int,
        category_id: uuid.UUID,
        account_id: uuid.UUID | None,
        note: str,
        biz_date: date,
        occurred_at: datetime,
        source: str = "manual",
    ) -> Transaction:
        transaction = Transaction(
            book_id=book_id,
            type=tx_type,
            amount_cents=amount_cents,
            category_id=category_id,
            account_id=account_id,
            note=note,
            biz_date=biz_date,
            occurred_at=occurred_at,
            source=source,
        )
        self.db.add(transaction)
        await self.db.flush()
        await self.db.refresh(transaction)
        return transaction

    async def create_batch(
        self,
        transactions: list[dict],
    ) -> list[Transaction]:
        """Batch create transactions atomically.

        Each dict in ``transactions`` must have keys: book_id, type, amount_cents,
        category_id, account_id (optional), note, biz_date, occurred_at, source.

        All transactions are added in a single flush — if any fails validation,
        the entire batch is rolled back (atomic).
        """
        created: list[Transaction] = []
        for tx_data in transactions:
            tx = Transaction(**tx_data)
            self.db.add(tx)
            created.append(tx)
        await self.db.flush()
        # Refresh all to get generated IDs
        for tx in created:
            await self.db.refresh(tx)
        return created

    async def update(
        self,
        transaction: Transaction,
        tx_type: str | None,
        amount_cents: int | None,
        category_id: uuid.UUID | None,
        account_id: uuid.UUID | None,
        note: str | None,
        biz_date: date | None,
    ) -> Transaction:
        if tx_type is not None:
            transaction.type = tx_type
        if amount_cents is not None:
            transaction.amount_cents = amount_cents
        if category_id is not None:
            transaction.category_id = category_id
        if account_id is not None:
            transaction.account_id = account_id
        if note is not None:
            transaction.note = note
        if biz_date is not None:
            transaction.biz_date = biz_date
        transaction.version += 1
        await self.db.flush()
        await self.db.refresh(transaction)
        return transaction

    async def soft_delete(self, transaction: Transaction) -> None:
        transaction.deleted_at = datetime.now(UTC)
        transaction.version += 1
        await self.db.flush()

    async def restore(self, transaction: Transaction) -> None:
        """Restore a soft-deleted transaction."""
        transaction.deleted_at = None
        transaction.version += 1
        await self.db.flush()

    async def get_deleted_by_id(self, tx_id: uuid.UUID, owner_id: uuid.UUID) -> Transaction | None:
        """Get a soft-deleted transaction by ID for restoration."""
        from src.models.ledger import Book

        result = await self.db.execute(
            select(Transaction)
            .join(Book, Transaction.book_id == Book.id)
            .where(
                Transaction.id == tx_id,
                Book.owner_id == owner_id,
                Transaction.deleted_at.isnot(None),
            )
        )
        return result.scalar_one_or_none()
