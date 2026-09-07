"""T-M5-04c: Account deletion API routes.

Two-phase deletion flow:

- ``POST /me/deletion-request`` — issue a short-lived confirm_token
- ``DELETE /me`` — consume the token and freeze the account
- ``POST /me/deletion-cancel`` — un-freeze within the grace window
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database import get_db_session
from src.models.user import User
from src.modules.auth.dependencies import get_current_user

from .schemas import DeletionResponse, DeletionTokenResponse
from .service import DeletionService

router = APIRouter(tags=["me"])


@router.post(
    "/me/deletion-request",
    response_model=DeletionTokenResponse,
)
async def request_deletion_token(
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    """Issue a one-time confirm_token for ``DELETE /me``.

    The token is valid for 5 minutes and bound to the authenticated
    user. Any previously-issued unused token for the same user is
    invalidated.
    """
    service = DeletionService(db)
    token = await service.request_deletion_token(current_user)
    return DeletionTokenResponse(
        confirm_token=token,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )


@router.delete("/me", response_model=DeletionResponse)
async def delete_me(
    confirm_token: Annotated[str, Query(..., min_length=1, max_length=128)],
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    """Consume *confirm_token* and move the account to ``frozen``.

    Physical deletion happens 7 days later via
    ``infra/cleanup/account-deletion.py``. During the grace window the
    account is frozen: ``/sync/push`` returns 403, ``/sync/pull``
    returns empty, and the user's data is excluded from ``/stats/*``.
    """
    service = DeletionService(db)
    await service.confirm_deletion(current_user, confirm_token)
    return DeletionResponse(
        status="frozen",
        frozen_at=current_user.frozen_at,
        deletion_requested_at=current_user.deletion_requested_at,
        message=(
            "Your account has been frozen. All data will be permanently "
            "deleted in 7 days. If you change your mind, log in again "
            "and use POST /me/deletion-cancel before the deadline."
        ),
    )


@router.post(
    "/me/deletion-cancel",
    status_code=204,
)
async def cancel_deletion(
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    """Cancel a pending deletion (only works while the account is frozen)."""
    service = DeletionService(db)
    await service.cancel_deletion(current_user)
