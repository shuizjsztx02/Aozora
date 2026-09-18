"""T-M4-01: AI Gateway routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.config import get_config
from src.common.database import get_db_session, get_session_factory
from src.common.errors import AppException
from src.models.user import User
from src.modules.auth.dependencies import get_current_user
from src.modules.auth.frozen import require_active as _require_active

from .context import load_validation_context
from .conversation import ConversationService
from .provider import (
    LLMProvider,
    ToolCallingProvider,
    get_agent_provider,
    get_llm_provider,
)
from .schemas import (
    AIAgentRequest,
    AIExecuteProposalRequest,
    AIExecuteProposalResponse,
    AIInterpretRequest,
    AIInterpretResponse,
    AIParseImageRequest,
    AIParseImageResponse,
    AIReportRequest,
    AIReportResponse,
    AIValidateProposalRequest,
    AIValidateProposalResponse,
    AIValidationError,
)
from .service import AIService, check_daily_quota, local_today
from .stream import agent_event_stream

router = APIRouter(prefix="/ai", tags=["ai"])

#: SSE 响应头（T-M7-02g / R-PROD-22）。
#:
#: ``X-Accel-Buffering: no`` —— nginx 默认 ``proxy_buffering on``，会把事件攒批
#: 下发、把步骤可见性抹平。nginx 从 1.5.6 起尊重上游这个头，**服务器配置因此
#: 不用动**（部署实况见 ``infra/deploy/prod/nginx/DEPLOYED.md``：``/aozora-api/``
#: 既无 ``proxy_ignore_headers`` 也无 ``proxy_cache``，该头不会被忽略）。
#: ``Cache-Control: no-cache`` —— 事件流不该被任何中间层缓存。
_SSE_HEADERS = {
    "X-Accel-Buffering": "no",
    "Cache-Control": "no-cache",
}


@router.post("/interpret", response_model=AIInterpretResponse)
async def interpret(
    body: AIInterpretRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
    provider: Annotated[LLMProvider | None, Depends(get_llm_provider)] = ...,
):
    """Interpret a short ledger sentence via the server-side LLM gateway.

    Degradation contract: 503 ``ai_unavailable`` / ``ai_invalid_output``
    and 429 ``ai_rate_limited`` / ``ai_quota_exceeded`` all signal the
    client to fall back to its local rule NLU (APP-CONTEXT §6.4).
    """
    _require_active(current_user)
    service = AIService(db, provider)
    return await service.interpret(current_user, request.state.request_id, body)


@router.post("/validate", response_model=AIValidateProposalResponse)
async def validate_proposal(
    body: AIValidateProposalRequest,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    """T-M4-02: validate an ActionProposal before execution.

    The client sends its assembled proposal + book_id. The server loads
    authorised category/account IDs from the DB and runs both schema
    and context checks. A rejected proposal MUST NOT be executed by the
    client. The verdict tells the client whether the failure is a
    schema violation, a hallucinated/unauthorised ID, or a low-
    confidence result that needs explicit user confirmation.
    """
    _require_active(current_user)
    try:
        book_uuid = uuid.UUID(body.book_id)
    except ValueError:
        return AIValidateProposalResponse(
            valid=False,
            errors=[AIValidationError(path="book_id", message="invalid UUID")],
            verdict="rejected_schema",
        )

    ctx = await load_validation_context(db, current_user.id, book_uuid)
    result = AIService.validate_proposal(body.proposal, ctx)
    return AIValidateProposalResponse(
        valid=result["valid"],
        errors=[AIValidationError(**e) for e in result["errors"]],
        verdict=result["verdict"],
    )


@router.post("/execute", response_model=AIExecuteProposalResponse)
async def execute_proposal(
    body: AIExecuteProposalRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
    provider: Annotated[LLMProvider | None, Depends(get_llm_provider)] = ...,
):
    """T-M4-02b: Execute a validated ActionProposal.

    The client MUST first call /ai/validate. Only proposals with verdict
    "accepted" (auto-execute) or "rejected_low_confidence" (after explicit
    user confirmation) can be executed.

    Security:
    - Re-validates the proposal (TOCTOU prevention)
    - Atomic batch creation (all-or-nothing)
    - Confirmation token required for low-confidence proposals

    The response includes transaction IDs and an undo window (5 seconds).
    The client should show an "Undo" toast during this window.
    """
    _require_active(current_user)
    try:
        book_uuid = uuid.UUID(body.book_id)
    except ValueError:
        from src.common.errors import AppException
        raise AppException(422, "invalid_book_id", "Invalid book ID")

    service = AIService(db, provider)
    result = await service.execute_proposal(
        user=current_user,
        request_id=request.state.request_id,
        proposal=body.proposal,
        book_id=book_uuid,
        confirmation_token=body.confirmation_token,
    )

    return AIExecuteProposalResponse(**result)


@router.post("/report", response_model=AIReportResponse)
async def report(
    body: AIReportRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
    provider: Annotated[LLMProvider | None, Depends(get_llm_provider)] = ...,
):
    """T-M4-03: Generate a monthly report / insight.

    Returns a deterministic FactPack (monthly aggregation, category breakdown,
    budget execution, trend) and optionally an LLM-generated narrative when
    the provider is available.

    The narrative is only generated when the data has changed (same data_hash
    returns cached narrative). If the LLM output fails number verification,
    only the fact pack is returned (no narrative).
    """
    _require_active(current_user)
    service = AIService(db, provider)
    return await service.generate_report(current_user, request.state.request_id, body)


@router.post("/parse-image", response_model=AIParseImageResponse)
async def parse_image(
    body: AIParseImageRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
    provider: Annotated[LLMProvider | None, Depends(get_llm_provider)] = ...,
):
    """T-M4-04e: Parse a receipt/bill image into an ActionProposal.

    The client first uploads the image via ``POST /attachments/upload``,
    receives an ``attachment_id``, then calls this endpoint. The server
    reads the stored image, sends it to a vision model, resolves category
    names to authorised IDs, and returns a complete ActionProposal
    (``add_tx_batch``) ready for ``/ai/validate`` → ``/ai/execute``.

    Degradation: 503 on provider failure / invalid output — the client
    preserves the draft and prompts retry when connectivity resumes.
    """
    _require_active(current_user)
    try:
        book_uuid = uuid.UUID(body.book_id)
    except ValueError:
        from src.common.errors import AppException
        raise AppException(422, "invalid_book_id", "Invalid book ID")

    service = AIService(db, provider)
    return await service.parse_image(
        user=current_user,
        request_id=request.state.request_id,
        attachment_id=body.attachment_id,
        book_id=book_uuid,
    )


@router.post("/agent", response_class=StreamingResponse)
async def agent(
    body: AIAgentRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
    provider: Annotated[ToolCallingProvider | None, Depends(get_agent_provider)] = ...,
):
    """T-M7-02g: run one agent turn and stream it back as SSE.

    Events are the eight in architecture §8.1 (see ``stream.py`` for what P1
    actually produces); the stream carries its own heartbeat and ends with
    ``done`` or ``error``. **Pre-flight failures are HTTP errors**, not
    events: the status code has to be chosen before the response starts, so
    the flag, the book/conversation ownership and both quota checks run here
    and surface as ordinary 4xx/5xx (the client's existing degradation
    chain understands ``ai_quota_exceeded`` / ``ai_unavailable``).

    ``db`` is only used for authentication and pre-flight: the streaming
    generator runs after the response has been returned, by which time a
    request-scoped session is already closed (architecture §8.4). The turn
    itself gets its own session, owned by ``stream.agent_event_stream``.

    When ``ai_agent_enabled`` is false the endpoint is unavailable (503
    ``agent_disabled``, same shape as the other config-disabled feature:
    ``image_parsing_disabled``). Clients that were never upgraded never call
    it, so nothing changes for them.
    """
    _require_active(current_user)
    cfg = get_config()
    if not cfg.ai_agent_enabled:
        raise AppException(
            503, "agent_disabled",
            "The AI agent endpoint is disabled (ai_agent_enabled=false)",
        )
    if provider is None:
        raise AppException(
            503, "ai_unavailable", "AI service is temporarily unavailable",
        )
    try:
        book_uuid = uuid.UUID(body.book_id)
    except ValueError:
        raise AppException(422, "invalid_book_id", "Invalid book ID")

    # 前置检查用端点自己的短命 session：流式生成器在响应返回后才开始跑，
    # 请求作用域的 session 那时已关闭（架构 §8.4 第 3 项）。会话行在这里提交，
    # 客户端拿到的 conversation_id 因此从第一轮起就有效。
    async with get_session_factory()() as session:
        conversations = ConversationService(session, cfg)
        conversation = await conversations.resolve(
            current_user, book_uuid, body.conversation_id,
        )
        # 配额两道都在跑 loop 之前（02f 的交接）：日配额按用户/按天，
        # 会话配额按会话/按整个会话生命周期。
        await check_daily_quota(session, current_user, cfg)
        await conversations.check_quota(conversation)
        await session.commit()
        conversation_id = conversation.id

    return StreamingResponse(
        agent_event_stream(
            provider=provider,
            cfg=cfg,
            user=current_user,
            book_id=book_uuid,
            conversation_id=conversation_id,
            request_id=request.state.request_id,
            user_input=body.input,
            # 「今天」按**用户本地时区**算，与配额日界同一套解析（模型换算
            # 「今天 / 昨天」全靠它，服务端不猜用户在哪个时区）。
            today=local_today(current_user.timezone),
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
