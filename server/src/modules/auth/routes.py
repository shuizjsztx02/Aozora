"""T-M2-03: Auth API routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.config import get_config
from src.common.database import get_db_session
from src.models.user import User

from .dependencies import check_auth_rate_limit, get_current_user
from .schemas import (
    GuestRequest,
    GuestResponse,
    MeResponse,
    PhoneLoginRequest,
    PhoneLoginResponse,
    RefreshRequest,
    TokenResponse,
)
from .service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])

# R-M2-27: /me lives at the top level (not under /auth) — clients use it
# to fetch the current user's profile after login. Registered separately
# in main.py so the path is /me, not /auth/me.
me_router = APIRouter(tags=["auth"])


def _token_response(access_token: str, refresh_token: str) -> TokenResponse:
    cfg = get_config()
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=cfg.access_token_expire_minutes * 60,
    )


@router.post("/guest", response_model=GuestResponse)
async def create_guest(
    body: GuestRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_session)],
):
    """Create a new guest account (no phone required)."""
    check_auth_rate_limit(request)
    service = AuthService(db)
    user, access_token, refresh_token = await service.create_guest(
        device_id=body.device_id,
        nickname=body.nickname,
        timezone=body.timezone,
    )
    return GuestResponse(
        user_id=user.id,
        tokens=_token_response(access_token, refresh_token),
    )


@router.post("/login", response_model=PhoneLoginResponse)
async def phone_login(
    body: PhoneLoginRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_session)],
):
    """Login or register via phone + verification code."""
    check_auth_rate_limit(request)
    service = AuthService(db)
    user, access_token, refresh_token, is_new = await service.phone_login(
        phone=body.phone,
        verification_code=body.verification_code,
        device_id=body.device_id,
    )
    return PhoneLoginResponse(
        user_id=user.id,
        is_new_user=is_new,
        tokens=_token_response(access_token, refresh_token),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    body: RefreshRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_session)],
):
    """Rotate refresh token — old token is invalidated."""
    check_auth_rate_limit(request)
    service = AuthService(db)
    access_token, new_refresh = await service.refresh_tokens(
        raw_refresh_token=body.refresh_token,
        device_id=body.device_id,
    )
    return _token_response(access_token, new_refresh)


@router.post("/logout", status_code=204)
async def logout(
    body: RefreshRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_session)],
):
    """Revoke a device session by refresh token + device_id."""
    check_auth_rate_limit(request)
    service = AuthService(db)
    await service.logout(
        raw_refresh_token=body.refresh_token,
        device_id=body.device_id,
    )


@me_router.get("/me", response_model=MeResponse)
async def get_me(
    current_user: Annotated[User, Depends(get_current_user)] = ...,
):
    """R-M2-27: return the authenticated user's own profile."""
    return MeResponse.model_validate(current_user)
