"""T-M4-01: AI Gateway tests — contract, limits, degradation, tracing, redaction.

Acceptance mapping (docs/APP-CONTEXT.md T-M4-01):
- 用户限额有效        → TestLimits
- provider 故障可降级 → TestDegradation
- requestId 可追踪    → TestRequestTracing
- 产物无服务商密钥    → TestConfigAndProvider (build_provider needs env key;
  repo-wide secret scan is done out-of-band in the task checklist)
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from src.common.config import Settings, get_config, set_config
from src.common.database import get_db_session
from src.common.logging import JSONFormatter
from src.models.ai import AIUsage
from src.modules.ai.provider import (
    DashScopeProvider,
    ProviderError,
    ProviderResult,
    build_provider,
    get_llm_provider,
    reset_llm_provider,
)
from src.modules.ai.service import MAX_ATTEMPTS, _ai_rate_limits, local_day_start_utc

VALID_OUTPUT = {
    "type": "expense",
    "amountCents": 1250,
    "category": "餐饮",
    "note": "午饭",
    "confidence": "high",
}
VALID_JSON = json.dumps(VALID_OUTPUT, ensure_ascii=False)
PROMPT_TOKENS = 120
COMPLETION_TOKENS = 45


# ── Fakes & fixtures ────────────────────────────────────────────────────

class FakeProvider:
    """In-process stand-in for DashScopeProvider.

    ``results`` is consumed FIFO; each item is a ProviderResult to return
    or a ProviderError to raise. ``default`` is used once results run out.
    """

    name = "fake"

    def __init__(self, results=None, default=None):
        self.results = list(results or [])
        self.default = default
        self.calls: list[dict] = []

    async def complete(self, messages, *, model, max_tokens, timeout):
        self.calls.append(
            {"messages": messages, "model": model,
             "max_tokens": max_tokens, "timeout": timeout},
        )
        if self.results:
            item = self.results.pop(0)
        elif self.default is not None:
            item = self.default
        else:
            raise AssertionError("FakeProvider exhausted")
        if isinstance(item, ProviderError):
            raise item
        return item


class AIEnv:
    def __init__(self, client, factory, provider):
        self.client = client
        self.factory = factory
        self.provider = provider


@pytest.fixture()
def ai_env():
    """TestClient with isolated DB, FakeProvider, and cleared rate limiters."""
    from src.main import app
    from src.modules.auth.dependencies import _rate_limits

    _rate_limits.clear()
    _ai_rate_limits.clear()
    reset_llm_provider()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _create_tables():
        from src.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_tables())

    async def _override_get_db_session():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    provider = FakeProvider(default=ProviderResult(VALID_JSON, PROMPT_TOKENS, COMPLETION_TOKENS))
    app.dependency_overrides[get_db_session] = _override_get_db_session
    app.dependency_overrides[get_llm_provider] = lambda: provider

    with TestClient(app) as c:
        yield AIEnv(c, factory, provider)

    app.dependency_overrides.clear()
    _rate_limits.clear()
    _ai_rate_limits.clear()
    reset_llm_provider()


def _guest(env: AIEnv, device_id: str = "ai-device-1") -> dict:
    resp = env.client.post(
        "/auth/guest", json={"device_id": device_id, "nickname": "ai-tester"},
    )
    assert resp.status_code == 200
    data = resp.json()
    return {"id": data["user_id"], "token": data["tokens"]["access_token"]}


def _active_user(env: AIEnv) -> dict:
    resp = env.client.post("/auth/login", json={
        "phone": "13900001111",
        "verification_code": "000000",
        "device_id": "ai-device-9",
    })
    assert resp.status_code == 200
    data = resp.json()
    return {"id": data["user_id"], "token": data["tokens"]["access_token"]}


def _auth(user: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {user['token']}"}


def _interpret(env: AIEnv, user: dict, text: str = "午饭 十二块五", headers: dict | None = None):
    return env.client.post(
        "/ai/interpret",
        json={"input": text},
        headers={**_auth(user), **(headers or {})},
    )


def _seed_usage(env: AIEnv, user_id: str, created_at: datetime, count: int) -> None:
    async def _run():
        async with env.factory() as session:
            for _ in range(count):
                session.add(AIUsage(
                    user_id=uuid.UUID(user_id),
                    provider="fake",
                    model="qwen3.7-flash",
                    tokens=100,
                    cost=0,
                    request_id="seed",
                    created_at=created_at,
                ))
            await session.commit()

    asyncio.run(_run())


def _fetch_usage(env: AIEnv, user_id: str) -> list[AIUsage]:
    async def _run():
        async with env.factory() as session:
            rows = await session.scalars(
                select(AIUsage).where(
                    AIUsage.user_id == uuid.UUID(user_id),
                    AIUsage.request_id != "seed",
                )
            )
            return list(rows)

    return asyncio.run(_run())


# ── Contract & validation ───────────────────────────────────────────────

class TestInterpretContract:
    def test_requires_auth(self, ai_env):
        resp = ai_env.client.post("/ai/interpret", json={"input": "午饭 12 元"})
        assert resp.status_code == 401

    def test_empty_input_rejected(self, ai_env):
        user = _guest(ai_env)
        resp = _interpret(ai_env, user, text="")
        assert resp.status_code == 422
        assert resp.json()["code"] == "validation_error"

    def test_too_long_input_rejected(self, ai_env):
        user = _guest(ai_env)
        resp = _interpret(ai_env, user, text="啊" * 501)
        assert resp.status_code == 422
        assert resp.json()["code"] == "validation_error"

    def test_success_returns_parsed_output(self, ai_env):
        user = _guest(ai_env)
        resp = _interpret(ai_env, user)
        assert resp.status_code == 200
        body = resp.json()
        assert body["task"] == "ledger_parse"
        assert body["model"] == "qwen3.7-flash"
        assert body["output"] == VALID_OUTPUT
        assert body["usage"] == {
            "prompt_tokens": PROMPT_TOKENS, "completion_tokens": COMPLETION_TOKENS,
        }
        # cost is deliberately not exposed to clients
        assert "cost" not in body

    def test_provider_receives_rendered_messages(self, ai_env):
        user = _guest(ai_env)
        assert _interpret(ai_env, user).status_code == 200
        call = ai_env.provider.calls[0]
        assert call["model"] == "qwen3.7-flash"
        assert call["max_tokens"] == 512
        assert call["timeout"] == get_config().ai_timeout_seconds
        assert call["messages"][0]["role"] == "system"
        assert call["messages"][1] == {"role": "user", "content": "午饭 十二块五"}

    def test_markdown_fenced_json_parsed(self, ai_env):
        """Qwen often wraps JSON in ``` fences — the online path must cope."""
        ai_env.provider.results = [
            ProviderResult(f"```json\n{VALID_JSON}\n```", PROMPT_TOKENS, COMPLETION_TOKENS),
        ]
        user = _guest(ai_env)
        resp = _interpret(ai_env, user)
        assert resp.status_code == 200
        assert resp.json()["output"] == VALID_OUTPUT


# ── Usage recording & cost ──────────────────────────────────────────────

class TestUsageRecording:
    def test_success_records_usage_row(self, ai_env):
        user = _guest(ai_env)
        assert _interpret(ai_env, user).status_code == 200
        rows = _fetch_usage(ai_env, user["id"])
        assert len(rows) == 1
        row = rows[0]
        assert row.provider == "fake"
        assert row.model == "qwen3.7-flash"
        assert row.tokens == PROMPT_TOKENS + COMPLETION_TOKENS
        assert row.cost == 0  # default price is 0

    def test_failure_records_no_usage_row(self, ai_env):
        ai_env.provider.results = [
            ProviderError(status=500, retryable=True, reason="http_5xx"),
        ] * MAX_ATTEMPTS
        user = _guest(ai_env)
        assert _interpret(ai_env, user).status_code == 503
        assert _fetch_usage(ai_env, user["id"]) == []

    def test_cost_computed_from_price(self, ai_env):
        user = _guest(ai_env)
        set_config(Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            secret_key="test-secret",
            env="test",
            api_prefix="",
            ai_price_per_1m_tokens=1_000_000,  # 1 micro-CNY per token
        ))
        assert _interpret(ai_env, user).status_code == 200
        row = _fetch_usage(ai_env, user["id"])[0]
        assert row.cost == PROMPT_TOKENS + COMPLETION_TOKENS


# ── Request tracing ─────────────────────────────────────────────────────

class TestRequestTracing:
    def test_client_request_id_echoed(self, ai_env):
        user = _guest(ai_env)
        resp = _interpret(ai_env, user, headers={"X-Request-Id": "trace-abc-123"})
        assert resp.status_code == 200
        assert resp.json()["request_id"] == "trace-abc-123"
        assert resp.headers["X-Request-Id"] == "trace-abc-123"

    def test_generated_request_id_in_body_and_usage(self, ai_env):
        user = _guest(ai_env)
        resp = _interpret(ai_env, user)
        request_id = resp.json()["request_id"]
        assert request_id
        assert resp.headers["X-Request-Id"] == request_id
        assert _fetch_usage(ai_env, user["id"])[0].request_id == request_id

    def test_long_request_id_truncated_for_usage(self, ai_env):
        """X-Request-Id is client-supplied and unbounded; column is String(64)."""
        user = _guest(ai_env)
        long_id = "r" * 100
        resp = _interpret(ai_env, user, headers={"X-Request-Id": long_id})
        assert resp.status_code == 200
        assert resp.json()["request_id"] == long_id  # echoed unchanged
        assert _fetch_usage(ai_env, user["id"])[0].request_id == long_id[:64]


# ── Limits (acceptance: 用户限额有效) ───────────────────────────────────

class TestLimits:
    def test_rate_limit_rejects_eleventh_call(self, ai_env):
        user = _guest(ai_env)
        limit = get_config().ai_rate_limit_per_minute
        for _ in range(limit):
            assert _interpret(ai_env, user).status_code == 200
        resp = _interpret(ai_env, user)
        assert resp.status_code == 429
        assert resp.json()["code"] == "ai_rate_limited"

    def test_rate_limit_isolated_per_user(self, ai_env):
        user_a = _guest(ai_env, device_id="ai-device-a")
        user_b = _guest(ai_env, device_id="ai-device-b")
        for _ in range(get_config().ai_rate_limit_per_minute):
            assert _interpret(ai_env, user_a).status_code == 200
        assert _interpret(ai_env, user_a).status_code == 429
        assert _interpret(ai_env, user_b).status_code == 200

    def test_guest_daily_quota_exceeded(self, ai_env):
        user = _guest(ai_env)
        quota = get_config().ai_daily_quota_guest
        _seed_usage(ai_env, user["id"], datetime.now(UTC), quota)
        resp = _interpret(ai_env, user)
        assert resp.status_code == 429
        assert resp.json()["code"] == "ai_quota_exceeded"
        assert ai_env.provider.calls == []  # rejected before hitting provider

    def test_guest_quota_yesterday_rows_do_not_count(self, ai_env):
        user = _guest(ai_env)
        quota = get_config().ai_daily_quota_guest
        day_start = local_day_start_utc("Asia/Shanghai")
        _seed_usage(ai_env, user["id"], day_start - timedelta(hours=1), quota)
        assert _interpret(ai_env, user).status_code == 200

    def test_active_user_has_no_daily_quota(self, ai_env):
        user = _active_user(ai_env)
        quota = get_config().ai_daily_quota_guest
        _seed_usage(ai_env, user["id"], datetime.now(UTC), quota + 5)
        assert _interpret(ai_env, user).status_code == 200


# ── Degradation (acceptance: provider 故障可降级) ───────────────────────

class TestDegradation:
    def test_no_provider_configured_returns_503(self, ai_env):
        from src.main import app
        app.dependency_overrides[get_llm_provider] = lambda: None
        user = _guest(ai_env)
        resp = _interpret(ai_env, user)
        assert resp.status_code == 503
        assert resp.json()["code"] == "ai_unavailable"

    def test_retryable_error_retried_once_then_degrades(self, ai_env):
        ai_env.provider.results = [
            ProviderError(status=500, retryable=True, reason="http_5xx"),
        ] * MAX_ATTEMPTS
        user = _guest(ai_env)
        resp = _interpret(ai_env, user)
        assert resp.status_code == 503
        assert resp.json()["code"] == "ai_unavailable"
        assert len(ai_env.provider.calls) == MAX_ATTEMPTS

    def test_transient_error_then_success(self, ai_env):
        ai_env.provider.results = [
            ProviderError(status=429, retryable=True, reason="http_429"),
            ProviderResult(VALID_JSON, PROMPT_TOKENS, COMPLETION_TOKENS),
        ]
        user = _guest(ai_env)
        resp = _interpret(ai_env, user)
        assert resp.status_code == 200
        assert len(ai_env.provider.calls) == 2

    def test_timeout_is_never_retried(self, ai_env):
        """§6.4: client cancels at 25 s — a timeout retry would stack latency."""
        ai_env.provider.results = [
            ProviderError(status=None, retryable=False, reason="timeout"),
        ]
        user = _guest(ai_env)
        resp = _interpret(ai_env, user)
        assert resp.status_code == 503
        assert len(ai_env.provider.calls) == 1

    def test_invalid_json_then_valid_retries_once(self, ai_env):
        ai_env.provider.results = [
            ProviderResult("sorry, I cannot parse that", 90, 30),
            ProviderResult(VALID_JSON, PROMPT_TOKENS, COMPLETION_TOKENS),
        ]
        user = _guest(ai_env)
        resp = _interpret(ai_env, user)
        assert resp.status_code == 200
        assert resp.json()["output"] == VALID_OUTPUT
        assert len(ai_env.provider.calls) == MAX_ATTEMPTS

    def test_invalid_json_twice_degrades(self, ai_env):
        ai_env.provider.results = [
            ProviderResult("not json at all", 90, 30),
        ] * MAX_ATTEMPTS
        user = _guest(ai_env)
        resp = _interpret(ai_env, user)
        assert resp.status_code == 503
        assert resp.json()["code"] == "ai_invalid_output"

    def test_non_dict_json_degrades(self, ai_env):
        ai_env.provider.results = [ProviderResult("[1, 2, 3]", 90, 30)] * MAX_ATTEMPTS
        user = _guest(ai_env)
        resp = _interpret(ai_env, user)
        assert resp.status_code == 503
        assert resp.json()["code"] == "ai_invalid_output"

    def test_degraded_response_hides_model_output(self, ai_env):
        """security.md: model output / provider errors never reach clients."""
        ai_env.provider.results = [
            ProviderResult("SECRET-OUTPUT-MARKER not json", 90, 30),
        ] * MAX_ATTEMPTS
        user = _guest(ai_env)
        resp = _interpret(ai_env, user)
        assert resp.status_code == 503
        assert "SECRET-OUTPUT-MARKER" not in resp.text
        assert resp.json()["message"] == "AI service is temporarily unavailable"


# ── Redacted logging ────────────────────────────────────────────────────

class TestRedactedLogging:
    def test_success_log_has_metadata_but_no_content(self, ai_env, caplog):
        user = _guest(ai_env)
        with caplog.at_level(logging.INFO):
            assert _interpret(ai_env, user, text="午饭 十二块五").status_code == 200
        ai_records = [r for r in caplog.records if getattr(r, "ai_task", None)]
        assert len(ai_records) == 1
        record = ai_records[0]
        assert record.levelno == logging.INFO
        assert record.ai_task == "ledger_parse"
        assert record.ai_model == "qwen3.7-flash"
        assert record.ai_provider == "fake"
        assert record.prompt_tokens == PROMPT_TOKENS
        assert record.completion_tokens == COMPLETION_TOKENS
        assert record.outcome == "ok"
        assert record.request_id
        # never the conversation itself
        assert "十二块五" not in caplog.text
        assert "amountCents" not in caplog.text

    def test_failure_log_redacted(self, ai_env, caplog):
        ai_env.provider.results = [
            ProviderResult("SECRET-OUTPUT-MARKER nope", 90, 30),
        ] * MAX_ATTEMPTS
        user = _guest(ai_env)
        with caplog.at_level(logging.WARNING):
            assert _interpret(ai_env, user).status_code == 503
        failures = [r for r in caplog.records if getattr(r, "outcome", None)]
        assert len(failures) == 1
        assert failures[0].outcome == "invalid_json"
        assert failures[0].attempt == MAX_ATTEMPTS
        assert "SECRET-OUTPUT-MARKER" not in caplog.text

    def test_provider_error_failure_log_uses_safe_reason(self, ai_env, caplog):
        ai_env.provider.results = [
            ProviderError(status=503, retryable=True, reason="http_5xx"),
        ] * MAX_ATTEMPTS
        user = _guest(ai_env)
        with caplog.at_level(logging.WARNING):
            assert _interpret(ai_env, user).status_code == 503
        outcomes = [r.outcome for r in caplog.records if getattr(r, "outcome", None)]
        assert outcomes == ["http_5xx"]  # one WARNING at final degradation only

    def test_json_formatter_passes_ai_keys(self):
        record = logging.LogRecord(
            "src.modules.ai.service", logging.INFO, __file__, 1, "AI interpret ok", (), None,
        )
        record.request_id = "rid-1"
        record.ai_task = "ledger_parse"
        record.ai_model = "qwen3.7-flash"
        record.ai_provider = "dashscope"
        record.prompt_tokens = 10
        record.completion_tokens = 5
        record.outcome = "ok"
        payload = json.loads(JSONFormatter().format(record))
        assert payload["ai_task"] == "ledger_parse"
        assert payload["ai_model"] == "qwen3.7-flash"
        assert payload["ai_provider"] == "dashscope"
        assert payload["prompt_tokens"] == 10
        assert payload["completion_tokens"] == 5
        assert payload["outcome"] == "ok"


class TestSlowHint:
    """APP-CONTEXT §6.4/§10.3: 12 s slow hint — server side observability."""

    def test_slow_request_logs_warning(self, ai_env, monkeypatch, caplog):
        user = _guest(ai_env)
        # Make the provider take 50 ms per call
        original_complete = ai_env.provider.complete

        async def slow_complete(messages, *, model, max_tokens, timeout):
            await asyncio.sleep(0.05)
            return await original_complete(
                messages, model=model, max_tokens=max_tokens, timeout=timeout,
            )
        monkeypatch.setattr(ai_env.provider, "complete", slow_complete)
        # Lower the threshold so 50 ms counts as "slow"
        monkeypatch.setattr("src.modules.ai.service.SLOW_HINT_SECONDS", 0.01)

        with caplog.at_level(logging.WARNING):
            assert _interpret(ai_env, user).status_code == 200

        slow = [r for r in caplog.records if getattr(r, "outcome", None) == "slow"]
        assert len(slow) >= 1
        rec = slow[0]
        assert rec.levelno == logging.WARNING
        assert rec.ai_task == "ledger_parse"
        assert rec.duration_ms >= 50

    def test_fast_request_no_slow_log(self, ai_env, caplog):
        user = _guest(ai_env)
        with caplog.at_level(logging.WARNING):
            assert _interpret(ai_env, user).status_code == 200
        slow = [r for r in caplog.records if getattr(r, "outcome", None) == "slow"]
        assert len(slow) == 0

    def test_slow_failure_also_logs_slow(self, ai_env, monkeypatch, caplog):
        """R-M4 P2b: failure path must emit the same outcome=slow event."""
        user = _guest(ai_env)

        async def slow_fail(messages, *, model, max_tokens, timeout):
            await asyncio.sleep(0.05)
            raise ProviderError(status=None, retryable=False, reason="timeout")

        monkeypatch.setattr(ai_env.provider, "complete", slow_fail)
        monkeypatch.setattr("src.modules.ai.service.SLOW_HINT_SECONDS", 0.01)

        with caplog.at_level(logging.WARNING):
            assert _interpret(ai_env, user).status_code == 503

        slow = [r for r in caplog.records if getattr(r, "outcome", None) == "slow"]
        assert len(slow) >= 1
        assert slow[0].levelno == logging.WARNING
        assert slow[0].ai_task == "ledger_parse"
        assert slow[0].duration_ms >= 50
        # the failure itself is still logged with its enumerated reason
        failed = [r for r in caplog.records if getattr(r, "outcome", None) == "timeout"]
        assert len(failed) == 1

    def test_fast_failure_no_slow_log(self, ai_env, monkeypatch, caplog):
        user = _guest(ai_env)

        async def fast_fail(messages, *, model, max_tokens, timeout):
            raise ProviderError(status=None, retryable=False, reason="transport")

        monkeypatch.setattr(ai_env.provider, "complete", fast_fail)

        with caplog.at_level(logging.WARNING):
            assert _interpret(ai_env, user).status_code == 503

        slow = [r for r in caplog.records if getattr(r, "outcome", None) == "slow"]
        assert len(slow) == 0


# ── Config & provider unit tests ────────────────────────────────────────

def _settings(**overrides) -> Settings:
    base = {
        "database_url": "sqlite+aiosqlite:///:memory:",
        "secret_key": "test-secret",
        "env": "test",
        "api_prefix": "",
    }
    base.update(overrides)
    return Settings(**base)


class TestConfigAndProvider:
    def test_default_config_has_no_key_and_qwen_model(self):
        cfg = get_config()
        assert cfg.ai_provider_api_key is None
        assert cfg.ai_provider_model == "qwen3.7-flash"
        assert cfg.ai_timeout_seconds == 10.0
        assert cfg.ai_rate_limit_per_minute == 10
        assert cfg.ai_daily_quota_guest == 20

    def test_build_provider_without_key_returns_none(self):
        assert build_provider(_settings()) is None

    def test_build_provider_with_key(self):
        provider = build_provider(_settings(ai_provider_api_key="sk-test-only"))
        assert isinstance(provider, DashScopeProvider)

    def test_local_day_start_utc_invalid_tz_falls_back_to_utc(self):
        dt = local_day_start_utc("Mars/Olympus_Mons")
        assert dt.tzinfo == UTC
        assert (dt.hour, dt.minute, dt.second) == (0, 0, 0)

    def test_local_day_start_utc_beijing_is_16h_utc(self):
        dt = local_day_start_utc("Asia/Shanghai")
        assert dt.tzinfo == UTC
        assert dt.hour == 16  # 00:00 +08:00 == 16:00 UTC previous day


def _dashscope(handler) -> DashScopeProvider:
    return DashScopeProvider(
        api_key="test-key",
        base_url="https://provider.example/v1",
        transport=httpx.MockTransport(handler),
    )


_OK_BODY = {
    "choices": [{"message": {"role": "assistant", "content": VALID_JSON}}],
    "usage": {"prompt_tokens": PROMPT_TOKENS, "completion_tokens": COMPLETION_TOKENS},
}


class TestDashScopeProvider:
    async def test_request_construction(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["request"] = request
            return httpx.Response(200, json=_OK_BODY)

        provider = _dashscope(handler)
        result = await provider.complete(
            [{"role": "user", "content": "hi"}],
            model="qwen3.7-flash", max_tokens=512, timeout=7.5,
        )
        request = captured["request"]
        assert str(request.url) == "https://provider.example/v1/chat/completions"
        assert request.method == "POST"
        assert request.headers["authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["model"] == "qwen3.7-flash"
        assert body["messages"] == [{"role": "user", "content": "hi"}]
        assert body["max_tokens"] == 512
        assert result == ProviderResult(VALID_JSON, PROMPT_TOKENS, COMPLETION_TOKENS)

    @pytest.mark.parametrize(
        ("status", "retryable", "reason"),
        [
            (429, True, "http_429"),
            (500, True, "http_5xx"),
            (503, True, "http_5xx"),
            (401, False, "http_4xx"),
            (400, False, "http_4xx"),
        ],
    )
    async def test_http_error_classification(self, status, retryable, reason):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status, json={"error": {"message": "SECRET-BODY-MARKER"}},
            )

        with pytest.raises(ProviderError) as exc_info:
            await _dashscope(handler).complete(
                [{"role": "user", "content": "hi"}],
                model="m", max_tokens=8, timeout=1.0,
            )
        assert exc_info.value.status == status
        assert exc_info.value.retryable is retryable
        assert exc_info.value.reason == reason
        # provider error bodies may echo user input — never in str()
        assert "SECRET-BODY-MARKER" not in str(exc_info.value)

    async def test_timeout_not_retryable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("too slow")

        with pytest.raises(ProviderError) as exc_info:
            await _dashscope(handler).complete(
                [{"role": "user", "content": "hi"}],
                model="m", max_tokens=8, timeout=1.0,
            )
        assert exc_info.value.retryable is False
        assert exc_info.value.reason == "timeout"

    async def test_transport_error_retryable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with pytest.raises(ProviderError) as exc_info:
            await _dashscope(handler).complete(
                [{"role": "user", "content": "hi"}],
                model="m", max_tokens=8, timeout=1.0,
            )
        assert exc_info.value.retryable is True
        assert exc_info.value.reason == "transport"

    async def test_malformed_response_body_retryable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"unexpected": "shape"})

        with pytest.raises(ProviderError) as exc_info:
            await _dashscope(handler).complete(
                [{"role": "user", "content": "hi"}],
                model="m", max_tokens=8, timeout=1.0,
            )
        assert exc_info.value.retryable is True
        assert exc_info.value.reason == "bad_response"

    async def test_missing_usage_defaults_to_zero(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "{}"}}],
            })

        result = await _dashscope(handler).complete(
            [{"role": "user", "content": "hi"}],
            model="m", max_tokens=8, timeout=1.0,
        )
        assert result == ProviderResult("{}", 0, 0)
