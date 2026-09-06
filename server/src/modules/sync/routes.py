"""T-M3-01: Sync API routes — push / pull / bootstrap."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database import get_db_session
from src.modules.auth.dependencies import get_current_user
from src.models.user import User

from .schemas import BootstrapResponse, PullRequest, PullResponse, PushRequest, PushResponse
from .service import SyncService

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/push", response_model=PushResponse)
async def sync_push(
    body: PushRequest,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    """Submit a batch of client mutations to be applied by the server."""
    service = SyncService(db)
    return await service.push(current_user.id, body.mutations)


@router.post("/pull", response_model=PullResponse)
async def sync_pull(
    body: PullRequest,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    """Pull changes since the given cursor."""
    service = SyncService(db)
    return await service.pull(current_user.id, body.cursor, body.limit)


@router.post("/bootstrap", response_model=BootstrapResponse)
async def sync_bootstrap(
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    """Return a full data snapshot for device rebuild."""
    service = SyncService(db)
    return await service.bootstrap(current_user.id)
