"""T-M3-01 + T-M5-04c: Sync API routes — push / pull / bootstrap.

T-M5-04c: frozen users (pending deletion) are blocked from push (403),
get an empty pull response, and are excluded from bootstrap.

F-M5-04a-F1: push also requires a recorded privacy-policy consent for
at least one of the user's devices (defense-in-depth; client-side gates
are the primary line).
"""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database import get_db_session
from src.common.errors import AppException
from src.common.telemetry import record_sync_push
from src.models.user import User
from src.modules.auth.dependencies import get_current_user
from src.modules.auth.frozen import is_frozen as _is_frozen
from src.modules.auth.frozen import require_active as _require_active
from src.modules.consent.service import ConsentService

from .schemas import (
    BootstrapResponse,
    PullRequest,
    PullResponse,
    PushRequest,
    PushResponse,
)
from .service import SyncService

router = APIRouter(prefix="/sync", tags=["sync"])


async def _require_consented(user: User, db: AsyncSession) -> None:
    """Reject push when the user has no consented device.

    F-M5-04a-F1: defense-in-depth. Clients gate push before consent
    at three layers (OnboardingPage / MainActivity / SyncWorker), but
    a tampered client could POST directly. This check enforces that at
    least one ``device_sessions.device_id`` linked to the user has a
    matching row in ``user_consents``.
    """
    service = ConsentService(db)
    if not await service.has_user_consented(user.id):
        raise AppException(
            403, "consent_required",
            "Privacy policy consent is required before syncing",
        )


@router.post("/push", response_model=PushResponse)
async def sync_push(
    body: PushRequest,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    """Submit a batch of client mutations to be applied by the server."""
    _require_active(current_user)
    await _require_consented(current_user, db)
    service = SyncService(db)
    start = time.perf_counter()
    try:
        result = await service.push(current_user.id, body.mutations)
        record_sync_push(
            status="ok",
            error_class="none",
            duration_ms=round((time.perf_counter() - start) * 1000, 2),
        )
        return result
    except Exception as exc:
        record_sync_push(
            status="error",
            error_class=type(exc).__name__,
            duration_ms=round((time.perf_counter() - start) * 1000, 2),
        )
        raise


@router.post("/pull", response_model=PullResponse)
async def sync_pull(
    body: PullRequest,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    """Pull changes since the given cursor."""
    # T-M5-04c: frozen users get an empty pull response (not 403).
    if _is_frozen(current_user):
        return PullResponse(changes=[], cursor=body.cursor, has_more=False)
    service = SyncService(db)
    return await service.pull(current_user.id, body.cursor, body.limit)


@router.post("/bootstrap", response_model=BootstrapResponse)
async def sync_bootstrap(
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    """Return a full data snapshot for device rebuild."""
    _require_active(current_user)
    service = SyncService(db)
    return await service.bootstrap(current_user.id)
