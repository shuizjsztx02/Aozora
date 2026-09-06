"""T-M2-04: Shared pagination utilities."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class PaginationParams(BaseModel):
    """Query parameters for paginated list endpoints."""

    limit: int = 50
    offset: int = 0

    def model_post_init(self, __context) -> None:
        if self.limit < 1 or self.limit > 200:
            raise ValueError("limit must be between 1 and 200")
        if self.offset < 0:
            raise ValueError("offset must be >= 0")


class PaginatedResponse(BaseModel, Generic[T]):
    """Standard paginated response envelope."""

    items: list[T]
    total: int
    limit: int
    offset: int
    has_more: bool
