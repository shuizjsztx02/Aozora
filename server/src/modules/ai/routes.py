"""T-M4-01: AI Gateway routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database import get_db_session
from src.models.user import User
from src.modules.auth.dependencies import get_current_user

from .context import load_validation_context
from .provider import LLMProvider, get_llm_provider
from .schemas import (
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
from .service import AIService

router = APIRouter(prefix="/ai", tags=["ai"])


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
