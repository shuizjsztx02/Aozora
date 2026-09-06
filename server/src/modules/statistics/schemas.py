"""T-M2-05: Pydantic schemas for Statistics API."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class CategoryStats(BaseModel):
    """Per-category aggregation within a period."""
    category_id: uuid.UUID
    category_name: str
    total_cents: int
    count: int


class MonthlyStats(BaseModel):
    """Monthly statistics for a book.

    balance = total_income - total_expense（允许负值表示赤字）。
    """
    book_id: uuid.UUID
    period: str  # YYYY-MM
    total_income: int = Field(ge=0)
    total_expense: int = Field(ge=0)
    balance: int  # can be negative (deficit)
    transaction_count: int
    expense_by_category: list[CategoryStats]
    income_by_category: list[CategoryStats]


class TrendPoint(BaseModel):
    """Single month in a trend series."""
    period: str
    total_income: int = Field(ge=0)
    total_expense: int = Field(ge=0)
    balance: int  # can be negative (deficit)
    transaction_count: int


class TrendStats(BaseModel):
    """Multi-month trend for a book."""
    book_id: uuid.UUID
    points: list[TrendPoint]
