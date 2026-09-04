"""T-M2-01: Health and readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Response
from pydantic import BaseModel

from src.common.database import check_health_db

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str


class ReadinessDetail(BaseModel):
    database: str


class ReadinessResponse(BaseModel):
    status: str
    checks: ReadinessDetail


@router.get("/health", response_model=HealthResponse)
async def liveness() -> HealthResponse:
    """Liveness probe — always returns 200 when the process is up."""
    return HealthResponse(status="ok")


@router.get("/health/ready", response_model=ReadinessResponse)
async def readiness(response: Response) -> ReadinessResponse:
    """Readiness probe — checks database connectivity.

    Returns 200 when all dependencies are healthy, 503 otherwise.
    """
    db_ok = await check_health_db()
    if not db_ok:
        response.status_code = 503
        return ReadinessResponse(
            status="unhealthy",
            checks=ReadinessDetail(database="unreachable"),
        )
    return ReadinessResponse(
        status="ready",
        checks=ReadinessDetail(database="ok"),
    )
