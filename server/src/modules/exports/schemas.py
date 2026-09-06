"""T-M2-05: Pydantic schemas for Export API."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class ExportFormat(str, Enum):
    JSON = "json"
    CSV = "csv"


class ExportStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


class ExportCreate(BaseModel):
    book_id: uuid.UUID
    format: ExportFormat


class ExportResponse(BaseModel):
    id: uuid.UUID
    book_id: uuid.UUID
    format: str
    status: str
    created_at: datetime
    expires_at: datetime | None = None
    file_size: int | None = None

    model_config = {"from_attributes": True}
