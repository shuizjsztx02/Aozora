"""T-M2-04: Pydantic schemas for Category API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    kind: str = Field(pattern=r"^(expense|income)$")
    # REQ-73（T19-2，ADR-014）：图标与 book / account 同规格——跨端只承载
    # ≤64 字符（emoji），照片图标超长时客户端根本不上行，这里是兜底。
    icon: str | None = Field(default=None, max_length=64)
    book_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    # None = 不修改（与 name / parent_id 同一局部合并语义，同 AccountUpdate）。
    icon: str | None = Field(default=None, max_length=64)
    parent_id: uuid.UUID | None = None
    version: int = Field(description="Expected version for optimistic concurrency")


class CategoryResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    # REQ-73（T19-2）：读口同样出 icon——只收紧写口会让 REST 的「写进去了」与
    # 「读不回来」各说一半（book / account 的 Response 都带它）。
    icon: str | None
    kind: str
    book_id: uuid.UUID | None
    parent_id: uuid.UUID | None
    version: int
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None
