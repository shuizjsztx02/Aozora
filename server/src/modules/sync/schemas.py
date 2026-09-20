"""T-M3-01: Pydantic schemas for Sync API (push/pull/bootstrap)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Push
# ---------------------------------------------------------------------------

class MutationItem(BaseModel):
    """A single client mutation to be applied by the server."""

    mutation_id: uuid.UUID
    entity_type: str = Field(
        pattern=r"^(book|category|account|transaction|budget"
                r"|savings_goal|scheduled_rule|chat_persona"
                r"|user_preference)$",
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
    # T-M5-05ab fix (S1-5): optional client-side queue-depth snapshot.
    # Clients MAY include the number of pending (unsynced) mutations they
    # currently hold; the server forwards this to the sync_queue_depth Gauge
    # so SyncBacklogHigh can fire when the aggregate exceeds the threshold.
    # Optional to preserve backward compatibility with older clients.
    pending_count: int | None = Field(
        default=None,
        ge=0,
        description="Client-side count of pending mutations not yet pushed",
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

    T-M7-01 (三实体上云): `savings_goals` / `scheduled_rules` / `chat_personas`
    are appended to the snapshot.  Two checksums are emitted so that **older
    clients keep working unchanged**:

    - ``checksum`` — legacy algorithm, over books|categories|accounts|
      transactions|budgets|cursor only.  Byte-for-byte identical to what a
      pre-M7 server would have returned, so an old client's recomputation
      still matches despite the three new keys being present (unknown keys
      are ignored by its JSON parser).
    - ``checksum_v2`` — over all eight lists + cursor, for clients that
      know about the cloud entities.

    T-M7-05j (偏好项上云): ``user_preferences`` joins the snapshot, and a
    **third** checksum is added rather than extending v2:

    - ``checksum_v3`` — over the nine lists above + cursor, for clients that
      know about preferences.

    Extending v2 instead would have put every already-built client into a
    bootstrap mismatch (and therefore a RETRYABLE loop) as soon as the account
    had a single preference row — the mismatch is invisible while the list is
    empty, because an empty list contributes no hash segment.
    """
    books: list[dict[str, Any]]
    categories: list[dict[str, Any]]
    accounts: list[dict[str, Any]]
    transactions: list[dict[str, Any]]
    budgets: list[dict[str, Any]]
    savings_goals: list[dict[str, Any]] = []
    scheduled_rules: list[dict[str, Any]] = []
    chat_personas: list[dict[str, Any]] = []
    # T-M7-05j: 偏好项上云。每行一个键（``{id, key, value, version}``），
    # 客户端 pull 后把每个键回灌进本地 ``sync_meta`` 的同名键。旧客户端忽略
    # 未知键，与三实体上云的 ``savings_goals`` 等同处理。
    user_preferences: list[dict[str, Any]] = []
    cursor: int
    checksum: str = ""
    checksum_v2: str = ""
    checksum_v3: str = ""
