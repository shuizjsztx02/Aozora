"""T-M2-04: Transaction business logic."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.errors import AppException
from src.common.telemetry import record_transaction
from src.models.ledger import Account, Book, Category, Transaction

from .repository import TransactionRepository
from .schemas import TransactionCreate, TransactionUpdate


def balance_delta(tx_type: str, amount_cents: int) -> int:
    """第十八轮 REQ-70（T18-7）：一笔交易对账户余额的净影响 —— **收入为正、支出为负**。

    这是**跨端契约**：客户端 ``db/Repository.kt`` 的 ``balanceDelta`` 用的是同一式
    （``if (type == "income") amountCents else -amountCents``）。两处必须一致 ——
    否则「AI 记一笔」与「手动记一笔」会把同一个账户的余额推向相反方向。
    两端各有用例钉住（``test_transaction_balance_link.py`` /
    ``RepositoryTransactionBalanceLinkTest``）。
    """
    return amount_cents if tx_type == "income" else -amount_cents


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

    async def _apply_balance_deltas(
        self, user_id: uuid.UUID, deltas: dict[uuid.UUID | None, int],
    ) -> list[Account]:
        """REQ-70：把各账户的净增量加到 ``balance_cents`` 上（调用方事务内）。

        **单点实现**：REST 路由与 AI 执行面都经本类的写方法进来，因此余额联动全仓
        只有这一处；符号由 :func:`balance_delta` 给出，绝不在这里另算一遍。

        - 同一账户的多笔在调用前已聚合（批量记账），本方法不重复合并；
        - 账户查不到 / 不属于该用户 → **静默跳过**：联动是记账的附属效果，不该让
          一笔合法流水因为账户查不到而写不进去（归档账户照样联动 —— 余额是事实，
          且本查询不过滤 ``archived_at``）；
        - **调用方负责登记 sync change**（AI 执行面）：服务端权威直写必须让设备
          看得见，这是 T14-8 的既有口径，与交易自身的登记同一分工。
        """
        accounts: list[Account] = []
        for account_id, delta in deltas.items():
            if account_id is None or delta == 0:
                continue
            account = await self.db.scalar(
                select(Account).where(
                    Account.id == account_id, Account.owner_id == user_id,
                )
            )
            if account is None:
                continue
            account.balance_cents += delta
            account.version += 1
            accounts.append(account)
        if accounts:
            await self.db.flush()
        return accounts

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
        # REQ-70：记账联动账户余额（无账户 / 账户查不到 → 不动）
        await self._apply_balance_deltas(
            user_id, {data.account_id: balance_delta(data.type, data.amount_cents)},
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
        # REQ-70：批量记账按账户聚合净增量 —— 同一账户的多笔只查改一次，
        # version 也只 +1（否则同一账户在一个批次里会被叠加多次）。
        deltas: dict[uuid.UUID | None, int] = {}
        for item in items:
            account_id = item.get("account_id")
            if account_id is not None:
                deltas[account_id] = deltas.get(account_id, 0) + balance_delta(
                    item["type"], item["amount_cents"],
                )
        await self._apply_balance_deltas(user_id, deltas)
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

        # REQ-70：编辑必须**重算**而非叠加增量 —— 旧账户回补 + 新账户加计。
        # 局部合并语义：``None`` 表示该字段不动，故先取有效值再算差额；
        # 账户没换时两者相抵即为净差额。
        new_account_id = data.account_id if data.account_id is not None else tx.account_id
        new_type = data.type if data.type is not None else tx.type
        new_amount = (
            data.amount_cents if data.amount_cents is not None else tx.amount_cents
        )
        new_signed = balance_delta(new_type, new_amount)
        old_signed = balance_delta(tx.type, tx.amount_cents)
        if tx.account_id == new_account_id:
            deltas = {tx.account_id: new_signed - old_signed}
        else:
            deltas = {tx.account_id: -old_signed, new_account_id: new_signed}

        updated = await self.repo.update(
            tx,
            tx_type=data.type,
            amount_cents=data.amount_cents,
            category_id=data.category_id,
            account_id=data.account_id,
            note=data.note,
            biz_date=data.biz_date,
        )
        await self._apply_balance_deltas(user_id, deltas)
        return updated

    async def delete_transaction(self, tx_id: uuid.UUID, user_id: uuid.UUID) -> None:
        tx = await self.get_transaction(tx_id, user_id)
        await self.repo.soft_delete(tx)
        # REQ-70：软删回补 —— 这笔钱不再占用该账户
        await self._apply_balance_deltas(
            user_id, {tx.account_id: -balance_delta(tx.type, tx.amount_cents)},
        )

    async def undo_delete_transaction(self, tx_id: uuid.UUID, user_id: uuid.UUID) -> Transaction:
        """Restore a soft-deleted transaction."""
        tx = await self.repo.get_deleted_by_id(tx_id, user_id)
        if tx is None:
            raise AppException(404, "not_found", "Deleted transaction not found")
        await self.repo.restore(tx)
        await self.db.refresh(tx)
        # REQ-70：撤销删除 = 这笔钱重新占用账户，回补的方向与软删相反
        await self._apply_balance_deltas(
            user_id, {tx.account_id: balance_delta(tx.type, tx.amount_cents)},
        )
        return tx
