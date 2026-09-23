"""T-M2-04: Pydantic schemas for Account API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    icon: str | None = Field(default=None, max_length=64)
    # REQ-69（T18-5，ADR-013）：账户分类（自由文本，与客户端 AccountsPage 同值域）
    # 与当前余额（分，**可为负** = 信用卡欠款）。
    account_type: str | None = Field(default=None, max_length=64)
    balance_cents: int = 0


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    icon: str | None = Field(default=None, max_length=64)
    # None = 不修改（与 name/icon 同一局部合并语义）。余额恒传**绝对值**，不传增量。
    account_type: str | None = Field(default=None, max_length=64)
    balance_cents: int | None = Field(default=None)
    version: int = Field(description="Expected version for optimistic concurrency")


class AccountResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    icon: str | None
    account_type: str | None = None
    balance_cents: int = 0
    version: int
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None
