"""T-M5-04a: Consent API routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database import get_db_session
from src.modules.auth.dependencies import check_auth_rate_limit

from .schemas import (
    ConsentRecordRequest,
    ConsentRecordResponse,
    ConsentStatusResponse,
)
from .service import ConsentService

router = APIRouter(prefix="/consent", tags=["consent"])


@router.post("", response_model=ConsentRecordResponse, status_code=201)
async def record_consent(
    body: ConsentRecordRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    """Record privacy policy consent for a device.

    No authentication required — consent is recorded before the user
    registers (guest or phone login).  Rate-limited per IP to prevent
    unauthenticated write amplification (T-M5-04a S0-2).
    """
    check_auth_rate_limit(request)
    service = ConsentService(db)
    consent = await service.record_consent(
        device_id=body.device_id,
        policy_version=body.policy_version,
        consented_at=body.consented_at,
    )
    return ConsentRecordResponse(
        id=str(consent.id),
        device_id=consent.device_id,
        policy_version=consent.policy_version,
        consented_at=consent.consented_at,
    )


@router.get("/status", response_model=ConsentStatusResponse)
async def consent_status(
    device_id: Annotated[str, Query(min_length=1)],
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    """Check whether a device has a recorded consent."""
    service = ConsentService(db)
    consent = await service.get_latest_consent(device_id)
    if consent is None:
        return ConsentStatusResponse(has_consented=False)
    return ConsentStatusResponse(
        has_consented=True,
        policy_version=consent.policy_version,
        consented_at=consent.consented_at,
    )
