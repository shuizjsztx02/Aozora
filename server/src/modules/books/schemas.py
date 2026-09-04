"""T-M2-04: Pydantic schemas for Book API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class BookCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    icon: str | None = Field(default=None, max_length=64)


class BookUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    icon: str | None = Field(default=None, max_length=64)
    version: int = Field(description="Expected version for optimistic concurrency")


class BookResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    icon: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None
