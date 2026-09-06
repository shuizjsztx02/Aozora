"""T-M3-01: Pydantic schemas for Sync API (push/pull/bootstrap)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, computed_field


# ---------------------------------------------------------------------------
# Push
# ---------------------------------------------------------------------------

class MutationItem(BaseModel):
    """A single client mutation to be applied by the server."""

    mutation_id: uuid.UUID
    entity_type: str = Field(
        pattern=r"^(book|category|account|transaction|budget)$",
    )
    operation: str = Field(
        pattern=r"^(insert|update|delete)$",
    )
    entity_id: uuid.UUID
    version: int | None = Field(
        default=None,
        description="Expected version for update/delete; ignored for insert",
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Entity fields for insert/update; empty for delete",
    )


class PushRequest(BaseModel):
    mutations: list[MutationItem] = Field(
        min_length=1, max_length=100,
        description="Batch of mutations (1–100)",
    )


class MutationResult(BaseModel):
    mutation_id: uuid.UUID
    status: str  # "applied" | "duplicate" | "conflict"
    cursor: int | None = None  # sync cursor when applied or duplicate
    version: int | None = None  # entity version after applying
    current_version: int | None = None  # server version when conflict


class PushResponse(BaseModel):
    results: list[MutationResult]
    cursor: int  # latest sync cursor


# ---------------------------------------------------------------------------
# Pull
# ---------------------------------------------------------------------------

class ChangeItem(BaseModel):
    cursor: int
    entity_type: str
    entity_id: uuid.UUID
    operation: str
    version: int
    changed_at: datetime
    entity: dict[str, Any] | None = None  # null for delete operations


class PullRequest(BaseModel):
    cursor: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=500)


class PullResponse(BaseModel):
    changes: list[ChangeItem]
    cursor: int  # latest cursor (== request cursor if no changes)
    has_more: bool


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

class BootstrapResponse(BaseModel):
    """Full data snapshot for device rebuild.

    T-M3-03: includes `checksum` — a deterministic SHA-256 of the response
    payload (excluding the checksum itself), formatted as lowercase hex.
    Clients recompute the same hash over the received entities and compare;
    mismatch indicates transport corruption or incomplete rebuild.
    """
    books: list[dict[str, Any]]
    categories: list[dict[str, Any]]
    accounts: list[dict[str, Any]]
    transactions: list[dict[str, Any]]
    budgets: list[dict[str, Any]]
    cursor: int
    checksum: str = ""
