"""T-M3-01: Data access layer for sync operations."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.budget import Budget
from src.models.ledger import Account, Book, Category, Transaction
from src.models.mutation import MutationIdempotency
from src.models.sync import SyncChange, SyncOperation


class SyncRepository:
    """Data access for sync-related entities."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # -- Idempotency -------------------------------------------------------

    async def get_mutation(
        self, mutation_id: uuid.UUID, user_id: uuid.UUID,
    ) -> MutationIdempotency | None:
        """Look up a previously applied mutation."""
        result = await self.db.execute(
            select(MutationIdempotency).where(
                MutationIdempotency.mutation_id == mutation_id,
                MutationIdempotency.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def record_mutation(
        self,
        mutation_id: uuid.UUID,
        user_id: uuid.UUID,
        entity_type: str,
        entity_id: uuid.UUID,
        sync_cursor: int,
        result_version: int,
    ) -> MutationIdempotency:
        record = MutationIdempotency(
            mutation_id=mutation_id,
            user_id=user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            sync_cursor=sync_cursor,
            result_version=result_version,
        )
        self.db.add(record)
        await self.db.flush()
        return record

    # -- SyncChange --------------------------------------------------------

    async def write_change(
        self,
        user_id: uuid.UUID,
        entity_type: str,
        entity_id: uuid.UUID,
        operation: SyncOperation,
        version: int,
    ) -> SyncChange:
        change = SyncChange(
            user_id=user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            operation=operation,
            version=version,
        )
        self.db.add(change)
        await self.db.flush()
        return change

    async def get_changes_since(
        self,
        user_id: uuid.UUID,
        cursor: int,
        limit: int = 100,
    ) -> list[SyncChange]:
        """Return changes after *cursor* for the given user, ordered by cursor."""
        result = await self.db.execute(
            select(SyncChange)
            .where(
                SyncChange.user_id == user_id,
                SyncChange.cursor > cursor,
            )
            .order_by(SyncChange.cursor.asc())
            .limit(limit + 1)  # fetch one extra to detect has_more
        )
        return list(result.scalars().all())

    async def get_latest_cursor(self, user_id: uuid.UUID) -> int:
        """Return the latest cursor for the user, or 0 if no changes."""
        result = await self.db.execute(
            select(func.max(SyncChange.cursor)).where(
                SyncChange.user_id == user_id,
            )
        )
        return result.scalar_one() or 0

    # -- Entity reads (for pull/bootstrap) ---------------------------------

    async def get_entity(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
        user_id: uuid.UUID,
        include_deleted: bool = False,
    ) -> object | None:
        """Read a single entity, verifying ownership.

        Args:
            include_deleted: If True, return entity even if deleted/archived.
                Used by pull to reconstruct change history.
        """
        return await self._read_entity(entity_type, entity_id, user_id, include_deleted)

    async def get_all_entities(
        self, user_id: uuid.UUID,
    ) -> dict[str, list]:
        """Read all non-deleted/non-archived entities for bootstrap."""
        # Books
        books_result = await self.db.execute(
            select(Book).where(
                Book.owner_id == user_id,
                Book.deleted_at.is_(None),
            )
        )
        books = list(books_result.scalars().all())

        book_ids = [b.id for b in books]

        # Categories
        cats_result = await self.db.execute(
            select(Category).where(
                Category.owner_id == user_id,
                Category.archived_at.is_(None),
            )
        )
        categories = list(cats_result.scalars().all())

        # Accounts
        accs_result = await self.db.execute(
            select(Account).where(
                Account.owner_id == user_id,
                Account.archived_at.is_(None),
            )
        )
        accounts = list(accs_result.scalars().all())

        # Transactions (only for user's books)
        txs_result = await self.db.execute(
            select(Transaction).where(
                Transaction.book_id.in_(book_ids),
                Transaction.deleted_at.is_(None),
            ) if book_ids else select(Transaction).where(False)  # noqa: E712
        )
        transactions = list(txs_result.scalars().all())

        # Budgets (only for user's books, excluding soft-deleted — T-M3-02)
        budgets_result = await self.db.execute(
            select(Budget).where(
                Budget.book_id.in_(book_ids),
                Budget.deleted_at.is_(None),
            ) if book_ids else select(Budget).where(False)  # noqa: E712
        )
        budgets = list(budgets_result.scalars().all())

        return {
            "books": books,
            "categories": categories,
            "accounts": accounts,
            "transactions": transactions,
            "budgets": budgets,
        }

    # -- Entity reads (for ownership verification) -------------------------

    async def get_book(self, book_id: uuid.UUID, user_id: uuid.UUID, include_deleted: bool = False) -> Book | None:
        # P2-11: 过滤 deleted_at，防止对已删 book 推 update 产生 zombie 变更
        # 但 pull 需要 include_deleted=True 来 reconstruct 历史
        conditions = [Book.id == book_id, Book.owner_id == user_id]
        if not include_deleted:
            conditions.append(Book.deleted_at.is_(None))
        result = await self.db.execute(select(Book).where(*conditions))
        return result.scalar_one_or_none()

    async def get_category(self, cat_id: uuid.UUID, user_id: uuid.UUID, include_deleted: bool = False) -> Category | None:
        # F-M3-1: filter archived_at so sync update/delete cannot touch
        # archived categories (mirrors get_book's deleted_at filter).
        conditions = [Category.id == cat_id, Category.owner_id == user_id]
        if not include_deleted:
            conditions.append(Category.archived_at.is_(None))
        result = await self.db.execute(select(Category).where(*conditions))
        return result.scalar_one_or_none()

    async def get_account(self, acc_id: uuid.UUID, user_id: uuid.UUID, include_deleted: bool = False) -> Account | None:
        # F-M3-1: filter archived_at so sync update/delete cannot touch
        # archived accounts (mirrors get_book's deleted_at filter).
        conditions = [Account.id == acc_id, Account.owner_id == user_id]
        if not include_deleted:
            conditions.append(Account.archived_at.is_(None))
        result = await self.db.execute(select(Account).where(*conditions))
        return result.scalar_one_or_none()

    async def get_transaction(self, tx_id: uuid.UUID, user_id: uuid.UUID, include_deleted: bool = False) -> Transaction | None:
        # P2-11: 过滤 deleted_at
        # NEW-1: 同时过滤父级 Book.deleted_at（include_deleted=False 时），
        # 防止对软删 book 下的 tx 执行 update；pull 使用 include_deleted=True
        # 来 reconstruct 历史，不受影响。
        conditions = [Transaction.id == tx_id, Book.owner_id == user_id]
        if not include_deleted:
            conditions.append(Transaction.deleted_at.is_(None))
            conditions.append(Book.deleted_at.is_(None))
        result = await self.db.execute(
            select(Transaction)
            .join(Book, Transaction.book_id == Book.id)
            .where(*conditions)
        )
        return result.scalar_one_or_none()

    async def get_budget(self, budget_id: uuid.UUID, user_id: uuid.UUID, include_deleted: bool = False) -> Budget | None:
        # P2-11: 过滤 deleted_at
        # NEW-1: 同时过滤父级 Book.deleted_at（include_deleted=False 时），
        # 防止对软删 book 下的 budget 执行 update；pull 使用
        # include_deleted=True 来 reconstruct 历史，不受影响。
        conditions = [Budget.id == budget_id, Book.owner_id == user_id]
        if not include_deleted:
            conditions.append(Budget.deleted_at.is_(None))
            conditions.append(Book.deleted_at.is_(None))
        result = await self.db.execute(
            select(Budget)
            .join(Book, Budget.book_id == Book.id)
            .where(*conditions)
        )
        return result.scalar_one_or_none()

    # -- Internal -----------------------------------------------------------

    async def _read_entity(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
        user_id: uuid.UUID,
        include_deleted: bool = False,
    ) -> object | None:
        """Read entity by type, verifying ownership via user_id."""
        if entity_type == "book":
            return await self.get_book(entity_id, user_id, include_deleted)
        if entity_type == "category":
            return await self.get_category(entity_id, user_id, include_deleted)
        if entity_type == "account":
            return await self.get_account(entity_id, user_id, include_deleted)
        if entity_type == "transaction":
            return await self.get_transaction(entity_id, user_id, include_deleted)
        if entity_type == "budget":
            return await self.get_budget(entity_id, user_id, include_deleted)
        return None
