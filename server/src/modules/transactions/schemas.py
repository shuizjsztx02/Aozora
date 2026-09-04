"""T-M2-04: Pydantic schemas for Transaction API."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


class TransactionCreate(BaseModel):
    book_id: uuid.UUID
    type: str = Field(pattern=r"^(expense|income)$")
    amount_cents: int = Field(gt=0)
    category_id: uuid.UUID
    account_id: uuid.UUID | None = None
    note: str = Field(default="", max_length=2000)
    biz_date: date
    occurred_at: datetime


class TransactionUpdate(BaseModel):
    type: str | None = Field(default=None, pattern=r"^(expense|income)$")
    amount_cents: int | None = Field(default=None, gt=0)
    category_id: uuid.UUID | None = None
    account_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=2000)
    biz_date: date | None = None
    version: int = Field(description="Expected version for optimistic concurrency")


class TransactionResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    book_id: uuid.UUID
    type: str
    amount_cents: int
    category_id: uuid.UUID
    account_id: uuid.UUID | None
    note: str
    biz_date: date
    occurred_at: datetime
    source: str
    version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
