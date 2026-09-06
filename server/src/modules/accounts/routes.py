"""T-M2-04: Account API routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database import get_db_session
from src.common.pagination import PaginatedResponse
from src.models.user import User
from src.modules.auth.dependencies import get_current_user

from .schemas import AccountCreate, AccountResponse, AccountUpdate
from .service import AccountService

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("", response_model=PaginatedResponse[AccountResponse])
async def list_accounts(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    service = AccountService(db)
    items, total = await service.list_accounts(current_user.id, limit, offset)
    return PaginatedResponse(
        items=[AccountResponse.model_validate(a) for a in items],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + limit < total,
    )


@router.post("", response_model=AccountResponse, status_code=201)
async def create_account(
    body: AccountCreate,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    service = AccountService(db)
    account = await service.create_account(current_user.id, body)
    return AccountResponse.model_validate(account)


@router.get("/{account_id}", response_model=AccountResponse)
async def get_account(
    account_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    service = AccountService(db)
    account = await service.get_account(account_id, current_user.id)
    return AccountResponse.model_validate(account)


@router.patch("/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: uuid.UUID,
    body: AccountUpdate,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    service = AccountService(db)
    account = await service.update_account(account_id, current_user.id, body)
    return AccountResponse.model_validate(account)


@router.delete("/{account_id}", status_code=204)
async def delete_account(
    account_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    service = AccountService(db)
    await service.archive_account(account_id, current_user.id)
