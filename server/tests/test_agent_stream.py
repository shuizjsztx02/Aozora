"""T-M7-02g: SSE 端点与流式测试样板。

验收映射（docs/milestones/production-execution-plan.md T-M7-02g）：
- 帧格式                    → TestFrameFormat
- 事件顺序                  → TestStreamEvents
- heartbeat                 → TestHeartbeat
- 事件逐条下发而非攒批      → TestIncrementalDelivery（直连 ASGI）
- X-Accel-Buffering 响应头  → TestStreamEvents::test_headers_disable_buffering
- 开关关闭时端点不可用、旧客户端行为不变 → TestFeatureFlag
- 配额：日配额在跑 loop 之前（**会话双限已由 T20-9 移除**，用例改为「不再拦」的守护）→ TestQuota
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
from collections.abc import AsyncIterator
from copy import deepcopy
from datetime import UTC, datetime, timedelta
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
    StreamChunk,
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

    **两条交付路径共用同一份脚本取值**（``_resolve``）：``complete_with_tools``
    返回整段结果，``complete_with_tools_stream`` 把它分帧下发。loop 自 02g-2 起
    只走后者，前者仍保留——收尾请求（``_wrap_up``）刻意不流式。
    """

    name = "scripted"

    def __init__(self, script: list[Any] | None = None) -> None:
        self.script: list[Any] = list(script or [_text("你好呀！")])
        self.calls: list[dict[str, Any]] = []
        #: 被取消的次数——断连用例据此断言「在途请求真的被中断了」。
        self.cancelled = 0
        #: 摘要通路（``complete``）收到的请求与固定答复（T-M7-04c）。**刻意不走
        #: ``_resolve``**：那会消耗 loop 的脚本项，把两个通路的序号搅在一起。
        self.summaries: list[list[dict[str, str]]] = []
        self.summary_reply: Any = "用户此前问过几笔账。"

    def reset(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls = []
        self.cancelled = 0
        self.summaries = []

    async def _resolve(self, messages: list[dict], **record: Any) -> Any:
        """取第 n 个脚本项：记录调用，处理可调用 / 可等待 / 异常项。"""
        index = len(self.calls)
        self.calls.append({"messages": deepcopy(messages), **record})
        item: Any = self.script[index] if index < len(self.script) else self.script[-1]
        try:
            if callable(item):
                item = item()
            if inspect.isawaitable(item):
                item = await item
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return item

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
        item = await self._resolve(
            messages, tools=list(tools), reasoning=reasoning, timeout=timeout,
        )
        if isinstance(item, Exception):
            raise item
        return item.result if isinstance(item, StreamScript) else item

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
        item = await self._resolve(
            messages, tools=list(tools), reasoning=reasoning, timeout=timeout,
        )
        if isinstance(item, Exception):
            raise item
        if isinstance(item, StreamScript):
            for chunk in item.chunks():
                yield chunk
                if item.gap:
                    await asyncio.sleep(item.gap)
            if item.error is not None:
                # 增量已交出后断流：调用方必须当作「不可重试」（02b 的规则）。
                raise item.error
            yield StreamChunk(result=item.result)
            return
        # 默认形态：整段一帧。既有用例的断言因此不必按 4 字符/帧改写。
        if item.reasoning_content:
            yield StreamChunk(reasoning_delta=item.reasoning_content)
        if item.content:
            yield StreamChunk(delta=item.content)
        yield StreamChunk(result=item)

    def sent(self, index: int = 0) -> str:
        """第 index 次请求的全部消息文本（用于「载荷里有没有某内容」类断言）。"""
        return json.dumps(self.calls[index]["messages"], ensure_ascii=False)

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        max_tokens: int,
        timeout: float,
    ) -> ProviderResult:
        """摘要通路（T-M7-04c）：真实 provider 的 ``complete`` 是同一条补全接口。

        默认给一段形状合法的短摘要——所以**跑够轮次的用例会顺手触发一次真实压缩**，
        而不是在日志里留下一串「provider 没有 complete」的告警。要断言压缩行为
        （或它的降级）的用例换掉 ``summary_reply``（异常实例即「provider 报错」）。
        """
        self.summaries.append(deepcopy(messages))
        if isinstance(self.summary_reply, Exception):
            raise self.summary_reply
        return ProviderResult(
            content=str(self.summary_reply), prompt_tokens=3, completion_tokens=5,
        )


class StreamScript:
    """脚本项：把一次应答拆成若干分片下发（建模真实 provider 的帧粒度）。

    默认的 ``ProviderResult`` 脚本项走「整段一帧」，既有用例不必改写；需要验
    **逐帧下发 / 合并窗 / 推理通道 / 中途断流**的用例才用它显式给出规格：

    - ``chunk_size`` —— 每片字符数（实测真实链路约 4 字符/帧）；
    - ``gap`` —— 两片之间的等待。**要跨过帧泵的合并窗就得给它**（约 0.1 s）：
      窗内的同通道增量会被并成一帧，这正是生产形态；
    - ``error`` —— 分片送完后不提结果、改抛它（模拟「说到一半断流」）。
    """

    def __init__(
        self,
        result: ProviderResult,
        *,
        chunk_size: int = 4,
        gap: float = 0.0,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.chunk_size = chunk_size
        self.gap = gap
        self.error = error

    def chunks(self) -> list[StreamChunk]:
        """分片序列：推理在前、正文在后（真实 provider 的形状）。"""
        chunks: list[StreamChunk] = []
        if self.result.reasoning_content:
            chunks += [
                StreamChunk(reasoning_delta=piece)
                for piece in _split(self.result.reasoning_content, self.chunk_size)
            ]
        if self.result.content:
            chunks += [
                StreamChunk(delta=piece)
                for piece in _split(self.result.content, self.chunk_size)
            ]
        return chunks


def _split(text: str, size: int) -> list[str]:
    if size <= 0:
        return [text]
    return [text[i:i + size] for i in range(0, len(text), size)]


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
        contract_version: int | None = None,
    ) -> httpx.Response:
        body: dict[str, Any] = {"input": text, "book_id": book_id or self.book_id}
        if conversation_id:
            body["conversation_id"] = conversation_id
        if contract_version is not None:
            # 05b：客户端声明的 ActionProposal 契约版本（不传 = 缺省 1 = 老 APK）
            body["proposal_contract_version"] = contract_version
        return await self.client.post(
            "/ai/agent", json=body, headers=self.auth_headers,
        )

    async def turn(
        self,
        text: str,
        *,
        conversation_id: str | None = None,
        book_id: str | None = None,
        contract_version: int | None = None,
    ) -> tuple[httpx.Response, str]:
        """跑一轮并返回 ``(响应, 正文)``。正文用 ``read()`` 完整收下。"""
        response = await self.post_turn(
            text, conversation_id=conversation_id, book_id=book_id,
            contract_version=contract_version,
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
        # 设备通道的**类默认**钉在关闭（用例要开就自己 set_config 覆盖，见
        # TestDeviceChannel）。不钉的话本机 .env 打开它时，``tools_for_domains``
        # 的注入面会多出设备工具，契约车道两条断言一并变红。
        ai_device_channel_enabled=False,
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
        但新工具掉队必须被发现，否则步骤卡片长期显示「正在处理…」。

        按 **v2 车道**取全集（05b）：v1 车道只注入一个子集，拿它当全集会让
        v2-only 的工具既查不出缺文案、又变成反向检查里的「孤儿」。
        """
        all_tools = tools_for_domains(DOMAINS, contract_version=2)
        missing = [tool.name for tool in all_tools if tool.name not in S.TOOL_PROGRESS]
        assert missing == []
        # 反向也不能有孤儿：文案表里的名字必须都在注册表里
        assert set(S.TOOL_PROGRESS) <= {tool.name for tool in all_tools}

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
            "phase",        # 域选择（routing，step 0；T-M7-05g）
            "phase",        # step 1 入口（thinking）
            "phase",        # 工具批次入口（tool_calling）
            "tool_call",
            "tool_result",
            "phase",        # step 2 入口
            "delta",
            "done",
        ]

    async def test_routing_frame_precedes_thinking_and_carries_the_domains(
        self, stream_env: StreamEnv,
    ):
        """``phase: routing`` 的生产者（T-M7-05g）。

        三件事一起钉住：① 它是**第一帧**（域选择发生在第一个模型请求之前）；
        ② ``step`` 是 0——用 1 会被紧随其后的 ``thinking`` 覆盖，03c 预留的那行
        「正在理解你的需求…」就永远看不见；③ 载荷带 ``domains`` / ``matched``。
        """
        stream_env.provider.reset([
            _tool_turn(_call("budget_list", _budget_args())),
            _text("没有预算。"),
        ])
        _, body = await stream_env.turn("看看预算")

        first = parse_frames(body)[0]
        assert first[0] == "phase"
        assert first[1] == {
            "phase": "routing", "step": 0,
            "domains": ["budget"], "matched": True,
        }
        # 紧随其后就是第一个 Step 的 thinking（路由收窄的是注入面，不是档位）
        assert parse_frames(body)[1][1]["phase"] == "thinking"

    async def test_routing_frame_reports_the_fallback(self, stream_env: StreamEnv):
        """没命中任何信号 → 全量注入，帧上如实报 ``matched: false``。

        客户端只认 ``phase`` / ``step``（多余字段一律忽略），这两个字段是给日志
        与人看的：生产上 ``matched=false`` 的比例就是词表覆盖率。
        """
        stream_env.provider.reset([_text("你好呀！")])
        _, body = await stream_env.turn("你好")

        routing_frame = parse_frames(body)[0][1]
        assert routing_frame["matched"] is False
        assert routing_frame["domains"] == list(DOMAINS)
        # 兜底 = P1 的注入面：旧客户端看到的工具清单一个不少
        assert [t["function"]["name"] for t in stream_env.provider.calls[0]["tools"]] == [
            tool.name for tool in tools_for_domains(DOMAINS, contract_version=1)
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
        # 首帧是 routing（域选择，05g）：它不在任何一个 Step 里（step = 0），
        # **不带** reasoning——档位只属于 thinking。
        assert phases[0]["phase"] == "routing" and phases[0]["step"] == 0
        assert "reasoning" not in phases[0]
        assert [p["reasoning"] for p in phases if p["phase"] == "thinking"] == [
            "fast", "deep",
        ]
        assert [p["phase"] for p in phases] == [
            "routing", "thinking", "tool_calling", "thinking",
        ]

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
        # 两帧 phase：routing（域选择，step 0）+ step 1 入口
        assert event_names(body) == ["phase", "phase", "delta", "done"]

    async def test_empty_answer_emits_no_delta(self, stream_env: StreamEnv):
        """空回答（thinking 吃光 token 的形态）不该发一个空气泡——
        原因由 ``done.stop_reason`` 交代。"""
        stream_env.provider.reset([_text("")])
        _, body = await stream_env.turn("你好")

        assert event_names(body) == ["phase", "phase", "done"]
        assert payload_of(body, "done")["stop_reason"] == "empty_response"

    async def test_device_request_has_no_p1_producer(self, stream_env: StreamEnv):
        """八类事件里的 ``device_request`` 在 P1 形态下没有生产者。

        这条用例把这个事实钉住：协议支持它，但**闸门关闭时**（``stream_env`` 的
        默认配置，即 05f 落地前的生产形态）正常回合里不该出现。设备通道打开后的
        形状见 :class:`TestDeviceChannel`。"""
        stream_env.provider.reset([
            _tool_turn(_call("budget_list", _budget_args())),
            _text("没有预算。"),
        ])
        _, body = await stream_env.turn("看看预算")

        assert "device_request" not in event_names(body)


# ── 2.4 设备能力通道（05e）──────────────────────────────────────────────


class TestDeviceChannel:
    """T-M7-05e：``device_request`` 的生产者与上行回传（ADR-010）。

    帧级的一条端到端：**需结果类**的挂起 → 客户端上行 ``POST /ai/device-result``
    → loop 恢复 → 这一轮跑完。loop 级的两类分法与收敛（超时 / 取消 / 归属）在
    ``tests/test_ai_device_channel.py``，这里只证明**它在真实 SSE 链路上成立**
    （``_build_loop`` 的接线 + 帧的形状 + 挂起期间心跳不绝）。
    """

    @pytest_asyncio.fixture(autouse=True)
    async def _channel_on(self, stream_env: StreamEnv):
        """本类把设备通道打开（其余用例保持默认的 P1 形态）。

        单例要在 ``set_config`` **之后**清掉：timeout 是建表那一刻从配置取的
        （``devices.device_channel``），不清就会带上一个用例的配置。
        """
        from src.modules.ai import devices as D

        set_config(stream_env.cfg.model_copy(update={
            "ai_device_channel_enabled": True,
            "ai_device_timeout_seconds": 5.0,
        }))
        D._channel = None
        try:
            yield
        finally:
            set_config(stream_env.cfg)
            D._channel = None

    async def test_result_class_pauses_then_resumes_via_uplink(
        self, stream_env: StreamEnv,
    ):
        stream_env.provider.reset([
            _tool_turn(_call("device_pick_image", {"source": "gallery"})),
            _text("看到小票了，帮你记一下。"),
        ])
        stream = open_direct_stream(
            stream_env.app,
            path="/ai/agent",
            payload={"input": "拍一下这张小票", "book_id": stream_env.book_id},
            token=stream_env.auth_headers["Authorization"].removeprefix("Bearer "),
        )
        # 挂起：帧已下发（含回传凭据），这一轮停在这里
        await stream.wait_for("device_request")
        frame = payload_of(stream.body, "device_request")
        assert frame["kind"] == "result"
        assert frame["capability"] == "image.pick"
        assert frame["payload"] == {"source": "gallery"}
        assert frame["step"] == 1
        assert frame["timeout_seconds"] == 5.0
        assert frame["request_id"].startswith("dev-")
        assert "done" not in event_names(stream.body)   # ← 真的还挂着

        # 挂起期间连接照常活着：帧泵在没有事件可发时发心跳（fixture 的 0.05 s）
        await asyncio.sleep(0.2)
        assert ": ping" in stream.body

        response = await stream_env.client.post(
            "/ai/device-result",
            json={
                "request_id": frame["request_id"], "status": "ok",
                "data": {"mimeType": "image/jpeg"},
            },
            headers=stream_env.auth_headers,
        )
        assert response.status_code == 200, response.text

        await stream.wait_done()
        assert event_names(stream.body)[-1] == "done"
        # 设备回传的数据进了下一次模型请求（内部 id 已在 agent 侧剥掉——铁律 1）
        assert "image/jpeg" in stream_env.provider.sent(1)
        assert "attachmentId" not in stream_env.provider.sent(1)

    async def test_one_way_frames_are_emitted_deterministically(
        self, stream_env: StreamEnv,
    ):
        """M8-成本：触感 / 小组件刷新由服务端在成功轮确定性下发。

        它们不再是模型工具（``agent.DETERMINISTIC_DEVICE_TOOLS``）——模型这轮
        只回文本，仍应收到两个 one-way 帧；单向指令不等待任何上行。
        """
        stream_env.provider.reset([_text("记好了。")])
        _, body = await stream_env.turn("你好")

        frames = [p for name, p in parse_frames(body) if name == "device_request"]
        assert [f["capability"] for f in frames] == [
            "haptic.feedback", "widget.refresh",
        ]
        assert all(
            f["kind"] == "one_way" and "timeout_seconds" not in f for f in frames
        )
        assert event_names(body)[-1] == "done"
        assert payload_of(body, "done")["stop_reason"] == "completed"

    async def test_device_frame_is_gated_off_by_default(self, stream_env: StreamEnv):
        """闸门关闭时模型**看不到**设备能力——调用它只会拿到 tool_unavailable。"""
        from src.modules.ai import devices as D

        set_config(stream_env.cfg)      # 关掉本类的开关，回到默认形态
        D._channel = None
        stream_env.provider.reset([
            _tool_turn(_call("device_pick_image", {"source": "gallery"})),
            _text("这个我做不了。"),
        ])
        _, body = await stream_env.turn("拍一下小票")

        assert "device_request" not in event_names(body)
        assert payload_of(body, "done")["stop_reason"] == "completed"


# ── 2.5 增量：两个通道、合并窗、中途断流（02g-2）────────────────────────


class TestIncrements:
    """T-M7-02g-2：``delta`` 从「一轮一帧」升级为 token 级增量（架构 §8.5）。"""

    async def test_reasoning_and_content_arrive_on_separate_channels(
        self, stream_env: StreamEnv,
    ):
        """推理与正文**分通道**：客户端据 ``channel`` 把推理放进灰字折叠区、正文
        放进气泡。合并窗按通道隔离，两者的头尾绝不会拼进同一帧。"""
        stream_env.provider.reset([
            StreamScript(_text("这个月还没有设置预算。", reasoning="先看预算表再回答。")),
        ])
        _, body = await stream_env.turn("看看预算")

        deltas = [p for name, p in parse_frames(body) if name == "delta"]
        assert [(d["channel"], d["text"]) for d in deltas] == [
            ("reasoning", "先看预算表再回答。"),
            ("content", "这个月还没有设置预算。"),
        ]
        assert all(d["step"] == 1 for d in deltas)

    async def test_fragments_inside_the_merge_window_collapse_into_one_frame(
        self, stream_env: StreamEnv, monkeypatch,
    ):
        """实测约 4 字符/帧、一轮三四百帧——帧泵把**窗内**的同通道增量并成一帧，
        桥调用量因此降一个数量级。合并只改帧的**分法**：拼接结果与逐帧一致。

        窗刻意调到远大于心跳（fixture 的 ``ai_agent_heartbeat_seconds`` 是 0.05），
        分片间隔也刻意大于心跳：这样一来**唯一**能把它们并起来的机制就是合并窗
        本身——退化成「空闲了就冲刷」的实现会在这里裂成两帧。
        """
        monkeypatch.setattr(S, "_DELTA_MERGE_SECONDS", 1.0)
        answer = "这个月还没有设置预算，你可以先设一个。"
        stream_env.provider.reset([
            StreamScript(_text(answer), chunk_size=len(answer) // 2, gap=0.3),
        ])
        _, body = await stream_env.turn("看看预算")

        deltas = [p["text"] for name, p in parse_frames(body) if name == "delta"]
        assert deltas == [answer], "窗内的分片应当并成一帧"

    async def test_fragments_beyond_the_merge_window_are_sent_separately(
        self, stream_env: StreamEnv,
    ):
        """分片之间超过合并窗就各成一帧——客户端**不能**假设「一轮一帧」，
        按追加渲染即可（拼接结果仍与整段一致）。"""
        stream_env.provider.reset([
            StreamScript(_text("甲乙丙丁戊己庚辛"), chunk_size=4, gap=0.15),
        ])
        _, body = await stream_env.turn("你好")

        deltas = [p["text"] for name, p in parse_frames(body) if name == "delta"]
        assert deltas == ["甲乙丙丁", "戊己庚辛"]
        assert "".join(deltas) == "甲乙丙丁戊己庚辛"

    async def test_each_step_tags_its_own_increments(self, stream_env: StreamEnv):
        """增量带 ``step``：两次模型请求的正文归到各自的分组，不会串台。"""
        stream_env.provider.reset([
            _tool_turn(_call("budget_list", _budget_args()), content="我先查一下。"),
            StreamScript(_text("没有设置预算。", reasoning="汇总一下。")),
        ])
        _, body = await stream_env.turn("看看预算")

        assert [(p["channel"], p["step"]) for name, p in parse_frames(body)
                if name == "delta"] == [
            ("content", 1), ("reasoning", 2), ("content", 2),
        ]

    async def test_tool_events_never_overtake_the_text_that_preceded_them(
        self, stream_env: StreamEnv,
    ):
        """模型先交代一句、再调工具（很常见的形态）：那句话必须**排在**工具事件
        之前。帧泵的增量缓冲因此不能无限期挂着——任何非 ``delta`` 事件到达都要
        先把它冲刷出去，否则客户端会看到「正在查询预算…」抢在「我先查一下」前面。"""
        stream_env.provider.reset([
            _tool_turn(_call("budget_list", _budget_args()), content="我先查一下。"),
            _text("没有设置预算。"),
        ])
        _, body = await stream_env.turn("看看预算")

        assert event_names(body) == [
            "phase",        # routing（域选择，step 0）
            "phase",        # step 1 入口
            "delta",        # 「我先查一下。」——冲在工具事件前面
            "phase",        # 工具批次入口
            "tool_call",
            "tool_result",
            "phase",        # step 2 入口
            "delta",
            "done",
        ]

    async def test_wrap_up_text_is_emitted_exactly_once(self, stream_env: StreamEnv):
        """撞边界时的收尾文案**不是流式产出的**（收尾请求刻意不流式，见
        ``agent._wrap_up``），由 loop 自己发一帧；帧层自本卡起不再补发——02g 的
        收尾 ``delta`` 已随之删除，重复发会让客户端把同一段话渲染两遍。"""
        stream_env.provider.reset(
            [_tool_turn(_call("budget_list", _budget_args())) for _ in range(8)]
            + [_text("已完成：查完了。未完成：没有了。")]
        )
        _, body = await stream_env.turn("看看预算")

        deltas = [p["text"] for name, p in parse_frames(body) if name == "delta"]
        assert deltas == ["已完成：查完了。未完成：没有了。"]
        assert payload_of(body, "done")["stop_reason"] == "step_limit"

    async def test_a_cut_stream_leaves_the_partial_text_on_the_wire(
        self, stream_env: StreamEnv,
    ):
        """说到一半断流：客户端**已经拿到半截正文**（架构 §8.5 的契约是「保留
        半截 + 标错」而不是清空）。服务端要做的只是别把它擦掉——``error`` 帧排在
        增量**之后**，客户端按追加渲染即可。

        这一步的 token 拿不回来（usage 在尾块），且它是首次调用 → 一条用量行都
        不写，与 02g 的「首次调用即失败 = 零计费」同口径。
        """
        stream_env.provider.reset([
            StreamScript(
                _text("这个月还没有设置预算，你可以先"),
                chunk_size=4, gap=0.15,
                error=ProviderError(status=503, retryable=False, reason="transport"),
            ),
        ])
        _, body = await stream_env.turn("看看预算")

        assert event_names(body)[-1] == "error"
        assert payload_of(body, "error")["code"] == "ai_unavailable"
        deltas = [p["text"] for name, p in parse_frames(body) if name == "delta"]
        assert "".join(deltas) == "这个月还没有设置预算，你可以先"
        # T-M7-04b 起本轮的写入是**逐事件**的：用户输入早已落库，失败再由
        # ``_record_failure`` 补一条 ``error`` 事件留档。**在途的半截正文不在日志里**
        # ——它还没产出完，无从落库；客户端照架构 §8.5 保留它并标错。
        events = await _rows(stream_env, AIConversationEvent)
        assert [(e.type, e.data.get("content")) for e in events] == [
            ("user", "看看预算"), ("error", None),
        ]
        assert "这个月还没有设置预算" not in json.dumps(
            [e.data for e in events], ensure_ascii=False,
        )
        # 首次调用即失败 = 零计费 → 一条用量行都不写（口径未变）
        assert await _rows(stream_env, AIUsage, user_id=stream_env.user_id) == []


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
        assert event_names(body) == ["phase", "phase", "delta", "done"]


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

    async def test_disconnect_cancels_the_turn_and_keeps_what_was_written(
        self, stream_env: StreamEnv,
    ):
        """断连 = 取消（不把最多 20 s 的在途请求跑完）、连接归还，**且已经落库的
        部分留在日志里**。

        P1（02f 的一轮原子写入）在这里断言的是「一条事件都没有」；T-M7-04b 把写入
        粒度改成逐事件之后，正确的契约是「**回滚尚未提交的那一批**」——本用例这一
        轮只写了用户输入那一条（模型请求还没回来），所以日志里正好是它。用户看到
        的答案、以及这一轮已经跑完的步骤，从此断连也不会消失（这是本卡的全部
        意义，代价是会话日志里会留下没跑完的轮次——由 ``error`` 事件说明原因）。
        """
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
        events = await _rows(stream_env, AIConversationEvent,
                             conversation_id=conversation_id)
        assert [(e.type, e.data["content"]) for e in events] == [("user", "你好")]
        # 一整个 provider 调用都没回来 = 零计费 → 不写用量行（口径未变）
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

    async def test_a_conversation_past_the_old_session_limits_keeps_running(
        self, stream_env: StreamEnv,
    ):
        """T20-9（REQ-82）：原「50 轮 / 500k token」双限的**会话仍能继续跑 loop**。

        卡面判据是「不再抛 429」，所以这里必须**把超限的用量真的造出来**再发一轮
        ——只断言 ``check_quota`` 不存在是自反的（恒真 ⇒ 假绿，见 R-PROD-46 那类
        「没测」）。种子行落在 40 天前：它们要穿过**会话维度**的聚合（按整个会话
        生命周期），又不能触发 guest 的**日配额**（按用户/按天，另一把尺子）——
        故本用例同时证明了两道闸门是分开的。
        """
        stream_env.provider.reset([_text("你好呀！")])
        _, body = await stream_env.turn("你好")
        conversation_id = payload_of(body, "done")["conversation_id"]

        async with stream_env.factory() as session:
            long_ago = datetime.now(UTC) - timedelta(days=40)
            for index in range(60):          # 原轮数上限 50
                session.add(AIUsage(
                    user_id=stream_env.user_id, conversation_id=uuid.UUID(conversation_id),
                    provider="seed", model="seed", tokens=10_000,   # 60 万 > 原 500k
                    cost=0, request_id=f"seed-{index}", created_at=long_ago,
                ))
            await session.commit()

        stream_env.provider.reset([_text("还在呢")])
        response, body = await stream_env.turn("再来一句", conversation_id=conversation_id)

        assert response.status_code == 200
        assert payload_of(body, "done")["conversation_id"] == conversation_id
        assert len(stream_env.provider.calls) == 1

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
        # 末尾的 ``quota`` 是本轮的用量事件（T-M7-04a）——它与消息**同轮同批**，
        # 是投影 C 的留痕（``ignorable``），不进模型上下文。
        assert [e.type for e in events] == [
            "user", "assistant", "tool", "assistant", "quota",
        ]
        assert [e.seq for e in events] == [1, 2, 3, 4, 5]
        assert [e.turn for e in events] == [1, 1, 1, 1, 1]
        assert [e.ignorable for e in events] == [False] * 4 + [True]

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
        # T20-4（REQ-78）：卡上「为什么问我」的来源——记账 op 是 low（风险两级不命中），
        # 挂起是**步数**触发的，所以理由码是 multi_step。
        assert confirm["confirmReason"] == "multi_step"

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
        # routing 帧在 provider 调用**之前**就发出去了，因此失败轮也带着它
        assert event_names(body) == ["phase", "phase", "error"]
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

    async def test_failed_turn_keeps_what_it_wrote_and_records_the_error(
        self, stream_env: StreamEnv,
    ):
        """失败轮**不再什么都不留**（P1 的挂账 2「失败轮不落库」的收口）。

        T-M7-04b 起：本轮已完成的步骤留在日志里 + 一条 ``error`` 事件说明它为什么
        停在半路。这条事件是 04a 留给 04b 的生产者（架构 §6.2 的类型表）。
        """
        stream_env.provider.reset([ProviderError(status=None, retryable=False, reason="timeout")])
        _, body = await stream_env.turn("你好")
        conversation_id = uuid.UUID(payload_of(body, "error")["conversation_id"])

        events = await _rows(stream_env, AIConversationEvent,
                             conversation_id=conversation_id)
        assert [e.type for e in events] == ["user", "error"]
        assert events[1].data["code"] == payload_of(body, "error")["code"]
        # 结构性事件：不进模型上下文（投影 A），只进投影 B / C
        assert events[1].ignorable is True
        # 载荷只有安全枚举码与固定文案——异常正文绝不落日志（脱敏口径）
        assert set(events[1].data) == {"code", "message"}

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


# ── 10. 摘要压缩（T-M7-04c） ─────────────────────────────────────────────


class TestCompression:
    """窗口之外的轮次在 ``done`` 之后被压成摘要，写一条 ``replace`` 事件。

    窗口在用例里调到 1（``stream_env.cfg`` 就是 ``set_config`` 的那个对象，
    改它即改全局配置），免得为了触发一次压缩跑满 9 轮。
    """

    async def test_overflowing_conversation_is_compressed_after_done(
        self, stream_env: StreamEnv,
    ):
        stream_env.cfg.ai_agent_history_turns = 1
        stream_env.provider.reset([_text("记好了。")])

        _, first = await stream_env.turn("第一轮")
        conversation_id = payload_of(first, "done")["conversation_id"]
        _, body = await stream_env.turn("第二轮", conversation_id=conversation_id)

        # 压缩**不产生任何帧**：它在 done 之后跑，协议面一行未变。
        assert event_names(body)[-1] == "done"
        assert "error" not in event_names(body)

        events = await _rows(stream_env, AIConversationEvent,
                             conversation_id=uuid.UUID(conversation_id))
        replaces = [e for e in events if e.type == "replace"]
        assert len(replaces) == 1
        assert replaces[0].data["upto_turn"] == 1
        assert replaces[0].data["summary"] == "用户此前问过几笔账。"
        assert replaces[0].ignorable is True
        # 原文仍在日志里（压缩是新增一条事件，不是覆写）
        assert [e.type for e in events][:2] == ["user", "assistant"]

        conversation = (await _rows(stream_env, AIConversation,
                                    id=uuid.UUID(conversation_id)))[0]
        assert conversation.summary == "用户此前问过几笔账。"

    async def test_summary_reaches_the_next_turn_request(self, stream_env: StreamEnv):
        """摘要经**系统提示**进下一轮——不是 messages 里的一条。"""
        stream_env.cfg.ai_agent_history_turns = 1
        stream_env.provider.reset([_text("记好了。")])
        stream_env.provider.summary_reply = "用户此前问过一笔午饭。"

        _, first = await stream_env.turn("第一轮")
        conversation_id = payload_of(first, "done")["conversation_id"]
        # 第二轮跑完才产生第一次压缩（那时才有一轮落在窗口外）。
        await stream_env.turn("第二轮", conversation_id=conversation_id)
        stream_env.provider.reset([_text("好。")])
        await stream_env.turn("第三轮", conversation_id=conversation_id)

        payload = stream_env.provider.calls[0]["messages"]
        assert payload[0]["role"] == "system"
        assert "用户此前问过一笔午饭。" in payload[0]["content"]
        assert all(
            "用户此前问过一笔午饭。" not in json.dumps(m)
            for m in payload[1:]
        )

    async def test_compression_failure_does_not_break_the_turn(
        self, stream_env: StreamEnv,
    ):
        """摘要生成失败 → 这一轮照常交付；**不再发帧**（done 已经发过）。"""
        stream_env.cfg.ai_agent_history_turns = 1
        stream_env.provider.reset([_text("记好了。")])
        _, first = await stream_env.turn("第一轮")
        conversation_id = payload_of(first, "done")["conversation_id"]

        stream_env.provider.summary_reply = ProviderError(
            status=503, retryable=True, reason="transport",
        )
        response, body = await stream_env.turn("第二轮", conversation_id=conversation_id)

        assert response.status_code == 200
        assert event_names(body)[-1] == "done"
        assert "error" not in event_names(body)
        events = await _rows(stream_env, AIConversationEvent,
                             conversation_id=uuid.UUID(conversation_id))
        assert [e.type for e in events if e.type == "replace"] == []
        conversation = (await _rows(stream_env, AIConversation,
                                    id=uuid.UUID(conversation_id)))[0]
        assert conversation.summary == ""

    async def test_compression_crash_does_not_break_the_turn(
        self, stream_env: StreamEnv, monkeypatch,
    ):
        """第二道防线：压缩**自身**抛异常（不是 provider 报错）也不能影响这一轮。

        上一条打的是 provider 失败——那一支在 ``summarize_overflow`` 内部就被吞掉了，
        走不到这里。这一条打的是它之后的事（写事件、提交）出错：``done`` 已经发出去
        了，此时**任何帧都是协议违规**，所以那一层 try 必须在。
        """
        from src.modules.ai.conversation import ConversationService

        async def boom(self, conversation, provider):
            raise RuntimeError("compression exploded")

        monkeypatch.setattr(ConversationService, "summarize_overflow", boom)
        stream_env.cfg.ai_agent_history_turns = 1
        stream_env.provider.reset([_text("记好了。")])

        _, first = await stream_env.turn("第一轮")
        response, body = await stream_env.turn(
            "第二轮", conversation_id=payload_of(first, "done")["conversation_id"],
        )

        assert response.status_code == 200
        assert event_names(body)[-1] == "done"
        assert "error" not in event_names(body)

    async def test_interrupted_step_usage_is_not_estimated(self, stream_env: StreamEnv):
        """失败那一步的 token **不估算**——P1 挂账 4 的最终结论（T-M7-04c）。

        脚本项「已经吐出正文、然后断流」：正文有，usage 一个也没有（真实链路的
        usage 在尾块）。记账口径因此是**已完成步骤之和**，一行不估、一行不补——
        估算值混进 ``ai_usage.tokens`` 会污染配额基座（那是「轮数 + token」双限的
        计数表），而缺的这部分只在 provider 中途失败这一支出现。
        """
        stream_env.provider.reset([
            _tool_turn(_call("budget_list", _budget_args())),
            StreamScript(
                _text("说到一半"),
                error=ProviderError(status=None, retryable=False, reason="interrupted"),
            ),
        ])
        _, body = await stream_env.turn("看看预算")

        assert payload_of(body, "error")["code"] == "ai_unavailable"
        rows = await _rows(stream_env, AIUsage, user_id=stream_env.user_id)
        assert len(rows) == 1
        assert rows[0].tokens == 12  # 只有第 1 步（5 + 7）；失败那一步零


# ── 13. 契约车道（T-M7-05b）─────────────────────────────────────────────────


class TestContractLane:
    """客户端声明的 ``proposal_contract_version`` 决定车道——**注入面与信封两处**。

    v1 车道必须与 P1 逐字一致（旧 APK 什么都不声明），v2 车道才拿到全量工具与
    ``contractVersion: 2`` 的信封。这是 05b 的「旧客户端行为不变」在真实链路上的
    证据（注入面在 ``test_ai_agent.py``、组装面在 ``test_ai_agent_approval.py``
    另有直测）。
    """

    async def test_a_v1_client_keeps_the_p1_tool_set(self, stream_env: StreamEnv):
        stream_env.provider.reset([_text("你好")])
        # 不声明版本 = 旧 APK
        await stream_env.turn("你好")

        names = [t["function"]["name"] for t in stream_env.provider.calls[0]["tools"]]
        assert names == [
            tool.name for tool in tools_for_domains(
                DOMAINS, contract_version=1,
            )
        ]
        assert "book_delete" not in names and "meta_set" not in names

    async def test_a_v2_client_gets_the_full_tool_set(self, stream_env: StreamEnv):
        stream_env.provider.reset([_text("你好")])
        await stream_env.turn("你好", contract_version=2)

        names = [t["function"]["name"] for t in stream_env.provider.calls[0]["tools"]]
        assert names == [tool.name for tool in tools_for_domains(DOMAINS, contract_version=2)]
        assert "book_delete" in names and "meta_set" in names

    async def test_a_v1_client_cannot_call_a_v2_only_tool(self, stream_env: StreamEnv):
        """装不进 v1 车道的工具**模型看不见**——即便它硬报出这个名字，也只拿到
        一条可纠正的错误结果，且**一次写库都不会发生**。"""
        from src.models.ledger import Book

        before = len(await _rows(stream_env, Book, owner_id=stream_env.user_id))
        stream_env.provider.reset([
            _tool_turn(_call("book_create", {"name": "旅行"}, id="w1")),
            _text("我没有这个能力。"),
        ])
        _, body = await stream_env.turn("建个旅行账本")

        assert payload_of(body, "done")["stop_reason"] == "completed"
        assert len(await _rows(stream_env, Book, owner_id=stream_env.user_id)) == before

    async def test_a_v2_write_runs_the_whole_chain(self, stream_env: StreamEnv):
        """单 Step 高置信 → 自动执行：组装（v2 信封）→ 校验 → **payload 授权** →
        执行（``execute_op``）→ sync 登记。一条链路全在真实端点上过一遍。"""
        from src.models.ledger import Book
        from src.models.sync import SyncChange

        stream_env.provider.reset([
            _tool_turn(_call("book_create", {"name": "旅行"}, id="w1")),
            _text("建好了。"),
        ])
        _, body = await stream_env.turn("建个旅行账本", contract_version=2)

        done = payload_of(body, "done")
        assert done["proposals"] == [], "高置信单步写沿用快路径：不留确认卡"
        assert len(done["executions"]) == 1
        # 覆盖率哨兵：book_create 确实在注入面上（否则「执行成功」可能是空转）
        assert "book_create" in [
            t["function"]["name"] for t in stream_env.provider.calls[0]["tools"]
        ]

        created = [
            b for b in await _rows(stream_env, Book, owner_id=stream_env.user_id)
            if b.name == "旅行"
        ]
        assert len(created) == 1
        # 服务端权威直写必须自己登 sync change，否则别的设备 pull 不到
        changes = await _rows(stream_env, SyncChange, entity_id=created[0].id)
        assert [str(c.operation) for c in changes] == ["insert"]

    async def test_a_v2_medium_risk_write_hangs_instead_of_autoexecuting(
        self, stream_env: StreamEnv,
    ):
        """REQ-78（T20-3）：**v2 车道上删一笔账不再自动执行**——单 Step + 高置信也
        要挂起出确认卡。改前这条走的是快路径，真机上的表现就是「不问一声就删了」。

        这是验收里「真机弹确认卡」那条在链路上的可自动化替身：走的是真实端点
        （``/ai/agent``）、真实组装 / 校验 / payload 授权，只有模型是脚本化的
        （真机那一遍归收口卡 T20-7）。**为什么强调 v2**：v1 车道只注入
        ``V1_LANE_OPS`` 里的记账 op，``transaction_delete`` 在那边根本不可见——
        生产客户端声明 ``proposal_contract_version = 2``，风险分级影响的正是它。
        """
        from datetime import UTC, date, datetime

        from src.models.ledger import Transaction, TxSource, TxType

        async with stream_env.factory() as session:
            category = (await session.scalars(
                select(Category).where(Category.name == CATEGORY),
            )).one()
            tx = Transaction(
                id=uuid.uuid4(), book_id=uuid.UUID(stream_env.book_id),
                type=TxType.EXPENSE, amount_cents=3000, category_id=category.id,
                account_id=None, note="打车", biz_date=date(2026, 9, 18),
                source=TxSource.MANUAL, occurred_at=datetime.now(UTC),
            )
            session.add(tx)
            await session.commit()

        stream_env.provider.reset([
            _tool_turn(_call(
                "transaction_delete",
                {"locator": {"bizDate": "2026-09-18", "amountCents": 3000}},
                id="w1",
            )),
            _text("先跟你确认一下。"),
        ])
        _, body = await stream_env.turn("把打车那笔删了", contract_version=2)

        confirm = payload_of(body, "confirm")
        assert confirm["proposal"]["action"] == "transaction_delete"
        assert confirm["proposal"]["requiresConfirmation"] is True
        # T20-4（REQ-78）：理由码随挂起帧下发（卡上那句「会改动你已有的记录」）。
        # **这一格是判别性的**：单 Step + 高置信 + 无 warnings 的形态，改前连卡都没有；
        # 有了它，卡片才答得上「为什么这次问我」而不是让用户猜。
        assert confirm["confirmReason"] == "medium_risk"
        assert payload_of(body, "done")["executions"] == []

        rows = await _rows(stream_env, Transaction, id=tx.id)
        assert len(rows) == 1 and rows[0].deleted_at is None, "挂起＝软删也没发生"

    async def test_a_device_op_hangs_instead_of_executing(self, stream_env: StreamEnv):
        """``sync_trigger`` 的执行位置在设备（T-M7-05j 后 ``meta_set`` 已迁回
        服务端执行，不再是设备 op）：服务端**只挂起**，不参与审批策略的判定
        （没有执行通道时它也只是挂起——这里连执行通道都在，仍然一次不执行）。"""
        from src.models.sync import SyncChange

        before = len(await _rows(stream_env, SyncChange))
        stream_env.provider.reset([
            _tool_turn(_call("sync_trigger", {}, id="w1")),
            _text("已在手机上确认。"),
        ])
        _, body = await stream_env.turn("同步一下", contract_version=2)

        confirm = payload_of(body, "confirm")
        proposal = confirm["proposal"]
        assert proposal["contractVersion"] == 2
        assert proposal["action"] == "sync_trigger"
        assert "confirmReason" not in confirm

        done = payload_of(body, "done")
        assert [p["proposalId"] for p in done["proposals"]] == [proposal["proposalId"]]
        assert done["executions"] == []
        assert len(await _rows(stream_env, SyncChange)) == before, "设备 op 不动服务端数据"

    async def test_a_medium_risk_device_op_still_carries_no_reason(
        self, stream_env: StreamEnv,
    ):
        """T20-4（REQ-78）：设备 op **不带理由码**，哪怕它的风险档是 ``medium``。

        **这条是判别性的**（上面那条 ``sync_trigger`` 不是）：``sync_trigger`` 是
        ``low``，``confirm_reason`` 对它本来就返回 ``None``——「帧里没有理由码」在
        那里是必然的，把理由码硬塞进设备分支也看不出来。``consent_record`` 是
        ``medium``：**若不显式拦住，``confirm_reason`` 会给出 ``medium_risk``**，
        卡片就会说「会改动你已有的记录」——而它的挂起理由其实是「这条要在设备上执行，
        服务端做不了」（`tools.DEVICE_EXECUTED_OPS`）。变异探针（把设备分支也传
        ``confirm_reason``）在这条上会红。
        """
        stream_env.provider.reset([
            _tool_turn(_call("consent_record", {}, id="w1")),
            _text("等你在手机上确认。"),
        ])
        _, body = await stream_env.turn("我同意隐私政策", contract_version=2)

        confirm = payload_of(body, "confirm")
        assert confirm["proposal"]["action"] == "consent_record"
        assert confirm["proposal"]["requiresConfirmation"] is True
        assert "confirmReason" not in confirm
        assert payload_of(body, "done")["executions"] == []
