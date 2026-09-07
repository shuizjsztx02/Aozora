"""T-M3-01 + T-M5-04c: Sync API routes — push / pull / bootstrap.

T-M5-04c: frozen users (pending deletion) are blocked from push (403),
get an empty pull response, and are excluded from bootstrap.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database import get_db_session
from src.common.errors import AppException
from src.models.user import User, UserStatus
from src.modules.auth.dependencies import get_current_user

from .schemas import (
    BootstrapResponse,
    PullRequest,
    PullResponse,
    PushRequest,
    PushResponse,
)
from .service import SyncService

router = APIRouter(prefix="/sync", tags=["sync"])


def _require_active(user: User) -> None:
    """Reject frozen users from data-plane sync operations.

    T-M5-04c: a user in the 7-day deletion grace window must not push
    new mutations or receive fresh data. Pull returns empty rather
    than 403 so the client can retry after cancellation.
    """
    if user.status == UserStatus.FROZEN:
        raise AppException(
            403, "account_frozen",
            "Account is pending deletion; sync is disabled",
        )


@router.post("/push", response_model=PushResponse)
async def sync_push(
    body: PushRequest,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    """Submit a batch of client mutations to be applied by the server."""
    _require_active(current_user)
    service = SyncService(db)
    return await service.push(current_user.id, body.mutations)


@router.post("/pull", response_model=PullResponse)
async def sync_pull(
    body: PullRequest,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    """Pull changes since the given cursor."""
    # T-M5-04c: frozen users get an empty pull response (not 403).
    if current_user.status == UserStatus.FROZEN:
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
