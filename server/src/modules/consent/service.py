"""T-M5-04a: Consent service."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.consent import UserConsent
from src.models.user import DeviceSession


class ConsentService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def record_consent(
        self,
        device_id: str,
        policy_version: str,
        consented_at: datetime,
    ) -> UserConsent:
        """Record a new consent entry for a device."""
        consent = UserConsent(
            device_id=device_id,
            policy_version=policy_version,
            consented_at=consented_at,
        )
        self._db.add(consent)
        await self._db.flush()
        return consent

    async def get_latest_consent(self, device_id: str) -> UserConsent | None:
        """Return the most recent consent record for a device."""
        result = await self._db.execute(
            select(UserConsent)
            .where(UserConsent.device_id == device_id)
            .order_by(UserConsent.consented_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def has_user_consented(self, user_id: uuid.UUID) -> bool:
        """Check whether the user has any consented device.

        F-M5-04a-F1: server-side defense-in-depth. The consent record is
        stored per-device (before authentication), so we correlate through
        ``device_sessions`` to find whether any device bound to this user
        has a matching ``user_consents.device_id``. Returns True if at
        least one such pair exists.
        """
        consent_correlated = select(literal(1)).where(
            UserConsent.device_id == DeviceSession.device_id,
        ).correlate(DeviceSession).exists()
        stmt = select(literal(1)).where(
            DeviceSession.user_id == user_id,
            consent_correlated,
        ).exists()
        result = await self._db.execute(select(stmt))
        return bool(result.scalar())
