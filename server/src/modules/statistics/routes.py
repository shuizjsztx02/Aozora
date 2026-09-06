"""T-M2-05: Statistics API routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database import get_db_session
from src.models.user import User
from src.modules.auth.dependencies import get_current_user

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
    service = StatisticsService(db)
    return await service.get_trend_stats(book_id, current_user.id, period_from, period_to)
