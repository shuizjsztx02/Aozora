"""T-M7-02b: agent-path provider — tool calling, streaming, reasoning tiers.

Acceptance coverage (docs/milestones/production-execution-plan.md T-M7-02b):
tool_calls parsing / malformed JSON arguments / unknown tool name / streaming
fragment assembly (with id+name intact) / ``[DONE]`` termination / mid-stream
cut / multi-turn ``reasoning_content`` echo / three-tier reasoning injection /
timeout staying on the non-retryable path / the provider key never reaching
logs or responses.

Everything runs against ``httpx.MockTransport`` — no network, no database.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from src.common.config import Settings, set_config
from src.modules.ai.provider import (
    DashScopeProvider,
    DeepSeekProvider,
    ProviderError,
    ProviderPurpose,
    ProviderResult,
    ReasoningLevel,
    ToolCall,
    build_provider,
    close_llm_provider,
    get_agent_provider,
    reset_llm_provider,
)

# A distinctive key so leakage assertions are unambiguous.
API_KEY = "sk-deepseek-LEAK-MARKER"
BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-flash"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "budget.get",
            "description": "读取某月预算",
            "parameters": {
                "type": "object",
                "properties": {"month": {"type": "string"}},
            },
        },
    },
]


def _settings(**overrides) -> Settings:
    base = {
        "database_url": "sqlite+aiosqlite:///:memory:",
        "secret_key": "test-secret",
        "env": "test",
        "api_prefix": "",
        # 密封：init kwarg 压过 .env，默认两条路径都无 key。
        "ai_provider_api_key": None,
        "ai_agent_provider_api_key": None,
    }
    base.update(overrides)
    return Settings(**base)


def _body(message: dict, usage: dict | None = None) -> dict:
    return {
        "choices": [{"message": message}],
        "usage": usage or {"prompt_tokens": 11, "completion_tokens": 7},
    }


def _sse(*payloads: str, done: bool = True) -> str:
    """Render SSE frames; ``done=False`` simulates a stream cut before [DONE]."""
    frames = "".join(f"data: {payload}\n\n" for payload in payloads)
    return frames + "data: [DONE]\n\n" if done else frames


def _delta(**fields) -> str:
    return json.dumps({"choices": [{"delta": fields}]})


# ── fixtures ────────────────────────────────────────────────────────────


@pytest.fixture()
async def provider_factory():
    """Build MockTransport-backed providers and close them afterwards."""
    created: list[DeepSeekProvider] = []

    def _make(handler) -> DeepSeekProvider:
        provider = DeepSeekProvider(
            api_key=API_KEY,
            base_url=BASE_URL,
            transport=httpx.MockTransport(handler),
        )
        created.append(provider)
        return provider

    yield _make

    for provider in created:
        # MockTransport holds no sockets, but the pooled client should still
        # be closed explicitly rather than left to the GC.
        await provider.aclose()


# ── build_provider dispatch ─────────────────────────────────────────────


class TestBuildProviderByPurpose:
    def test_default_purpose_is_the_fast_path(self):
        provider = build_provider(_settings(ai_provider_api_key="sk-fast"))
        assert isinstance(provider, DashScopeProvider)

    def test_agent_purpose_builds_deepseek(self):
        provider = build_provider(
            _settings(ai_agent_provider_api_key=API_KEY), ProviderPurpose.AGENT,
        )
        assert isinstance(provider, DeepSeekProvider)
        assert provider.name == "deepseek"

    def test_agent_purpose_without_key_returns_none(self):
        assert build_provider(_settings(), ProviderPurpose.AGENT) is None

    def test_same_config_can_serve_both_paths(self):
        cfg = _settings(ai_provider_api_key="sk-fast", ai_agent_provider_api_key=API_KEY)
        assert isinstance(build_provider(cfg, ProviderPurpose.FAST), DashScopeProvider)
        assert isinstance(build_provider(cfg, ProviderPurpose.AGENT), DeepSeekProvider)

    def test_paths_do_not_displace_each_other(self):
        # Agent configured, fast path not — each side reads only its own key.
        agent_only = _settings(ai_agent_provider_api_key=API_KEY)
        assert isinstance(
            build_provider(agent_only, ProviderPurpose.AGENT), DeepSeekProvider,
        )
        assert build_provider(agent_only, ProviderPurpose.FAST) is None

        fast_only = _settings(ai_provider_api_key="sk-fast")
        assert isinstance(
            build_provider(fast_only, ProviderPurpose.FAST), DashScopeProvider,
        )
        assert build_provider(fast_only, ProviderPurpose.AGENT) is None

    def test_agent_defaults_follow_adr_007(self):
        cfg = _settings()
        assert cfg.ai_agent_provider_model == "deepseek-flash"
        assert cfg.ai_agent_provider_base_url == "https://api.deepseek.com"

    def test_agent_key_is_not_required_in_production(self):
        """The agent path ships behind a default-off switch, so unlike
        ``AI_PROVIDER_API_KEY`` its absence must not block startup."""
        cfg = _settings(
            env="production",
            secret_key="a-real-production-secret-value",
            dev_auth_code="",
            cors_origins="https://aozora.app",
            ai_provider_api_key="sk-fast",
            ai_agent_provider_api_key=None,
        )
        assert cfg.ai_agent_provider_api_key is None

    async def test_get_agent_provider_is_cached_and_reset(self):
        set_config(_settings(ai_agent_provider_api_key=API_KEY))
        reset_llm_provider()
        try:
            first = get_agent_provider()
            assert isinstance(first, DeepSeekProvider)
            assert get_agent_provider() is first
            await close_llm_provider()
            assert get_agent_provider() is not first
        finally:
            await close_llm_provider()
            reset_llm_provider()


# ── request construction ────────────────────────────────────────────────


class TestRequestConstruction:
    async def test_tool_request_shape(self, provider_factory):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["request"] = request
            return httpx.Response(
                200, json=_body({"role": "assistant", "content": "ok"}),
            )

        await provider_factory(handler).complete_with_tools(
            [{"role": "user", "content": "查一下八月预算"}],
            model=MODEL, max_tokens=512, timeout=7.5, tools=TOOLS,
        )

        request = captured["request"]
        assert str(request.url) == f"{BASE_URL}/chat/completions"
        assert request.method == "POST"
        assert request.headers["authorization"] == f"Bearer {API_KEY}"
        body = json.loads(request.content)
        assert body["model"] == MODEL
        assert body["max_tokens"] == 512
        assert body["tools"] == TOOLS
        assert body["tool_choice"] == "auto"
        assert "stream" not in body

    async def test_tool_choice_can_be_pinned(self, provider_factory):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=_body({"role": "assistant", "content": "ok"}))

        await provider_factory(handler).complete_with_tools(
            [{"role": "user", "content": "hi"}],
            model=MODEL, max_tokens=64, timeout=5.0, tools=TOOLS,
            tool_choice="none",
        )
        assert captured["body"]["tool_choice"] == "none"

    async def test_no_tools_key_when_no_tools(self, provider_factory):
        """``complete()`` must not send an empty ``tools`` array — providers
        reject it, and the fast-path shape stays tool-free."""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=_body({"role": "assistant", "content": "ok"}))

        await provider_factory(handler).complete(
            [{"role": "user", "content": "hi"}],
            model=MODEL, max_tokens=64, timeout=5.0,
        )
        assert "tools" not in captured["body"]
        assert "tool_choice" not in captured["body"]
        # thinking default-on means the plain surface must still say disabled.
        assert captured["body"]["thinking"] == {"type": "disabled"}

    async def test_messages_forwarded_verbatim_for_reasoning_echo(self, provider_factory):
        """ADR-007: a tool-calling conversation must carry each turn's
        ``reasoning_content`` back. The provider's job is to not strip it."""
        messages = [
            {"role": "user", "content": "把上个月预算改成 3000"},
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "先查上个月预算，再改。",
                "tool_calls": [
                    {
                        "id": "call_a",
                        "type": "function",
                        "function": {"name": "budget.get", "arguments": "{}"},
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "call_a", "content": '{"amount": 2500}'},
        ]
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=_body({"role": "assistant", "content": "ok"}))

        await provider_factory(handler).complete_with_tools(
            messages, model=MODEL, max_tokens=64, timeout=5.0, tools=TOOLS,
        )
        sent = captured["body"]["messages"]
        assert sent == messages
        assert sent[1]["reasoning_content"] == "先查上个月预算，再改。"
        assert sent[1]["tool_calls"][0]["id"] == "call_a"


# ── response parsing ────────────────────────────────────────────────────


class TestToolCallParsing:
    async def test_tool_calls_parsed_verbatim(self, provider_factory):
        raw_arguments = '{"month": "2026-08"}'

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_body({
                "role": "assistant",
                "content": None,
                "reasoning_content": "先读预算。",
                "tool_calls": [{
                    "id": "call_a",
                    "type": "function",
                    "function": {"name": "budget.get", "arguments": raw_arguments},
                }],
            }))

        result = await provider_factory(handler).complete_with_tools(
            [{"role": "user", "content": "查八月预算"}],
            model=MODEL, max_tokens=64, timeout=5.0, tools=TOOLS,
        )
        assert result.tool_calls == (
            ToolCall(id="call_a", name="budget.get", arguments=raw_arguments),
        )
        assert result.tool_calls[0].parse_arguments() == {"month": "2026-08"}
        assert result.content == ""
        assert result.reasoning_content == "先读预算。"

    async def test_multiple_tool_calls_keep_their_ids(self, provider_factory):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_body({
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_a", "function": {"name": "budget.get", "arguments": "{}"}},
                    {"id": "call_b", "function": {"name": "book.get", "arguments": "{}"}},
                ],
            }))

        result = await provider_factory(handler).complete_with_tools(
            [{"role": "user", "content": "hi"}],
            model=MODEL, max_tokens=64, timeout=5.0, tools=TOOLS,
        )
        assert [call.id for call in result.tool_calls] == ["call_a", "call_b"]
        assert [call.name for call in result.tool_calls] == ["budget.get", "book.get"]

    async def test_malformed_arguments_survive_and_fail_at_parse(self, provider_factory):
        """Broken JSON in ``arguments`` is a model-output problem: the raw text
        must reach the loop so it can answer with a tool-result error, rather
        than being dropped or misread as a transport fault."""
        broken = '{"month": '

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_body({
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_a", "function": {"name": "budget.get", "arguments": broken}},
                ],
            }))

        result = await provider_factory(handler).complete_with_tools(
            [{"role": "user", "content": "hi"}],
            model=MODEL, max_tokens=64, timeout=5.0, tools=TOOLS,
        )
        assert result.tool_calls[0].arguments == broken
        with pytest.raises(ValueError):
            result.tool_calls[0].parse_arguments()

    async def test_non_object_arguments_rejected_at_parse(self, provider_factory):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_body({
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_a", "function": {"name": "budget.get", "arguments": "[1, 2]"}},
                ],
            }))

        result = await provider_factory(handler).complete_with_tools(
            [{"role": "user", "content": "hi"}],
            model=MODEL, max_tokens=64, timeout=5.0, tools=TOOLS,
        )
        with pytest.raises(ValueError):
            result.tool_calls[0].parse_arguments()

    async def test_empty_arguments_parse_to_empty_object(self, provider_factory):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_body({
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_a", "function": {"name": "budget.list", "arguments": ""}},
                ],
            }))

        result = await provider_factory(handler).complete_with_tools(
            [{"role": "user", "content": "hi"}],
            model=MODEL, max_tokens=64, timeout=5.0, tools=TOOLS,
        )
        assert result.tool_calls[0].parse_arguments() == {}

    async def test_unknown_tool_name_passes_through(self, provider_factory):
        """The provider does not own the tool registry (that is T-M7-02c), so
        an unknown name must reach the loop rather than be rejected here."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_body({
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_a", "function": {"name": "nope.not_a_tool", "arguments": "{}"}},
                ],
            }))

        result = await provider_factory(handler).complete_with_tools(
            [{"role": "user", "content": "hi"}],
            model=MODEL, max_tokens=64, timeout=5.0, tools=TOOLS,
        )
        assert result.tool_calls[0].name == "nope.not_a_tool"

    async def test_plain_completion_has_no_tool_calls(self, provider_factory):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_body({"role": "assistant", "content": "你好"}))

        result = await provider_factory(handler).complete_with_tools(
            [{"role": "user", "content": "hi"}],
            model=MODEL, max_tokens=64, timeout=5.0, tools=TOOLS,
        )
        assert result.tool_calls == ()
        assert result.content == "你好"
        assert result.reasoning_content == ""

    async def test_null_content_without_tool_calls_is_bad_response(self, provider_factory):
        """Same guard as the fast path: null content with nothing else is a
        filter/refusal/length cut, not a usable turn."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_body({"role": "assistant", "content": None}))

        with pytest.raises(ProviderError) as exc_info:
            await provider_factory(handler).complete_with_tools(
                [{"role": "user", "content": "hi"}],
                model=MODEL, max_tokens=64, timeout=5.0, tools=TOOLS,
            )
        assert exc_info.value.reason == "bad_response"
        assert exc_info.value.retryable is True

    async def test_tool_call_without_name_passes_through_empty(self, provider_factory):
        """A nameless tool call is a model-output problem the loop can answer
        with a tool-result error, so it must reach the loop rather than fail
        the step — the same contract the streaming path implements."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_body({
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call_a", "function": {"arguments": "{}"}}],
            }))

        result = await provider_factory(handler).complete_with_tools(
            [{"role": "user", "content": "hi"}],
            model=MODEL, max_tokens=64, timeout=5.0, tools=TOOLS,
        )
        assert result.tool_calls == (ToolCall(id="call_a", name="", arguments="{}"),)

    async def test_tool_call_without_id_passes_through_empty(self, provider_factory):
        """缺 id 不是传输故障：配对只发生在 loop 自己的消息列表内，loop 会补一个
        稳定 id 并同时写回 assistant 消息（``agent.AgentLoop._assistant_tool_calls``）。
        本层在此拒绝，只会把一个调用方处理得了的情况变成一个失败的回合。"""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_body({
                "role": "assistant",
                "content": None,
                "tool_calls": [{"function": {"name": "budget_get", "arguments": "{}"}}],
            }))

        result = await provider_factory(handler).complete_with_tools(
            [{"role": "user", "content": "hi"}],
            model=MODEL, max_tokens=64, timeout=5.0, tools=TOOLS,
        )
        assert result.tool_calls == (
            ToolCall(id="", name="budget_get", arguments="{}"),
        )

    async def test_missing_usage_defaults_to_zero(self, provider_factory):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            })

        result = await provider_factory(handler).complete_with_tools(
            [{"role": "user", "content": "hi"}],
            model=MODEL, max_tokens=64, timeout=5.0, tools=TOOLS,
        )
        assert (result.prompt_tokens, result.completion_tokens) == (0, 0)

    async def test_default_result_shape_is_backward_compatible(self):
        """The fast path constructs ProviderResult positionally; the agent
        fields must not disturb that."""
        assert ProviderResult("x", 1, 2) == ProviderResult("x", 1, 2, (), "")


# ── reasoning tiers ─────────────────────────────────────────────────────


class TestReasoningTiers:
    @pytest.mark.parametrize(
        ("level", "expected"),
        [
            (ReasoningLevel.FAST, {"thinking": {"type": "disabled"}}),
            (
                ReasoningLevel.MEDIUM,
                {"thinking": {"type": "enabled"}, "reasoning_effort": "low"},
            ),
            (
                ReasoningLevel.DEEP,
                {"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
            ),
        ],
    )
    async def test_tier_parameters_injected(self, provider_factory, level, expected):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=_body({"role": "assistant", "content": "ok"}))

        await provider_factory(handler).complete_with_tools(
            [{"role": "user", "content": "hi"}],
            model=MODEL, max_tokens=64, timeout=5.0, tools=TOOLS, reasoning=level,
        )
        body = captured["body"]
        for key, value in expected.items():
            assert body[key] == value
        if level is ReasoningLevel.FAST:
            # An effort alongside disabled is meaningless and is not sent.
            assert "reasoning_effort" not in body

    async def test_default_tier_is_fast(self, provider_factory):
        """``deepseek-flash`` thinks by default, so omitting the tier must not
        mean "let it think" — the default has to be the cheap one."""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=_body({"role": "assistant", "content": "ok"}))

        await provider_factory(handler).complete_with_tools(
            [{"role": "user", "content": "hi"}],
            model=MODEL, max_tokens=64, timeout=5.0, tools=TOOLS,
        )
        assert captured["body"]["thinking"] == {"type": "disabled"}

    async def test_deep_tier_applies_to_streaming_too(self, provider_factory):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, text=_sse(_delta(content="ok")))

        async for _ in provider_factory(handler).complete_with_tools_stream(
            [{"role": "user", "content": "hi"}],
            model=MODEL, max_tokens=64, timeout=5.0, tools=TOOLS,
            reasoning=ReasoningLevel.DEEP,
        ):
            pass
        assert captured["body"]["thinking"] == {"type": "enabled"}
        assert captured["body"]["reasoning_effort"] == "high"


# ── streaming ───────────────────────────────────────────────────────────


class _BrokenStream(httpx.AsyncByteStream):
    """Yields one frame, then drops the connection (no [DONE])."""

    async def __aiter__(self):
        yield b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'
        raise httpx.RemoteProtocolError("peer closed the stream")

    async def aclose(self) -> None:
        pass


class TestStreaming:
    async def _collect(self, provider, **overrides):
        kwargs = {
            "model": MODEL, "max_tokens": 64, "timeout": 5.0, "tools": TOOLS,
        }
        kwargs.update(overrides)
        stream = provider.complete_with_tools_stream(
            [{"role": "user", "content": "hi"}], **kwargs,
        )
        return [chunk async for chunk in stream]

    async def test_text_fragments_assembled(self, provider_factory):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_sse(
                _delta(content="你"), _delta(content="好"),
            ))

        chunks = await self._collect(provider_factory(handler))
        assert [chunk.delta for chunk in chunks] == ["你", "好", ""]
        assert chunks[-1].result.content == "你好"

    async def test_stream_flag_and_usage_request(self, provider_factory):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, text=_sse(_delta(content="ok")))

        await self._collect(provider_factory(handler))
        assert captured["body"]["stream"] is True
        assert captured["body"]["stream_options"] == {"include_usage": True}

    async def test_tool_call_fragments_assembled_with_id_and_name(self, provider_factory):
        """First fragment carries id + name, later ones only arguments — the
        merger must keep the identity and concatenate the rest in order."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_sse(
                json.dumps({"choices": [{"delta": {"tool_calls": [{
                    "index": 0, "id": "call_a", "type": "function",
                    "function": {"name": "budget.get", "arguments": ""},
                }]}}]}),
                json.dumps({"choices": [{"delta": {"tool_calls": [{
                    "index": 0, "function": {"arguments": '{"month":'},
                }]}}]}),
                json.dumps({"choices": [{"delta": {"tool_calls": [{
                    "index": 0, "function": {"arguments": ' "2026-08"}'},
                }]}}]}),
            ))

        chunks = await self._collect(provider_factory(handler))
        assert [chunk.delta for chunk in chunks] == [""]
        assert chunks[-1].result.tool_calls == (
            ToolCall(id="call_a", name="budget.get", arguments='{"month": "2026-08"}'),
        )
        assert chunks[-1].result.tool_calls[0].parse_arguments() == {"month": "2026-08"}

    async def test_parallel_tool_calls_stay_separate(self, provider_factory):
        """Interleaved fragments are keyed by ``index``, not by arrival order."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_sse(
                json.dumps({"choices": [{"delta": {"tool_calls": [
                    {"index": 0, "id": "call_a", "function": {"name": "budget.get", "arguments": "{}"}},
                    {"index": 1, "id": "call_b", "function": {"name": "book.get", "arguments": "{}"}},
                ]}}]}),
                json.dumps({"choices": [{"delta": {"tool_calls": [
                    {"index": 1, "function": {"arguments": ""}},
                ]}}]}),
            ))

        chunks = await self._collect(provider_factory(handler))
        result = chunks[-1].result
        assert [(call.id, call.name) for call in result.tool_calls] == [
            ("call_a", "budget.get"), ("call_b", "book.get"),
        ]

    async def test_usage_collected_from_trailer(self, provider_factory):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_sse(
                _delta(content="ok"),
                json.dumps({"choices": [], "usage": {
                    "prompt_tokens": 30, "completion_tokens": 12,
                }}),
            ))

        chunks = await self._collect(provider_factory(handler))
        result = chunks[-1].result
        assert (result.prompt_tokens, result.completion_tokens) == (30, 12)

    async def test_reasoning_deltas_accumulated_not_forwarded(self, provider_factory):
        """Reasoning is persisted for the next turn's echo, but the assistant's
        visible text must stay free of it."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_sse(
                _delta(reasoning_content="先查"),
                _delta(reasoning_content="预算。"),
                _delta(content="好的"),
            ))

        chunks = await self._collect(provider_factory(handler))
        assert [chunk.delta for chunk in chunks] == ["好的", ""]
        result = chunks[-1].result
        assert result.reasoning_content == "先查预算。"
        assert result.content == "好的"

    async def test_comment_and_blank_frames_are_ignored(self, provider_factory):
        """Heartbeat/keep-alive frames carry no payload and must not abort."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, text=": ping\n\n\n" + _sse(_delta(content="ok")),
            )

        chunks = await self._collect(provider_factory(handler))
        assert chunks[-1].result.content == "ok"

    async def test_done_marker_terminates_the_stream(self, provider_factory):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_sse(_delta(content="ok")))

        chunks = await self._collect(provider_factory(handler))
        # Exactly one terminal chunk, carrying the result.
        assert sum(1 for chunk in chunks if chunk.result is not None) == 1
        assert chunks[-1].result is not None

    async def test_stream_cut_before_done_is_bad_response(self, provider_factory):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_sse(_delta(content="半"), done=False))

        received: list[str] = []
        with pytest.raises(ProviderError) as exc_info:
            async for chunk in provider_factory(handler).complete_with_tools_stream(
                [{"role": "user", "content": "hi"}],
                model=MODEL, max_tokens=64, timeout=5.0, tools=TOOLS,
            ):
                received.append(chunk.delta)
        # Deltas already forwarded are not retracted; the error marks the turn
        # as incomplete so it cannot be mistaken for a finished answer — and it
        # is not retryable, since a retry would resend the same half.
        assert received == ["半"]
        assert exc_info.value.reason == "bad_response"
        assert exc_info.value.retryable is False

    async def test_connection_drop_mid_stream_is_transport(self, provider_factory):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=_BrokenStream())

        received: list[str] = []
        with pytest.raises(ProviderError) as exc_info:
            async for chunk in provider_factory(handler).complete_with_tools_stream(
                [{"role": "user", "content": "hi"}],
                model=MODEL, max_tokens=64, timeout=5.0, tools=TOOLS,
            ):
                received.append(chunk.delta)
        # The frame before the drop really did arrive — otherwise this test
        # would pass vacuously.
        assert received == ["a"]
        assert exc_info.value.reason == "transport"
        # Retryability flips at the first forwarded delta: a retry would replay
        # text the caller has already seen.
        assert exc_info.value.retryable is False

    async def test_drop_before_any_delta_is_retryable(self, provider_factory):
        """Nothing was forwarded yet, so a retry is safe and still on offer."""

        class _DropsImmediately(httpx.AsyncByteStream):
            async def __aiter__(self):
                raise httpx.RemoteProtocolError("peer closed the stream")
                yield b""  # keeps this an async generator (never reached)

            async def aclose(self) -> None:
                pass

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=_DropsImmediately())

        with pytest.raises(ProviderError) as exc_info:
            async for _ in provider_factory(handler).complete_with_tools_stream(
                [{"role": "user", "content": "hi"}],
                model=MODEL, max_tokens=64, timeout=5.0, tools=TOOLS,
            ):
                pass
        assert exc_info.value.reason == "transport"
        assert exc_info.value.retryable is True

    async def test_empty_data_frames_are_keepalives(self, provider_factory):
        """``data:`` with no payload is legal SSE and used as keep-alive; it
        must not be decoded as a malformed frame."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, text="data:\n\ndata:   \n\n" + _sse(_delta(content="ok")),
            )

        chunks = await self._collect(provider_factory(handler))
        assert chunks[-1].result.content == "ok"

    async def test_index_less_continuation_stays_one_call(self, provider_factory):
        """A fragment without ``index`` continues the open call. Indexing it by
        ``len()`` would open a new slot and split the arguments."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_sse(
                json.dumps({"choices": [{"delta": {"tool_calls": [{
                    "index": 0, "id": "call_a",
                    "function": {"name": "budget.get", "arguments": ""},
                }]}}]}),
                json.dumps({"choices": [{"delta": {"tool_calls": [{
                    "function": {"arguments": '{"month":'},
                }]}}]}),
                json.dumps({"choices": [{"delta": {"tool_calls": [{
                    "function": {"arguments": ' "2026-08"}'},
                }]}}]}),
            ))

        chunks = await self._collect(provider_factory(handler))
        assert chunks[-1].result.tool_calls == (
            ToolCall(id="call_a", name="budget.get", arguments='{"month": "2026-08"}'),
        )

    async def test_choices_as_mapping_is_bad_response(self, provider_factory):
        """A shape error inside a frame is a malformed frame — it must not
        escape the generator as a bare KeyError."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_sse(
                json.dumps({"choices": {"0": {"delta": {"content": "x"}}}}),
                _delta(content="ok"),
            ))

        with pytest.raises(ProviderError) as exc_info:
            async for _ in provider_factory(handler).complete_with_tools_stream(
                [{"role": "user", "content": "hi"}],
                model=MODEL, max_tokens=64, timeout=5.0, tools=TOOLS,
            ):
                pass
        assert exc_info.value.reason == "bad_response"

    async def test_tool_call_streamed_without_id_passes_through_empty(self, provider_factory):
        """与一次性路径同口径：流式组装的调用缺 id 时如实给出 ``""``。"""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_sse(
                json.dumps({"choices": [{"delta": {"tool_calls": [{
                    "index": 0, "function": {"name": "budget_get", "arguments": "{}"},
                }]}}]}),
            ))

        chunks = await self._collect(provider_factory(handler))
        assert chunks[-1].result.tool_calls == (
            ToolCall(id="", name="budget_get", arguments="{}"),
        )

    async def test_malformed_frame_is_bad_response(self, provider_factory):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_sse("{not json"))

        with pytest.raises(ProviderError) as exc_info:
            async for _ in provider_factory(handler).complete_with_tools_stream(
                [{"role": "user", "content": "hi"}],
                model=MODEL, max_tokens=64, timeout=5.0, tools=TOOLS,
            ):
                pass
        assert exc_info.value.reason == "bad_response"

    async def test_wrong_typed_usage_is_bad_response(self, provider_factory):
        """A malformed field must not escape as a bare ValueError — the stream
        path classifies it the same way the non-streaming path does."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_sse(json.dumps({
                "choices": [], "usage": {"prompt_tokens": "not-a-number"},
            })))

        with pytest.raises(ProviderError) as exc_info:
            async for _ in provider_factory(handler).complete_with_tools_stream(
                [{"role": "user", "content": "hi"}],
                model=MODEL, max_tokens=64, timeout=5.0, tools=TOOLS,
            ):
                pass
        assert exc_info.value.reason == "bad_response"

    async def test_wrong_typed_tool_call_fragment_is_bad_response(self, provider_factory):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_sse(json.dumps({
                "choices": [{"delta": {"tool_calls": ["not-an-object"]}}],
            })))

        with pytest.raises(ProviderError) as exc_info:
            async for _ in provider_factory(handler).complete_with_tools_stream(
                [{"role": "user", "content": "hi"}],
                model=MODEL, max_tokens=64, timeout=5.0, tools=TOOLS,
            ):
                pass
        assert exc_info.value.reason == "bad_response"

    async def test_stream_http_error_classification(self, provider_factory):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="upstream down")

        with pytest.raises(ProviderError) as exc_info:
            async for _ in provider_factory(handler).complete_with_tools_stream(
                [{"role": "user", "content": "hi"}],
                model=MODEL, max_tokens=64, timeout=5.0, tools=TOOLS,
            ):
                pass
        assert exc_info.value.status == 500
        assert exc_info.value.retryable is True
        assert exc_info.value.reason == "http_5xx"


# ── error contract ──────────────────────────────────────────────────────


class TestErrorContract:
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
    async def test_http_error_classification(
        self, provider_factory, status, retryable, reason,
    ):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status, json={"error": {"message": "SECRET-BODY-MARKER"}},
            )

        with pytest.raises(ProviderError) as exc_info:
            await provider_factory(handler).complete_with_tools(
                [{"role": "user", "content": "hi"}],
                model=MODEL, max_tokens=8, timeout=1.0, tools=TOOLS,
            )
        assert exc_info.value.status == status
        assert exc_info.value.retryable is retryable
        assert exc_info.value.reason == reason
        # Provider error bodies may echo user input — never in str().
        assert "SECRET-BODY-MARKER" not in str(exc_info.value)

    async def test_timeout_is_not_retryable(self, provider_factory):
        """Same semantics as the fast path: the client's own cancel line is the
        real deadline, so retrying a slow provider only stacks latency."""
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("too slow")

        with pytest.raises(ProviderError) as exc_info:
            await provider_factory(handler).complete_with_tools(
                [{"role": "user", "content": "hi"}],
                model=MODEL, max_tokens=8, timeout=1.0, tools=TOOLS,
            )
        assert exc_info.value.retryable is False
        assert exc_info.value.reason == "timeout"

    async def test_stream_timeout_is_not_retryable(self, provider_factory):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("too slow")

        with pytest.raises(ProviderError) as exc_info:
            async for _ in provider_factory(handler).complete_with_tools_stream(
                [{"role": "user", "content": "hi"}],
                model=MODEL, max_tokens=8, timeout=1.0, tools=TOOLS,
            ):
                pass
        assert exc_info.value.retryable is False
        assert exc_info.value.reason == "timeout"

    async def test_transport_error_is_retryable(self, provider_factory):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with pytest.raises(ProviderError) as exc_info:
            await provider_factory(handler).complete_with_tools(
                [{"role": "user", "content": "hi"}],
                model=MODEL, max_tokens=8, timeout=1.0, tools=TOOLS,
            )
        assert exc_info.value.retryable is True
        assert exc_info.value.reason == "transport"

    async def test_malformed_body_is_retryable(self, provider_factory):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"unexpected": "shape"})

        with pytest.raises(ProviderError) as exc_info:
            await provider_factory(handler).complete_with_tools(
                [{"role": "user", "content": "hi"}],
                model=MODEL, max_tokens=8, timeout=1.0, tools=TOOLS,
            )
        assert exc_info.value.retryable is True
        assert exc_info.value.reason == "bad_response"

    async def test_key_never_reaches_error_message_or_logs(
        self, provider_factory, caplog,
    ):
        def handler(request: httpx.Request) -> httpx.Response:
            # The key travels in the header only; this echoes it back the way a
            # misbehaving gateway might.
            assert request.headers["authorization"] == f"Bearer {API_KEY}"
            return httpx.Response(
                401, json={"error": {"message": f"invalid api key: {API_KEY}"}},
            )

        with caplog.at_level(logging.DEBUG):
            with pytest.raises(ProviderError) as exc_info:
                await provider_factory(handler).complete_with_tools(
                    [{"role": "user", "content": "hi"}],
                    model=MODEL, max_tokens=8, timeout=1.0, tools=TOOLS,
                )

        assert API_KEY not in str(exc_info.value)
        assert API_KEY not in repr(exc_info.value)
        assert API_KEY not in caplog.text

    async def test_key_never_reaches_stream_error_path(self, provider_factory, caplog):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403, json={"error": {"message": f"key rejected: {API_KEY}"}},
            )

        with caplog.at_level(logging.DEBUG):
            with pytest.raises(ProviderError) as exc_info:
                async for _ in provider_factory(handler).complete_with_tools_stream(
                    [{"role": "user", "content": "hi"}],
                    model=MODEL, max_tokens=8, timeout=1.0, tools=TOOLS,
                ):
                    pass

        assert API_KEY not in str(exc_info.value)
        assert API_KEY not in caplog.text

    async def test_provider_repr_hides_the_key(self, provider_factory):
        """The default object repr would hide the key too — which is exactly
        why the test asserts the *redacting* repr is the one in place: delete
        it (or make the class a dataclass, whose repr prints every field) and
        the key is one ``%r`` log line away from leaking."""
        provider = provider_factory(
            lambda request: httpx.Response(200, json=_body({"role": "assistant", "content": "ok"})),
        )
        assert API_KEY not in repr(provider)
        assert repr(provider).startswith("<DeepSeekProvider url=")
