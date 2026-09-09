"""T-M4-01: AI Gateway API schemas."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

# Hard cap on user input keeps prompt costs bounded and limits abuse.
# Static constant on purpose — Pydantic max_length needs a literal value,
# and an env-configurable cap would create a second source of truth.
MAX_INPUT_CHARS = 500


class AIInterpretRequest(BaseModel):
    input: str = Field(min_length=1, max_length=MAX_INPUT_CHARS)
    # T11-6（决策 #7）启用：{persona:{name,text}|null, profile:{...}|null} →
    # render_messages_with_context 拼入 system 尾部（各段 ≤2000 截断、反注入前缀）。
    context: dict[str, Any] | None = None


class AIUsageInfo(BaseModel):
    prompt_tokens: int
    completion_tokens: int


class AIInterpretResponse(BaseModel):
    request_id: str
    task: str
    model: str
    # output 是最小解析结果（type/amountCents/category/note/bizDate/confidence），不是完整 ActionProposal。
    # 由客户端组装为 ActionProposal 后，再经 POST /ai/validate 校验 → POST /ai/execute 执行。
    # R-M4-03 契约澄清：此字段不做 ActionProposal Schema 校验，仅为中间表示。
    output: Any
    usage: AIUsageInfo


# ─── T-M4-02: ActionProposal validation endpoint ────────────────────

class AIValidateProposalRequest(BaseModel):
    """Client sends its assembled ActionProposal + book_id for validation.

    The server loads the user's authorised category/account IDs from the
    DB (``context.py``) and runs ``validator.validate_proposal``. Any
    hallucinated / unauthorised ID, unresolved category, or schema
    violation is reported in ``errors``. The client MUST NOT execute a
    proposal whose ``valid`` is false.
    """

    proposal: dict[str, Any]
    book_id: str = Field(min_length=1)


class AIValidationError(BaseModel):
    path: str
    message: str


class AIValidateProposalResponse(BaseModel):
    valid: bool
    errors: list[AIValidationError] = Field(default_factory=list)
    # One of: "accepted" | "rejected_hallucination" | "rejected_schema" |
    # "rejected_low_confidence". The client uses this for UX and for
    # security event logging (see APP-CONTEXT §6.4).
    verdict: Literal[
        "accepted",
        "rejected_hallucination",
        "rejected_schema",
        "rejected_low_confidence",
    ]


# ─── T-M4-02b: Execute validated proposal ────────────────────────────────

class AIExecuteProposalRequest(BaseModel):
    """Execute a validated ActionProposal.

    The client MUST first call /ai/validate and only execute when verdict
    is "accepted" or "rejected_low_confidence" (after explicit user
    confirmation). The server re-validates to prevent TOCTOU attacks.
    """

    proposal: dict[str, Any]
    book_id: str = Field(min_length=1)
    # Client-supplied confirmation token. For "accepted" proposals, this
    # can be any non-empty string. For "rejected_low_confidence", the
    # client must obtain explicit user confirmation and pass a token
    # (e.g. timestamp or UUID) to prove confirmation happened.
    # S-C2 fix: 允许空字符串，仅在低置信时由 execute_proposal 逻辑校验非空
    confirmation_token: str = Field(default="")


class AIExecuteProposalResponse(BaseModel):
    """Result of executing a proposal."""

    transaction_ids: list[str] = Field(default_factory=list)
    # Undo window in seconds. The client should show an "Undo" toast for
    # this duration. After the window closes, the transactions are
    # permanently committed.
    undo_window_seconds: int = 5
    # If the proposal was a batch, this is the batch ID (proposalId).
    # The client uses this for idempotency — re-executing the same
    # proposalId returns the same transaction IDs without creating
    # duplicates.
    proposal_id: str


# ─── T-M4-03: AI report / insight ─────────────────────────────────────

class AIReportRequest(BaseModel):
    """Request an AI-generated monthly report / insight.

    The server builds a deterministic FactPack (T-M4-03a) from the
    StatisticsService, then optionally generates an LLM narrative
    (T-M4-03b) when a provider is available and the data has changed.
    """

    book_id: str = Field(min_length=1)
    period: str = Field(
        min_length=7, max_length=7,
        pattern=r"^\d{4}-\d{2}$",
        description="YYYY-MM",
    )


class CategoryFactSchema(BaseModel):
    category_id: str
    category_name: str
    total_cents: int
    count: int


class BudgetFactSchema(BaseModel):
    amount_cents: int
    spent_cents: int
    remaining_cents: int
    pct_used: float


class TransactionFactSchema(BaseModel):
    id: str
    amount_cents: int
    category_name: str
    note: str
    biz_date: str


class TrendFactSchema(BaseModel):
    period: str
    total_income: int
    total_expense: int
    balance: int
    transaction_count: int


class AIReportResponse(BaseModel):
    request_id: str
    book_id: str
    period: str
    data_hash: str
    # Deterministic fact pack (always present)
    total_income: int
    total_expense: int
    balance: int
    transaction_count: int
    expense_by_category: list[CategoryFactSchema]
    income_by_category: list[CategoryFactSchema]
    budget: BudgetFactSchema | None
    largest_expense: TransactionFactSchema | None
    largest_income: TransactionFactSchema | None
    trend: list[TrendFactSchema]
    # LLM-generated narrative (absent when provider is unavailable or
    # data has not changed and cached narrative is returned).
    narrative: str | None = None
    # Whether the narrative was served from cache (no new LLM call).
    narrative_cached: bool = False
    usage: AIUsageInfo | None = None


# ─── T-M4-04e: Image receipt parsing ────────────────────────────────────

class AIParseImageRequest(BaseModel):
    """Parse a receipt/bill image into an ActionProposal.

    The client uploads the image via ``POST /attachments/upload`` first,
    receives an ``attachment_id``, then calls this endpoint to invoke the
    vision model. The server reads the stored file, sends it to the
    multimodal LLM, and returns a pre-assembled ActionProposal with
    category IDs resolved against the user's book.
    """

    attachment_id: uuid.UUID
    book_id: str = Field(min_length=1)


class AIParseImageResponse(BaseModel):
    """Result of image receipt parsing.

    ``proposal`` is a complete ActionProposal (action: ``add_tx_batch``)
    ready for ``/ai/validate`` → ``/ai/execute``. ``confidence`` reflects
    the vision model's overall certainty; the client uses it to decide
    whether to show the confirmation card (low confidence) or auto-execute.
    """

    request_id: str
    proposal: dict[str, Any]
    attachment_id: str
    confidence: float
    usage: AIUsageInfo
