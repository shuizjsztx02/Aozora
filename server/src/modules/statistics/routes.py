"""T-M2-05 + T-M5-04c: Statistics API routes.

T-M5-04c: frozen users (pending deletion) are excluded from statistics
— endpoints return empty responses so their data does not contribute
to any aggregate view.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database import get_db_session
from src.models.user import User
from src.modules.auth.dependencies import get_current_user
from src.modules.auth.frozen import is_frozen as _is_frozen

from .schemas import MonthlyStats, TrendStats
from .service import StatisticsService

router = APIRouter(prefix="/stats", tags=["statistics"])


@router.get("/monthly", response_model=MonthlyStats)
async def get_monthly_stats(
    book_id: uuid.UUID = Query(...),
    period: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    # T-M5-04c: frozen users do not contribute to stats.
    if _is_frozen(current_user):
        return _empty_monthly_stats(book_id, period)
    service = StatisticsService(db)
    return await service.get_monthly_stats(book_id, current_user.id, period)


@router.get("/trends", response_model=TrendStats)
async def get_trend_stats(
    book_id: uuid.UUID = Query(...),
    period_from: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    period_to: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    # T-M5-04c: frozen users do not contribute to stats.
    if _is_frozen(current_user):
        return TrendStats(book_id=book_id, points=[])
    service = StatisticsService(db)
    return await service.get_trend_stats(
        book_id, current_user.id, period_from, period_to,
    )


def _empty_monthly_stats(book_id: uuid.UUID, period: str) -> MonthlyStats:
    """Empty response for frozen users — zero totals, no category breakdown."""
    return MonthlyStats(
        book_id=book_id,
        period=period,
        total_income=0,
        total_expense=0,
        balance=0,
        transaction_count=0,
        expense_by_category=[],
        income_by_category=[],
    )
