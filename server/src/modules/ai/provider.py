"""T-M4-01: LLM provider adapter.

The server is the only holder of provider keys; clients never call model
providers directly (docs/architecture/ai-architecture.md). First release:
Alibaba DashScope in OpenAI-compatible mode with model ``qwen3.7-flash``
(D-PROD-05).

Security: provider errors are reduced to enumerated ``reason`` strings.
Provider response bodies may echo user input, so they must never reach
API responses or logs (docs/architecture/security.md).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

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
class ProviderResult:
    content: str
    prompt_tokens: int
    completion_tokens: int


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
        payload = {"model": model, "messages": messages, "max_tokens": max_tokens}
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


# ---------------------------------------------------------------------------
# Factory / FastAPI dependency
# ---------------------------------------------------------------------------

_provider: LLMProvider | None = None
_provider_initialized = False


def build_provider(cfg: Settings) -> LLMProvider | None:
    """Return the configured provider, or None when no API key is set."""
    if not cfg.ai_provider_api_key:
        return None
    return DashScopeProvider(cfg.ai_provider_api_key, cfg.ai_provider_base_url)


def get_llm_provider() -> LLMProvider | None:
    """FastAPI dependency — cached per process."""
    global _provider, _provider_initialized
    if not _provider_initialized:
        _provider = build_provider(get_config())
        _provider_initialized = True
    return _provider


def reset_llm_provider() -> None:
    """Clear the cached provider (used in tests)."""
    global _provider, _provider_initialized
    _provider = None
    _provider_initialized = False


async def close_llm_provider() -> None:
    """Close the cached provider's HTTP client (called on app shutdown)."""
    global _provider, _provider_initialized
    if _provider is not None and hasattr(_provider, "aclose"):
        await _provider.aclose()  # type: ignore[attr-defined]
    _provider = None
    _provider_initialized = False
