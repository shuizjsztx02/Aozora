"""T-M5-04a: Consent service."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.consent import UserConsent


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
