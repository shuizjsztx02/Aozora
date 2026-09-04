"""T-M2-04: Account business logic."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.common.errors import AppException
from src.models.ledger import Account

from .repository import AccountRepository
from .schemas import AccountCreate, AccountUpdate


class AccountService:
    """Business logic for Account operations."""

    def __init__(self, db: AsyncSession):
        self.repo = AccountRepository(db)

    async def list_accounts(
        self,
        user_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Account], int]:
        return await self.repo.list_by_owner(user_id, limit, offset)

    async def get_account(self, account_id: uuid.UUID, user_id: uuid.UUID) -> Account:
        account = await self.repo.get_by_id(account_id, user_id)
        if account is None:
            raise AppException(404, "not_found", "Account not found")
        return account

    async def create_account(self, user_id: uuid.UUID, data: AccountCreate) -> Account:
        return await self.repo.create(user_id, data.name, data.icon)

    async def update_account(self, account_id: uuid.UUID, user_id: uuid.UUID, data: AccountUpdate) -> Account:
        account = await self.get_account(account_id, user_id)

        # Optimistic concurrency check
        if account.version != data.version:
            raise AppException(
                409, "version_conflict",
                f"Account version mismatch: expected {data.version}, current {account.version}",
            )

        return await self.repo.update(account, data.name, data.icon)

    async def archive_account(self, account_id: uuid.UUID, user_id: uuid.UUID) -> None:
        account = await self.get_account(account_id, user_id)
        await self.repo.archive(account)
