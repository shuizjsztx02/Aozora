"""T-M7-02g: SSE 端点与流式测试样板。

验收映射（docs/milestones/production-execution-plan.md T-M7-02g）：
- 帧格式                    → TestFrameFormat
- 事件顺序                  → TestStreamEvents
- heartbeat                 → TestHeartbeat
- 事件逐条下发而非攒批      → TestIncrementalDelivery（直连 ASGI）
- X-Accel-Buffering 响应头  → TestStreamEvents::test_headers_disable_buffering
- 开关关闭时端点不可用、旧客户端行为不变 → TestFeatureFlag
- 配额（日 + 会话）在跑 loop 之前 → TestQuota
- 落库与多轮上下文          → TestPersistence
- 写路径（挂起确认卡 / 自动执行）→ TestWritePath
- 失败收尾                  → TestFailure

**两条测试通道，各有各的理由：**

1. ``httpx.AsyncClient(ASGITransport(app))`` —— 卡面建议的样板，绝大多数用例走它。
   注意 httpx 0.28 的 ``ASGITransport`` 会**等应用跑完**才返回（``await self.app(...)``
   之后才构造 Response），所以它拿到的是一次性收全的响应体：**证明不了「逐条下发」，
   也模拟不了断连**。
2. :func:`open_direct_stream` —— 直连 ASGI 的驱动器（自己实现 ``receive`` / ``send``），
   每收到一块就落进 ``chunks``。只有它能测「帧在生成时就已发出」与「断连即取消」。
   spec 版本取 **2.3** 与 uvicorn 0.52 实况一致（``{"version": "3.0", "spec_version":
   "2.3"}``），因此走的是 Starlette 的 task-group 分支：断连 = ``receive`` 返回
   ``http.disconnect`` → anyio 取消 ``stream_response``。

全部用例无网络：agent provider 是脚本化的假 provider，DB 是临时文件库（不用
``tmp_path``——本机 basetemp 权限已坏，见 ``pytest-basetemp-broken`` 记录）。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import shutil
import tempfile
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from src.common.config import Settings, reset_config, set_config
from src.models.ai import AIConversation, AIConversationEvent, AIUsage
from src.models.ledger import Category, CategoryKind
from src.modules.ai import stream as S
from src.modules.ai.provider import (
    ProviderError,
    ProviderResult,
    ReasoningLevel,
    ToolCall,
)
from src.modules.ai.tools import DOMAINS, tools_for_domains

MODEL = "deepseek-flash"
DEVICE = "stream-device-1"
CATEGORY = "餐饮"


# ── 脚本化 provider 与结果构造 ─────────────────────────────────────────────


def _text(
    content: str, *, prompt: int = 5, completion: int = 7, reasoning: str = "",
) -> ProviderResult:
    return ProviderResult(
        content=content,
        prompt_tokens=prompt,
        completion_tokens=completion,
        reasoning_content=reasoning,
    )


def _tool_turn(*calls: ToolCall, content: str = "") -> ProviderResult:
    return ProviderResult(
        content=content, prompt_tokens=5, completion_tokens=7, tool_calls=calls,
    )


def _call(name: str, args: dict[str, Any] | None = None, *, id: str = "") -> ToolCall:
    return ToolCall(id=id, name=name, arguments=json.dumps(args or {}, ensure_ascii=False))


def _tx_args(**overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "type": "expense",
        "amountCents": 1250,
        "categoryName": CATEGORY,
        "bizDate": "2026-09-18",
        "note": "午饭",
    }
    args.update(overrides)
    return args


def _budget_args(**overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {"period": "2026-09"}
    args.update(overrides)
    return args


def _blocked(release: asyncio.Event, result: ProviderResult):
    """脚本项：等到 ``release`` 被置位才返回——用于制造「空闲期」。"""

    async def _wait() -> ProviderResult:
        await release.wait()
        return result

    return _wait


class ScriptedProvider:
    """脚本化的 agent provider：第 n 次 ``complete_with_tools`` 用 ``script[n]``。

    脚本项可以是 ``ProviderResult``、异常实例、可调用对象，或返回可等待对象的
    可调用对象（后者用于「请求进行中」制造空闲 / 断连）。脚本用尽后重复最后一项。
    """

    name = "scripted"

    def __init__(self, script: list[Any] | None = None) -> None:
        self.script: list[Any] = list(script or [_text("你好呀！")])
        self.calls: list[dict[str, Any]] = []
        #: 被取消的次数——断连用例据此断言「在途请求真的被中断了」。
        self.cancelled = 0

    def reset(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls = []
        self.cancelled = 0

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
        index = len(self.calls)
        self.calls.append({
            "messages": deepcopy(messages),
            "tools": list(tools),
            "reasoning": reasoning,
            "timeout": timeout,
        })
        item: Any = self.script[index] if index < len(self.script) else self.script[-1]
        try:
            if callable(item):
                item = item()
            if inspect.isawaitable(item):
                item = await item
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        if isinstance(item, Exception):
            raise item
        return item

    def sent(self, index: int = 0) -> str:
        """第 index 次请求的全部消息文本（用于「载荷里有没有某内容」类断言）。"""
        return json.dumps(self.calls[index]["messages"], ensure_ascii=False)


class FakeFastProvider:
    """快路径的假 provider（只实现 ``complete`` / ``complete_with_images``）。"""

    name = "fake-fast"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def complete(self, messages, *, model, max_tokens, timeout):
        self.calls.append({"messages": messages})
        return ProviderResult(
            content=json.dumps({
                "type": "expense", "amountCents": 1250, "category": CATEGORY,
                "note": "午饭", "confidence": "high",
            }, ensure_ascii=False),
            prompt_tokens=11, completion_tokens=22,
        )

    async def complete_with_images(self, messages, *, model, max_tokens, timeout):
        return await self.complete(
            messages, model=model, max_tokens=max_tokens, timeout=timeout,
        )


# ── 帧解析 ────────────────────────────────────────────────────────────────


def parse_frames(body: str) -> list[tuple[str, dict[str, Any]]]:
    """SSE 响应体 → ``[(event, payload), …]``，注释帧（心跳）被跳过。"""
    frames: list[tuple[str, dict[str, Any]]] = []
    for block in body.split("\n\n"):
        if not block.strip() or block.lstrip().startswith(":"):
            continue
        event = ""
        data = ""
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data = line[len("data: "):]
        frames.append((event, json.loads(data) if data else {}))
    return frames


def event_names(body: str) -> list[str]:
    return [event for event, _ in parse_frames(body)]


def payload_of(body: str, event: str) -> dict[str, Any]:
    for name, payload in parse_frames(body):
        if name == event:
            return payload
    raise AssertionError(f"no {event!r} frame in: {body!r}")


# ── 直连 ASGI 的流式驱动器 ────────────────────────────────────────────────


class DirectStream:
    """自己驱动 ASGI 的一次请求：逐块拿响应、可随时模拟断连。"""

    def __init__(self, task: asyncio.Task[None]) -> None:
        self._task = task
        self.status: int | None = None
        self.headers: dict[str, str] = {}
        self.chunks: list[str] = []
        self._disconnect = asyncio.Event()

    @property
    def body(self) -> str:
        return "".join(self.chunks)

    async def wait_for(self, needle: str, *, timeout: float = 5.0) -> None:
        """等到已收到的正文里出现 ``needle``（轮询 5 ms；超时即断言失败）。"""
        deadline = asyncio.get_running_loop().time() + timeout
        while needle not in self.body:
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(
                    f"timed out waiting for {needle!r}; got: {self.body!r}"
                )
            await asyncio.sleep(0.005)

    async def disconnect(self, *, timeout: float = 5.0) -> None:
        """模拟客户端断连，并等应用退出。"""
        self._disconnect.set()
        await asyncio.wait_for(self._task, timeout=timeout)

    async def wait_done(self, *, timeout: float = 5.0) -> None:
        await asyncio.wait_for(self._task, timeout=timeout)


def open_direct_stream(
    app: Any,
    *,
    path: str,
    payload: dict[str, Any],
    token: str,
) -> DirectStream:
    """启动一次直连 ASGI 请求并立刻返回（不等待应用跑完）。"""
    raw = json.dumps(payload).encode()
    holder: dict[str, Any] = {}

    scope = {
        "type": "http",
        # 与 uvicorn 0.52 实况一致（spec 2.3 → Starlette 的 task-group 分支）
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"test"),
            (b"content-type", b"application/json"),
            (b"authorization", f"Bearer {token}".encode()),
        ],
        "server": ("test", 80),
        "client": ("test", 12345),
    }

    body_sent = False

    async def receive() -> dict[str, Any]:
        nonlocal body_sent
        if not body_sent:
            body_sent = True
            return {"type": "http.request", "body": raw, "more_body": False}
        await holder["stream"]._disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        stream: DirectStream = holder["stream"]
        if message["type"] == "http.response.start":
            stream.status = message["status"]
            stream.headers = {
                k.decode(): v.decode() for k, v in message.get("headers", [])
            }
        elif message["type"] == "http.response.body":
            body = message.get("body", b"")
            if body:
                stream.chunks.append(body.decode())

    async def run() -> None:
        await app(scope, receive, send)

    task = asyncio.create_task(run())
    stream = DirectStream(task)
    holder["stream"] = stream
    return stream


# ── 环境夹具 ──────────────────────────────────────────────────────────────


class StreamEnv:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)

    async def post_turn(
        self,
        text: str,
        *,
        conversation_id: str | None = None,
        book_id: str | None = None,
    ) -> httpx.Response:
        body: dict[str, Any] = {"input": text, "book_id": book_id or self.book_id}
        if conversation_id:
            body["conversation_id"] = conversation_id
        return await self.client.post(
            "/ai/agent", json=body, headers=self.auth_headers,
        )

    async def turn(
        self,
        text: str,
        *,
        conversation_id: str | None = None,
        book_id: str | None = None,
    ) -> tuple[httpx.Response, str]:
        """跑一轮并返回 ``(响应, 正文)``。正文用 ``read()`` 完整收下。"""
        response = await self.post_turn(
            text, conversation_id=conversation_id, book_id=book_id,
        )
        return response, response.text


@pytest_asyncio.fixture()
async def stream_env():
    """临时文件库 + 假 provider + 已登录 guest + 一个账本与一个分类。"""
    from src.common.database import get_engine, get_session_factory, reset_engine
    from src.main import app
    from src.models.base import Base
    from src.modules.ai.provider import get_agent_provider, get_llm_provider
    from src.modules.ai.service import _ai_rate_limits
    from src.modules.auth.dependencies import _rate_limits

    reset_config()
    _rate_limits.clear()
    _ai_rate_limits.clear()

    db_dir = Path(tempfile.mkdtemp(prefix="aozora_test_stream_"))
    cfg = Settings(
        database_url=f"sqlite+aiosqlite:///{(db_dir / 'test.db').as_posix()}",
        secret_key="test-secret",
        env="test",
        api_prefix="",
        ai_agent_enabled=True,
        ai_agent_provider_api_key=None,
        ai_agent_provider_model=MODEL,
        ai_agent_heartbeat_seconds=0.05,
        ai_agent_step_timeout_seconds=5.0,
        ai_daily_quota_guest=20,
    )
    set_config(cfg)
    reset_engine()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    provider = ScriptedProvider()
    fast = FakeFastProvider()
    app.dependency_overrides[get_agent_provider] = lambda: provider
    app.dependency_overrides[get_llm_provider] = lambda: fast

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        guest = await client.post(
            "/auth/guest", json={"device_id": DEVICE, "nickname": "stream-tester"},
        )
        assert guest.status_code == 200
        user_id = uuid.UUID(guest.json()["user_id"])
        token = guest.json()["tokens"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        book = await client.post("/books", json={"name": "日常"}, headers=headers)
        assert book.status_code == 201
        book_id = book.json()["id"]

        # 一个本账本的支出分类：写工具要按名字解析到它（模型永不输出 UUID）。
        async with get_session_factory()() as session:
            session.add(Category(
                id=uuid.uuid4(), owner_id=user_id, book_id=uuid.UUID(book_id),
                kind=CategoryKind.EXPENSE, name=CATEGORY,
            ))
            await session.commit()

        yield StreamEnv(
            app=app,
            client=client,
            cfg=cfg,
            provider=provider,
            fast_provider=fast,
            engine=engine,
            factory=get_session_factory(),
            user_id=user_id,
            book_id=book_id,
            auth_headers=headers,
        )

    app.dependency_overrides.clear()
    await engine.dispose()
    reset_config()
    reset_engine()
    _rate_limits.clear()
    _ai_rate_limits.clear()
    shutil.rmtree(db_dir, ignore_errors=True)


async def _rows(env: StreamEnv, model: Any, **filters: Any) -> list[Any]:
    async with env.factory() as session:
        stmt = select(model)
        for column, value in filters.items():
            stmt = stmt.where(getattr(model, column) == value)
        return list((await session.scalars(stmt)).all())


# ── 1. 帧格式 ─────────────────────────────────────────────────────────────


class TestFrameFormat:
    def test_frame_is_event_and_single_data_line(self):
        frame = S.format_frame(S.EventType.PHASE, {"phase": "thinking", "step": 1})
        assert frame.startswith("event: phase\n")
        assert frame.endswith("\n\n")
        assert json.loads(frame.split("data: ", 1)[1]) == {"phase": "thinking", "step": 1}

    def test_payload_text_with_newlines_stays_on_one_data_line(self):
        """载荷里含换行也不能破帧——否则客户端会把它当成多行 data 或新帧。"""
        frame = S.format_frame(S.EventType.DELTA, {"text": "第一行\n第二行"})
        assert frame.count("\n") == 3  # event 行 + data 行 + 结束空行
        assert json.loads(frame.split("data: ", 1)[1]) == {"text": "第一行\n第二行"}

    def test_heartbeat_is_a_comment_frame(self):
        assert S.HEARTBEAT_FRAME.startswith(":")
        assert S.HEARTBEAT_FRAME.endswith("\n\n")
        # 注释帧必须能被解析器整体忽略（客户端与本文的 parse_frames 同口径）
        assert parse_frames(S.HEARTBEAT_FRAME) == []

    @pytest.mark.parametrize("event", list(S.EventType))
    def test_every_event_type_renders(self, event: S.EventType):
        assert S.format_frame(event, {"k": "v"}).startswith(f"event: {event.value}\n")

    def test_eight_event_types_exactly(self):
        assert [e.value for e in S.EventType] == [
            "phase", "tool_call", "tool_result", "confirm",
            "device_request", "delta", "done", "error",
        ]

    def test_every_registered_tool_has_progress_text(self):
        """``tool_call`` 的文案是**人看的**，漏一个只会退化到兜底文案——
        但新工具掉队必须被发现，否则步骤卡片长期显示「正在处理…」。"""
        missing = [
            tool.name for tool in tools_for_domains(DOMAINS)
            if tool.name not in S.TOOL_PROGRESS
        ]
        assert missing == []
        # 反向也不能有孤儿：文案表里的名字必须都在注册表里
        registered = {tool.name for tool in tools_for_domains(DOMAINS)}
        assert set(S.TOOL_PROGRESS) <= registered

    def test_outcome_summary_carries_no_raw_data(self):
        from src.modules.ai.tools import ToolOutcome

        ok = S.summarize_outcome(ToolOutcome.result({"rows": [1, 2, 3], "secret": "x"}))
        assert ok == {"status": "ok"}
        bad = S.summarize_outcome(ToolOutcome.failure("unknown_tool", argument="x.y"))
        assert bad["status"] == "error" and bad["code"] == "unknown_tool"
        # 模型可控的 argument 不进帧
        assert "x.y" not in json.dumps(bad, ensure_ascii=False)


# ── 2. 事件顺序与响应头 ───────────────────────────────────────────────────


class TestStreamEvents:
    async def test_two_step_turn_event_order(self, stream_env: StreamEnv):
        stream_env.provider.reset([
            _tool_turn(_call("budget_list", _budget_args())),
            _text("这个月还没有设置预算。"),
        ])
        response, body = await stream_env.turn("看看这个月的预算")

        assert response.status_code == 200
        assert event_names(body) == [
            "phase",        # step 1 入口（thinking）
            "phase",        # 工具批次入口（tool_calling）
            "tool_call",
            "tool_result",
            "phase",        # step 2 入口
            "delta",
            "done",
        ]

    async def test_headers_disable_buffering(self, stream_env: StreamEnv):
        """R-PROD-22 第 1 项：不改服务器 nginx，靠响应头让 nginx 关掉缓冲。"""
        stream_env.provider.reset([_text("你好呀！")])
        response, _ = await stream_env.turn("你好")

        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["x-accel-buffering"] == "no"
        assert response.headers["cache-control"] == "no-cache"

    async def test_tool_call_frame_carries_progress_text(self, stream_env: StreamEnv):
        stream_env.provider.reset([
            _tool_turn(_call("budget_list", _budget_args())),
            _text("没有预算。"),
        ])
        _, body = await stream_env.turn("看看预算")

        frame = payload_of(body, "tool_call")
        assert frame["tool"] == "budget_list"
        assert frame["message"] == S.TOOL_PROGRESS["budget_list"]
        assert frame["step"] == 1

    async def test_tool_result_frame_is_a_summary(self, stream_env: StreamEnv):
        stream_env.provider.reset([
            _tool_turn(_call("budget_list", _budget_args())),
            _text("没有预算。"),
        ])
        _, body = await stream_env.turn("看看预算")

        frame = payload_of(body, "tool_result")
        assert frame["status"] == "ok"
        assert set(frame) == {"tool", "step", "status"}

    async def test_failed_tool_reports_safe_error_code(self, stream_env: StreamEnv):
        """未知工具名沿用 02c 的降级契约：回合照常，帧里给安全枚举码。

        载荷里的 ``tool`` 是模型给的字符串（步骤卡片要显示它），但**错误文案
        是固定枚举文本**——模型可控的内容不进 message，也不回显原始参数。
        """
        stream_env.provider.reset([
            _tool_turn(_call("not_a_tool", {"secret": "用户原话"})),
            _text("换个办法。"),
        ])
        _, body = await stream_env.turn("随便说点什么")

        frame = payload_of(body, "tool_result")
        assert frame["status"] == "error"
        assert frame["code"] == "unknown_tool"
        from src.modules.ai.tools import _TOOL_ERROR_MESSAGES

        assert frame["message"] == _TOOL_ERROR_MESSAGES["unknown_tool"]
        assert "secret" not in json.dumps(frame, ensure_ascii=False)
        assert "用户原话" not in body

    async def test_phase_reports_reasoning_escalation(self, stream_env: StreamEnv):
        """入口判浅、实际多步 → 第 2 步升深档（02d 的 Step 升级），
        ``phase`` 帧如实反映每一档。"""
        stream_env.provider.reset([
            _tool_turn(_call("budget_list", _budget_args())),
            _text("没有预算。"),
        ])
        _, body = await stream_env.turn("看看预算")

        phases = [p for name, p in parse_frames(body) if name == "phase"]
        assert [p["reasoning"] for p in phases if p["phase"] == "thinking"] == [
            "fast", "deep",
        ]
        assert phases[1]["phase"] == "tool_calling"

    async def test_done_payload_shape(self, stream_env: StreamEnv):
        stream_env.provider.reset([
            _tool_turn(_call("budget_list", _budget_args())),
            _text("这个月还没有设置预算。"),
        ])
        _, body = await stream_env.turn("看看预算")
        done = payload_of(body, "done")

        assert done["stop_reason"] == "completed"
        assert done["steps"] == 2
        assert done["tool_calls"] == 1
        assert done["writes"] == 0
        assert done["usage"] == {"prompt_tokens": 10, "completion_tokens": 14}
        assert done["proposals"] == [] and done["executions"] == []
        assert done["pending_input"] == []
        assert uuid.UUID(done["conversation_id"])  # 下一轮要用
        assert event_names(body)[-1] == "done"

    async def test_delta_carries_the_answer_text(self, stream_env: StreamEnv):
        stream_env.provider.reset([_text("你好呀，今天想记点什么？")])
        _, body = await stream_env.turn("你好")

        assert payload_of(body, "delta")["text"] == "你好呀，今天想记点什么？"
        assert event_names(body) == ["phase", "delta", "done"]

    async def test_empty_answer_emits_no_delta(self, stream_env: StreamEnv):
        """空回答（thinking 吃光 token 的形态）不该发一个空气泡——
        原因由 ``done.stop_reason`` 交代。"""
        stream_env.provider.reset([_text("")])
        _, body = await stream_env.turn("你好")

        assert event_names(body) == ["phase", "done"]
        assert payload_of(body, "done")["stop_reason"] == "empty_response"

    async def test_device_request_has_no_p1_producer(self, stream_env: StreamEnv):
        """八类事件里的 ``device_request`` 在 P1 没有生产者（无设备类工具），
        这条用例把这个事实钉住：协议支持它，但正常回合里不该出现。"""
        stream_env.provider.reset([
            _tool_turn(_call("budget_list", _budget_args())),
            _text("没有预算。"),
        ])
        _, body = await stream_env.turn("看看预算")

        assert "device_request" not in event_names(body)


# ── 3. heartbeat ──────────────────────────────────────────────────────────


class TestHeartbeat:
    async def test_idle_stream_emits_comment_frames(self, stream_env: StreamEnv):
        """空闲期发注释帧：服务器 nginx 用默认 60 s 读超时，静默即断。"""
        release = asyncio.Event()
        stream_env.provider.reset([
            _blocked(release, _tool_turn(_call("budget_list", _budget_args()))),
            _text("没有预算。"),
        ])

        async def _release_soon() -> None:
            await asyncio.sleep(0.2)  # ≈ 4 × heartbeat（0.05s）
            release.set()

        releaser = asyncio.create_task(_release_soon())
        try:
            response, body = await stream_env.turn("看看预算")
        finally:
            releaser.cancel()

        assert response.status_code == 200
        assert body.count(S.HEARTBEAT_FRAME) >= 1
        # 心跳只出现在空闲期：它在第一个 phase 之后
        assert body.index(S.HEARTBEAT_FRAME) > body.index("event: phase")

    async def test_heartbeat_does_not_disturb_the_frame_sequence(
        self, stream_env: StreamEnv,
    ):
        release = asyncio.Event()
        stream_env.provider.reset([
            _blocked(release, _text("好了。")),
        ])

        async def _release_soon() -> None:
            await asyncio.sleep(0.15)
            release.set()

        releaser = asyncio.create_task(_release_soon())
        try:
            _, body = await stream_env.turn("你好")
        finally:
            releaser.cancel()

        # 注释帧被解析器忽略，事件序列仍然干净
        assert event_names(body) == ["phase", "delta", "done"]


# ── 4. 逐条下发（直连 ASGI）──────────────────────────────────────────────


class TestIncrementalDelivery:
    async def test_frames_are_sent_while_the_turn_is_still_running(
        self, stream_env: StreamEnv,
    ):
        """**这一条是「不攒批」的仓内证据**：模型还在跑，前面的帧已经到客户端了。

        httpx 的 ASGITransport 拿不到这个结论（它等应用跑完才返回），所以走
        直连 ASGI。真机链路（含 nginx）的确认在部署后由收口执行，见 R-PROD-22。
        """
        release = asyncio.Event()
        stream_env.provider.reset([
            _blocked(release, _tool_turn(_call("budget_list", _budget_args()))),
            _text("没有预算。"),
        ])
        stream = open_direct_stream(
            stream_env.app,
            path="/ai/agent",
            payload={"input": "看看预算", "book_id": stream_env.book_id},
            token=stream_env.auth_headers["Authorization"].removeprefix("Bearer "),
        )
        try:
            # 第一个 phase 帧在模型请求**之前**发出——此时模型还没返回
            await stream.wait_for("event: phase")
            assert "tool_call" not in stream.body
            assert stream_env.provider.calls, "模型请求应当已经发出"
            release.set()
            await stream.wait_done()
        finally:
            release.set()

        assert stream.status == 200
        assert stream.headers["x-accel-buffering"] == "no"
        assert event_names(stream.body)[-1] == "done"

    async def test_disconnect_cancels_the_turn_and_persists_nothing(
        self, stream_env: StreamEnv,
    ):
        """断连 = 取消（不把最多 20 s 的在途请求跑完），且本轮不落库、连接归还。"""
        never = asyncio.Event()
        stream_env.provider.reset([_blocked(never, _text("不会走到这里"))])
        stream = open_direct_stream(
            stream_env.app,
            path="/ai/agent",
            payload={"input": "你好", "book_id": stream_env.book_id},
            token=stream_env.auth_headers["Authorization"].removeprefix("Bearer "),
        )
        await stream.wait_for("event: phase")
        conversation_id = await _only_conversation_id(stream_env)
        await stream.disconnect()

        assert stream_env.provider.cancelled == 1
        assert await _rows(stream_env, AIConversationEvent,
                           conversation_id=conversation_id) == []
        assert await _rows(stream_env, AIUsage, conversation_id=conversation_id) == []
        # 连接归还连接池：不归还的话池满之后所有请求一起挂
        pool = stream_env.engine.sync_engine.pool
        for _ in range(100):
            if pool.checkedout() == 0:
                break
            await asyncio.sleep(0.02)
        assert pool.checkedout() == 0


async def _only_conversation_id(env: StreamEnv) -> uuid.UUID:
    rows = await _rows(env, AIConversation, user_id=env.user_id)
    assert len(rows) == 1
    return rows[0].id


# ── 5. 灰度开关 ───────────────────────────────────────────────────────────


class TestFeatureFlag:
    async def test_disabled_endpoint_is_unavailable(self, stream_env: StreamEnv):
        set_config(stream_env.cfg.model_copy(update={"ai_agent_enabled": False}))
        try:
            response = await stream_env.post_turn("你好")
        finally:
            set_config(stream_env.cfg)

        assert response.status_code == 503
        assert response.json()["code"] == "agent_disabled"
        assert stream_env.provider.calls == []

    async def test_disabled_agent_leaves_the_fast_path_untouched(
        self, stream_env: StreamEnv,
    ):
        """「旧客户端行为不变」不是靠不调新端点来论证的——这里真的把快路径跑一遍。"""
        set_config(stream_env.cfg.model_copy(update={"ai_agent_enabled": False}))
        try:
            response = await stream_env.client.post(
                "/ai/interpret", json={"input": "午饭 12 块"},
                headers=stream_env.auth_headers,
            )
        finally:
            set_config(stream_env.cfg)

        assert response.status_code == 200
        assert response.json()["output"]["amountCents"] == 1250
        assert stream_env.fast_provider.calls  # 走的是快路径 provider

    async def test_missing_agent_key_is_unavailable(self, stream_env: StreamEnv):
        """没有 agent provider（未配 DeepSeek key）时端点照样挂了，但不可用。"""
        from src.modules.ai.provider import get_agent_provider

        stream_env.app.dependency_overrides[get_agent_provider] = lambda: None
        try:
            response = await stream_env.post_turn("你好")
        finally:
            stream_env.app.dependency_overrides[get_agent_provider] = (
                lambda: stream_env.provider
            )

        assert response.status_code == 503
        assert response.json()["code"] == "ai_unavailable"


# ── 6. 配额 ───────────────────────────────────────────────────────────────


class TestQuota:
    async def test_daily_quota_blocks_before_the_loop(self, stream_env: StreamEnv):
        """02f 的交接项 1：agent 路径必须一并吃 guest 日配额，且与快路径同一份实现。"""
        async with stream_env.factory() as session:
            for _ in range(stream_env.cfg.ai_daily_quota_guest):
                session.add(AIUsage(
                    user_id=stream_env.user_id, provider="seed", model="seed",
                    tokens=1, cost=0, request_id="seed",
                ))
            await session.commit()

        response = await stream_env.post_turn("你好")

        assert response.status_code == 429
        assert response.json()["code"] == "ai_quota_exceeded"
        assert stream_env.provider.calls == []

    async def test_conversation_turn_limit_blocks_the_next_turn(
        self, stream_env: StreamEnv,
    ):
        stream_env.provider.reset([_text("你好呀！")])
        _, body = await stream_env.turn("你好")
        conversation_id = payload_of(body, "done")["conversation_id"]

        set_config(stream_env.cfg.model_copy(
            update={"ai_agent_conversation_max_turns": 1},
        ))
        try:
            response = await stream_env.post_turn("再来一句", conversation_id=conversation_id)
        finally:
            set_config(stream_env.cfg)

        assert response.status_code == 429
        assert response.json()["code"] == "ai_quota_exceeded"

    async def test_unknown_conversation_is_404(self, stream_env: StreamEnv):
        response = await stream_env.post_turn("你好", conversation_id=str(uuid.uuid4()))
        assert response.status_code == 404
        assert response.json()["code"] == "conversation_not_found"

    async def test_foreign_book_is_404(self, stream_env: StreamEnv):
        response = await stream_env.post_turn("你好", book_id=str(uuid.uuid4()))
        assert response.status_code == 404

    async def test_invalid_book_id_is_422(self, stream_env: StreamEnv):
        response = await stream_env.post_turn("你好", book_id="not-a-uuid")
        assert response.status_code == 422
        assert response.json()["code"] == "invalid_book_id"


# ── 7. 落库与多轮上下文 ───────────────────────────────────────────────────


class TestPersistence:
    async def test_turn_is_persisted_as_events_and_one_usage_row(
        self, stream_env: StreamEnv,
    ):
        stream_env.provider.reset([
            _tool_turn(_call("budget_list", _budget_args())),
            _text("这个月还没有设置预算。"),
        ])
        _, body = await stream_env.turn("看看预算")
        conversation_id = uuid.UUID(payload_of(body, "done")["conversation_id"])

        events = await _rows(stream_env, AIConversationEvent,
                             conversation_id=conversation_id)
        assert [e.type for e in events] == ["user", "assistant", "tool", "assistant"]
        assert [e.seq for e in events] == [1, 2, 3, 4]

        usage = await _rows(stream_env, AIUsage, conversation_id=conversation_id)
        assert len(usage) == 1  # 一个 Turn 一条行
        assert usage[0].tokens == 24  # prompt 10 + completion 14

    async def test_no_system_message_is_stored(self, stream_env: StreamEnv):
        stream_env.provider.reset([_text("你好呀！")])
        _, body = await stream_env.turn("你好")
        conversation_id = uuid.UUID(payload_of(body, "done")["conversation_id"])

        events = await _rows(stream_env, AIConversationEvent,
                             conversation_id=conversation_id)
        assert all(e.type != "system" for e in events)
        assert all(e.data.get("role") != "system" for e in events)

    async def test_second_turn_replays_the_first(self, stream_env: StreamEnv):
        stream_env.provider.reset([_text("你上句说的是「你好」。")])
        _, first = await stream_env.turn("你好")
        conversation_id = payload_of(first, "done")["conversation_id"]

        stream_env.provider.reset([_text("收到了。")])
        _, second = await stream_env.turn("再说一次", conversation_id=conversation_id)

        assert payload_of(second, "done")["conversation_id"] == conversation_id
        # 第二轮请求的载荷里能看到第一轮的问与答（窗口投影生效）
        sent = stream_env.provider.sent(0)
        assert "你好" in sent and "你上句说的是" in sent
        # 两轮各一条用量行
        rows = await _rows(stream_env, AIUsage,
                           conversation_id=uuid.UUID(conversation_id))
        assert len(rows) == 2

    async def test_guest_daily_quota_counts_agent_turns(self, stream_env: StreamEnv):
        """agent 轮次进同一张用量表 → 日配额天然对齐（02f 交接项 1 的另一半）。"""
        stream_env.provider.reset([_text("你好呀！")])
        await stream_env.turn("你好")

        rows = await _rows(stream_env, AIUsage, user_id=stream_env.user_id)
        assert len(rows) == 1 and rows[0].conversation_id is not None


# ── 8. 写路径 ─────────────────────────────────────────────────────────────


class TestWritePath:
    async def test_multi_step_write_hangs_and_emits_confirm(
        self, stream_env: StreamEnv,
    ):
        """多步写入一律出确认卡（02e 的策略）——P1 的确认卡走既有 v2 链路，
        所以这里只要求「帧与 done 都带上完整提案」，不接审批通道。"""
        stream_env.provider.reset([
            _tool_turn(_call("category_list")),
            _tool_turn(_call("transaction_add", _tx_args())),
            _text("已经帮你拟好了。"),
        ])
        _, body = await stream_env.turn("记一笔午饭 12 块")

        confirm = payload_of(body, "confirm")
        proposal = confirm["proposal"]
        assert proposal["action"] == "add_tx"
        assert proposal["requiresConfirmation"] is True
        assert proposal["proposalId"].startswith("agent-")
        assert confirm["step"] == 2

        done = payload_of(body, "done")
        assert [p["proposalId"] for p in done["proposals"]] == [proposal["proposalId"]]
        assert done["executions"] == []
        assert done["writes"] == 1
        # 挂起 = 一次写库都没发生
        from src.models.ledger import Transaction

        assert await _rows(stream_env, Transaction) == []

    async def test_hung_proposal_history_has_no_uuid(self, stream_env: StreamEnv):
        """02c 的硬约束：草案含服务端解析的 categoryId，回喂模型/落库的历史里
        必须一个 id 都没有。"""
        stream_env.provider.reset([
            _tool_turn(_call("category_list")),
            _tool_turn(_call("transaction_add", _tx_args())),
            _text("已经帮你拟好了。"),
        ])
        _, body = await stream_env.turn("记一笔午饭 12 块")
        proposal = payload_of(body, "confirm")["proposal"]
        category_id = proposal["transactions"][0]["categoryId"]

        conversation_id = uuid.UUID(payload_of(body, "done")["conversation_id"])
        events = await _rows(stream_env, AIConversationEvent,
                             conversation_id=conversation_id)
        assert category_id not in json.dumps(
            [e.data for e in events], ensure_ascii=False,
        )
        # 覆盖率哨兵：id 确实存在于提案里（否则这条断言是空转）
        assert category_id in json.dumps(proposal, ensure_ascii=False)

    async def test_step_one_write_autoexecutes(self, stream_env: StreamEnv):
        """单 Step 高置信写入沿用快路径策略：直接执行 + 撤销窗（02e 决策 3）。"""
        stream_env.provider.reset([
            _tool_turn(_call("transaction_add", _tx_args())),
            _text("记好了。"),
        ])
        _, body = await stream_env.turn("记一笔午饭 12 块")

        done = payload_of(body, "done")
        assert done["proposals"] == []
        assert len(done["executions"]) == 1
        receipt = done["executions"][0]
        assert receipt["undo_window_seconds"] == 5
        assert len(receipt["transaction_ids"]) == 1
        assert "confirm" not in event_names(body)

        from src.models.ledger import Transaction

        rows = await _rows(stream_env, Transaction)
        assert len(rows) == 1 and rows[0].amount_cents == 1250


# ── 9. 失败收尾 ───────────────────────────────────────────────────────────


class TestFailure:
    async def test_provider_error_ends_with_ai_unavailable(
        self, stream_env: StreamEnv,
    ):
        stream_env.provider.reset([
            ProviderError(status=503, retryable=True, reason="transport"),
        ])
        response, body = await stream_env.turn("你好")

        assert response.status_code == 200  # 响应头早已发出，失败只能走帧
        assert event_names(body) == ["phase", "error"]
        error = payload_of(body, "error")
        assert error["code"] == "ai_unavailable"
        # 会话索引在前置检查里已提交 → 失败帧也带 id，客户端不必重开会话
        assert error["conversation_id"] == str(
            (await _rows(stream_env, AIConversation, user_id=stream_env.user_id))[0].id
        )

    async def test_unexpected_error_is_agent_failed(self, stream_env: StreamEnv):
        stream_env.provider.reset([RuntimeError("boom: internal detail")])
        _, body = await stream_env.turn("你好")

        payload = payload_of(body, "error")
        assert payload["code"] == "agent_failed"
        assert set(payload) == {"code", "message", "conversation_id"}
        # 异常正文（可能含用户原话或库结构）绝不外泄
        assert "boom" not in body and "internal detail" not in body

    async def test_failed_turn_persists_no_events(self, stream_env: StreamEnv):
        stream_env.provider.reset([ProviderError(status=None, retryable=False, reason="timeout")])
        _, body = await stream_env.turn("你好")
        conversation_id = uuid.UUID(payload_of(body, "error")["conversation_id"])

        assert await _rows(stream_env, AIConversationEvent,
                           conversation_id=conversation_id) == []

    async def test_failed_turn_with_nothing_billed_records_nothing(
        self, stream_env: StreamEnv,
    ):
        """第一次 provider 调用就失败 = 一个 token 都没计费 → 不写用量行。

        与快路径的 ``_record_failure_usage`` 同口径（其 docstring 明说
        「ProviderError on the first attempt — no result, no tokens billed」即
        no-op）。**计费过的部分不丢**由下一条用例钉住。
        """
        stream_env.provider.reset([
            ProviderError(status=503, retryable=False, reason="transport"),
        ])
        await stream_env.turn("你好")

        assert await _rows(stream_env, AIUsage, user_id=stream_env.user_id) == []

    async def test_failure_after_a_tool_step_records_that_step_usage(
        self, stream_env: StreamEnv,
    ):
        """第 1 步成功、第 2 步挂掉：第 1 步的 token 必须记账（观察者累计）。"""
        stream_env.provider.reset([
            _tool_turn(_call("budget_list", _budget_args())),
            ProviderError(status=503, retryable=False, reason="transport"),
        ])
        _, body = await stream_env.turn("看看预算")

        assert payload_of(body, "error")["code"] == "ai_unavailable"
        rows = await _rows(stream_env, AIUsage, user_id=stream_env.user_id)
        assert len(rows) == 1
        assert rows[0].tokens == 12  # 第 1 步的 prompt 5 + completion 7
