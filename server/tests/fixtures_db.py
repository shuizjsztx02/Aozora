"""Shared async fixtures for model and migration tests."""

from __future__ import annotations

import uuid

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.models.base import Base
from src.models.ledger import (
    Book,
    Category,
    CategoryKind,
)
from src.models.user import User


@pytest_asyncio.fixture()
async def db_session():
    """Provide an async session backed by in-memory SQLite."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture()
async def sample_data(db_session: AsyncSession) -> dict:
    """Insert a user + book + categories for constraint tests."""
    user = User(id=uuid.uuid4(), nickname="tester")
    db_session.add(user)
    await db_session.flush()

    book = Book(id=uuid.uuid4(), owner_id=user.id, name="日常")
    db_session.add(book)

    cat_expense = Category(
        id=uuid.uuid4(), owner_id=user.id, book_id=book.id,
        kind=CategoryKind.EXPENSE, name="餐饮",
    )
    cat_income = Category(
        id=uuid.uuid4(), owner_id=user.id, book_id=book.id,
        kind=CategoryKind.INCOME, name="工资",
    )
    db_session.add_all([cat_expense, cat_income])
    await db_session.flush()

    return {
        "user": user,
        "book": book,
        "cat_expense": cat_expense,
        "cat_income": cat_income,
    }
