#!/usr/bin/env python3
"""infra/cleanup/account-deletion.py — T-M5-04c 物理删除已冻结账号。

定期（建议每日 04:30 UTC）扫描 users.frozen_at <= now - 7 天 的用户，
执行物理删除并在 deletion_audit 表写入完成证明。

运行方式（docker-compose 环境）：
    docker compose exec api \
      python -m infra.cleanup.account-deletion

或作为独立 cron：
    DATABASE_URL=postgresql+asyncpg://... python infra/cleanup/account-deletion.py

幂等性：同一 user_id_hash 在 deletion_audit 中已有记录时跳过。
退出码：0 成功（含 0 条删除），1 运行时错误。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Repo root is two levels up from this script.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "server") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "server"))

from sqlalchemy import delete, func, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from src.common.database import get_session_factory  # noqa: E402
from src.models.attachment import Attachment  # noqa: E402
from src.models.budget import Budget  # noqa: E402
from src.models.consent import UserConsent  # noqa: E402
from src.models.deletion import DeletionAudit  # noqa: E402
from src.models.export import Export  # noqa: E402
from src.models.ledger import Account, Book, Category, Transaction  # noqa: E402
from src.models.user import User, UserStatus  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("account-deletion")

# Grace window length (matches the 7 days promised to users).
GRACE_DAYS = 7

# Tables that hold per-user data to purge.  Order matters because of
# foreign-key constraints — children first, parents last.  The users
# row itself is deleted LAST.
_PER_USER_TABLES: list[tuple[str, object]] = [
    ("attachments", Attachment),
    ("transactions", Transaction),
    ("budgets", Budget),
    ("accounts", Account),
    ("categories", Category),
    ("books", Book),
    ("exports", Export),
    ("user_consents", UserConsent),
]


def _hash_user_id(user_id: uuid.UUID) -> str:
    return hashlib.sha256(str(user_id).encode()).hexdigest()


async def delete_frozen_users(session: AsyncSession) -> int:
    """Scan and physically delete users whose grace window has elapsed."""
    cutoff = datetime.now(UTC) - timedelta(days=GRACE_DAYS)
    result = await session.execute(
        select(User).where(
            User.status == UserStatus.FROZEN,
            User.frozen_at.isnot(None),
            User.frozen_at <= cutoff,
        )
    )
    candidates = list(result.scalars().all())

    deleted_users = 0
    for user in candidates:
        user_hash = _hash_user_id(user.id)
        # Idempotency: skip if an audit row already exists.
        existing = await session.execute(
            select(DeletionAudit).where(
                DeletionAudit.user_id_hash == user_hash,
            )
        )
        if existing.scalar_one_or_none() is not None:
            logger.info(
                "Skipping user %s — audit row already present", user_hash[:16],
            )
            continue

        rows_total = 0
        deleted_tables: list[str] = []

        for table_name, model in _PER_USER_TABLES:
            # Count rows to be removed (for the audit record).
            count_result = await session.execute(
                select(func.count()).select_from(model).where(
                    # All per-user models use ``user_id`` or ``owner_id``;
                    # we filter with an OR to cover both naming conventions.
                    getattr(model, "user_id", None) == user.id
                    if hasattr(model, "user_id")
                    else getattr(model, "owner_id") == user.id
                )
            )
            count = count_result.scalar() or 0
            if count > 0:
                # Use model-aware delete to respect ORM relationships.
                await session.execute(
                    delete(model).where(
                        getattr(model, "user_id", None) == user.id
                        if hasattr(model, "user_id")
                        else getattr(model, "owner_id") == user.id
                    )
                )
                rows_total += count
                deleted_tables.append(table_name)

        # Device sessions are CASCADE-deleted with users, but count them
        # in the audit for completeness.
        from src.models.user import DeviceSession
        ds_count_result = await session.execute(
            select(func.count()).select_from(DeviceSession).where(
                DeviceSession.user_id == user.id,
            )
        )
        ds_count = ds_count_result.scalar() or 0
        if ds_count > 0:
            await session.execute(
                delete(DeviceSession).where(DeviceSession.user_id == user.id)
            )
            rows_total += ds_count
            deleted_tables.append("device_sessions")

        # Delete the user row itself.
        await session.delete(user)

        # Write the audit record.
        audit = DeletionAudit(
            id=uuid.uuid4(),
            user_id_hash=user_hash,
            requested_at=user.deletion_requested_at or user.frozen_at,
            completed_at=datetime.now(UTC),
            deleted_tables=",".join(sorted(deleted_tables)),
            rows_count=rows_total,
        )
        session.add(audit)
        await session.commit()

        logger.info(
            "Physically deleted user %s (%d rows across %s)",
            user_hash[:16], rows_total, ",".join(sorted(deleted_tables)),
        )
        deleted_users += 1

    return deleted_users


async def main() -> int:
    logger.info("Account-deletion sweep starting (grace=%d days)", GRACE_DAYS)
    factory = get_session_factory()
    async with factory() as session:
        deleted = await delete_frozen_users(session)
    logger.info("Account-deletion sweep complete — %d users deleted", deleted)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
