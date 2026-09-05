"""T-M2-03: Pydantic schemas for auth API request/response."""

from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import available_timezones

from pydantic import BaseModel, Field, field_validator


# Cache once — available_timezones() is pure but non-trivial.
_VALID_TIMEZONES = available_timezones()

# R-M2-08: device_id is stored in ``device_sessions.device_id`` which is
# ``String(128)``; enforce the same cap at the API boundary so oversized
# input returns 422 rather than surfacing as a 500 from the DB layer.
DEVICE_ID_MAX_LENGTH = 128


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

class GuestRequest(BaseModel):
    nickname: str | None = Field(default=None, max_length=64)
    device_id: str = Field(max_length=DEVICE_ID_MAX_LENGTH)
    timezone: str = "Asia/Shanghai"

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, v: str) -> str:
        if v not in _VALID_TIMEZONES:
            raise ValueError(f"Unknown timezone: {v}")
        return v


class PhoneLoginRequest(BaseModel):
    phone: str = Field(pattern=r"^\d{11}$")
    verification_code: str = Field(pattern=r"^\d{4,8}$")
    device_id: str = Field(max_length=DEVICE_ID_MAX_LENGTH)


class RefreshRequest(BaseModel):
    refresh_token: str
    device_id: str = Field(max_length=DEVICE_ID_MAX_LENGTH)


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class GuestResponse(BaseModel):
    user_id: uuid.UUID
    tokens: TokenResponse


class PhoneLoginResponse(BaseModel):
    user_id: uuid.UUID
    is_new_user: bool
    tokens: TokenResponse


# R-M2-27: profile of the authenticated user, served by GET /me.
class MeResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    nickname: str | None
    phone: str | None
    status: str
    timezone: str
    created_at: datetime
    updated_at: datetime


# R-M2-21: DeviceSessionResponse previously defined here was unused; removed.
