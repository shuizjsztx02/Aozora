"""T-M5-05b + T-M5-05c: Telemetry routes — crash reporting, heartbeat, feedback.

Endpoints:
- ``POST /telemetry/crash`` — receive a redacted crash summary.
  Requires authentication (the user is rate-limited, not stored).
- ``POST /telemetry/heartbeat`` — periodic liveness from client; used
  to populate ``active_devices_total`` in Grafana.
- ``POST /telemetry/feedback`` — receive in-app feedback from beta users.
  Requires authentication; the user is hashed and rate-limited.

All endpoints are write-only: there is no read API — data is consumed
via Grafana / on-call tooling or direct ops-table query during the
seed beta program.
"""

from __future__ import annotations

import hashlib
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database import get_db_session
from src.models.user import User
from src.modules.auth.dependencies import check_auth_rate_limit, get_current_user
from src.modules.auth.frozen import is_frozen as _is_frozen

from .schemas import (
    CrashReportRequest,
    CrashReportResponse,
    FeedbackRequest,
    FeedbackResponse,
    HeartbeatRequest,
    HeartbeatResponse,
)
from .service import (
    TelemetryService,
    _check_feedback_rate_limit,
    _check_rate_limit,
)

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.post("/crash", response_model=CrashReportResponse, status_code=201)
async def submit_crash_report(
    body: CrashReportRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
) -> CrashReportResponse:
    """Accept a redacted crash report.

    Per-device rate limit (20/hour) is enforced at the service layer;
    per-IP rate limit is enforced by the auth dependency above.

    T-M5-05ab: frozen users (pending deletion) cannot submit — they have
    no sync access, and telemetry is secondary to the deletion flow.
    """
    if _is_frozen(current_user):
        raise HTTPException(status_code=403, detail="Account is pending deletion")

    check_auth_rate_limit(request)

    if not _check_rate_limit(body.device_id_hash):
        raise HTTPException(
            status_code=429,
            detail="Crash report rate limit exceeded for this device",
        )

    service = TelemetryService(db)
    report = await service.record_crash(body)
    return CrashReportResponse(id=str(report.id))


@router.post("/heartbeat", response_model=HeartbeatResponse)
async def heartbeat(
    body: HeartbeatRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
) -> HeartbeatResponse:
    """Client liveness ping — increments active_devices_total.

    T-M5-05ab: frozen users are excluded so their devices don't inflate
    the active_devices_total metric after deletion is requested.
    """
    if _is_frozen(current_user):
        raise HTTPException(status_code=403, detail="Account is pending deletion")

    check_auth_rate_limit(request)

    # Extract device_id_hash from X-Device-Id header if present.
    # T-M5-05ab fix (S2-2): full 64-char SHA-256 hex for collision resistance
    # (birthday-paradox safe up to ~2^128 devices; previously 16 chars = 64 bit).
    device_id_raw = request.headers.get("X-Device-Id")
    device_id_hash = None
    if device_id_raw:
        device_id_hash = hashlib.sha256(device_id_raw.encode()).hexdigest()

    service = TelemetryService(db=None)
    await service.heartbeat(body.app_version, device_id_hash)
    return HeartbeatResponse()


@router.post("/feedback", response_model=FeedbackResponse, status_code=201)
async def submit_feedback(
    body: FeedbackRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
) -> FeedbackResponse:
    """Accept in-app feedback from a beta user.

    The user id is hashed (SHA-256[:16]) so the feedback table cannot be
    reverse-joined to ``users``. Per-user rate limit (5/hour) is enforced
    at the service layer; per-IP rate limit at the auth dependency above.

    T-M5-05ab: frozen users are blocked — they should use the deletion
    confirmation flow, not feedback.
    """
    if _is_frozen(current_user):
        raise HTTPException(status_code=403, detail="Account is pending deletion")

    check_auth_rate_limit(request)

    user_id_hash = hashlib.sha256(str(current_user.id).encode()).hexdigest()[:16]
    if not _check_feedback_rate_limit(user_id_hash):
        raise HTTPException(
            status_code=429,
            detail="Feedback rate limit exceeded for this user",
        )

    service = TelemetryService(db)
    try:
        feedback = await service.submit_feedback(body, user_id_hash)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return FeedbackResponse(id=str(feedback.id))
