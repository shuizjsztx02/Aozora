"""T-M2-04: Pydantic schemas for Category API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    kind: str = Field(pattern=r"^(expense|income)$")
    book_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    parent_id: uuid.UUID | None = None
    version: int = Field(description="Expected version for optimistic concurrency")


class CategoryResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    kind: str
    book_id: uuid.UUID | None
    parent_id: uuid.UUID | None
    version: int
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None
