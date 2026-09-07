"""T-M5-04c: Account deletion — service layer.

Implements the two-phase deletion flow:

1. ``POST /me/deletion-request`` — issue a one-time ``confirm_token``
   with a short TTL (5 minutes). Stored in ``deletion_tokens``.
2. ``DELETE /me?confirm_token=...`` — validate the token, mark the user
   as ``frozen`` (not immediately deleted). Physical deletion happens
   7 days later via ``infra/cleanup/account-deletion.py``.

Why a token? The mobile client renders a confirmation dialog asking the
user to type ``DELETE``; the token is acquired by a separate network
round-trip, which prevents a single accidental call (or a CSRF-style
attack on a logged-in page) from irrevocably starting the deletion.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.errors import AppException
from src.models.deletion import DeactivationToken
from src.models.user import User, UserStatus

logger = logging.getLogger(__name__)

# Token validity window. 5 minutes is short enough to prevent reuse in
# any realistic attack scenario, long enough to survive a single
# confirmation dialog.
TOKEN_TTL_MINUTES = 5


class DeletionService:
    """Orchestrates account-deletion token issuance and consumption."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------
    # Token issuance
    # ------------------------------------------------------------------

    async def request_deletion_token(self, user: User) -> str:
        """Issue a fresh confirm_token bound to *user*.

        Previous unused tokens for the same user are invalidated (so the
        user can only confirm with the most-recently-issued token).
        """
        if user.status == UserStatus.FROZEN:
            raise AppException(
                409, "deletion_pending",
                "Account deletion already in progress",
            )
        if user.status in (UserStatus.DEACTIVATED, UserStatus.DELETED):
            raise AppException(403, "account_disabled", "Account is disabled")

        # Invalidate any prior unused tokens for this user.
        await self.db.execute(
            delete(DeactivationToken).where(
                DeactivationToken.user_id == user.id,
                DeactivationToken.used_at.is_(None),
            )
        )

        now = datetime.now(UTC)
        token_str = secrets.token_urlsafe(32)
        token = DeactivationToken(
            id=uuid.uuid4(),
            user_id=user.id,
            token=token_str,
            created_at=now,
            expires_at=now + timedelta(minutes=TOKEN_TTL_MINUTES),
        )
        self.db.add(token)
        await self.db.flush()
        return token_str

    # ------------------------------------------------------------------
    # Token consumption
    # ------------------------------------------------------------------

    async def confirm_deletion(self, user: User, confirm_token: str) -> None:
        """Consume *confirm_token* and move *user* to the frozen state.

        Raises 400 ``invalid_confirm_token`` if the token is unknown,
        expired, already used, or belongs to a different user.
        """
        if user.status == UserStatus.FROZEN:
            raise AppException(
                409, "deletion_pending",
                "Account deletion already in progress",
            )

        result = await self.db.execute(
            select(DeactivationToken).where(
                DeactivationToken.token == confirm_token,
            ),
        )
        token = result.scalar_one_or_none()

        if token is None:
            raise AppException(400, "invalid_confirm_token", "Invalid confirmation token")
        if token.user_id != user.id:
            raise AppException(400, "invalid_confirm_token", "Invalid confirmation token")
        if token.used_at is not None:
            raise AppException(400, "confirm_token_used", "Confirmation token already used")

        now = datetime.now(UTC)
        if token.expires_at < now:
            raise AppException(400, "confirm_token_expired", "Confirmation token has expired")

        # Mark token consumed.
        token.used_at = now

        # Freeze the user.
        user.status = UserStatus.FROZEN
        user.frozen_at = now
        if user.deletion_requested_at is None:
            user.deletion_requested_at = now

        await self.db.flush()
        logger.info(
            "User %s entered frozen state (deletion grace window started)",
            _hash_user_id(user.id),
        )

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def cancel_deletion(self, user: User) -> None:
        """Un-freeze a user who changed their mind within the 7-day window.

        Not strictly required by the spec but keeps the deletion flow
        recoverable during the grace period — without it, a user who
        tapped ``DELETE`` on the wrong device would lose their data
        irreversibly after 7 days.
        """
        if user.status != UserStatus.FROZEN:
            raise AppException(409, "not_frozen", "Account is not pending deletion")

        # Invalidate any unused tokens.
        await self.db.execute(
            delete(DeactivationToken).where(
                DeactivationToken.user_id == user.id,
                DeactivationToken.used_at.is_(None),
            )
        )

        user.status = UserStatus.ACTIVE if user.phone else UserStatus.GUEST
        user.frozen_at = None
        user.deletion_requested_at = None
        await self.db.flush()
        logger.info("User %s cancelled deletion", _hash_user_id(user.id))


def _hash_user_id(user_id: uuid.UUID) -> str:
    """Stable one-way digest for audit logging (no raw UUID in logs)."""
    return hashlib.sha256(str(user_id).encode()).hexdigest()[:16]
