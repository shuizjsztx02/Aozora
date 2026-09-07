"""T-M5-04c: Account deletion audit trail and confirmation tokens.

Two tables:

- ``deletion_tokens``: short-lived (5-minute) one-time tokens issued by
  ``POST /me/deletion-request``. ``DELETE /me?confirm_token=<token>``
  consumes exactly one row; expiry is enforced at read time.

- ``deletion_audit``: permanent record written by
  ``infra/cleanup/account-deletion.py`` after physical deletion. The
  ``user_id_hash`` (SHA-256 of the original UUID) lets auditors prove
  that a particular user was deleted without retaining PII.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class DeactivationToken(Base):
    """One-time confirmation token for account deletion.

    Token lifetime is 5 minutes. ``used_at`` is set on consumption so
    tokens cannot be replayed (idempotency guard).
    """

    __tablename__ = "deletion_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True,
    )
    token: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )


class DeletionAudit(Base):
    """Permanent record of a completed account deletion.

    Written by ``infra/cleanup/account-deletion.py`` after physical
    deletion. ``user_id_hash`` is a one-way SHA-256 digest of the user
    UUID — enough to prove a deletion occurred, not enough to recover
    the user's identity.
    """

    __tablename__ = "deletion_audit"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True,
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    # Comma-separated list of tables that were purged, e.g.
    # "transactions,categories,accounts,books,budgets,attachments,user_consents"
    deleted_tables: Mapped[str] = mapped_column(Text, nullable=False)
    rows_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False,
    )
