"""T-M4-01: AI Gateway routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.config import get_config
from src.common.database import get_db_session, get_session_factory
from src.common.errors import AppException
from src.models.user import User
from src.modules.auth.dependencies import get_current_user
from src.modules.auth.frozen import require_active as _require_active

from .authorization import authorize_payload
from .context import load_validation_context
from .conversation import ConversationService
from .devices import DeviceOutcome, device_channel
from .provider import (
    LLMProvider,
    ToolCallingProvider,
    get_agent_provider,
    get_llm_provider,
)
from .schemas import (
    AIAgentRequest,
    AIConversationEventItem,
    AIConversationEventsResponse,
    AIConversationForkRequest,
    AIConversationForkResponse,
    AIConversationListResponse,
    AIConversationRenameRequest,
    AIConversationSummary,
    AIDeviceResultRequest,
    AIDeviceResultResponse,
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

    **两条车道（T-M7-05b）**：车道按 ``contractVersion`` 在场与否判定。
    v1 走 ``ProposalValidationContext`` 的 categoryIds / accountIds 对拍（一字
    未改）；v2 只校验信封，payload 里的 id 由 ``authorize_payload`` 查库授权——
    与 loop 组装、``/ai/execute`` 是同一个函数（三处口径不许漂移）。
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

    proposal = body.proposal if isinstance(body.proposal, dict) else {}
    if result["verdict"] != "rejected_schema" and "contractVersion" in proposal:
        # v2 车道：payload 级授权（05a 的代价项，05b 落地）。授权失败一律判
        # 「幻觉」——语义与 v1 那条一致：id 不在服务端认可的集合里。
        auth_errors = await authorize_payload(
            db,
            user_id=current_user.id,
            book_id=book_uuid,
            op=str(proposal.get("action") or ""),
            payload=proposal.get("payload"),
        )
        if auth_errors:
            return AIValidateProposalResponse(
                valid=False,
                errors=[AIValidationError(**e) for e in auth_errors],
                verdict="rejected_hallucination",
            )

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


@router.get("/conversations", response_model=AIConversationListResponse)
async def list_conversations(
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    """T-M7-03a: 列出本人的会话（未归档，按活跃时间倒序）。

    会话索引是**服务端权威**（架构 §6.1：客户端本地只是投影缓存），所以列表以
    这里为准，客户端缓存对不上时按这份重建。只回本人的会话行——别人的会话既不在
    列表里，也无法从响应中推断它存在（读取面的唯一防线，见 ``conversation.py``）。

    不分页：一个账号的会话数由用户自己创建，且每个会话另有轮数上限，一次全给
    客户端最简单（侧栏本来就要全量）。
    """
    _require_active(current_user)
    conversations = await ConversationService(db).list_conversations(current_user)
    return AIConversationListResponse(
        conversations=[
            AIConversationSummary.from_conversation(c) for c in conversations
        ],
    )


@router.patch(
    "/conversations/{conversation_id}", response_model=AIConversationSummary,
)
async def rename_conversation(
    conversation_id: uuid.UUID,
    body: AIConversationRenameRequest,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    """T-M7-03a: 重命名会话。

    别人的 / 已删除的 / 不存在的 id 一律 404 ``conversation_not_found``——
    三者不可分辨。标题长度由 schema 校验（对齐列宽）。
    """
    _require_active(current_user)
    service = ConversationService(db)
    conversation = await service.load_owned(current_user, conversation_id)
    await service.rename(conversation, body.title)
    return AIConversationSummary.from_conversation(conversation)


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    """T-M7-03a: 删除会话（行归档，正文事件随之清理）。

    两半的理由见 ``conversation.py``：行留着是为了 ``ai_usage`` 的审计链与 P3
    保留期的口径（04d 只删事件、不删会话行），正文没有留的理由。

    **重试是安全的**：删除成功后旧 id 与别人的 id 拿到同一个 404，客户端把它当作
    「这个 id 不是我的会话了」即可，不该当作错误弹给用户（P2 挂账 1 的降级契约）。
    """
    _require_active(current_user)
    service = ConversationService(db)
    conversation = await service.load_owned(current_user, conversation_id)
    await service.delete(conversation)


@router.get(
    "/conversations/{conversation_id}/events",
    response_model=AIConversationEventsResponse,
)
async def conversation_events(
    conversation_id: uuid.UUID,
    after_seq: Annotated[int | None, Query(ge=0)] = None,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    """T-M7-03a: 读会话事件（``after_seq`` 增量读取）。

    这是**客户端投影的重建源**：本地缓存丢了、或断线期间落了帧，客户端拿上一批
    最后一条的 ``seq`` 再拉一次就能补齐（``after_seq`` 是严格大于，不重复）。

    事件原样返回——不做 Surface 子集过滤、不补 ``ignorable``（完整事件日志是 P3
    的 04a）。载荷与 ``/ai/agent`` 的模型上下文是同一个数据面：工具结果里的内部
    ID 在 agent 侧就被剥掉了。
    """
    _require_active(current_user)
    service = ConversationService(db)
    conversation = await service.load_owned(current_user, conversation_id)
    events = await service.events(conversation, after_seq)
    return AIConversationEventsResponse(
        conversation_id=conversation.id,
        events=[AIConversationEventItem.model_validate(e) for e in events],
    )


@router.post(
    "/conversations/{conversation_id}/fork",
    response_model=AIConversationForkResponse,
)
async def fork_conversation(
    conversation_id: uuid.UUID,
    body: AIConversationForkRequest,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    """T-M7-04b: 从某个事件序号起分叉出新会话（原会话不动）。

    架构 §6.2 的「可分叉——用户『回到某轮重说』不需要复制消息数组」：客户端只报
    一个 ``seq``，前缀由服务端从事件日志里复制，**不要求也不接受**客户端把历史
    传上来。

    ``seq`` 向下取整到完整的配对组边界（响应里的 ``seq`` 是实际切点）：切在某条
    ``assistant`` 与它的 ``tool`` 结果之间，复制出来的会话里会有一个悬空的
    ``tool_calls``，下一轮模型请求直接 400（架构 §4.4 不变式 1）。

    归属校验同其它会话端点：别人的 / 已删除的 / 不存在的 id 一律 404
    ``conversation_not_found``，三者不可分辨。
    """
    _require_active(current_user)
    service = ConversationService(db)
    conversation = await service.load_owned(current_user, conversation_id)
    forked, cut = await service.fork(conversation, body.seq)
    return AIConversationForkResponse(
        conversation=AIConversationSummary.from_conversation(forked),
        seq=cut,
    )


@router.post("/device-result", response_model=AIDeviceResultResponse)
async def device_result(
    body: AIDeviceResultRequest,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    """T-M7-05e: 设备能力通道的**上行回传**——唤醒挂起中的那一轮。

    单向指令（触感 / 刷新小组件 / 分享）不走这里：它们不回传。**需结果**类
    （权限 / 选图）在服务端 loop 里挂起，客户端执行完后把结局投到这里，
    loop 拿它当作那次工具调用的结果继续往下跑（ADR-010）。

    **四种「回不去」的情形一律 404** ``device_request_not_found``：没有这条请求、
    已经回过（重复回传）、已经超时注销、请求属于别人。三者（四者）不可分辨是
    刻意的——①重试安全：客户端在弱网下重发一次不该被当作错误弹给用户（与 03a
    的删除端点同口径）；②不泄漏存在性：别人的 request_id 与不存在的 request_id
    得到同一个答案。

    ``db`` 只用于鉴权（``get_current_user``）；挂起表是**进程内的**
    （``devices.DeviceChannel``，单 worker 前提，见 ADR-010 决策 2）。
    """
    _require_active(current_user)
    accepted = device_channel().deliver(
        body.request_id,
        user_id=current_user.id,
        outcome=DeviceOutcome(status=body.status, data=body.data or {}),
    )
    if not accepted:
        raise AppException(
            404, "device_request_not_found",
            "No pending device request for this id",
        )
    return AIDeviceResultResponse()


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
            # 客户端声明的 ActionProposal 契约版本（05b）：决定注入哪些写工具、
            # 提案装进哪条信封。缺省 1 = 旧 APK 的 v1 车道，行为与 P1 逐字一致。
            contract_version=body.proposal_contract_version,
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
