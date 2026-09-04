"""T-M2-02: Tests for SQLAlchemy model constraints and invariants."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from src.models.budget import Budget, BudgetType
from src.models.ledger import Category, CategoryKind, Transaction, TxType, TxSource
from src.models.sync import SyncChange, SyncOperation
from src.models.user import User, UserStatus

from tests.fixtures_db import db_session, sample_data  # noqa: F401 — re-exported


# ── User ──────────────────────────────────────────────────────────────

class TestUser:
    @pytest.mark.asyncio
    async def test_create_and_defaults(self, db_session):
        user = User(nickname="newbie")
        db_session.add(user)
        await db_session.flush()

        assert user.id is not None
        assert user.status == UserStatus.GUEST
        assert user.timezone == "Asia/Shanghai"

    @pytest.mark.asyncio
    async def test_phone_unique(self, db_session):
        db_session.add_all([
            User(phone="13800000001", nickname="a"),
            User(phone="13800000001", nickname="b"),
        ])
        with pytest.raises(IntegrityError):
            await db_session.flush()


# ── Transaction ───────────────────────────────────────────────────────

class TestTransaction:
    @pytest.mark.asyncio
    async def test_create_valid(self, db_session, sample_data):
        tx = Transaction(
            book_id=sample_data["book"].id,
            type=TxType.EXPENSE,
            amount_cents=3500,
            category_id=sample_data["cat_expense"].id,
            note="午餐",
            biz_date=date(2026, 9, 4),
            occurred_at=datetime(2026, 9, 4, 12, 30, tzinfo=timezone.utc),
            source=TxSource.MANUAL,
        )
        db_session.add(tx)
        await db_session.flush()
        assert tx.version == 1
        assert tx.deleted_at is None

    @pytest.mark.asyncio
    async def test_amount_cents_must_be_positive(self, db_session, sample_data):
        """CK: amount_cents > 0 — zero and negative must be rejected."""
        tx = Transaction(
            book_id=sample_data["book"].id,
            type=TxType.EXPENSE,
            amount_cents=0,
            category_id=sample_data["cat_expense"].id,
            biz_date=date(2026, 9, 4),
            occurred_at=datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc),
        )
        db_session.add(tx)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    @pytest.mark.asyncio
    async def test_negative_amount_rejected(self, db_session, sample_data):
        tx = Transaction(
            book_id=sample_data["book"].id,
            type=TxType.EXPENSE,
            amount_cents=-100,
            category_id=sample_data["cat_expense"].id,
            biz_date=date(2026, 9, 4),
            occurred_at=datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc),
        )
        db_session.add(tx)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    @pytest.mark.asyncio
    async def test_income_is_positive(self, db_session, sample_data):
        """Income is also stored as positive amount_cents."""
        tx = Transaction(
            book_id=sample_data["book"].id,
            type=TxType.INCOME,
            amount_cents=500000,
            category_id=sample_data["cat_income"].id,
            biz_date=date(2026, 9, 1),
            occurred_at=datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc),
            source=TxSource.MANUAL,
        )
        db_session.add(tx)
        await db_session.flush()
        assert tx.amount_cents == 500000


# ── Budget ────────────────────────────────────────────────────────────

class TestBudget:
    @pytest.mark.asyncio
    async def test_overall_and_category_budget_coexist(self, db_session, sample_data):
        """(book_id, budget_type, period, category_id) unique —
        null category and specific category can coexist in same period."""
        book_id = sample_data["book"].id
        cat_id = sample_data["cat_expense"].id

        overall = Budget(
            book_id=book_id, budget_type=BudgetType.MONTHLY,
            period="2026-09", amount_cents=300000,
        )
        specific = Budget(
            book_id=book_id, budget_type=BudgetType.MONTHLY,
            category_id=cat_id, period="2026-09", amount_cents=50000,
        )
        db_session.add_all([overall, specific])
        await db_session.flush()

        rows = (
            await db_session.execute(
                select(Budget).where(Budget.book_id == book_id)
            )
        ).scalars().all()
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_duplicate_budget_rejected(self, db_session, sample_data):
        book_id = sample_data["book"].id
        cat_id = sample_data["cat_expense"].id

        db_session.add_all([
            Budget(
                book_id=book_id, budget_type=BudgetType.MONTHLY,
                category_id=cat_id, period="2026-09", amount_cents=50000,
            ),
            Budget(
                book_id=book_id, budget_type=BudgetType.MONTHLY,
                category_id=cat_id, period="2026-09", amount_cents=60000,
            ),
        ])
        with pytest.raises(IntegrityError):
            await db_session.flush()

    @pytest.mark.asyncio
    async def test_budget_amount_positive(self, db_session, sample_data):
        b = Budget(
            book_id=sample_data["book"].id,
            budget_type=BudgetType.MONTHLY,
            period="2026-09",
            amount_cents=0,
        )
        db_session.add(b)
        with pytest.raises(IntegrityError):
            await db_session.flush()


# ── Category ──────────────────────────────────────────────────────────

class TestCategory:
    @pytest.mark.asyncio
    async def test_duplicate_name_rejected(self, db_session, sample_data):
        """Same (book_id, parent_id, kind, name, owner_id) must be rejected."""
        book_id = sample_data["book"].id
        user_id = sample_data["user"].id
        parent_id = sample_data["cat_expense"].id  # use non-null parent_id

        db_session.add_all([
            Category(
                owner_id=user_id, book_id=book_id, parent_id=parent_id,
                kind=CategoryKind.EXPENSE, name="交通",
            ),
            Category(
                owner_id=user_id, book_id=book_id, parent_id=parent_id,
                kind=CategoryKind.EXPENSE, name="交通",
            ),
        ])
        with pytest.raises(IntegrityError):
            await db_session.flush()

    @pytest.mark.asyncio
    async def test_same_name_different_kind_ok(self, db_session, sample_data):
        book_id = sample_data["book"].id
        user_id = sample_data["user"].id

        db_session.add_all([
            Category(owner_id=user_id, book_id=book_id, kind=CategoryKind.EXPENSE, name="其他"),
            Category(owner_id=user_id, book_id=book_id, kind=CategoryKind.INCOME, name="其他"),
        ])
        await db_session.flush()  # should NOT raise


# ── SyncChange ────────────────────────────────────────────────────────

class TestSyncChange:
    @pytest.mark.asyncio
    async def test_cursor_auto_increments(self, db_session, sample_data):
        user_id = sample_data["user"].id
        entity_id = uuid.uuid4()

        c1 = SyncChange(
            user_id=user_id, entity_type="transaction", entity_id=entity_id,
            operation=SyncOperation.INSERT, version=1,
        )
        c2 = SyncChange(
            user_id=user_id, entity_type="transaction", entity_id=entity_id,
            operation=SyncOperation.UPDATE, version=2,
        )
        db_session.add_all([c1, c2])
        await db_session.flush()
        assert c2.cursor > c1.cursor
