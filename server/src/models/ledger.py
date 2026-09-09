"""T-M2-02: Ledger models — Book, Category, Account, Transaction."""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import AuditMixin, Base, SoftDeleteMixin, VersionedMixin


class TxType(str, enum.Enum):
    EXPENSE = "expense"
    INCOME = "income"


class CategoryKind(str, enum.Enum):
    EXPENSE = "expense"
    INCOME = "income"


class TxSource(str, enum.Enum):
    MANUAL = "manual"
    LOCAL_NLU = "local_nlu"
    AI_GATEWAY = "ai_gateway"
    # T10-8 收口：对齐客户端 Repository.kt 合法集（nlu_* 此前 push 恒 422 毒丸；
    # 'scheduled' 随第十一轮 T11-4 定时记账引入）
    NLU_LOCAL = "nlu_local"
    NLU_AI = "nlu_ai"
    IMPORT = "import"


class Book(Base, AuditMixin, VersionedMixin, SoftDeleteMixin):
    __tablename__ = "books"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    categories: Mapped[list[Category]] = relationship(back_populates="book")
    transactions: Mapped[list[Transaction]] = relationship(back_populates="book")
    budgets: Mapped[list[Budget]] = relationship(back_populates="book")


class Category(Base, AuditMixin, VersionedMixin):
    """Category with soft-delete via ``archived_at`` (not ``deleted_at``)."""

    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint(
            "book_id", "parent_id", "kind", "name", "owner_id",
            name="uq_categories_scope_name",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    book_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=True,
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
    )
    kind: Mapped[CategoryKind] = mapped_column(
        String(10), nullable=False,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # R-M2-25: prefer direct class reference over string ``remote_side``.
    # ``parent_id`` disambiguates the self-referential FK.
    book: Mapped[Book | None] = relationship(back_populates="categories")
    parent: Mapped[Category | None] = relationship(
        "Category", remote_side="Category.id", backref="children",
    )


class Account(Base, AuditMixin, VersionedMixin):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )


class Transaction(Base, AuditMixin, VersionedMixin, SoftDeleteMixin):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="ck_transactions_amount_positive"),
        Index("ix_transactions_book_biz_date", "book_id", "biz_date"),
        Index("ix_transactions_book_deleted", "book_id", "deleted_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    book_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("books.id", ondelete="RESTRICT"),
        nullable=False,
    )
    type: Mapped[TxType] = mapped_column(String(10), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="RESTRICT"),
        nullable=False,
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    # N-13: String(2000) matches the Pydantic schema max_length so the DB
    # enforces the cap even if a future caller bypasses the API boundary.
    note: Mapped[str] = mapped_column(String(2000), default="", server_default="", nullable=False)
    biz_date: Mapped[date] = mapped_column(Date, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    source: Mapped[TxSource] = mapped_column(
        String(20),
        default=TxSource.MANUAL,
        server_default=TxSource.MANUAL.value,
        nullable=False,
    )

    book: Mapped[Book] = relationship(back_populates="transactions")


# Avoid circular import — Budget imported after Transaction is defined.
from src.models.budget import Budget
