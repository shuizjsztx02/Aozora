"""T-M2-04: Account data access layer."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.ledger import Account


class AccountRepository:
    """Data access for Account entities."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_by_owner(
        self,
        owner_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Account], int]:
        """List non-archived accounts for a user with pagination."""
        base = select(Account).where(
            Account.owner_id == owner_id,
            Account.archived_at.is_(None),
        )

        # Count
        count_q = select(func.count()).select_from(base.subquery())
        total = (await self.db.execute(count_q)).scalar_one()

        # Fetch
        q = base.order_by(Account.created_at.desc()).offset(offset).limit(limit)
        result = await self.db.execute(q)
        items = list(result.scalars().all())

        return items, total

    async def get_by_id(self, account_id: uuid.UUID, owner_id: uuid.UUID) -> Account | None:
        """Get an account by ID, ensuring it belongs to the owner."""
        result = await self.db.execute(
            select(Account).where(
                Account.id == account_id,
                Account.owner_id == owner_id,
                Account.archived_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        owner_id: uuid.UUID,
        name: str,
        icon: str | None,
        account_type: str | None = None,
        balance_cents: int = 0,
    ) -> Account:
        account = Account(
            owner_id=owner_id, name=name, icon=icon,
            account_type=account_type, balance_cents=balance_cents,
        )
        self.db.add(account)
        await self.db.flush()
        await self.db.refresh(account)
        return account

    async def update(
        self,
        account: Account,
        name: str | None,
        icon: str | None,
        account_type: str | None = None,
        balance_cents: int | None = None,
    ) -> Account:
        if name is not None:
            account.name = name
        if icon is not None:
            account.icon = icon
        # None = 不修改（局部合并语义）。balance_cents 允许 0，故判 None 而非真值。
        if account_type is not None:
            account.account_type = account_type
        if balance_cents is not None:
            account.balance_cents = balance_cents
        account.version += 1
        await self.db.flush()
        await self.db.refresh(account)
        return account

    async def archive(self, account: Account) -> None:
        account.archived_at = datetime.now(UTC)
        account.version += 1
        await self.db.flush()
