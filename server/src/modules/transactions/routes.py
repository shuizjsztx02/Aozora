"""T-M2-04: Transaction API routes."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database import get_db_session
from src.common.pagination import PaginatedResponse
from src.models.user import User
from src.modules.auth.dependencies import get_current_user
from src.modules.auth.suspended import require_not_suspended as _require_not_suspended

from .schemas import TransactionCreate, TransactionResponse, TransactionUpdate
from .service import TransactionService

router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.get("", response_model=PaginatedResponse[TransactionResponse])
async def list_transactions(
    book_id: uuid.UUID = Query(...),
    biz_date_from: date | None = Query(default=None),
    biz_date_to: date | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    service = TransactionService(db)
    items, total = await service.list_transactions(
        book_id, current_user.id, biz_date_from, biz_date_to, limit, offset,
    )
    return PaginatedResponse(
        items=[TransactionResponse.model_validate(tx) for tx in items],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + limit < total,
    )


@router.post("", response_model=TransactionResponse, status_code=201)
async def create_transaction(
    body: TransactionCreate,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    _require_not_suspended(current_user)
    service = TransactionService(db)
    tx = await service.create_transaction(current_user.id, body)
    return TransactionResponse.model_validate(tx)


@router.get("/{transaction_id}", response_model=TransactionResponse)
async def get_transaction(
    transaction_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    service = TransactionService(db)
    tx = await service.get_transaction(transaction_id, current_user.id)
    return TransactionResponse.model_validate(tx)


@router.patch("/{transaction_id}", response_model=TransactionResponse)
async def update_transaction(
    transaction_id: uuid.UUID,
    body: TransactionUpdate,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    _require_not_suspended(current_user)
    service = TransactionService(db)
    tx = await service.update_transaction(transaction_id, current_user.id, body)
    return TransactionResponse.model_validate(tx)


@router.delete("/{transaction_id}", status_code=204)
async def delete_transaction(
    transaction_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    _require_not_suspended(current_user)
    service = TransactionService(db)
    await service.delete_transaction(transaction_id, current_user.id)


@router.post("/{transaction_id}/undo", response_model=TransactionResponse)
async def undo_delete_transaction(
    transaction_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    """Restore a soft-deleted transaction."""
    _require_not_suspended(current_user)
    service = TransactionService(db)
    tx = await service.undo_delete_transaction(transaction_id, current_user.id)
    return TransactionResponse.model_validate(tx)
