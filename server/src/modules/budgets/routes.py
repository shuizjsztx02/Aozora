"""T-M2-04: Budget API routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database import get_db_session
from src.models.user import User
from src.modules.auth.dependencies import get_current_user

from .schemas import BudgetResponse, BudgetUpsert
from .service import BudgetService

router = APIRouter(prefix="/budgets", tags=["budgets"])


@router.get("", response_model=list[BudgetResponse])
async def list_budgets(
    book_id: uuid.UUID = Query(...),
    period: str | None = Query(default=None),
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    service = BudgetService(db)
    budgets = await service.list_budgets(book_id, current_user.id, period)
    return [BudgetResponse.model_validate(b) for b in budgets]


@router.put("/{period}", response_model=BudgetResponse)
async def upsert_budget(
    period: Annotated[
        str,
        Path(
            pattern=r"^\d{4}-\d{2}$",
            title="Period in YYYY-MM format (e.g. 2024-01)",
        ),
    ],
    body: BudgetUpsert,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    """Create or update an overall budget for a period (category_id=null)."""
    service = BudgetService(db)
    budget = await service.upsert_budget(current_user.id, period, body)
    return BudgetResponse.model_validate(budget)


@router.put("/{period}/category/{category_id}", response_model=BudgetResponse)
async def upsert_category_budget(
    period: Annotated[
        str,
        Path(
            pattern=r"^\d{4}-\d{2}$",
            title="Period in YYYY-MM format (e.g. 2024-01)",
        ),
    ],
    category_id: uuid.UUID,
    body: BudgetUpsert,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    """Create or update a category-specific budget for a period."""
    # Override category_id in body
    body.category_id = category_id
    service = BudgetService(db)
    budget = await service.upsert_budget(current_user.id, period, body)
    return BudgetResponse.model_validate(budget)
