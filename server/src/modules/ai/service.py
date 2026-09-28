"""T-M4-01: AI Gateway service — limits, retry, usage, redacted logs.

Failure contract (APP-CONTEXT §6.4):
- invalid JSON output → retry exactly once, then degrade;
- provider 5xx/429/transport faults → retry exactly once, then degrade;
- timeouts → NEVER retried (the client cancels at 25 s; retrying a slow
  provider only stacks latency and burns tokens);
- degraded responses use generic messages — provider error bodies may
  echo user input and must never reach clients or logs (security.md).
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.config import Settings, get_config
from src.common.errors import AppException
from src.common.telemetry import record_ai_gateway
from src.models.ai import AIUsage
from src.models.user import User, UserStatus

from .authorization import authorize_payload
from .prompts import (
    INTERPRET_TASK,
    REPORT_TASK,
    PromptTemplate,
    get_template,
    render_messages,
    render_messages_with_context,
)
from .proposal_ops import execute_op, register_balance_account_changes
from .provider import LLMProvider, ProviderError, ProviderResult
from .schemas import (
    AIInterpretRequest,
    AIInterpretResponse,
    AIParseImageResponse,
    AIReportRequest,
    AIReportResponse,
    AIUsageInfo,
    BudgetFactSchema,
    CategoryFactSchema,
    TransactionFactSchema,
    TrendFactSchema,
)
from .tools import PROPOSAL_CONTRACT_V2

if TYPE_CHECKING:
    from .context import ProposalValidationContext
    from .fact_pack import FactPack

logger = logging.getLogger(__name__)

# Per-user in-memory rate limiter — same pattern and same multi-instance
# caveat as src/modules/ai/fact_pack.py (replace with Redis when
# deploying multiple instances). Module-level so tests can clear it.
_ai_rate_limits: dict[uuid.UUID, list[float]] = defaultdict(list)
_last_cleanup: float = 0.0
_MAX_ENTRIES = 10_000
_CLEANUP_INTERVAL_SECONDS = 60
_RATE_WINDOW_SECONDS = 60.0

# Narrative cache: (book_id, period, data_hash) → narrative string
# Same TTL and max entries as FactPack cache (fact_pack.py:96-97)
_narrative_cache: dict[str, tuple[float, str]] = {}
_NARRATIVE_CACHE_TTL_SECONDS = 300  # 5 minutes
_MAX_NARRATIVE_CACHE_ENTRIES = 500

# NEW-E4: attachment IDs currently being parsed (in-process dedup).
# Same single-instance caveat as _ai_rate_limits — replace with Redis
# SETNX when deploying multiple workers. Prevents two concurrent
# parse_image calls for the same attachment from both triggering a
# vision model call (double billing).
_parsing_attachments: set[str] = set()

# APP-CONTEXT §6.4 contract constant — "invalid JSON → retry once".
# Deliberately NOT configurable: a knob here would break the documented
# 25 s client-cancel budget (2 × timeout + overhead < 25 s).
MAX_ATTEMPTS = 2

# APP-CONTEXT §6.4 / §10.3: the client shows a "slow" hint at 12 s and
# cancels at 25 s. The server can't push mid-request, so our share of
# the contract is (a) staying inside the budget and (b) flagging requests
# that crossed the 12 s line for monitoring (T-M5-05b latency dashboard).
SLOW_HINT_SECONDS = 12.0

#: 回执里**带 `before` 快照**的 op（T20-1 / REQ-77 决策 #4）：只有改 / 删类需要新旧
#: 对照——新增没有「旧」。快照必须在**执行之前**抓：执行之后本地与服务端都可能已经
#: 查不到那笔（软删 / 同步时序），事后取必然拿不全。
_BEFORE_SNAPSHOT_OPS: frozenset[str] = frozenset(
    {"transaction_update", "transaction_delete"},
)


def _execute_receipt(
    proposal: dict[str, Any],
    transaction_ids: list[str],
    *,
    lane: int,
    before: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """组装 ``/ai/execute`` 的回执（T20-1 / REQ-77 决策 #2）。

    回执从「一串实体 id」扩成**能渲染业务卡的一整份**：客户端拿到 id 之外的
    ``action`` / ``message`` / ``payload`` / ``before``，才画得出记账卡与新旧对照。
    四个新键**全部是回带**（v2 信封本来就带前三个，``before`` 由调用方在执行前抓
    好），服务端不新增任何计算。

    **v1 车道逐字不变**：那条车道的信封只有 action + ``transactions``（``_v1_envelope``），
    没有 ``message`` / ``payload`` 可回带；补空壳进去只会让既有契约多三个恒空的键。

    `transaction_ids` 的语义两条车道不同（v1 = 交易 id；v2 = **被影响的实体** id，
    改 / 归档是同一个 id 的新版本），字段名沿用的理由见 ``execute_proposal`` 内的
    注释——本函数不改这层语义。
    """
    receipt: dict[str, Any] = {
        "transaction_ids": transaction_ids,
        "proposal_id": proposal.get("proposalId") or "",
        "undo_window_seconds": 5,
    }
    if lane != PROPOSAL_CONTRACT_V2:
        return receipt
    payload = proposal.get("payload")
    receipt["action"] = str(proposal.get("action") or "")
    receipt["message"] = str(proposal.get("message") or "")
    receipt["payload"] = dict(payload) if isinstance(payload, dict) else {}
    if before is not None:
        receipt["before"] = before
    return receipt


def _cleanup_expired(now: float) -> None:
    """Remove entries whose timestamps have all expired; cap dict size."""
    expired_keys = [
        key for key, timestamps in _ai_rate_limits.items()
        if all(now - t >= _RATE_WINDOW_SECONDS for t in timestamps)
    ]
    for key in expired_keys:
        _ai_rate_limits.pop(key, None)
    if len(_ai_rate_limits) > _MAX_ENTRIES:
        for key in sorted(
            _ai_rate_limits,
            key=lambda k: _ai_rate_limits[k][-1] if _ai_rate_limits[k] else 0.0,
        )[: len(_ai_rate_limits) - _MAX_ENTRIES]:
            _ai_rate_limits.pop(key, None)


def _narrative_cache_key(book_id: str, period: str, data_hash: str) -> str:
    """Build cache key for narrative cache."""
    return f"narrative:{book_id}:{period}:{data_hash}"


def _narrative_cache_get(key: str) -> str | None:
    """Get cached narrative if present and not expired."""
    entry = _narrative_cache.get(key)
    if entry is None:
        return None
    inserted, narrative = entry
    if time.time() - inserted > _NARRATIVE_CACHE_TTL_SECONDS:
        _narrative_cache.pop(key, None)
        return None
    return narrative


def _narrative_cache_set(key: str, narrative: str) -> None:
    """Store narrative in cache with TTL."""
    if len(_narrative_cache) >= _MAX_NARRATIVE_CACHE_ENTRIES:
        # Evict oldest entry
        oldest = min(_narrative_cache.items(), key=lambda kv: kv[1][0])[0]
        _narrative_cache.pop(oldest, None)
    _narrative_cache[key] = (time.time(), narrative)


def _narrative_cache_clear() -> None:
    """Test helper: clear narrative cache."""
    _narrative_cache.clear()


def local_day_start_utc(tz_name: str) -> datetime:
    """Start of *today* in the user's timezone, converted to UTC.

    Quota days reset at local midnight (product decision 2026-09-06).
    Unknown/invalid timezones fall back to UTC.
    """
    tz = user_timezone(tz_name)
    day_start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return day_start.astimezone(UTC)


def user_timezone(tz_name: str) -> ZoneInfo:
    """The user's timezone; unknown/invalid names fall back to UTC.

    Single resolution point for the two places that need it: the quota day
    boundary (``local_day_start_utc``) and the agent loop's "today"
    (``local_today``). Keeping one implementation means the two can never
    disagree about what day it is for the same user.
    """
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def local_today(tz_name: str) -> date:
    """Today in the user's timezone — ``AgentLoop`` 的 ``today``（T-M7-02g）。

    模型换算「今天 / 昨天 / 上周」全靠这一个日期（架构 §4.6 的系统提示），服务端
    不猜用户在东八区还是别处，与配额日界共用同一套时区解析。
    """
    return datetime.now(user_timezone(tz_name)).date()


async def check_daily_quota(db: AsyncSession, user: User, cfg: Settings) -> None:
    """GUEST 用户的日配额（ACTIVE 用户不设上限）。

    **模块级函数而不是 ``AIService`` 的方法**：agent 路径（02g 的端点）同样要吃
    这道限，但它不需要构造 ``AIService``（那要一个 provider）。两条路径共用同一
    份实现，端点因此不必手抄 SQL——抄一份的结果只会是两边慢慢漂开。
    """
    if user.status != UserStatus.GUEST:
        return
    day_start = local_day_start_utc(user.timezone)
    count = await db.scalar(
        select(func.count())
        .select_from(AIUsage)
        .where(AIUsage.user_id == user.id, AIUsage.created_at >= day_start)
    )
    if count is not None and count >= cfg.ai_daily_quota_guest:
        raise AppException(
            429, "ai_quota_exceeded", "Daily AI quota used up, please try again tomorrow",
        )


def build_usage_row(
    *,
    user_id: uuid.UUID,
    provider: str,
    model: str,
    request_id: str,
    tokens: int,
    cfg: Settings | None = None,
    conversation_id: uuid.UUID | None = None,
) -> AIUsage:
    """Build one ``ai_usage`` row.

    Single implementation point for the cost formula and the
    ``request_id`` truncation, shared by the fast path (:class:`AIService`)
    and the agent path (``conversation.ConversationService``).

    ``conversation_id`` is set for agent turns only — one row per **Turn**
    (``tokens`` = that turn's total provider calls), which is what makes
    the conversation-level "turns + tokens" quota a plain COUNT/SUM
    aggregate over this table. Fast-path rows leave it ``NULL``.
    """
    price = (cfg or get_config()).ai_price_per_1m_tokens
    cost = round(tokens * price / 1_000_000) if price else 0
    if len(request_id) > 64:
        logger.debug(
            "X-Request-Id truncated to 64 chars",
            extra={"request_id_prefix": request_id[:16], "original_length": len(request_id)},
        )
    return AIUsage(
        user_id=user_id,
        provider=provider,
        model=model,
        tokens=tokens,
        cost=cost,
        # X-Request-Id is client-supplied and unbounded; the column is
        # String(64) — truncation keeps PostgreSQL from erroring.
        request_id=request_id[:64],
        conversation_id=conversation_id,
    )


class AIService:
    def __init__(
        self,
        db: AsyncSession,
        provider: LLMProvider | None,
        cfg: Settings | None = None,
    ) -> None:
        self._db = db
        self._provider = provider
        self._cfg = cfg or get_config()

    # -- public ------------------------------------------------------------

    async def interpret(
        self, user: User, request_id: str, req: AIInterpretRequest,
    ) -> AIInterpretResponse:
        tpl = get_template(INTERPRET_TASK)
        self._check_rate_limit(user.id)
        await self._check_daily_quota(user)

        if self._provider is None:
            self._log_failure(request_id, tpl, None, 0, "no_provider", None)
            raise AppException(
                503, "ai_unavailable", "AI service is temporarily unavailable",
            )

        model = tpl.model or self._cfg.ai_provider_model
        # T11-6（REQ-27 / 决策 #7）：context（画像/人设）渲染进 system 尾部；
        # 无 context 时与 render_messages 等价，REPORT 月报路径不受影响。
        messages = render_messages_with_context(tpl, req.input, req.context)
        started = time.perf_counter()

        result: ProviderResult | None = None
        output: object = None
        # R-M4-06: an attempt that reached the provider but returned unusable
        # output still consumed (and was billed for) tokens — accumulate them
        # so the audit row and response usage reflect true cost across retries.
        retry_prompt_tokens = 0
        retry_completion_tokens = 0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                result = await self._provider.complete(
                    messages,
                    model=model,
                    max_tokens=tpl.max_tokens,
                    timeout=self._cfg.ai_timeout_seconds,
                )
            except ProviderError as exc:
                if not exc.retryable or attempt >= MAX_ATTEMPTS:
                    # P1-1: record tokens from earlier attempts that
                    # reached the provider (billed but previously lost).
                    await self._record_failure_usage(
                        user, model, request_id,
                        extra_prompt_tokens=retry_prompt_tokens,
                        extra_completion_tokens=retry_completion_tokens,
                    )
                    self._log_failure(request_id, tpl, model, attempt, exc.reason, started)
                    raise AppException(
                        503, "ai_unavailable", "AI service is temporarily unavailable",
                    )
                continue

            if tpl.output_format != "json":
                output = result.content
                break
            try:
                output = self._parse_json_output(result.content)
                break
            except ValueError:
                retry_prompt_tokens += result.prompt_tokens
                retry_completion_tokens += result.completion_tokens
                if attempt >= MAX_ATTEMPTS:
                    # P1-1: the final attempt consumed tokens too
                    # (retry_*_tokens includes this attempt).
                    await self._record_failure_usage(
                        user, model, request_id,
                        extra_prompt_tokens=retry_prompt_tokens,
                        extra_completion_tokens=retry_completion_tokens,
                    )
                    self._log_failure(
                        request_id, tpl, model, attempt, "invalid_json", started,
                    )
                    raise AppException(
                        503, "ai_invalid_output", "AI service is temporarily unavailable",
                    )
                continue

        assert result is not None  # loop always breaks with a result or raises

        await self._record_usage(
            user, result, model, request_id,
            extra_prompt_tokens=retry_prompt_tokens,
            extra_completion_tokens=retry_completion_tokens,
        )
        self._log_success(request_id, tpl, model, result, started)
        self._log_slow_if_needed(request_id, tpl, model, started)
        record_ai_gateway(
            status="200",
            degraded=False,
            provider=self._provider.name,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            input_tokens=result.prompt_tokens + retry_prompt_tokens,
            output_tokens=result.completion_tokens + retry_completion_tokens,
        )
        return AIInterpretResponse(
            request_id=request_id,
            task=tpl.task_id,
            model=model,
            output=output,
            usage=AIUsageInfo(
                prompt_tokens=result.prompt_tokens + retry_prompt_tokens,
                completion_tokens=result.completion_tokens + retry_completion_tokens,
            ),
        )

    # -- limits --------------------------------------------------------------

    def _check_rate_limit(self, user_id: uuid.UUID) -> None:
        """Per-user burst protection (all users, guests and active alike)."""
        global _last_cleanup
        now = time.time()

        if now - _last_cleanup > _CLEANUP_INTERVAL_SECONDS:
            _cleanup_expired(now)
            _last_cleanup = now

        bucket = [t for t in _ai_rate_limits[user_id] if now - t < _RATE_WINDOW_SECONDS]
        if len(bucket) >= self._cfg.ai_rate_limit_per_minute:
            _ai_rate_limits[user_id] = bucket
            raise AppException(
                429, "ai_rate_limited", "Too many AI requests, please slow down",
            )
        bucket.append(now)
        _ai_rate_limits[user_id] = bucket

    async def _check_daily_quota(self, user: User) -> None:
        """Daily quota for GUEST users only; ACTIVE users are uncapped.

        Delegates to the module-level :func:`check_daily_quota` so the agent
        path checks the same number with the same query (T-M7-02g).
        """
        await check_daily_quota(self._db, user, self._cfg)

    # -- output parsing --------------------------------------------------------

    @staticmethod
    def _parse_json_output(content: str) -> dict:
        """Parse model output as a JSON object, tolerating markdown fences.

        Qwen models frequently wrap JSON in ``` fences despite prompt
        instructions; stripping them is what makes the online path
        (Chinese numerals etc.) usable in practice.

        P2#7: also tolerate a prose preamble *before* the fence
        (``"Here is the result:\\n```json\\n{...}\\n```"``). We try the
        whole text first (so a bare JSON object whose string values happen
        to contain backticks is never mangled), then fall back to
        extracting the first fenced block.
        """
        text = content.strip()

        # Fast path: bare JSON object.
        parsed = AIService._loads_json_object(text)
        if parsed is not None:
            return parsed

        # Fallback: JSON wrapped in a markdown fence, with optional preamble.
        fence = text.find("```")
        if fence != -1:
            # Skip opening fence + optional language tag.
            after_fence = fence + 3
            nl = text.find("\n", after_fence)
            if nl != -1:
                inner = text[nl + 1:]
            else:
                # N-4: fence and content on the same line (no newline after ```)
                inner = text[after_fence:]
            close = inner.find("```")
            if close != -1:
                inner = inner[:close]
            parsed = AIService._loads_json_object(inner.strip())
            if parsed is not None:
                return parsed

        raise ValueError("model output is not a JSON object")

    @staticmethod
    def _loads_json_object(text: str) -> dict | None:
        """Return *text* parsed as a JSON object, or None if it isn't one."""
        try:
            obj = json.loads(text)
        except ValueError:
            return None
        return obj if isinstance(obj, dict) else None

    # -- usage & logging ---------------------------------------------------------

    async def _record_usage_from_tokens(
        self, user: User, provider_name: str, model: str, request_id: str,
        *, prompt_tokens: int, completion_tokens: int,
    ) -> None:
        """Record an AIUsage row from explicit token counts.

        Shared helper for success and failure paths. The caller is
        responsible for computing the total token counts (including any
        retry extras). See ``_record_usage`` for the success-path wrapper.
        """
        self._db.add(build_usage_row(
            user_id=user.id,
            provider=provider_name,
            model=model,
            request_id=request_id,
            tokens=prompt_tokens + completion_tokens,
            cfg=self._cfg,
        ))
        await self._db.flush()

    async def _record_usage(
        self, user: User, result: ProviderResult, model: str, request_id: str,
        *, extra_prompt_tokens: int = 0, extra_completion_tokens: int = 0,
    ) -> None:
        """Append one AIUsage row for the successful provider call.

        ``extra_*_tokens`` carry tokens consumed by earlier attempts that
        reached the provider but produced unusable output (R-M4-06) — the
        provider bills them, so the audit row must include them.

        Only recorded on the success path: ``get_db_session`` rolls the
        session back when the request raises, so failure-path rows would
        be discarded anyway. Failed attempts are tracked via the
        structured WARNING log (``attempt`` / ``outcome`` fields) AND via
        ``_record_failure_usage`` on the error path (P1-1 fix).
        """
        assert self._provider is not None
        await self._record_usage_from_tokens(
            user, self._provider.name, model, request_id,
            prompt_tokens=result.prompt_tokens + extra_prompt_tokens,
            completion_tokens=result.completion_tokens + extra_completion_tokens,
        )

    async def _record_failure_usage(
        self, user: User, model: str, request_id: str,
        *, extra_prompt_tokens: int = 0, extra_completion_tokens: int = 0,
    ) -> None:
        """Record tokens consumed by provider attempts that preceded a failure.

        When retries reach the provider (consuming tokens) but the overall
        request fails (5xx on final attempt, or invalid JSON after all
        retries), the tokens are still billed by the provider. This
        ensures they appear in the AIUsage audit table even though the
        request returned an error.

        Commits the usage row immediately on ``self._db`` so it survives
        the session rollback that follows when AppException propagates.
        At the call sites, no other pending writes exist in the session
        (the only prior DB operation is a read-only quota check), so the
        commit is safe.

        No-op when no tokens were consumed (e.g. ProviderError on the
        first attempt — no result, no tokens billed).
        """
        assert self._provider is not None
        total = extra_prompt_tokens + extra_completion_tokens
        if total == 0:
            return
        self._db.add(build_usage_row(
            user_id=user.id,
            provider=self._provider.name,
            model=model,
            request_id=request_id,
            tokens=total,
            cfg=self._cfg,
        ))
        await self._db.flush()
        await self._db.commit()

    def _log_success(
        self,
        request_id: str,
        tpl: PromptTemplate,
        model: str,
        result: ProviderResult,
        started: float,
    ) -> None:
        assert self._provider is not None
        # Redacted: task/model/tokens/timing only — never input or output
        # content (security.md forbids logging conversations).
        logger.info(
            "AI interpret ok",
            extra={
                "request_id": request_id,
                "ai_task": tpl.task_id,
                "ai_model": model,
                "ai_provider": self._provider.name,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "outcome": "ok",
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )

    def _log_failure(
        self,
        request_id: str,
        tpl: PromptTemplate,
        model: str | None,
        attempt: int,
        reason: str,
        started: float | None,
    ) -> None:
        # ``reason`` is an enumerated safe string from ProviderError or
        # "invalid_json"/"no_provider" — never a provider message.
        extra: dict = {
            "request_id": request_id,
            "ai_task": tpl.task_id,
            "ai_model": model,
            "ai_provider": getattr(self._provider, "name", None),
            "attempt": attempt,
            "outcome": reason,
        }
        if started is not None:
            extra["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        logger.warning("AI interpret failed", extra=extra)
        if started is not None:
            # Slow failures must be countable without parsing duration_ms
            # (same outcome="slow" event as the success path emits).
            self._log_slow_if_needed(request_id, tpl, model, started)

    def _log_slow_if_needed(
        self,
        request_id: str,
        tpl: PromptTemplate,
        model: str | None,
        started: float,
    ) -> None:
        """Emit a WARNING when the request exceeded the 12 s slow threshold.

        The client-side UX hint is the user-visible "slow" indicator;
        this server log is for the T-M5-05b latency dashboard so ops
        can count slow requests without parsing duration_ms fields.
        Called on both success and failure paths.
        """
        elapsed = time.perf_counter() - started
        if elapsed >= SLOW_HINT_SECONDS:
            logger.warning(
                "AI interpret slow",
                extra={
                    "request_id": request_id,
                    "ai_task": tpl.task_id,
                    "ai_model": model,
                    "ai_provider": getattr(self._provider, "name", None),
                    "outcome": "slow",
                    "duration_ms": round(elapsed * 1000, 2),
                },
            )

    # -- T-M4-02: proposal validation --------------------------------------

    @staticmethod
    def validate_proposal(
        proposal: object,
        ctx: ProposalValidationContext,
    ) -> dict:
        """Validate an ActionProposal against schema + context.

        Returns ``{"valid", "errors", "verdict"}``. ``verdict`` is one of:
        - ``"accepted"`` — schema + context both pass
        - ``"rejected_schema"`` — schema violation (shape/amount/date)
        - ``"rejected_hallucination"`` — categoryId or accountId not in context
        - ``"rejected_low_confidence"`` — confidence below threshold; client
          must surface for confirmation (not a security failure, but the
          proposal may not be auto-executed)

        The order matters: schema errors are reported first because they
        mean the input is malformed — hallucination checks would be
        meaningless on a structurally invalid proposal.

        **两条车道（T-M7-05b）。** 车道按 ``contractVersion`` 在场与否判定
        （05a 的双读入口）：

        - **v1** —— 逐字沿用 P1：schema → 幻觉（categoryId / accountId 对拍
          上下文）→ 低置信。下列判定与顺序一行未改。
        - **v2** —— 信封由 ``validate_proposal_v2`` 校验；**payload 里的 id 不在
          这里对拍**，因为它们需要查库（交易 / 账本归属），由
          ``authorization.authorize_payload`` 在**组装与执行时**施加——
          ``/ai/validate`` 与 ``execute_proposal`` 各自调用它一次。
          本函数的 v2 分支只做信封 + 低置信两道，因此它**不是**执行许可
          （同 ``ai-architecture.md`` 的「结构通过不是执行许可」）。
        """
        from .validator import validate_proposal_any

        # Confidence threshold — matches the local NLU in
        # ``app/src/domain/nlu/parser.ts`` (confidence < 0.9 → require
        # confirmation). The server mirrors it so a malicious client
        # cannot set requiresConfirmation=false on a low-confidence
        # AI-produced proposal and auto-execute it.
        CONFIDENCE_THRESHOLD = 0.9

        result = validate_proposal_any(proposal)
        proposal_dict = proposal if isinstance(proposal, dict) else {}

        if result["contractVersion"] == 2:
            if not result["valid"]:
                return {
                    "valid": False,
                    "errors": result["errors"],
                    "verdict": "rejected_schema",
                }
            confidence = proposal_dict.get("confidence")
            if isinstance(confidence, (int, float)) and confidence < CONFIDENCE_THRESHOLD:
                return {
                    "valid": True,
                    "errors": [],
                    "verdict": "rejected_low_confidence",
                }
            return {"valid": True, "errors": [], "verdict": "accepted"}

        if not result["valid"]:
            return {
                "valid": False,
                "errors": result["errors"],
                "verdict": "rejected_schema",
            }

        # Context-based checks. ``proposal`` is schema-valid here, so we
        # can trust its shape (typed access is safe).
        hallucination_errors: list[dict[str, str]] = []
        transactions = proposal_dict.get("transactions") or []
        for i, tx in enumerate(transactions):
            cat_id = tx.get("categoryId") if isinstance(tx, dict) else None
            # R-M4-64 fix: 零分类用户（ctx.category_ids 为空）时，允许
            # categoryId 缺失或为空 — 交易可不带分类；非空但不在集合中仍视为幻觉
            if not cat_id or cat_id == "":
                if ctx.category_ids:
                    hallucination_errors.append({
                        "path": f"transactions[{i}].categoryId",
                        "message": "categoryId is required when validation context is available",
                    })
            elif cat_id not in ctx.category_ids:
                hallucination_errors.append({
                    "path": f"transactions[{i}].categoryId",
                    "message": "category is unresolved or not authorized",
                })
            acc_id = tx.get("accountId") if isinstance(tx, dict) else None
            if acc_id is not None and acc_id != "" and acc_id not in ctx.account_ids:
                hallucination_errors.append({
                    "path": f"transactions[{i}].accountId",
                    "message": "account is not authorized",
                })
        if hallucination_errors:
            return {
                "valid": False,
                "errors": hallucination_errors,
                "verdict": "rejected_hallucination",
            }

        # Low-confidence gate — not an error per se, but the server
        # refuses to bless it as auto-executable.
        confidence = proposal_dict.get("confidence")
        if isinstance(confidence, (int, float)) and confidence < CONFIDENCE_THRESHOLD:
            return {
                "valid": True,
                "errors": [],
                "verdict": "rejected_low_confidence",
            }

        return {"valid": True, "errors": [], "verdict": "accepted"}

    async def execute_proposal(
        self,
        user: User,
        request_id: str,
        proposal: dict,
        book_id: uuid.UUID,
        confirmation_token: str,
    ) -> dict:
        """Execute a validated ActionProposal.

        Security:
        1. Re-validate the proposal (TOCTOU prevention — the client may have
           modified it after /ai/validate).
        2. Check verdict: "accepted" → auto-execute; "rejected_low_confidence"
           → require confirmation_token; any other verdict → reject.
        3. Execute transactions atomically via TransactionService.

        Idempotency: The proposal's ``proposalId`` is used as the idempotency
        key (scoped per user). Re-executing the same proposalId returns the
        cached transaction IDs without creating duplicates. Concurrent
        duplicates are resolved via the unique constraint on
        ``(user_id, proposal_id)``.
        """
        from datetime import date as date_type

        from sqlalchemy.exc import IntegrityError

        from src.models.proposal import ProposalIdempotency
        from src.models.sync import SyncOperation
        from src.modules.sync.repository import SyncRepository
        from src.modules.transactions.service import TransactionService

        from .validator import validate_proposal_any

        proposal_id = proposal.get("proposalId")
        # 车道判定（纯函数、无副作用）：幂等命中与正常回执**都要**按它决定回执形状，
        # 故提到幂等检查之前——幂等命中若不带回卡片信息，重放那一次客户端手里又只剩
        # 一串 id（T20-1 的同一个坑，只是发生在第二次调用上）。
        lane = validate_proposal_any(proposal)["contractVersion"]

        # ── Idempotency check ────────────────────────────────────────────
        if proposal_id:
            existing = await self._db.scalar(
                select(ProposalIdempotency).where(
                    ProposalIdempotency.user_id == user.id,
                    ProposalIdempotency.proposal_id == proposal_id,
                )
            )
            if existing:
                logger.info(
                    "AI proposal idempotent hit",
                    extra={
                        "request_id": request_id,
                        "proposal_id": proposal_id,
                        "cached_transaction_count": len(existing.transaction_ids),
                    },
                )
                # 幂等命中不带 `before`：那次执行已经发生，此刻再去查「执行前」只会
                # 拿到被改过之后的值——宁可缺一个键，也不给一张假的「旧卡」。
                return _execute_receipt(
                    proposal, existing.transaction_ids, lane=lane,
                )

        # Re-validate
        ctx = await self._load_context(book_id, user.id)
        result = self.validate_proposal(proposal, ctx)
        verdict = result["verdict"]

        if verdict not in ("accepted", "rejected_low_confidence"):
            raise AppException(
                403, "ai_proposal_rejected",
                f"Proposal validation failed: {verdict}",
            )

        # Low confidence requires explicit confirmation
        if verdict == "rejected_low_confidence" and not confirmation_token:
            raise AppException(
                403, "ai_low_confidence_requires_confirmation",
                "Low-confidence proposals require explicit user confirmation",
            )

        # Extract action and transactions
        action = proposal.get("action")
        #: 对外回执里的 id 列表。**v1 车道是交易的 id**（字段名 ``transaction_ids``
        #: 即由此而来）；v2 车道是本次 op 影响的**实体** id（改 / 归档是同一个 id
        #: 的新版本，新建是新 id）——列名沿用是为了不动既有回执契约，语义见
        #: ``proposal_ops.OpReceipt``。
        transaction_ids: list[str] = []

        if lane == 2:
            # ── v2 车道的 payload 级授权（**所有** op，含记账）─────────────
            # 再跑一次（TOCTOU）：/ai/validate 之后 payload 可能被改过，而它正是
            # 执行要用的那一份（05a：结构通过不是执行许可）。
            #
            # **记账 op 必须一起过这道门**：它虽然走与 v1 共用的批量写路径，
            # 但那条路径只校验账本与分类的归属（``_verify_category_ownership``），
            # **账户 id 不校验**——v1 靠上下文对拍挡住账户，v2 的对拍就在这里。
            # 漏掉它等于放行「把别人的账户 id 写进自己的流水」。
            auth_errors = await authorize_payload(
                self._db,
                user_id=user.id,
                book_id=book_id,
                op=str(action),
                payload=proposal.get("payload"),
            )
            if auth_errors:
                raise AppException(
                    403, "ai_proposal_rejected",
                    "Proposal payload failed authorization",
                )

        # ── T20-1（REQ-77 决策 #4）：执行前抓「旧值」快照 ─────────────────
        # 只对改 / 删类 op。抓在这一层而不是执行器内部：执行权威在本函数，快照必须与
        # 「执行前」是同一个瞬间；交给执行器抓也行，但那样 `before` 得穿过 `OpReceipt`
        # 才能回到这里，多一层转手（且注册表面是 T20-3 的地盘）。
        before = (
            await self._snapshot_before(user, book_id, proposal.get("payload"))
            if lane == 2 and action in _BEFORE_SNAPSHOT_OPS
            else None
        )

        if lane == 2 and action != "transaction_add":
            # ── v2 车道：非记账 op（T-M7-05b）────────────────────────────
            receipt = await execute_op(
                self._db,
                user_id=user.id,
                book_id=book_id,
                op=str(action),
                payload=proposal.get("payload"),
            )
            transaction_ids = [str(entity_id) for entity_id in receipt.entity_ids]
        else:
            # ── v1 车道 + v2 的记账 op：同一条写路径 ─────────────────────
            if lane == 2:
                payload = proposal.get("payload")
                transactions = (payload or {}).get("transactions") or []
            else:
                if action not in ("add_tx", "add_tx_batch"):
                    raise AppException(
                        400, "ai_unsupported_action",
                        f"Action '{action}' is not executable via /ai/execute",
                    )
                transactions = proposal.get("transactions", [])

            if not transactions:
                raise AppException(400, "ai_no_transactions", "Proposal has no transactions")

            # Build transaction items
            items = []
            for tx in transactions:
                biz_date_str = tx.get("bizDate")
                try:
                    biz_date = date_type.fromisoformat(biz_date_str)
                except (ValueError, TypeError):
                    raise AppException(400, "ai_invalid_date", f"Invalid bizDate: {biz_date_str}")

                items.append({
                    "type": tx.get("type"),
                    "amount_cents": tx.get("amountCents"),
                    "category_id": uuid.UUID(tx["categoryId"]) if tx.get("categoryId") else None,
                    "account_id": uuid.UUID(tx["accountId"]) if tx.get("accountId") else None,
                    "note": tx.get("note", ""),
                    "biz_date": biz_date,
                })

            # T14-3（REQ-40 / 决策 #5）：任一行缺分类 → 落库前 400 拦截。
            # Transaction.category_id 是 NOT NULL，缺分类直达 create_transaction_batch
            # 会 IntegrityError → 500 英文（旧链路）。400 + code 可被前端映射中文。
            if any(item["category_id"] is None for item in items):
                raise AppException(
                    400, "ai_missing_category",
                    "有记录未识别到分类，请在卡片中选择分类后重试",
                )

            # Execute atomically
            tx_service = TransactionService(self._db)
            created = await tx_service.create_transaction_batch(
                user_id=user.id,
                book_id=book_id,
                items=items,
                source="ai",
            )
            transaction_ids = [str(tx.id) for tx in created]

            # T14-8 修复：execute 是服务端权威直写，只有 sync push 路径写
            # sync_changes——不在此登记则任何设备 pull 永远看不到这笔交易
            # （模拟器实测：确认卡显示「已记录」但本机查无）。与 push 的
            # _apply_mutation 同口径：insert / version=1，同事务提交。
            sync_repo = SyncRepository(self._db)
            for tx in created:
                await sync_repo.write_change(
                    user_id=user.id,
                    entity_type="transaction",
                    entity_id=tx.id,
                    operation=SyncOperation.INSERT,
                    version=tx.version,
                )
            # REQ-70：`create_transaction_batch` 已联动账户余额，被改的账户同样
            # 要对设备可见（与上面交易自身同一口径）。
            await register_balance_account_changes(
                self._db,
                user_id=user.id,
                account_ids=[tx.account_id for tx in created],
            )

        # ── Record idempotency ───────────────────────────────────────────
        if proposal_id:
            record = ProposalIdempotency(
                user_id=user.id,
                proposal_id=proposal_id,
                book_id=book_id,
                transaction_ids=transaction_ids,
            )
            self._db.add(record)
            try:
                await self._db.flush()
            except IntegrityError:
                # Concurrent duplicate — this transaction loses. Roll back
                # (discarding the duplicate transactions just created) and
                # return the winner's cached result.
                await self._db.rollback()
                existing = await self._db.scalar(
                    select(ProposalIdempotency).where(
                        ProposalIdempotency.user_id == user.id,
                        ProposalIdempotency.proposal_id == proposal_id,
                    )
                )
                if existing:
                    logger.info(
                        "AI proposal idempotent race resolved",
                        extra={
                            "request_id": request_id,
                            "proposal_id": proposal_id,
                        },
                    )
                    return {
                        "transaction_ids": existing.transaction_ids,
                        "proposal_id": proposal_id,
                        "undo_window_seconds": 5,
                    }
                raise  # Should not happen — unique constraint fired but no row found

        # ── T-M4-04e: Auto-link attachment to first transaction ────────
        # If the proposal originated from image parsing, meta.attachmentId
        # is set. Link the attachment to the first created transaction so
        # the receipt is associated with the batch.
        meta = proposal.get("meta") or {}
        attachment_id_str = meta.get("attachmentId") if isinstance(meta, dict) else None
        if attachment_id_str and transaction_ids:
            try:
                from src.modules.attachments.service import AttachmentService
                att_service = AttachmentService(self._db)
                await att_service.link_attachment(
                    uuid.UUID(attachment_id_str),
                    user.id,
                    uuid.UUID(transaction_ids[0]),
                )
                logger.info(
                    "Auto-linked attachment to transaction",
                    extra={
                        "request_id": request_id,
                        "attachment_id": attachment_id_str,
                        "transaction_id": transaction_ids[0],
                    },
                )
            except Exception as exc:
                # Best-effort: don't fail the batch if linking fails.
                # Server cleanup will expire the attachment after 24h.
                logger.warning(
                    "Auto-link failed (non-fatal)",
                    extra={
                        "request_id": request_id,
                        "attachment_id": attachment_id_str,
                        "error": str(exc),
                    },
                )

        logger.info(
            "AI proposal executed",
            extra={
                "request_id": request_id,
                "proposal_id": proposal_id,
                "action": action,
                "affected_count": len(transaction_ids),
                "verdict": verdict,
            },
        )

        return _execute_receipt(
            proposal, transaction_ids, lane=lane, before=before,
        )

    async def _snapshot_before(
        self,
        user: User,
        book_id: uuid.UUID,
        payload: Any,
    ) -> dict[str, Any] | None:
        """改 / 删类 op 的「旧值」快照（T20-1 / REQ-77 决策 #4）。

        字段集 = 卡片要显示的那几项（分类 / 收支 / 金额 / 日期 / 备注 / 账本），键名与
        payload 逐字对齐，卡上的新旧对照才能按字段比对。**账户在决策点名的六项之外，
        这里照样带上**：卡的展示口径含账户 chip（`SnapshotCard`），不带的话「有账户」
        会显示成「不使用账户」——少一个键就是一次错误的显示。

        **取不到就返回 `None`，本函数从不报错**：payload 形状不对 / 不是本人的 / 不在
        本账本，这三件事执行器马上要用同一份 payload 再判一次，在这里抢先报只会让同一个
        错误有两个出口、两套文案。快照是锦上添花——缺了它卡上少两行，执行该怎么报错
        还怎么报错。
        """
        from src.modules.transactions.service import TransactionService

        if not isinstance(payload, dict):
            return None
        try:
            tx_id = uuid.UUID(str(payload.get("transactionId")))
        except (ValueError, AttributeError, TypeError):
            return None
        try:
            tx = await TransactionService(self._db).get_transaction(tx_id, user.id)
        except AppException:
            return None
        if tx.book_id != book_id:
            return None
        return {
            # ``TxType`` 是 ``(str, Enum)``：``str(tx.type)`` 会给出 ``"TxType.EXPENSE"``，
            # 必须取 ``.value``（卡上要显示的是 "expense"，对照才比得中）。
            "type": str(getattr(tx.type, "value", tx.type)),
            "amountCents": tx.amount_cents,
            "categoryId": str(tx.category_id),
            "accountId": str(tx.account_id) if tx.account_id else None,
            "note": tx.note,
            "bizDate": tx.biz_date.isoformat(),
            "bookId": str(tx.book_id),
        }

    async def _load_context(
        self, book_id: uuid.UUID, owner_id: uuid.UUID,
    ) -> ProposalValidationContext:
        """Load validation context from DB."""
        from .context import load_validation_context
        return await load_validation_context(self._db, owner_id, book_id)

    # -- T-M4-03: report generation -------------------------------------------

    async def generate_report(
        self, user: User, request_id: str, req: AIReportRequest,
    ) -> AIReportResponse:
        """Build a fact pack and optionally generate an LLM narrative.

        Flow:
        1. Build deterministic FactPack (T-M4-03a).
        2. If provider is available and data hash is new, call LLM for narrative.
        3. Verify all numbers in the narrative against the fact pack.
        4. Return the combined response.
        """
        from .fact_pack import FactPackService

        fp_service = FactPackService(self._db)
        # Parse book_id — normalize through UUID so both cache get (below)
        # and cache set (_generate_narrative → fact_pack.book_id) use the
        # same lowercase canonical form regardless of how the client
        # capitalizes the input (R-M4-41).
        try:
            book_id = uuid.UUID(req.book_id)
        except ValueError:
            raise AppException(422, "invalid_book_id", "Invalid book ID")
        normalized_book_id = str(book_id)

        fact_pack = await fp_service.build(book_id, user.id, req.period)

        # Build the base response
        response = AIReportResponse(
            request_id=request_id,
            # R-M4-41: echo back the canonical lowercase form so clients
            # that sent mixed-case get a consistent value back.
            book_id=normalized_book_id,
            period=req.period,
            data_hash=fact_pack.data_hash,
            total_income=fact_pack.total_income,
            total_expense=fact_pack.total_expense,
            balance=fact_pack.balance,
            transaction_count=fact_pack.transaction_count,
            expense_by_category=[
                CategoryFactSchema(
                    category_id=c.category_id,
                    category_name=c.category_name,
                    total_cents=c.total_cents,
                    count=c.count,
                ) for c in fact_pack.expense_by_category
            ],
            income_by_category=[
                CategoryFactSchema(
                    category_id=c.category_id,
                    category_name=c.category_name,
                    total_cents=c.total_cents,
                    count=c.count,
                ) for c in fact_pack.income_by_category
            ],
            budget=BudgetFactSchema(
                amount_cents=b.amount_cents,
                spent_cents=b.spent_cents,
                remaining_cents=b.remaining_cents,
                pct_used=b.pct_used,
            ) if (b := fact_pack.budget) is not None else None,
            largest_expense=TransactionFactSchema(
                id=t.id,
                amount_cents=t.amount_cents,
                category_name=t.category_name,
                note=t.note,
                biz_date=t.biz_date,
            ) if (t := fact_pack.largest_expense) is not None else None,
            largest_income=TransactionFactSchema(
                id=t.id,
                amount_cents=t.amount_cents,
                category_name=t.category_name,
                note=t.note,
                biz_date=t.biz_date,
            ) if (t := fact_pack.largest_income) is not None else None,
            trend=[
                TrendFactSchema(
                    period=t.period,
                    total_income=t.total_income,
                    total_expense=t.total_expense,
                    balance=t.balance,
                    transaction_count=t.transaction_count,
                ) for t in fact_pack.trend
            ],
        )

        # Try to generate narrative if provider is available
        # T13-2 (REQ-46 / 决策 #5): 0 笔期间数据没有可叙述的事实，跳过
        # provider 调用省 token——空数据由前端 showSyncHint 分支承接。
        if self._provider is not None and fact_pack.transaction_count > 0:
            # Check narrative cache first (R-M4-09 fix)
            # R-M4-41: use the canonical (lowercase) book_id for the cache
            # key so that differently-cased inputs hit the same entry.
            cache_key = _narrative_cache_key(normalized_book_id, req.period, fact_pack.data_hash)
            cached_narrative = _narrative_cache_get(cache_key)
            if cached_narrative is not None:
                # Cache hit: use cached narrative, no LLM call, no billing
                response.narrative = cached_narrative
                response.narrative_cached = True
                logger.info(
                    "AI report narrative served from cache",
                    extra={
                        "request_id": request_id,
                        "book_id": normalized_book_id,
                        "period": req.period,
                        "data_hash": fact_pack.data_hash,
                    },
                )
            else:
                # Cache miss: generate new narrative
                await self._generate_narrative(user, request_id, fact_pack, response)

        return response

    async def _generate_narrative(
        self,
        user: User,
        request_id: str,
        fact_pack: FactPack,
        response: AIReportResponse,
    ) -> None:
        """Call the LLM to generate a narrative from the fact pack.

        If the LLM output fails number verification, the narrative is
        discarded (response stays fact-pack-only) and a security event is
        logged.
        """
        from .narrative import build_context_dict, verify_narrative

        assert self._provider is not None

        tpl = get_template(REPORT_TASK)
        # P3#13 (corrected): the limit checks RAISE 429 — previously that
        # propagated out of generate_report and failed the whole /ai/report
        # request, even though the fact pack is deterministic and needs no
        # LLM. Degrade instead: skip the narrative, keep the fact pack, and
        # log INFO so ops can tell "limited" from "provider failed".
        try:
            self._check_rate_limit(user.id)
            await self._check_daily_quota(user)
        except AppException as exc:
            if exc.status_code != 429:
                raise
            logger.info(
                "AI report narrative skipped (rate limit / quota)",
                extra={
                    "request_id": request_id,
                    "ai_task": tpl.task_id,
                    "outcome": exc.code,
                },
            )
            return

        model = tpl.model or self._cfg.ai_provider_model
        # Build context from the fact pack — this is the ONLY data the LLM sees
        context = build_context_dict(fact_pack)
        context_str = json.dumps(context, ensure_ascii=False, indent=2)
        user_input = (
            f"请根据以下我{fact_pack.period}月度的财务数据生成月度总结。\n\n"
            f"事实数据：\n```json\n{context_str}\n```"
        )
        messages = render_messages(tpl, user_input)
        started = time.perf_counter()

        result: ProviderResult | None = None
        output: dict | None = None
        # R-M4-43: tokens consumed by earlier attempts that reached the
        # provider but produced unusable output. Accumulated after each
        # failed parse so retry_* includes all failed attempts (P2-1
        # semantics — same as interpret/parse_image, no subtraction).
        retry_prompt_tokens = 0
        retry_completion_tokens = 0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                result = await self._provider.complete(
                    messages,
                    model=model,
                    max_tokens=tpl.max_tokens,
                    timeout=self._cfg.ai_timeout_seconds,
                )
            except ProviderError as exc:
                if not exc.retryable or attempt >= MAX_ATTEMPTS:
                    # P1-1: record tokens from earlier attempts that
                    # reached the provider (billed but previously lost).
                    await self._record_failure_usage(
                        user, model, request_id,
                        extra_prompt_tokens=retry_prompt_tokens,
                        extra_completion_tokens=retry_completion_tokens,
                    )
                    self._log_failure(request_id, tpl, model, attempt, exc.reason, started)
                    return  # No narrative, no provider result → no usage to record
                continue

            try:
                output = self._parse_json_output(result.content)
                break
            except ValueError:
                retry_prompt_tokens += result.prompt_tokens
                retry_completion_tokens += result.completion_tokens
                if attempt >= MAX_ATTEMPTS:
                    # P1-1: retry_* now includes ALL failed attempts
                    # (this one included). Record them all — the provider
                    # billed every attempt regardless of parse outcome.
                    await self._record_failure_usage(
                        user, model, request_id,
                        extra_prompt_tokens=retry_prompt_tokens,
                        extra_completion_tokens=retry_completion_tokens,
                    )
                    self._log_failure(
                        request_id, tpl, model, attempt, "invalid_json", started,
                    )
                    return
                continue

        assert result is not None and output is not None

        # R-M4-10 fix: record usage immediately after successful provider call.
        # Even if verification fails, tokens were consumed and must be tracked.
        # R-M4-43: extra_*_tokens carry the wasted tokens from earlier attempts.
        await self._record_usage(
            user, result, model, request_id,
            extra_prompt_tokens=retry_prompt_tokens,
            extra_completion_tokens=retry_completion_tokens,
        )
        self._log_success(request_id, tpl, model, result, started)
        self._log_slow_if_needed(request_id, tpl, model, started)
        record_ai_gateway(
            status="200",
            degraded=False,
            provider=self._provider.name,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            input_tokens=result.prompt_tokens + retry_prompt_tokens,
            output_tokens=result.completion_tokens + retry_completion_tokens,
        )

        # Number verification
        errors = verify_narrative(output, fact_pack)
        if errors:
            logger.warning(
                "AI report narrative failed number verification",
                extra={
                    "request_id": request_id,
                    "ai_task": tpl.task_id,
                    "ai_model": model,
                    "ai_provider": self._provider.name,
                    "outcome": "number_verification_failed",
                    "errors": errors,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            # Usage already recorded above; discard narrative only
            return

        narrative = output.get("narrative", "")
        response.narrative = narrative
        response.narrative_cached = False
        response.usage = AIUsageInfo(
            prompt_tokens=result.prompt_tokens + retry_prompt_tokens,
            completion_tokens=result.completion_tokens + retry_completion_tokens,
        )

        # Store in narrative cache for future requests with same data (R-M4-09 fix)
        cache_key = _narrative_cache_key(fact_pack.book_id, fact_pack.period, fact_pack.data_hash)
        _narrative_cache_set(cache_key, narrative)

    # -- T-M4-04e: image receipt parsing ------------------------------------

    async def parse_image(
        self,
        user: User,
        request_id: str,
        attachment_id: uuid.UUID,
        book_id: uuid.UUID,
    ) -> AIParseImageResponse:
        """Parse a receipt/bill image into an ActionProposal.

        Flow:
        1. Verify attachment ownership + status = PENDING.
        2. Read image file → base64 data URL.
        3. Send to vision model with IMAGE_RECEIPT_PARSE prompt.
        4. Parse LLM output → resolve category names to IDs.
        5. Update attachment status → PARSED.
        6. Return complete ActionProposal.
        """
        import base64

        from src.models.attachment import AttachmentStatus
        from src.modules.attachments.service import AttachmentService, _get_upload_dir

        from .prompts import IMAGE_PARSE_TASK, get_template, render_image_messages

        tpl = get_template(IMAGE_PARSE_TASK)
        self._check_rate_limit(user.id)
        await self._check_daily_quota(user)

        if self._provider is None:
            self._log_failure(request_id, tpl, None, 0, "no_provider", None)
            raise AppException(
                503, "ai_unavailable", "AI service is temporarily unavailable",
            )

        # -- Load & verify attachment --
        att_service = AttachmentService(self._db)
        attachment = await att_service.get_attachment(attachment_id, user.id)
        if attachment.status == AttachmentStatus.EXPIRED:
            raise AppException(410, "attachment_expired", "Attachment has expired")
        # NEW-R6-1 fix: allow re-parsing PARSED attachments (e.g. zero-tx
        # retry or user wants to re-recognise).  Reset to PENDING first so
        # a mid-parse failure leaves the attachment in a retryable state.
        if attachment.status not in (AttachmentStatus.PENDING, AttachmentStatus.PARSED):
            status_str = attachment.status.value if hasattr(attachment.status, 'value') else str(attachment.status)
            raise AppException(
                409, "attachment_not_pending",
                f"Attachment status is '{status_str}', expected 'pending' or 'parsed'",
            )
        if attachment.status == AttachmentStatus.PARSED:
            attachment.status = AttachmentStatus.PENDING
            await self._db.flush()

        # -- Read image file --
        upload_dir = _get_upload_dir()
        file_path = upload_dir / attachment.object_key
        if not file_path.exists():
            raise AppException(404, "file_missing", "Attachment file is missing on disk")

        image_bytes = file_path.read_bytes()
        max_bytes = self._cfg.ai_image_max_bytes
        if max_bytes > 0 and len(image_bytes) > max_bytes:
            # Skip vision call for oversized images — the model would
            # reject or downscale anyway; better to fail fast.
            raise AppException(
                413, "image_too_large",
                f"Image {len(image_bytes)} bytes exceeds vision limit {max_bytes}",
            )
        if max_bytes == 0:
            raise AppException(
                503, "image_parsing_disabled",
                "Image parsing is disabled (ai_image_max_bytes=0)",
            )

        # -- Build data URL --
        mime = attachment.mime_type or "image/jpeg"
        b64 = base64.b64encode(image_bytes).decode("ascii")
        data_url = f"data:{mime};base64,{b64}"

        # -- NEW-E4 fix: in-process concurrent parse dedup --
        att_key = str(attachment_id)
        if att_key in _parsing_attachments:
            raise AppException(
                409, "parse_in_progress",
                "This attachment is already being parsed",
            )
        _parsing_attachments.add(att_key)

        # -- Call vision model --
        model = self._cfg.ai_vision_model
        # T14-3（REQ-40 / 决策 #5）：分类白名单在 server 侧注入——数据源与下方
        # _resolve_categories 的 ctx.category_names 同一（该账本分类表），
        # 客户端不上行分类；vision 只能在列表内选，对不上输出 ""。
        from .context import load_validation_context
        ctx = await load_validation_context(self._db, user.id, book_id)
        messages = render_image_messages(
            tpl, data_url, categories=list(ctx.category_names.keys()),
        )
        started = time.perf_counter()

        result: ProviderResult | None = None
        output: dict | None = None
        retry_prompt_tokens = 0
        retry_completion_tokens = 0

        try:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    result = await self._provider.complete_with_images(
                        messages,
                        model=model,
                        max_tokens=tpl.max_tokens,
                        timeout=self._cfg.ai_timeout_seconds,
                    )
                except ProviderError as exc:
                    if not exc.retryable or attempt >= MAX_ATTEMPTS:
                        # P1-1: record tokens from earlier attempts that
                        # reached the provider (billed but previously lost).
                        await self._record_failure_usage(
                            user, model, request_id,
                            extra_prompt_tokens=retry_prompt_tokens,
                            extra_completion_tokens=retry_completion_tokens,
                        )
                        self._log_failure(request_id, tpl, model, attempt, exc.reason, started)
                        raise AppException(
                            503, "ai_unavailable", "AI service is temporarily unavailable",
                        )
                    continue

                try:
                    output = self._parse_json_output(result.content)
                    break
                except ValueError:
                    retry_prompt_tokens += result.prompt_tokens
                    retry_completion_tokens += result.completion_tokens
                    if attempt >= MAX_ATTEMPTS:
                        # P1-1: the final attempt consumed tokens too
                        # (retry_*_tokens includes this attempt).
                        await self._record_failure_usage(
                            user, model, request_id,
                            extra_prompt_tokens=retry_prompt_tokens,
                            extra_completion_tokens=retry_completion_tokens,
                        )
                        self._log_failure(request_id, tpl, model, attempt, "invalid_json", started)
                        raise AppException(
                            503, "ai_invalid_output",
                            "AI service is temporarily unavailable",
                        )
                    continue

            assert result is not None and output is not None

            # -- Record usage --
            await self._record_usage(
                user, result, model, request_id,
                extra_prompt_tokens=retry_prompt_tokens,
                extra_completion_tokens=retry_completion_tokens,
            )
            self._log_success(request_id, tpl, model, result, started)
            self._log_slow_if_needed(request_id, tpl, model, started)
            record_ai_gateway(
                status="200",
                degraded=False,
                provider=self._provider.name,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                input_tokens=result.prompt_tokens + retry_prompt_tokens,
                output_tokens=result.completion_tokens + retry_completion_tokens,
            )

            # -- Extract transactions from LLM output --
            raw_transactions = output.get("transactions") or []
            confidence = output.get("confidence", 0)
            # T18-9：幂等键**必须由服务端生成**，不采信模型输出。
            # LLM 无法真正生成随机 UUID——实测它会照抄训练数据里的示例值
            # （``123e4567-e89b-12d3-a456-426614174000``）、也曾产出含 g/h/i/j
            # 这类非十六进制字符的伪 UUID。撞上历史幂等键时，「确认」会变成
            # 静默丢弃（``execute_proposal`` 命中缓存直接返回，用户看不到任何
            # 反馈，T18-9 真机复现）。与 agent 路径同款原则：幂等键不接受
            # 外部输入（见 ``tests/test_ai_agent_approval.py``）。
            proposal_id = f"img-{uuid.uuid4()}"

            if not isinstance(confidence, (int, float)):
                confidence = 0
            confidence = float(max(0, min(1, confidence)))

            # -- Resolve category names to IDs --
            # T14-3: ctx 已在构造 messages 前加载（白名单同源复用）
            resolved_txs = self._resolve_categories(
                raw_transactions, ctx, user.timezone,
            )

            # -- Build complete ActionProposal --
            # T-M4-04e: meta.attachmentId lets the client link transactions
            # to this attachment after executing the batch.
            proposal = {
                "action": "add_tx_batch",
                "proposalId": proposal_id,
                "message": f"图片识别到 {len(resolved_txs)} 笔交易",
                "confidence": confidence,
                "transactions": resolved_txs,
                "warnings": [],
                "requiresConfirmation": True,
                "meta": {
                    "source": "image_parse",
                    "attachmentId": str(attachment_id),
                },
            }

            # NEW-R6-1 fix: always mark PARSED, even for zero transactions.
            # This prevents the attachment from expiring (24h cleanup),
            # so the user can retry later without re-uploading.
            attachment.status = AttachmentStatus.PARSED
            attachment.expires_at = None  # parsed attachments don't expire
            await self._db.flush()

            logger.info(
                "AI image parse ok",
                extra={
                    "request_id": request_id,
                    "attachment_id": str(attachment_id),
                    "transaction_count": len(resolved_txs),
                    "confidence": confidence,
                },
            )

            return AIParseImageResponse(
                request_id=request_id,
                proposal=proposal,
                attachment_id=str(attachment_id),
                confidence=confidence,
                usage=AIUsageInfo(
                    prompt_tokens=result.prompt_tokens + retry_prompt_tokens,
                    completion_tokens=result.completion_tokens + retry_completion_tokens,
                ),
            )
        finally:
            # NEW-E4: always release the in-process parse lock
            _parsing_attachments.discard(att_key)

    @staticmethod
    def _resolve_categories(
        raw_transactions: list,
        ctx: "ProposalValidationContext",
        tz_name: str = "UTC",
    ) -> list[dict]:
        """Resolve category names from LLM output to category IDs.

        Uses exact matching first, then prefix fallback. T14-3（REQ-40 /
        决策 #5「宁缺勿错」）: unresolved categories omit the
        ``categoryId`` key entirely (no first-category fallback) — the
        client surfaces an in-card category picker and /ai/execute rejects
        missing categories with 400 ``ai_missing_category`` before insert.
        Empty ``bizDate`` falls back to today in the user's timezone
        (``format: date`` required).
        """
        if not raw_transactions:
            return []

        # Build name→id lookup (exact match first)
        name_to_id: dict[str, str] = {}
        for cat_name, cat_id in ctx.category_names.items():
            name_to_id[cat_name] = cat_id
            # Also add common aliases (strip whitespace)
            name_to_id[cat_name.strip()] = cat_id

        # R-M4-52 fix: today in the user's timezone — same day boundary as
        # the quota system (local_day_start_utc). A UTC fallback would date
        # early-morning entries one day back for UTC+ users.
        try:
            tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            tz = ZoneInfo("UTC")
        today_str = datetime.now(tz).date().isoformat()

        resolved = []
        for tx in raw_transactions:
            if not isinstance(tx, dict):
                continue
            cat_name = str(tx.get("category", "")).strip()
            cat_id = name_to_id.get(cat_name, "")

            # Fuzzy fallback: prefix match
            if not cat_id and cat_name:
                for name, cid in name_to_id.items():
                    if name.startswith(cat_name) or cat_name.startswith(name):
                        cat_id = cid
                        break

            # R-M4-52 fix: empty bizDate → today (schema format: date)
            biz_date = str(tx.get("bizDate", "")).strip()
            # NEW-E2 fix: non-empty but malformed bizDate → also fall back to today
            if not biz_date or not re.match(r"^\d{4}-\d{2}-\d{2}$", biz_date):
                biz_date = today_str

            # NEW-E1 fix: validate amountCents — skip transactions with
            # zero / non-numeric / negative amounts (schema minimum: 1)
            raw_amount = tx.get("amountCents", 0)
            try:
                amount_cents = int(raw_amount)
            except (TypeError, ValueError):
                logger.warning(
                    "Skipping transaction with invalid amountCents: %r",
                    raw_amount,
                )
                continue
            if amount_cents < 1:
                logger.warning(
                    "Skipping transaction with amountCents < 1: %d",
                    amount_cents,
                )
                continue

            # NEW-E3 fix: normalize type to enum — LLM may produce "refund" etc.
            tx_type = tx.get("type", "expense")
            if tx_type not in ("expense", "income"):
                tx_type = "expense"

            tx_dict: dict = {
                "type": tx_type,
                "amountCents": amount_cents,
                "note": str(tx.get("note", "")),
                "bizDate": biz_date,
            }
            # T14-3（决策 #5）：未匹配到分类 → 省略 categoryId 键（宁缺勿错，
            # schema 非 required；卡片引导手选，/ai/execute 落库前 400 拦截）
            if cat_id:
                tx_dict["categoryId"] = cat_id
            resolved.append(tx_dict)
        return resolved
