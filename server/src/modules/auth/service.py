"""T-M2-03: Auth business logic — guest creation, phone login, token refresh, logout."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.config import get_config
from src.common.errors import AppException
from src.models.user import DeviceSession, User, UserStatus

from .utils import (
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
)

logger = logging.getLogger(__name__)


class AuthService:
    """Handles guest creation, phone login, token refresh, and logout."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------
    # Guest
    # ------------------------------------------------------------------

    async def create_guest(
        self,
        device_id: str,
        nickname: str | None = None,
        timezone: str = "Asia/Shanghai",
    ) -> tuple[User, str, str]:
        """Create a guest user and return (user, access_token, refresh_token)."""
        user = User(nickname=nickname, timezone=timezone, status=UserStatus.GUEST)
        self.db.add(user)
        await self.db.flush()

        access_token, refresh_token = await self._create_session(user.id, device_id)
        return user, access_token, refresh_token

    # ------------------------------------------------------------------
    # Phone login
    # ------------------------------------------------------------------

    async def phone_login(
        self, phone: str, verification_code: str, device_id: str,
    ) -> tuple[User, str, str, bool]:
        """Login or register via phone + verification code.

        Returns (user, access_token, refresh_token, is_new_user).
        In dev mode, accepts ``config.dev_auth_code`` as the code.
        SMS provider integration is deferred to a later task.
        """
        cfg = get_config()
        if verification_code != cfg.dev_auth_code:
            raise AppException(401, "invalid_code", "Invalid verification code")

        result = await self.db.execute(select(User).where(User.phone == phone))
        user = result.scalar_one_or_none()
        is_new = False

        if user is None:
            user = User(phone=phone, status=UserStatus.ACTIVE, timezone="Asia/Shanghai")
            self.db.add(user)
            await self.db.flush()
            is_new = True
        elif user.status in (UserStatus.DEACTIVATED, UserStatus.DELETED):
            raise AppException(403, "account_disabled", "Account is deactivated or deleted")

        access_token, refresh_token = await self._create_session(user.id, device_id)
        return user, access_token, refresh_token, is_new

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    async def refresh_tokens(self, raw_refresh_token: str, device_id: str) -> tuple[str, str]:
        """Rotate refresh token and return (new_access, new_refresh).

        The old refresh token is invalidated (rotation). Re-use of an
        old token is rejected with 401.
        """
        token_hash = hash_refresh_token(raw_refresh_token)
        result = await self.db.execute(
            select(DeviceSession).where(
                DeviceSession.refresh_token_hash == token_hash,
                DeviceSession.device_id == device_id,
            )
        )
        session = result.scalar_one_or_none()

        if session is None:
            raise AppException(401, "invalid_token", "Invalid or expired refresh token")

        # SQLite may return naive datetimes; normalize before comparison
        expires_at = session.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(timezone.utc):
            # R-M2-15: the route-level rollback would undo this delete,
            # so we just reject the token and let future lookups hit the
            # same branch — cheap (indexed) and harmless.
            raise AppException(401, "token_expired", "Refresh token has expired")

        # Check user is still active
        user_result = await self.db.execute(select(User).where(User.id == session.user_id))
        user = user_result.scalar_one_or_none()
        if user is None or user.status in (UserStatus.DEACTIVATED, UserStatus.DELETED):
            raise AppException(403, "account_disabled", "Account is deactivated or deleted")

        # Delete old session (rotation)
        user_id = user.id
        await self.db.delete(session)
        await self.db.flush()

        # Create new session
        access_token, new_refresh_token = await self._create_session(user_id, device_id)
        return access_token, new_refresh_token

    # ------------------------------------------------------------------
    # Logout
    # ------------------------------------------------------------------

    async def logout(self, raw_refresh_token: str, device_id: str) -> None:
        """Revoke a single device session."""
        token_hash = hash_refresh_token(raw_refresh_token)
        result = await self.db.execute(
            select(DeviceSession).where(
                DeviceSession.refresh_token_hash == token_hash,
                DeviceSession.device_id == device_id,
            )
        )
        session = result.scalar_one_or_none()
        if session:
            await self.db.delete(session)
            await self.db.flush()

    async def revoke_all_sessions(self, user_id: uuid.UUID) -> int:
        """Revoke all sessions for a user. Returns count of revoked sessions.

        R-M2-11: uses a single DELETE … WHERE to avoid N+1.
        """
        # Count first so we can return the revoked count.
        result = await self.db.execute(
            select(DeviceSession).where(DeviceSession.user_id == user_id)
        )
        count = len(result.scalars().all())
        if count:
            await self.db.execute(
                delete(DeviceSession).where(DeviceSession.user_id == user_id)
            )
            await self.db.flush()
        return count

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _create_session(self, user_id: uuid.UUID, device_id: str) -> tuple[str, str]:
        """Create a device session and return (access_token, refresh_token).

        R-M2-13: concurrent refreshes for the same (user, device) could
        collide on the unique constraint. We retry once after an
        ``IntegrityError`` so both callers succeed sequentially.
        """
        cfg = get_config()
        raw_refresh = generate_refresh_token()
        token_hash = hash_refresh_token(raw_refresh)
        expires_at = datetime.now(timezone.utc) + timedelta(days=cfg.refresh_token_expire_days)

        # Upsert: delete any existing session for same (user, device)
        await self.db.execute(
            delete(DeviceSession).where(
                DeviceSession.user_id == user_id,
                DeviceSession.device_id == device_id,
            )
        )

        session = DeviceSession(
            user_id=user_id,
            device_id=device_id,
            refresh_token_hash=token_hash,
            expires_at=expires_at,
        )
        self.db.add(session)
        try:
            await self.db.flush()
        except IntegrityError:
            # Concurrent insert hit the (user_id, device_id) unique constraint.
            # Roll back the failed insert inside this transaction, delete again,
            # and retry once.
            logger.info("Concurrent session insert for user=%s device=%s; retrying", user_id, device_id)
            await self.db.rollback()
            await self.db.execute(
                delete(DeviceSession).where(
                    DeviceSession.user_id == user_id,
                    DeviceSession.device_id == device_id,
                )
            )
            # Re-issue the session insert with a fresh token.
            raw_refresh = generate_refresh_token()
            token_hash = hash_refresh_token(raw_refresh)
            session = DeviceSession(
                user_id=user_id,
                device_id=device_id,
                refresh_token_hash=token_hash,
                expires_at=expires_at,
            )
            self.db.add(session)
            await self.db.flush()

        access_token = create_access_token(str(user_id))
        return access_token, raw_refresh
