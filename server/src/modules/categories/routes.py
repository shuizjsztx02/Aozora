"""T-M2-04: Category API routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database import get_db_session
from src.common.pagination import PaginatedResponse
from src.models.user import User
from src.modules.auth.dependencies import get_current_user

from .schemas import CategoryCreate, CategoryResponse, CategoryUpdate
from .service import CategoryService

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("", response_model=PaginatedResponse[CategoryResponse])
async def list_categories(
    kind: str | None = Query(default=None, pattern=r"^(expense|income)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    service = CategoryService(db)
    items, total = await service.list_categories(current_user.id, kind, limit, offset)
    return PaginatedResponse(
        items=[CategoryResponse.model_validate(c) for c in items],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + limit < total,
    )


@router.post("", response_model=CategoryResponse, status_code=201)
async def create_category(
    body: CategoryCreate,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    service = CategoryService(db)
    category = await service.create_category(current_user.id, body)
    return CategoryResponse.model_validate(category)


@router.get("/{category_id}", response_model=CategoryResponse)
async def get_category(
    category_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    service = CategoryService(db)
    category = await service.get_category(category_id, current_user.id)
    return CategoryResponse.model_validate(category)


@router.patch("/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: uuid.UUID,
    body: CategoryUpdate,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    service = CategoryService(db)
    category = await service.update_category(category_id, current_user.id, body)
    return CategoryResponse.model_validate(category)


@router.delete("/{category_id}", status_code=204)
async def delete_category(
    category_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    service = CategoryService(db)
    await service.archive_category(category_id, current_user.id)
