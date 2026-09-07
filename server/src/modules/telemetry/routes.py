"""T-M5-05b: Telemetry routes — crash reporting and heartbeat.

Endpoints:
- ``POST /telemetry/crash`` — receive a redacted crash summary.
  Requires authentication (the user is rate-limited, not stored).
- ``POST /telemetry/heartbeat`` — periodic liveness from client; used
  to populate ``active_devices_total`` in Grafana.

Both endpoints are write-only: there is no read API — data is consumed
via Grafana / on-call tooling.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database import get_db_session
from src.modules.auth.dependencies import check_auth_rate_limit, get_current_user
from src.models.user import User

from .schemas import (
    CrashReportRequest,
    CrashReportResponse,
    HeartbeatRequest,
    HeartbeatResponse,
)
from .service import TelemetryService, _check_rate_limit

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
    """
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
    """Client liveness ping — increments active_devices_total."""
    check_auth_rate_limit(request)

    # Extract device_id_hash from X-Device-Id header if present.
    device_id_raw = request.headers.get("X-Device-Id")
    device_id_hash = None
    if device_id_raw:
        import hashlib
        device_id_hash = hashlib.sha256(device_id_raw.encode()).hexdigest()[:16]

    service = TelemetryService(db=None)  # type: ignore[arg-type]  # heartbeat is DB-free
    await service.heartbeat(body.app_version, device_id_hash)
    return HeartbeatResponse()
