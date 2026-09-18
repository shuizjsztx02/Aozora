"""T-M4-01: LLM provider adapter.

The server is the only holder of provider keys; clients never call model
providers directly (docs/architecture/ai-architecture.md). First release:
Alibaba DashScope in OpenAI-compatible mode with model ``qwen3.7-flash``
(D-PROD-05).

T-M7-02b adds a second, coexisting provider for the agent path: DeepSeek
``deepseek-flash`` (ADR-007), which brings tool calling, streaming and the
three-tier reasoning budget the fast path's model cannot express. The fast
path keeps its own provider unchanged.

Security: provider errors are reduced to enumerated ``reason`` strings.
Provider response bodies may echo user input, so they must never reach
API responses or logs (docs/architecture/security.md).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, AsyncIterator, Protocol, cast

import httpx

from src.common.config import Settings, get_config

logger = logging.getLogger(__name__)

# Fallback client-level timeout. ``complete()`` always passes an explicit
# per-request timeout (from Settings.ai_timeout_seconds), so this default only
# guards a hypothetical call that omits it.
_DEFAULT_CLIENT_TIMEOUT = 30.0


class ProviderError(Exception):
    """Provider call failed.

    ``reason`` is an enum-like safe string (``http_429`` | ``http_5xx`` |
    ``http_4xx`` | ``timeout`` | ``transport`` | ``bad_response``) —
    never a provider message or response body.
    """

    def __init__(self, *, status: int | None, retryable: bool, reason: str) -> None:
        self.status = status
        self.retryable = retryable
        self.reason = reason
        super().__init__(f"provider error: {reason}")


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One tool call emitted by the model (T-M7-02b).

    ``arguments`` is the provider's raw JSON *text*, kept verbatim rather
    than decoded here: a malformed payload is a model-output problem, not a
    transport one, and the agent loop has to see it to report it back to the
    model instead of losing the step. Call :meth:`parse_arguments` at the
    point of use.
    """

    id: str
    name: str
    arguments: str

    def parse_arguments(self) -> dict[str, Any]:
        """Decode ``arguments`` into a mapping.

        Raises ``ValueError`` (``json.JSONDecodeError`` included) when the
        text is not a JSON object — the loop turns that into a tool-result
        error so the model can retry with valid arguments.
        """
        decoded = json.loads(self.arguments or "{}")
        if not isinstance(decoded, dict):
            raise ValueError("tool call arguments must be a JSON object")
        return decoded


@dataclass(frozen=True, slots=True)
class ProviderResult:
    content: str
    prompt_tokens: int
    completion_tokens: int
    # T-M7-02b: agent-path additions. Both default to empty so the fast
    # path's ``ProviderResult(content, prompt, completion)`` construction is
    # unchanged.
    tool_calls: tuple[ToolCall, ...] = ()
    # Thinking models return their reasoning separately from ``content``.
    # DeepSeek requires it echoed back on later turns of a tool-calling
    # conversation to keep the reasoning chain continuous (ADR-007), so the
    # agent loop must be able to persist it — hence a field, not a discard.
    reasoning_content: str = ""


@dataclass(frozen=True, slots=True)
class StreamChunk:
    """One increment of a streaming completion (T-M7-02b).

    ``delta`` carries assistant-visible text. ``result`` is set exactly once,
    on the final chunk, holding the fully assembled ``ProviderResult`` (text,
    tool calls and usage all merged from the fragments).
    """

    delta: str = ""
    result: ProviderResult | None = None


class ReasoningLevel(StrEnum):
    """Reasoning budget tiers (ADR-007).

    Replaces the fast path's boolean ``enable_thinking``. ``deepseek-flash``
    thinks *by default*, so ``FAST`` must send an explicit ``disabled`` —
    omitting the parameter is not the same as turning thinking off.
    """

    FAST = "fast"
    MEDIUM = "medium"
    DEEP = "deep"


#: Tier → request-body parameters. Medium/deep enable thinking with a
#: ``reasoning_effort`` of ``low``/``high``; fast sends no effort at all
#: (the provider rejects an effort alongside ``disabled``).
_REASONING_PARAMS: dict[ReasoningLevel, dict[str, Any]] = {
    ReasoningLevel.FAST: {"thinking": {"type": "disabled"}},
    ReasoningLevel.MEDIUM: {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "low",
    },
    ReasoningLevel.DEEP: {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
    },
}


class LLMProvider(Protocol):
    name: str

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        max_tokens: int,
        timeout: float,
    ) -> ProviderResult: ...

    async def complete_with_images(
        self,
        messages: list[dict],
        *,
        model: str,
        max_tokens: int,
        timeout: float,
    ) -> ProviderResult: ...


class ToolCallingProvider(Protocol):
    """The extra surface the agent path depends on (T-M7-02b, ADR-007).

    Kept separate from :class:`LLMProvider` on purpose: only the loop needs
    tool calling, so the fast path's provider is neither required nor
    expected to implement it.
    """

    name: str

    async def complete_with_tools(
        self,
        messages: list[dict],
        *,
        model: str,
        max_tokens: int,
        timeout: float,
        tools: list[dict],
        tool_choice: str | None = None,
        reasoning: ReasoningLevel = ReasoningLevel.FAST,
    ) -> ProviderResult: ...

    def complete_with_tools_stream(
        self,
        messages: list[dict],
        *,
        model: str,
        max_tokens: int,
        timeout: float,
        tools: list[dict],
        tool_choice: str | None = None,
        reasoning: ReasoningLevel = ReasoningLevel.FAST,
    ) -> AsyncIterator[StreamChunk]: ...


class DashScopeProvider:
    """OpenAI-compatible ``POST {base_url}/chat/completions``."""

    name = "dashscope"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._url = base_url.rstrip("/") + "/chat/completions"
        # P1#1: reuse a single AsyncClient so TCP/TLS connections are pooled
        # across calls (no per-request handshake, no TIME_WAIT churn). The
        # per-call timeout is passed to ``post()`` and overrides this default.
        # ``transport`` is the test injection point (MockTransport).
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(_DEFAULT_CLIENT_TIMEOUT),
            transport=transport,
        )

    async def aclose(self) -> None:
        """Close the pooled HTTP client (called on app shutdown)."""
        await self._client.aclose()

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        max_tokens: int,
        timeout: float,
    ) -> ProviderResult:
        # T12-11 measurement: qwen3.7-flash thinks by default — edit/delete/
        # query multi-field outputs took 20-24 s, blowing the ai_timeout_seconds
        # budget (10 s, le=12) and silently degrading to canned. These are
        # structured-output tasks; disable thinking for them. DashScope
        # OpenAI-compatible mode accepts ``enable_thinking`` as a top-level
        # body param for hybrid-thinking models.
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "enable_thinking": False,
        }
        try:
            resp = await self._client.post(
                self._url,
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=timeout,
            )
        except httpx.TimeoutException:
            # Timeouts are NOT retryable: the client cancels at 25 s
            # (APP-CONTEXT §6.4), so retrying a slow provider only
            # stacks latency and burns tokens.
            raise ProviderError(status=None, retryable=False, reason="timeout")
        except httpx.HTTPError:
            raise ProviderError(status=None, retryable=True, reason="transport")

        if resp.status_code != 200:
            if resp.status_code == 429:
                raise ProviderError(status=429, retryable=True, reason="http_429")
            if resp.status_code >= 500:
                raise ProviderError(
                    status=resp.status_code, retryable=True, reason="http_5xx",
                )
            # 4xx (bad key, invalid request) — retrying cannot help.
            raise ProviderError(
                status=resp.status_code, retryable=False, reason="http_4xx",
            )

        try:
            body = resp.json()
            content = body["choices"][0]["message"]["content"]
            # R-M4-40: OpenAI-compatible providers return ``"content": null``
            # when a content filter fires, on refusal, or at ``finish_reason=length``.
            # Without this check the None reaches ``_parse_json_output(None)``
            # and crashes with ``AttributeError`` (``None.strip()``), which
            # is neither caught by the retry loop nor mapped to the §6.4
            # degradation contract. Treat it as a retryable bad response so
            # the standard 1-retry-then-503 path handles it correctly.
            if not isinstance(content, str):
                raise ProviderError(status=200, retryable=True, reason="bad_response")
            usage = body.get("usage") or {}
            return ProviderResult(
                content=content,
                prompt_tokens=int(usage.get("prompt_tokens", 0)),
                completion_tokens=int(usage.get("completion_tokens", 0)),
            )
        except (ValueError, KeyError, IndexError, TypeError):
            # Malformed 200 body — treat as retryable provider fault.
            raise ProviderError(status=200, retryable=True, reason="bad_response")

    async def complete_with_images(
        self,
        messages: list[dict],
        *,
        model: str,
        max_tokens: int,
        timeout: float,
    ) -> ProviderResult:
        """Multimodal completion — messages may contain image_url content parts.

        T-M4-04e: receipt/bill image parsing uses a vision model. The
        OpenAI-compatible format accepts ``content`` as a list of parts::

            [{"type": "text", "text": "..."},
             {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}]

        Same endpoint (``/chat/completions``), same error handling as
        text ``complete()``.
        """
        payload = {"model": model, "messages": messages, "max_tokens": max_tokens}
        try:
            resp = await self._client.post(
                self._url,
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=timeout,
            )
        except httpx.TimeoutException:
            raise ProviderError(status=None, retryable=False, reason="timeout")
        except httpx.HTTPError:
            raise ProviderError(status=None, retryable=True, reason="transport")

        if resp.status_code != 200:
            if resp.status_code == 429:
                raise ProviderError(status=429, retryable=True, reason="http_429")
            if resp.status_code >= 500:
                raise ProviderError(
                    status=resp.status_code, retryable=True, reason="http_5xx",
                )
            raise ProviderError(
                status=resp.status_code, retryable=False, reason="http_4xx",
            )

        try:
            body = resp.json()
            content = body["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ProviderError(status=200, retryable=True, reason="bad_response")
            usage = body.get("usage") or {}
            return ProviderResult(
                content=content,
                prompt_tokens=int(usage.get("prompt_tokens", 0)),
                completion_tokens=int(usage.get("completion_tokens", 0)),
            )
        except (ValueError, KeyError, IndexError, TypeError):
            raise ProviderError(status=200, retryable=True, reason="bad_response")


# ---------------------------------------------------------------------------
# T-M7-02b: agent-path provider (DeepSeek, ADR-007)
# ---------------------------------------------------------------------------


def _raise_for_status(resp: httpx.Response) -> None:
    """Map a non-200 response onto the enumerated ``ProviderError``."""
    if resp.status_code == 200:
        return
    if resp.status_code == 429:
        raise ProviderError(status=429, retryable=True, reason="http_429")
    if resp.status_code >= 500:
        raise ProviderError(
            status=resp.status_code, retryable=True, reason="http_5xx",
        )
    # 4xx (bad key, invalid request) — retrying cannot help.
    raise ProviderError(status=resp.status_code, retryable=False, reason="http_4xx")


def _parse_tool_calls(raw: Any) -> tuple[ToolCall, ...]:
    """Decode the ``tool_calls`` array, keeping ``arguments`` as raw text.

    The tool *name* is deliberately not validated against any registry —
    this layer does not own the tool list. An unknown or empty name is
    passed through so the loop can answer it with a tool-result error, which
    is a recoverable step rather than a failed request.
    """
    if not raw:
        return ()
    if not isinstance(raw, list):
        raise ProviderError(status=200, retryable=True, reason="bad_response")
    calls: list[ToolCall] = []
    for item in raw:
        function = item.get("function") if isinstance(item, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if not isinstance(name, str) or not name:
            raise ProviderError(status=200, retryable=True, reason="bad_response")
        arguments = function.get("arguments")
        if arguments is None:
            arguments = ""
        if not isinstance(arguments, str):
            raise ProviderError(status=200, retryable=True, reason="bad_response")
        call_id = item.get("id")
        calls.append(
            ToolCall(
                id=call_id if isinstance(call_id, str) else "",
                name=name,
                arguments=arguments,
            )
        )
    return tuple(calls)


def _parse_message(message: Any, usage: Any) -> ProviderResult:
    """Build a ``ProviderResult`` from one choice's ``message`` object."""
    if not isinstance(message, dict):
        raise ProviderError(status=200, retryable=True, reason="bad_response")
    tool_calls = _parse_tool_calls(message.get("tool_calls"))
    content = message.get("content")
    if content is None:
        # A tool-calling turn legitimately returns ``content: null``. Only a
        # null *without* tool calls is a bad response (content filter,
        # refusal, or a length cut — the same case the fast path guards).
        if not tool_calls:
            raise ProviderError(status=200, retryable=True, reason="bad_response")
        content = ""
    if not isinstance(content, str):
        raise ProviderError(status=200, retryable=True, reason="bad_response")

    reasoning = message.get("reasoning_content")
    tokens = usage if isinstance(usage, dict) else {}
    return ProviderResult(
        content=content,
        prompt_tokens=int(tokens.get("prompt_tokens", 0)),
        completion_tokens=int(tokens.get("completion_tokens", 0)),
        tool_calls=tool_calls,
        reasoning_content=reasoning if isinstance(reasoning, str) else "",
    )


class _StreamAccumulator:
    """Merge OpenAI-style streaming fragments into one ``ProviderResult``.

    Tool calls arrive spread across chunks: the first fragment for a given
    ``index`` carries ``id`` and ``function.name``, and later fragments carry
    only ``function.arguments`` pieces that must be concatenated in order.
    """

    def __init__(self) -> None:
        self._text: list[str] = []
        self._reasoning: list[str] = []
        self._tool_calls: dict[int, dict[str, str]] = {}
        self._prompt_tokens = 0
        self._completion_tokens = 0

    def feed(self, chunk: Any) -> str:
        """Merge one SSE payload; return that chunk's assistant text."""
        if not isinstance(chunk, dict):
            raise ProviderError(status=200, retryable=True, reason="bad_response")
        usage = chunk.get("usage")
        if isinstance(usage, dict):
            self._prompt_tokens = int(usage.get("prompt_tokens", 0))
            self._completion_tokens = int(usage.get("completion_tokens", 0))

        choices = chunk.get("choices")
        if not choices:
            # Usage-only trailer (stream_options.include_usage) — no delta.
            return ""
        choice = choices[0]
        delta = choice.get("delta") if isinstance(choice, dict) else None
        if not isinstance(delta, dict):
            return ""

        reasoning = delta.get("reasoning_content")
        if isinstance(reasoning, str):
            self._reasoning.append(reasoning)
        for fragment in delta.get("tool_calls") or []:
            self._merge_tool_call(fragment)

        content = delta.get("content")
        if isinstance(content, str) and content:
            self._text.append(content)
            return content
        return ""

    def _merge_tool_call(self, fragment: Any) -> None:
        if not isinstance(fragment, dict):
            raise ProviderError(status=200, retryable=True, reason="bad_response")
        index = fragment.get("index")
        if not isinstance(index, int):
            # Fragments without an index (some servers omit it for the first
            # call) append in arrival order.
            index = len(self._tool_calls)
        slot = self._tool_calls.setdefault(
            index, {"id": "", "name": "", "arguments": ""},
        )
        call_id = fragment.get("id")
        if isinstance(call_id, str) and call_id:
            slot["id"] = call_id
        function = fragment.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            if isinstance(name, str) and name:
                slot["name"] = name
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                slot["arguments"] += arguments

    def result(self) -> ProviderResult:
        calls = tuple(
            ToolCall(id=slot["id"], name=slot["name"], arguments=slot["arguments"])
            for _, slot in sorted(self._tool_calls.items())
        )
        return ProviderResult(
            content="".join(self._text),
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
            tool_calls=calls,
            reasoning_content="".join(self._reasoning),
        )


class DeepSeekProvider:
    """Agent-path provider — OpenAI-compatible ``POST {base_url}/chat/completions``.

    Serves the multi-step loop (ADR-007). Differences from
    ``DashScopeProvider`` that matter:

    * tool calling and streaming are first-class here;
    * reasoning is a three-tier budget (``thinking`` + ``reasoning_effort``),
      not a boolean;
    * ``messages`` are forwarded verbatim, so an assistant turn that carries
      ``reasoning_content`` (or ``tool_calls``) keeps it on the way back out.
      That echo is what keeps the reasoning chain continuous across turns.

    The fast path keeps its own provider; nothing here changes it.
    """

    name = "deepseek"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._url = base_url.rstrip("/") + "/chat/completions"
        # Same pooled-client rationale as the fast path: one AsyncClient per
        # provider, per-call timeouts passed to the request. ``transport`` is
        # the test injection point (MockTransport).
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(_DEFAULT_CLIENT_TIMEOUT),
            transport=transport,
        )

    async def aclose(self) -> None:
        """Close the pooled HTTP client (called on app shutdown)."""
        await self._client.aclose()

    # --- LLMProvider surface -------------------------------------------

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        max_tokens: int,
        timeout: float,
    ) -> ProviderResult:
        return await self._chat(
            messages, model=model, max_tokens=max_tokens, timeout=timeout,
        )

    async def complete_with_images(
        self,
        messages: list[dict],
        *,
        model: str,
        max_tokens: int,
        timeout: float,
    ) -> ProviderResult:
        """Image content parts ride inside ``messages``; no separate shape.

        Present to satisfy ``LLMProvider`` — the agent path does not route
        vision through this provider.
        """
        return await self._chat(
            messages, model=model, max_tokens=max_tokens, timeout=timeout,
        )

    # --- tool calling --------------------------------------------------

    async def complete_with_tools(
        self,
        messages: list[dict],
        *,
        model: str,
        max_tokens: int,
        timeout: float,
        tools: list[dict],
        tool_choice: str | None = None,
        reasoning: ReasoningLevel = ReasoningLevel.FAST,
    ) -> ProviderResult:
        return await self._chat(
            messages,
            model=model,
            max_tokens=max_tokens,
            timeout=timeout,
            tools=tools,
            tool_choice=tool_choice,
            reasoning=reasoning,
        )

    async def complete_with_tools_stream(
        self,
        messages: list[dict],
        *,
        model: str,
        max_tokens: int,
        timeout: float,
        tools: list[dict],
        tool_choice: str | None = None,
        reasoning: ReasoningLevel = ReasoningLevel.FAST,
    ) -> AsyncIterator[StreamChunk]:
        """Stream a tool-calling completion.

        Yields text increments as they arrive, then exactly one final chunk
        carrying the assembled result. A stream that ends before ``[DONE]``
        raises ``bad_response``: the answer is incomplete and must not be
        mistaken for a finished turn. A caller that already forwarded deltas
        should not retry on that error — the text would be duplicated.
        """
        payload = self._payload(
            messages,
            model=model,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            reasoning=reasoning,
            stream=True,
        )
        accumulator = _StreamAccumulator()
        ended = False
        try:
            async with self._client.stream(
                "POST",
                self._url,
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=timeout,
            ) as resp:
                _raise_for_status(resp)
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        # Blank separators, comments and heartbeat frames.
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        ended = True
                        break
                    try:
                        delta = accumulator.feed(json.loads(data))
                    except (ValueError, TypeError):
                        # Covers both an undecodable frame and a payload whose
                        # fields have the wrong type — same classification the
                        # non-streaming path applies to a malformed 200 body.
                        raise ProviderError(
                            status=200, retryable=True, reason="bad_response",
                        )
                    if delta:
                        yield StreamChunk(delta=delta)
        except httpx.TimeoutException:
            # Not retryable, same as the non-streaming path: the client's own
            # cancel line is the real budget, not the provider's speed.
            raise ProviderError(status=None, retryable=False, reason="timeout")
        except httpx.HTTPError:
            raise ProviderError(status=None, retryable=True, reason="transport")

        if not ended:
            raise ProviderError(status=200, retryable=True, reason="bad_response")
        yield StreamChunk(result=accumulator.result())

    # --- internals -----------------------------------------------------

    def _payload(
        self,
        messages: list[dict],
        *,
        model: str,
        max_tokens: int,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        reasoning: ReasoningLevel = ReasoningLevel.FAST,
        stream: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            # Forwarded verbatim — see the class docstring on reasoning echo.
            "messages": messages,
            "max_tokens": max_tokens,
            **_REASONING_PARAMS[reasoning],
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = (
                tool_choice if tool_choice is not None else "auto"
            )
        if stream:
            payload["stream"] = True
            # Without this the stream carries no usage block at all (standard
            # OpenAI behaviour), which would zero out token accounting for
            # every agent turn. 待 02g 实测确认 deepseek-flash 返回该块。
            payload["stream_options"] = {"include_usage": True}
        return payload

    async def _chat(
        self,
        messages: list[dict],
        *,
        model: str,
        max_tokens: int,
        timeout: float,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        reasoning: ReasoningLevel = ReasoningLevel.FAST,
    ) -> ProviderResult:
        payload = self._payload(
            messages,
            model=model,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            reasoning=reasoning,
        )
        try:
            resp = await self._client.post(
                self._url,
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=timeout,
            )
        except httpx.TimeoutException:
            raise ProviderError(status=None, retryable=False, reason="timeout")
        except httpx.HTTPError:
            raise ProviderError(status=None, retryable=True, reason="transport")

        _raise_for_status(resp)

        try:
            body = resp.json()
            return _parse_message(body["choices"][0]["message"], body.get("usage"))
        except (ValueError, KeyError, IndexError, TypeError):
            # Malformed 200 body — treat as retryable provider fault.
            raise ProviderError(status=200, retryable=True, reason="bad_response")


# ---------------------------------------------------------------------------
# Factory / FastAPI dependency
# ---------------------------------------------------------------------------

_provider: LLMProvider | None = None
_provider_initialized = False
_agent_provider: ToolCallingProvider | None = None
_agent_provider_initialized = False


class ProviderPurpose(StrEnum):
    """Which path a provider is built for (ADR-007 §4.6)."""

    FAST = "fast"
    AGENT = "agent"


def build_provider(
    cfg: Settings, purpose: ProviderPurpose = ProviderPurpose.FAST,
) -> LLMProvider | None:
    """Return the configured provider for ``purpose``, or None without a key.

    The two paths are independent: each reads its own key and base URL, and
    an agent path that is not configured leaves the fast path untouched.
    """
    if purpose is ProviderPurpose.AGENT:
        if not cfg.ai_agent_provider_api_key:
            return None
        return DeepSeekProvider(
            cfg.ai_agent_provider_api_key, cfg.ai_agent_provider_base_url,
        )
    if not cfg.ai_provider_api_key:
        return None
    return DashScopeProvider(cfg.ai_provider_api_key, cfg.ai_provider_base_url)


def get_llm_provider() -> LLMProvider | None:
    """FastAPI dependency — fast path, cached per process."""
    global _provider, _provider_initialized
    if not _provider_initialized:
        _provider = build_provider(get_config())
        _provider_initialized = True
    return _provider


def get_agent_provider() -> ToolCallingProvider | None:
    """FastAPI dependency — agent path, cached per process."""
    global _agent_provider, _agent_provider_initialized
    if not _agent_provider_initialized:
        _agent_provider = cast(
            "ToolCallingProvider | None",
            build_provider(get_config(), ProviderPurpose.AGENT),
        )
        _agent_provider_initialized = True
    return _agent_provider


def reset_llm_provider() -> None:
    """Clear the cached providers (used in tests)."""
    global _provider, _provider_initialized
    global _agent_provider, _agent_provider_initialized
    _provider = None
    _provider_initialized = False
    _agent_provider = None
    _agent_provider_initialized = False


async def close_llm_provider() -> None:
    """Close the cached providers' HTTP clients (called on app shutdown)."""
    global _provider, _provider_initialized
    global _agent_provider, _agent_provider_initialized
    if _provider is not None and hasattr(_provider, "aclose"):
        await _provider.aclose()  # type: ignore[attr-defined]
    _provider = None
    _provider_initialized = False
    if _agent_provider is not None and hasattr(_agent_provider, "aclose"):
        await _agent_provider.aclose()  # type: ignore[attr-defined]
    _agent_provider = None
    _agent_provider_initialized = False
