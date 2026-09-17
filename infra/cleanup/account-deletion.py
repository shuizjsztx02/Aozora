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

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from src.common.database import get_session_factory
from src.models.ai import AIConversation, AIUsage
from src.models.attachment import Attachment
from src.models.budget import Budget
from src.models.consent import UserConsent
from src.models.deletion import DeactivationToken, DeletionAudit
from src.models.export import Export
from src.models.ledger import Account, Book, Category, Transaction
from src.models.mutation import MutationIdempotency
from src.models.persona import ChatPersona
from src.models.proposal import ProposalIdempotency
from src.models.savings import SavingsGoal
from src.models.scheduled import ScheduledRule
from src.models.sync import SyncChange
from src.models.user import DeviceSession, User, UserStatus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("account-deletion")

# Grace window length (matches the 7 days promised to users).
GRACE_DAYS = 7

# Tables that hold per-user data to purge.  Order matters because of
# foreign-key constraints — children before parents, and user_consents
# (keyed by device_id, resolved via device_sessions) must run before
# device_sessions.
#
# T-M5-04c review P2-3: tables reachable through ON DELETE CASCADE from
# ``users.id`` (ai_conversations, ai_usage, sync_changes,
# proposal_idempotency, mutation_idempotency) plus the no-FK
# ``deletion_tokens`` (which would otherwise outlive the account with
# plaintext tokens) are deleted explicitly here so the audit record
# reflects every row actually removed and so the purge works on SQLite
# where FK enforcement is off in the test environment.
_PER_USER_TABLES: list[tuple[str, object]] = [
    ("attachments", Attachment),
    ("transactions", Transaction),
    ("budgets", Budget),
    # T-M7-01: 三实体上云后归入账号删除范围。scheduled_rules 引用
    # categories(RESTRICT) / books(RESTRICT)，必须排在两者之前。
    ("scheduled_rules", ScheduledRule),
    ("savings_goals", SavingsGoal),
    ("chat_personas", ChatPersona),
    ("accounts", Account),
    ("categories", Category),
    ("ai_conversations", AIConversation),
    ("proposal_idempotency", ProposalIdempotency),
    ("exports", Export),
    ("books", Book),
    ("ai_usage", AIUsage),
    ("sync_changes", SyncChange),
    ("mutation_idempotency", MutationIdempotency),
    # user_consents is keyed by device_id — must precede device_sessions.
    ("user_consents", UserConsent),
    ("device_sessions", DeviceSession),
    # deletion_tokens has no FK to users — purge explicitly.
    ("deletion_tokens", DeactivationToken),
]


def _hash_user_id(user_id: uuid.UUID) -> str:
    return hashlib.sha256(str(user_id).encode()).hexdigest()


def _ownership_filter(model, user_id: uuid.UUID):
    """Return a SQLAlchemy filter expression matching rows owned by *user_id*.

    Most per-user models expose ``user_id`` or ``owner_id`` directly.
    ``Transaction`` and ``Budget`` have neither — ownership flows through
    ``book_id`` → ``Book.owner_id``, so we use a sub-select in those cases.
    ``UserConsent`` is keyed by ``device_id``, so we join through
    ``DeviceSession`` to find the devices owned by *user_id*.
    """
    if hasattr(model, "user_id"):
        return model.user_id == user_id
    if hasattr(model, "owner_id"):
        return model.owner_id == user_id
    if model in (Transaction, Budget):
        book_subq = select(Book.id).where(Book.owner_id == user_id)
        return model.book_id.in_(book_subq)
    if model is UserConsent:
        device_subq = select(DeviceSession.device_id).where(
            DeviceSession.user_id == user_id,
        )
        return UserConsent.device_id.in_(device_subq)
    raise RuntimeError(f"Cannot determine ownership for {model.__name__}")


async def _purge_user(session: AsyncSession, user_id: uuid.UUID) -> tuple[int, list[str]]:
    """Delete every row belonging to *user_id* within the caller's tx.

    Returns ``(rows_purged, table_names_list)``.  Does NOT commit — the
    caller wraps the whole user in a try/except and commits on success
    or rolls back on failure so the audit record and deletes stay atomic.
    """
    rows_total = 0
    deleted_tables: list[str] = []

    for table_name, model in _PER_USER_TABLES:
        ownership = _ownership_filter(model, user_id)
        count_result = await session.execute(
            select(func.count()).select_from(model).where(ownership),
        )
        count = count_result.scalar() or 0
        if count == 0:
            continue
        await session.execute(delete(model).where(ownership))
        rows_total += count
        deleted_tables.append(table_name)
        logger.info("  %-24s %d row(s)", table_name, count)

    # Delete the user row itself via a core DELETE so we don't depend on
    # the ORM identity map (which would get confused after a rollback).
    # The deletion_audit row below proves the user was deleted.
    users_result = await session.execute(
        delete(User).where(User.id == user_id),
    )
    logger.info("  users row deleted: %d", users_result.rowcount or 0)

    return rows_total, deleted_tables


async def delete_frozen_users(session: AsyncSession) -> int:
    """Scan and physically delete users whose grace window has elapsed."""
    cutoff = datetime.now(UTC) - timedelta(days=GRACE_DAYS)
    result = await session.execute(
        select(User).where(
            User.status == UserStatus.FROZEN,
            User.frozen_at.isnot(None),
            User.frozen_at <= cutoff,
        ),
    )
    # Snapshot the primitives we need from each user before any deletes.
    # ORM instances get expired on rollback, which would trigger
    # ``MissingGreenlet`` errors in async attribute access if we touched
    # them after a per-user failure.
    candidates: list[tuple[uuid.UUID, datetime]] = [
        (u.id, u.deletion_requested_at or u.frozen_at)
        for u in result.scalars().all()
    ]

    deleted_users = 0
    failed_users = 0
    for user_id, requested_at in candidates:
        user_hash = _hash_user_id(user_id)

        # Idempotency: skip if an audit row already exists.
        existing = await session.execute(
            select(DeletionAudit).where(
                DeletionAudit.user_id_hash == user_hash,
            ),
        )
        if existing.scalar_one_or_none() is not None:
            logger.info(
                "Skipping user %s — audit row already present", user_hash[:16],
            )
            continue

        try:
            rows_total, deleted_tables = await _purge_user(session, user_id)
            session.add(DeletionAudit(
                id=uuid.uuid4(),
                user_id_hash=user_hash,
                requested_at=requested_at,
                completed_at=datetime.now(UTC),
                deleted_tables=",".join(sorted(deleted_tables)),
                rows_count=rows_total,
            ))
            await session.commit()
        except Exception:
            # T-M5-04c review P1 intent: per-user atomicity + failure
            # isolation. Rolling back this tx leaves no partial deletes
            # for THIS user (the audit row + deletes commit as one
            # transaction). The failed user will be retried on the next
            # sweep; other candidates continue unaffected.
            await session.rollback()
            failed_users += 1
            logger.exception(
                "Failed to delete user %s — rolled back, will retry next sweep",
                user_hash[:16],
            )
            continue

        logger.info(
            "Physically deleted user %s (%d row(s) across %s)",
            user_hash[:16], rows_total, ",".join(sorted(deleted_tables)) or "none",
        )
        deleted_users += 1

    if failed_users:
        logger.warning(
            "Account-deletion sweep: %d user(s) failed to delete, %d succeeded",
            failed_users, deleted_users,
        )
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
