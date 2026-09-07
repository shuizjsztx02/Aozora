"""T-M5-04a: Consent API schemas."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field, field_validator


class ConsentRecordRequest(BaseModel):
    """Request body for POST /consent."""

    device_id: str = Field(
        min_length=1,
        max_length=128,
        # T-M5-04a S1-2: 限定非空白字符集，杜绝纯空格 device_id。
        pattern=r"^[a-zA-Z0-9_\-.:+]+$",
    )
    policy_version: str = Field(min_length=1, max_length=32, default="v1")
    consented_at: datetime

    @field_validator("device_id", mode="before")
    @classmethod
    def _strip_device_id(cls, v: object) -> object:
        # T-M5-04a S1-2: 去除首尾空白，再交给 pattern 校验。
        if isinstance(v, str):
            v = v.strip()
        return v

    @field_validator("consented_at", mode="after")
    @classmethod
    def _clamp_consented_at(cls, v: datetime) -> datetime:
        # T-M5-04a S1-1: 客户端时钟可能漂移或恶意伪造，
        # 钳制到 [now-24h, now+5min] 保证审计数据可用。
        now = datetime.now(timezone.utc)
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        lower = now - timedelta(hours=24)
        upper = now + timedelta(minutes=5)
        if v < lower:
            return lower
        if v > upper:
            return now
        return v


class ConsentRecordResponse(BaseModel):
    """Response for POST /consent."""

    id: str
    device_id: str
    policy_version: str
    consented_at: datetime


class ConsentStatusResponse(BaseModel):
    """Response for GET /consent/status?device_id=..."""

    has_consented: bool
    policy_version: str | None = None
    consented_at: datetime | None = None
