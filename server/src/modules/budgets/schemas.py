"""T-M2-04: Pydantic schemas for Budget API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class BudgetUpsert(BaseModel):
    book_id: uuid.UUID
    budget_type: str = Field(pattern=r"^(monthly|annual)$")
    amount_cents: int = Field(gt=0)
    category_id: uuid.UUID | None = None
    version: int | None = Field(default=None, description="Expected version for optimistic concurrency (required for updates)")


class BudgetResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    book_id: uuid.UUID
    budget_type: str
    category_id: uuid.UUID | None
    period: str
    amount_cents: int
    version: int
    created_at: datetime
    updated_at: datetime
