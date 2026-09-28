"""T-M4-01: AI Gateway API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

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

    # ── T20-1（REQ-77 决策 #2）：回执从「一串实体 id」扩成**能渲染业务卡的一整份** ──
    # 三个新字段（外加只在改 / 删类 op 在场的 ``before``）都是**回带**，服务端不新增
    # 计算：v2 信封本来就带 action / message / payload，`execute_proposal` 只是把它们
    # 抄进回执。没有这一段，客户端手里只有实体 id——记账卡上的分类、金额、日期一个都
    # 画不出来（与 REQ-69 的 `account_type`、REQ-73 的 `category.icon` 同一形态：
    # 上层要用的字段，底层没有落点）。
    #
    # **v1 车道不填**（默认值即空）：那条车道的信封没有 ``message`` / ``payload``，
    # 回执对 v1 客户端逐字不变（既有契约不扰动）——`/ai/agent` 的 agent 车道恒发 v2。
    #
    # op 名（客户端据此分型：记账三 op 出业务卡，其余回落通用卡）。
    action: str = ""
    # 工具产出的那句人类可读中文（通用卡的主行，也是非记账 op 的回执文案）。
    message: str = ""
    # 工具产出的参数原样回带（含服务端确定性解析出的 id）。
    payload: dict[str, Any] = Field(default_factory=dict)
    # **仅** ``transaction_update`` / ``transaction_delete``：执行**之前**的关键字段
    # 快照（分类 / 收支 / 金额 / 日期 / 备注 / 账本 + 账户），供卡上的新旧对照。
    # 必须在执行前取——执行之后本地与服务端都可能已经查不到那笔（软删 / 同步时序）。
    before: dict[str, Any] | None = None


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


# ─── T-M7-02g: Agent 流式端点 ───────────────────────────────────────────

class AIAgentRequest(BaseModel):
    """Request for the streaming agent endpoint (``POST /ai/agent``).

    The response is ``text/event-stream``, not JSON — so this model is the
    only pydantic shape on the agent path: everything coming back is SSE
    frames, defined in ``stream.py``.

    ``conversation_id`` is optional: omit it on the first turn and the
    server creates a conversation, returning its id in the ``done`` frame
    for the client to reuse. ``book_id`` scopes the whole turn (tool
    context, system prompt, writes) and must belong to the caller.

    ``proposal_contract_version`` is the client's ActionProposal contract
    declaration (T-M7-05b; contract in ``ai-architecture.md``). **Default
    1** — an old APK sends nothing and gets the v1 lane, byte-for-byte the
    P1 behaviour. A v2 client declares 2 and gets the v2 envelope, the
    full 05b tool set and v2-only ops. There is no server-side switch:
    the declaration is the rollout knob (05a: 「声明字段天然按客户端版本
    灰度」).
    """

    input: str = Field(min_length=1, max_length=MAX_INPUT_CHARS)
    book_id: str = Field(min_length=1)
    conversation_id: str | None = None
    proposal_contract_version: Literal[1, 2] = 1


# ─── T-M7-03a: 会话列表 / 重命名 / 删除 / 事件读取 ─────────────────────────

#: 会话标题的字符上限——与 ``ai_conversations.title`` 的列宽一致。**自动派生的
#: 标题另有更短的上限**（``conversation.TITLE_MAX_CHARS``），两者不是一回事：
#: 前者管用户输入，后者管列表上的观感。
MAX_TITLE_CHARS = 120


class AIConversationSummary(BaseModel):
    """会话摘要——列表的一项，也是重命名的回执。

    ``last_active_at`` 一定在场：还没写成过一轮的会话回 ``created_at``
    （:meth:`from_conversation` 兜底），客户端因此不必自己处理 NULL。

    ``book_id`` 只给 id 不给名字：账本名在客户端本地就有（Room 副本），会话
    列表不必替它回显一份可能过期的。
    """

    id: uuid.UUID
    title: str
    book_id: uuid.UUID | None
    created_at: datetime
    last_active_at: datetime

    @classmethod
    def from_conversation(cls, conversation: Any) -> "AIConversationSummary":
        return cls(
            id=conversation.id,
            title=conversation.title,
            book_id=conversation.book_id,
            created_at=conversation.created_at,
            last_active_at=conversation.last_active_at or conversation.created_at,
        )


class AIConversationListResponse(BaseModel):
    conversations: list[AIConversationSummary]


class AIConversationRenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS)

    @field_validator("title", mode="before")
    @classmethod
    def _strip_first(cls, v: object) -> object:
        # mode="before" → 先 strip 再校验长度，纯空白因此被 min_length 挡住
        # （与 auth 的 UpdateProfileRequest 同口径）。
        return v.strip() if isinstance(v, str) else v


class AIConversationEventItem(BaseModel):
    """一条会话事件。``data`` 是 loop 的消息字典，**原样**（内部 ID 在 agent 侧
    就被剥掉了，见 ``agent._tool_result_content``）。

    ``turn`` 是会话内的轮号——客户端按它把事件分组成轮（与 ``history`` 的窗口
    口径同一个）。
    """

    model_config = {"from_attributes": True}

    seq: int
    turn: int
    type: str
    data: dict[str, Any]
    created_at: datetime


class AIConversationEventsResponse(BaseModel):
    """``GET /ai/conversations/{id}/events`` 的响应。

    ``conversation_id`` 一并回：客户端并发拉多个会话的增量时，不必靠请求上下文
    去猜这一批属于谁。
    """

    conversation_id: uuid.UUID
    events: list[AIConversationEventItem]


# ─── T-M7-04b: 会话分叉 ───────────────────────────────────────────────

class AIConversationForkRequest(BaseModel):
    """``POST /ai/conversations/{id}/fork`` 的入参。

    ``seq`` 是从事件读面拿到的序号——客户端挑一条说「从这里重说」。服务端把它
    向下取整到最近的**完整配对组边界**（切在组中间会复制出一个悬空的
    ``tool_calls``），并在响应里回报实际切点，客户端据此知道复制到哪儿。
    """

    seq: int = Field(ge=1)


class AIConversationForkResponse(BaseModel):
    """分叉的回执：新会话的摘要 + **实际**切点。

    ``seq`` 不一定等于请求里给的那个（见 ``AIConversationForkRequest``）。它是
    新会话里最后一条事件的序号——也就是这个分叉「记到哪儿为止」。
    """

    conversation: AIConversationSummary
    seq: int


# ─── T-M7-05e: 设备能力通道的上行回传 ─────────────────────────────────────

class AIDeviceResultRequest(BaseModel):
    """``POST /ai/device-result`` 的入参：一次**需结果**设备请求的结局。

    客户端收到 ``device_request`` 帧（``kind="result"``）后，在设备上执行，再
    把结局回传到这里——服务端据此唤醒挂起的那一轮（ADR-010）。

    ``request_id`` 原样带回帧里给的那个（**不解析、不入库**，只作为挂起表的键）。
    ``data`` 的形状由能力自己的实现决定，服务端不校验（payload 的所有权在能力
    侧，与 v2 契约同口径）：它是随后回喂模型的那条工具结果。
    """

    request_id: str = Field(min_length=1, max_length=64)
    #: ``ok`` / ``denied`` / ``unavailable`` / ``failed``。**没有 ``timeout``**：
    #: 那个结局只能由服务端产生（挂起期满），客户端报它没有意义。
    status: Literal["ok", "denied", "unavailable", "failed"]
    data: dict[str, Any] | None = None


class AIDeviceResultResponse(BaseModel):
    """回传的回执。``accepted`` 恒为 true——回不去的四种情形都是 404，
    不是 200 + false（重试安全，见 ``routes.device_result``）。"""

    accepted: bool = True
