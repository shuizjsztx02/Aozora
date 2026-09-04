"""T-M2-04: Book API routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database import get_db_session
from src.common.pagination import PaginatedResponse, PaginationParams
from src.modules.auth.dependencies import get_current_user
from src.models.user import User

from .schemas import BookCreate, BookResponse, BookUpdate
from .service import BookService

router = APIRouter(prefix="/books", tags=["books"])


@router.get("", response_model=PaginatedResponse[BookResponse])
async def list_books(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    service = BookService(db)
    items, total = await service.list_books(current_user.id, limit, offset)
    return PaginatedResponse(
        items=[BookResponse.model_validate(b) for b in items],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + limit < total,
    )


@router.post("", response_model=BookResponse, status_code=201)
async def create_book(
    body: BookCreate,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    service = BookService(db)
    book = await service.create_book(current_user.id, body)
    return BookResponse.model_validate(book)


@router.get("/{book_id}", response_model=BookResponse)
async def get_book(
    book_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    service = BookService(db)
    book = await service.get_book(book_id, current_user.id)
    return BookResponse.model_validate(book)


@router.patch("/{book_id}", response_model=BookResponse)
async def update_book(
    book_id: uuid.UUID,
    body: BookUpdate,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    service = BookService(db)
    book = await service.update_book(book_id, current_user.id, body)
    return BookResponse.model_validate(book)


@router.delete("/{book_id}", status_code=204)
async def delete_book(
    book_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    service = BookService(db)
    await service.delete_book(book_id, current_user.id)
