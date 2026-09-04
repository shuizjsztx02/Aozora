"""T-M2-02: Budget model."""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import AuditMixin, Base, VersionedMixin


class BudgetType(str, enum.Enum):
    MONTHLY = "monthly"
    ANNUAL = "annual"


# R-M2-14: Budget is the only value-bearing entity that lacked AuditMixin;
# add created_at/updated_at for audit parity with the rest of the schema.
class Budget(Base, AuditMixin, VersionedMixin):
    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint(
            "book_id", "budget_type", "period", "category_id",
            name="uq_budgets_period_category",
        ),
        CheckConstraint("amount_cents > 0", name="ck_budgets_amount_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    book_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
    )
    budget_type: Mapped[BudgetType] = mapped_column(
        String(10), nullable=False,
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
    )
    period: Mapped[str] = mapped_column(String(10), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)

    book: Mapped["Book"] = relationship(back_populates="budgets")


# Type-only import to avoid circular dependency at module load.
from src.models.ledger import Book  # noqa: E402, F401
