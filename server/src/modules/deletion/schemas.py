"""T-M5-04c: Pydantic schemas for account-deletion API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class DeletionTokenResponse(BaseModel):
    """Response to POST /me/deletion-request.

    The ``confirm_token`` must be echoed back to ``DELETE /me`` within
    ``expires_at`` (5 minutes).
    """

    confirm_token: str
    expires_at: datetime


class DeletionResponse(BaseModel):
    """Response to DELETE /me after successful token consumption."""

    status: str  # "frozen"
    frozen_at: datetime
    deletion_requested_at: datetime
    # Human-readable message explaining the 7-day grace window.
    message: str


class DeletionStatusResponse(BaseModel):
    """Optional response shape for GET /me/deletion-status.

    Returned by GET /me as nested fields — this schema exists mainly
    for the dedicated endpoint if we want to expose it later.
    """

    frozen: bool
    frozen_at: datetime | None = None
    deletion_requested_at: datetime | None = None
    # Days remaining in the grace window (7 - elapsed). Negative if
    # physical deletion is overdue (should not happen — the cron
    # should have run).
    days_remaining: int | None = None
