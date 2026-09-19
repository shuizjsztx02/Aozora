"""T-M7-05e: 设备能力通道——协议 / 目录 / 暂停恢复 / 超时 / 取消 / 上行端点。

验收映射（``docs/milestones/production-execution-plan.md`` T-M7-05e）：

- ``device_request`` 的首个生产者、两类协议      → :class:`TestProtocol`、:class:`TestOneWay`
- 需结果类可被上行回传唤醒                      → :class:`TestNeedsResult`
- **回传超时收敛到一致状态**（不变式 3）        → :class:`TestConvergence`
- 取消后无悬挂设备请求                          → :class:`TestConvergence`
- 暂停态存储（单 worker 内存态）                → :class:`TestDeviceChannel`
- ``AgentObserver`` 扩展                        → :class:`TestOneWay`（观察者收到帧）
- 开关默认关闭时行为与 P1 逐字相同              → :class:`TestGating`
- 上行端点 ``POST /ai/device-result``           → :class:`TestUplinkEndpoint`

两条通道各管一段：**loop 级**用例不碰网络与 DB（设备工具没有 DB handler，
``ToolContext`` 用内存假 DB），**端点级**用例走真实鉴权 + 真 HTTP。帧级的一条在
``test_agent_stream.py::TestDeviceChannel``（那里有 SSE 驱动器与临时库）。

临时库用 ``tempfile.mkdtemp`` 而不是 ``tmp_path``：本机 pytest basetemp 权限已坏
（见 ``pytest-basetemp-broken`` 记录）。
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import tempfile
import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import pytest_asyncio

from src.common.config import Settings, reset_config, set_config
from src.modules.ai import devices as D
from src.modules.ai import stream as S
from src.modules.ai.agent import AgentLoop
from src.modules.ai.devices import (
    NEEDS_RESULT,
    ONE_WAY,
    STATUS_DENIED,
    STATUS_FAILED,
    STATUS_OK,
    STATUS_TIMEOUT,
    STATUS_UNAVAILABLE,
    DeviceChannel,
    DeviceOutcome,
    build_request,
    get_device_tool,
)
from src.modules.ai.provider import (
    ProviderResult,
    ReasoningLevel,
    StreamChunk,
    ToolCall,
)
from src.modules.ai.tools import (
    DOMAINS,
    PROVIDER_TOOL_NAME_PATTERN,
    ToolContext,
    to_openai_tools,
    tools_for_domains,
)

MODEL = "deepseek-flash"
TODAY = date(2026, 9, 19)
DEVICE = "device-channel-1"
OTHER_DEVICE = "device-channel-2"


# ── 桩与夹具 ──────────────────────────────────────────────────────────────


class _FakeDb:
    """内存占位：设备工具不碰 DB（它们没有 handler），loop 只需要一个 ctx。"""

    async def execute(self, _query: Any) -> Any:  # pragma: no cover - 不该被调用
        raise AssertionError("device tools must not touch the database")

    async def scalars(self, _query: Any) -> Any:  # pragma: no cover
        raise AssertionError("device tools must not touch the database")


def _ctx(user_id: uuid.UUID | None = None) -> ToolContext:
    return ToolContext(
        db=_FakeDb(), user_id=user_id or uuid.uuid4(), book_id=uuid.uuid4(),
    )


def _text(content: str) -> ProviderResult:
    return ProviderResult(content=content, prompt_tokens=5, completion_tokens=7)


def _call(name: str, args: dict[str, Any] | None = None) -> ToolCall:
    return ToolCall(
        id="", name=name, arguments=json.dumps(args or {}, ensure_ascii=False),
    )


def _tool_turn(*calls: ToolCall) -> ProviderResult:
    return ProviderResult(
        content="", prompt_tokens=5, completion_tokens=7, tool_calls=calls,
    )


class ScriptedProvider:
    """脚本化 agent provider：第 n 次调用取 ``script[n]``，用尽后重复最后一项。"""

    name = "scripted-device"

    def __init__(self, script: list[ProviderResult]) -> None:
        self.script = list(script)
        self.calls: list[list[dict[str, Any]]] = []

    def _take(self, messages: list[dict[str, Any]]) -> ProviderResult:
        index = len(self.calls)
        self.calls.append([dict(m) for m in messages])
        return self.script[index] if index < len(self.script) else self.script[-1]

    async def complete_with_tools(self, messages: Any, **_kwargs: Any) -> ProviderResult:
        return self._take(messages)

    async def complete_with_tools_stream(self, messages: Any, **_kwargs: Any):
        result = self._take(messages)
        if result.content:
            yield StreamChunk(delta=result.content)
        yield StreamChunk(result=result)


class _RecordingObserver:
    """记下 loop 的每一次进度回调（六个钩子全实现——``_notify`` 用 getattr 取）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(self, hook: str, **kwargs: Any) -> None:
        self.calls.append((hook, kwargs))

    def step_started(self, *, step: int, level: ReasoningLevel) -> None:
        self._record("step_started", step=step, level=level)

    def step_finished(
        self, *, step: int, prompt_tokens: int, completion_tokens: int,
    ) -> None:
        self._record("step_finished", step=step)

    def tool_started(self, *, step: int, name: str) -> None:
        self._record("tool_started", step=step, name=name)

    def tool_finished(self, *, step: int, name: str, outcome: Any) -> None:
        self._record("tool_finished", step=step, name=name, outcome=outcome)

    def delta(self, *, step: int, text: str) -> None:
        self._record("delta", step=step, text=text)

    def reasoning_delta(self, *, step: int, text: str) -> None:
        self._record("reasoning_delta", step=step, text=text)

    def device_request(self, *, step: int, payload: Any) -> None:
        # 与 ``QueueObserver`` 同口径把 step 并进载荷：帧上的 step 就是这么来的。
        self._record("device_request", step=step, payload={**payload, "step": step})

    @property
    def device_requests(self) -> list[dict[str, Any]]:
        return [
            kwargs["payload"] for hook, kwargs in self.calls if hook == "device_request"
        ]


def _loop(
    provider: Any,
    *,
    ctx: ToolContext | None = None,
    device: DeviceChannel | None = None,
    observer: _RecordingObserver | None = None,
) -> tuple[AgentLoop, _RecordingObserver]:
    """``(loop, observer)``——观察者与 loop 同建同取，用例断言的就是它。"""
    observer = observer or _RecordingObserver()
    loop = AgentLoop(
        provider, ctx or _ctx(), model=MODEL, timeout=10.0, today=TODAY,
        observer=observer, device=device,
    )
    return loop, observer


def _tool_messages(result: Any) -> list[dict[str, Any]]:
    return [m for m in result.messages if m.get("role") == "tool"]


def _content_of(result: Any, index: int = 0) -> dict[str, Any]:
    return json.loads(_tool_messages(result)[index]["content"])


async def _wait_for(predicate: Any, *, timeout: float = 2.0) -> None:
    """轮询等一个条件成立（挂起是异步的，没有别的方式「等到它开始等」）。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition never became true")
        await asyncio.sleep(0.001)


@pytest.fixture(autouse=True)
def _fresh_channel():
    """每个用例一张新挂起表：单例的 timeout 与残留条目都不跨用例。

    ``DeviceChannel`` 的 timeout 在建表那一刻取自配置，而配置是逐用例设置的
    （``set_config``）；单例不清就等于把上一个用例的配置带进来。
    """
    D._channel = None
    yield
    D._channel = None


# ── 1. 协议与目录 ─────────────────────────────────────────────────────────


class TestProtocol:
    def test_catalog_has_both_classes(self):
        assert {tool.kind for tool in D.DEVICE_TOOLS} == {ONE_WAY, NEEDS_RESULT}

    def test_catalog_covers_the_architecture_list(self):
        """架构 §4.3 两类各举的例子都要在目录里（这是目录的下界）。"""
        assert {t.capability for t in D.DEVICE_TOOLS} >= {
            "widget.refresh", "haptic.feedback", "share.text",          # 单向
            "permissions.request", "voice.transcribe", "image.pick",    # 需结果
        }

    def test_tool_names_are_provider_safe(self):
        """工具名走 provider：点号会让 provider 直接 400（02c 收口实测）。"""
        pattern = re.compile(PROVIDER_TOOL_NAME_PATTERN)
        for tool in D.DEVICE_TOOLS:
            assert pattern.match(tool.name), tool.name
            assert "." not in tool.name

    def test_names_and_capabilities_are_unique(self):
        assert len(D.DEVICE_TOOLS_BY_NAME) == len(D.DEVICE_TOOLS)
        caps = [t.capability for t in D.DEVICE_TOOLS]
        assert len(set(caps)) == len(caps)

    def test_every_tool_has_description_and_message(self):
        for tool in D.DEVICE_TOOLS:
            assert tool.description.strip()
            assert tool.message.strip()

    def test_every_device_tool_has_progress_text(self):
        """步骤卡片文案随能力定义走（``DeviceTool.message``），不是另一张表。"""
        for tool in D.DEVICE_TOOLS:
            assert S.tool_progress(tool.name) == tool.message
        assert S.tool_progress("device_not_registered") == S._FALLBACK_PROGRESS

    def test_openai_payload_matches_tool_payload(self):
        """设备能力与注册表工具喂的是同一个 provider 的 ``tools`` 字段——
        两份清单的形状必须一致（设备能力不是 ``Tool``，各生成各的）。"""
        server = to_openai_tools(tools_for_domains(DOMAINS)[:1])[0]
        device = D.to_openai_tools(D.DEVICE_TOOLS[:1])[0]
        assert device.keys() == server.keys()
        assert device["type"] == "function"
        assert device["function"].keys() == server["function"].keys()

    def test_request_payload_shape(self):
        tool = get_device_tool("device_pick_image")
        request = build_request(tool, {"source": "gallery"}, step=3)
        assert request.to_payload(timeout_seconds=120.0) == {
            "request_id": request.request_id,
            "kind": NEEDS_RESULT,
            "capability": "image.pick",
            "payload": {"source": "gallery"},
            "timeout_seconds": 120.0,
        }
        assert request.request_id.startswith(D.REQUEST_ID_PREFIX)

    def test_one_way_payload_has_no_timeout(self):
        """单向类不挂起，因此**不带** timeout——它没有任何等待要被约束。"""
        tool = get_device_tool("device_haptic")
        payload = build_request(tool, {}, step=1).to_payload(timeout_seconds=None)
        assert "timeout_seconds" not in payload
        assert payload["kind"] == ONE_WAY

    def test_deliverable_statuses_exclude_timeout(self):
        """``timeout`` 只能由服务端产生（挂起期满），客户端报它没有意义。"""
        assert D.DELIVERABLE_STATUSES == {
            STATUS_OK, STATUS_DENIED, STATUS_UNAVAILABLE, STATUS_FAILED,
        }

    def test_validate_arguments_reports_paths_without_values(self):
        tool = get_device_tool("device_pick_image")
        assert D.validate_arguments(tool, {"source": "gallery"}) == []
        assert D.validate_arguments(tool, {}) == ["source"]
        # 取值不回显（实例值可能是用户原话）——只给字段路径
        assert D.validate_arguments(tool, {"source": "偷偷上传"}) == ["source"]

    def test_unknown_device_name_is_none(self):
        assert get_device_tool("device_nope") is None
        assert get_device_tool("transaction_add") is None  # 注册表的不在设备目录里


# ── 2. 单向指令：发出即继续 ───────────────────────────────────────────────


class TestOneWay:
    async def test_one_way_emits_a_frame_and_continues(self):
        channel = DeviceChannel(timeout_seconds=5.0)
        provider = ScriptedProvider([
            _tool_turn(_call("device_haptic", {"kind": "success"})),
            _text("已经记好了，给你个提示。"),
        ])
        loop, observer = _loop(provider, device=channel)
        result = await loop.run("记一笔午饭 12 块")

        frames = observer.device_requests
        assert len(frames) == 1
        assert frames[0]["kind"] == ONE_WAY
        assert frames[0]["capability"] == "haptic.feedback"
        assert frames[0]["payload"] == {"kind": "success"}
        assert frames[0]["step"] == 1
        assert "timeout_seconds" not in frames[0]

        # 下发即继续：立刻回一条结果给模型，且**不留挂起条目**
        assert _content_of(result) == {"status": "sent", "capability": "haptic.feedback"}
        assert channel.pending_count == 0
        assert result.stop_reason == "completed"
        assert result.steps == 2

    async def test_one_way_does_not_consume_the_write_budget(self):
        """设备动作不是写库：写预算是防「模型反复试图写库」的。"""
        channel = DeviceChannel(timeout_seconds=5.0)
        provider = ScriptedProvider([
            _tool_turn(_call("device_haptic"), _call("device_widget_refresh")),
            _text("好了。"),
        ])
        loop, _ = _loop(provider, device=channel)
        result = await loop.run("记好了没")

        assert result.writes == 0
        assert result.tool_calls == 2
        assert [_content_of(result, i) for i in range(2)] == [
            {"status": "sent", "capability": "haptic.feedback"},
            {"status": "sent", "capability": "widget.refresh"},
        ]

    async def test_invalid_arguments_are_reported_without_raising(self):
        channel = DeviceChannel(timeout_seconds=5.0)
        provider = ScriptedProvider([
            _tool_turn(_call("device_share_text", {})),   # 缺必填 text
            _text("分享不了，缺内容。"),
        ])
        loop, observer = _loop(provider, device=channel)
        result = await loop.run("分享一下")

        assert _content_of(result)["code"] == "invalid_arguments"
        assert _content_of(result)["paths"] == ["text"]
        assert observer.device_requests == []          # 没通过校验就不下发


# ── 3. 需结果：挂起 → 上行回传 → 恢复 ──────────────────────────────────────


class TestNeedsResult:
    async def test_pauses_then_resumes_on_delivery(self):
        channel = DeviceChannel(timeout_seconds=5.0)
        user_id = uuid.uuid4()
        provider = ScriptedProvider([
            _tool_turn(_call("device_pick_image", {"source": "gallery"})),
            _text("看到小票了，帮你记一下。"),
        ])
        loop, observer = _loop(provider, ctx=_ctx(user_id), device=channel)
        task = asyncio.create_task(loop.run("拍一下这张小票"))
        await _wait_for(lambda: channel.pending_count == 1)

        frames = observer.device_requests
        assert len(frames) == 1
        assert frames[0]["kind"] == NEEDS_RESULT
        assert frames[0]["timeout_seconds"] == 5.0
        assert not task.done()          # ← 挂起：这一轮停在这里

        assert channel.deliver(
            frames[0]["request_id"], user_id=user_id,
            outcome=DeviceOutcome(
                status=STATUS_OK,
                data={"attachmentId": str(uuid.uuid4()), "mimeType": "image/jpeg"},
            ),
        )
        result = await asyncio.wait_for(task, timeout=5.0)

        # 回传的数据进了配对的 tool 结果，**内部 id 已剥掉**（铁律 1）
        assert _content_of(result) == {
            "status": "ok", "capability": "image.pick", "mimeType": "image/jpeg",
        }
        assert channel.pending_count == 0
        assert result.stop_reason == "completed"
        assert result.content == "看到小票了，帮你记一下。"

    @pytest.mark.parametrize(
        ("status", "code"),
        [
            (STATUS_DENIED, "device_denied"),
            (STATUS_UNAVAILABLE, "device_unavailable"),
            (STATUS_FAILED, "device_failed"),
        ],
    )
    async def test_each_failure_status_has_its_own_code(self, status, code):
        """三个失败结局**说法不同**——模型据此对用户说不同的话。"""
        channel = DeviceChannel(timeout_seconds=5.0)
        user_id = uuid.uuid4()
        provider = ScriptedProvider([
            _tool_turn(_call("device_transcribe_voice")),
            _text("没听清。"),
        ])
        loop, observer = _loop(provider, ctx=_ctx(user_id), device=channel)
        task = asyncio.create_task(loop.run("我说一句"))
        await _wait_for(lambda: channel.pending_count == 1)
        channel.deliver(
            observer.device_requests[0]["request_id"],
            user_id=user_id, outcome=DeviceOutcome(status=status),
        )
        result = await asyncio.wait_for(task, timeout=5.0)

        assert _content_of(result)["code"] == code
        assert _content_of(result)["message"]
        # 设备失败**不终结回合**：模型照常拿到结果并给出回答（ADR-010 决策 4）
        assert result.stop_reason == "completed"
        assert result.content == "没听清。"

    async def test_permission_denied_is_a_tool_result_not_a_stop(self):
        channel = DeviceChannel(timeout_seconds=5.0)
        user_id = uuid.uuid4()
        provider = ScriptedProvider([
            _tool_turn(
                _call("device_request_permission", {"permission": "RECORD_AUDIO"}),
            ),
            _text("没有麦克风权限，我没法录音。"),
        ])
        loop, observer = _loop(provider, ctx=_ctx(user_id), device=channel)
        task = asyncio.create_task(loop.run("帮我语音记一笔"))
        await _wait_for(lambda: channel.pending_count == 1)
        frame = observer.device_requests[0]
        assert frame["capability"] == "permissions.request"
        assert frame["payload"] == {"permission": "RECORD_AUDIO"}
        channel.deliver(
            frame["request_id"], user_id=user_id,
            outcome=DeviceOutcome(status=STATUS_DENIED),
        )
        result = await asyncio.wait_for(task, timeout=5.0)

        assert _content_of(result)["code"] == "device_denied"
        assert result.content == "没有麦克风权限，我没法录音。"

    async def test_two_requests_in_one_step_stay_paired(self):
        """同一步里两次设备调用：各自独立挂起、各自配对（不变式 1）。

        **它们是串行的**（``_run_tools`` 逐条 await）：第二条要等第一条回来才发出，
        这不是缺陷——一次挂起必须能让出控制权，否则两条请求会同时挂在同一批里。
        """
        channel = DeviceChannel(timeout_seconds=5.0)
        user_id = uuid.uuid4()
        provider = ScriptedProvider([
            _tool_turn(
                _call("device_request_permission", {"permission": "CAMERA"}),
                _call("device_pick_image", {"source": "camera"}),
            ),
            _text("拍好了。"),
        ])
        loop, observer = _loop(provider, ctx=_ctx(user_id), device=channel)
        task = asyncio.create_task(loop.run("拍一张"))

        await _wait_for(lambda: len(observer.device_requests) == 1)
        first = observer.device_requests[0]["request_id"]
        channel.deliver(
            first, user_id=user_id, outcome=DeviceOutcome(status=STATUS_OK),
        )
        await _wait_for(lambda: len(observer.device_requests) == 2)
        channel.deliver(
            observer.device_requests[1]["request_id"], user_id=user_id,
            outcome=DeviceOutcome(status=STATUS_OK),
        )
        result = await asyncio.wait_for(task, timeout=5.0)

        calls = [m for m in result.messages if m.get("tool_calls")]
        assert len(calls[0]["tool_calls"]) == 2         # 一个配对组，两个调用
        assert len(_tool_messages(result)) == 2         # 两条结果各就各位
        assert channel.pending_count == 0


# ── 4. 收敛：超时与取消 ───────────────────────────────────────────────────


class TestConvergence:
    async def test_timeout_becomes_a_tool_result(self):
        """挂起期满 → 一条「没等到结果」的失败结果，回合照常收尾（不变式 3）。"""
        channel = DeviceChannel(timeout_seconds=0.05)
        provider = ScriptedProvider([
            _tool_turn(_call("device_pick_image", {"source": "camera"})),
            _text("没等到你选图。"),
        ])
        loop, _ = _loop(provider, device=channel)
        result = await asyncio.wait_for(loop.run("拍一张"), timeout=5.0)

        assert _content_of(result)["code"] == "device_timeout"
        assert channel.pending_count == 0        # 没有悬挂请求
        assert result.stop_reason == "completed"
        assert result.content == "没等到你选图。"

    async def test_cancel_while_paused_leaves_no_pending_request(self):
        channel = DeviceChannel(timeout_seconds=30.0)
        provider = ScriptedProvider([
            _tool_turn(_call("device_pick_image", {"source": "gallery"})),
            _text("不该跑到这里"),
        ])
        loop, _ = _loop(provider, device=channel)
        task = asyncio.create_task(loop.run("选一张"))
        await _wait_for(lambda: channel.pending_count == 1)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert channel.pending_count == 0

    async def test_delivery_after_cancel_is_refused(self):
        """断连之后客户端才回传：条目不在了 → 拒绝（端点上表现为 404）。"""
        channel = DeviceChannel(timeout_seconds=30.0)
        user_id = uuid.uuid4()
        provider = ScriptedProvider([
            _tool_turn(_call("device_pick_image", {"source": "gallery"})),
        ])
        loop, observer = _loop(provider, ctx=_ctx(user_id), device=channel)
        task = asyncio.create_task(loop.run("选一张"))
        await _wait_for(lambda: channel.pending_count == 1)
        request_id = observer.device_requests[0]["request_id"]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert channel.deliver(
            request_id, user_id=user_id, outcome=DeviceOutcome(status=STATUS_OK),
        ) is False


# ── 5. 挂起表（单 worker 内存态） ──────────────────────────────────────────


class TestDeviceChannel:
    @staticmethod
    def _request() -> Any:
        return build_request(get_device_tool("device_pick_image"), {}, step=1)

    async def test_register_and_deliver(self):
        channel = DeviceChannel(timeout_seconds=5.0)
        user_id = uuid.uuid4()
        request = self._request()
        channel.register(request, user_id=user_id)
        assert channel.pending_count == 1

        waiter = asyncio.create_task(channel.wait(request.request_id))
        await asyncio.sleep(0)                       # 让它真的等在 future 上
        outcome = DeviceOutcome(status=STATUS_OK, data={"a": 1})
        assert channel.deliver(request.request_id, user_id=user_id, outcome=outcome)
        assert await asyncio.wait_for(waiter, timeout=1.0) == outcome
        assert channel.pending_count == 0

    async def test_another_user_cannot_deliver(self):
        """归属校验：别人的 request_id 与不存在的 id 一样回不去（不泄漏存在性）。"""
        channel = DeviceChannel(timeout_seconds=5.0)
        request = self._request()
        channel.register(request, user_id=uuid.uuid4())

        assert channel.deliver(
            request.request_id, user_id=uuid.uuid4(),
            outcome=DeviceOutcome(status=STATUS_OK),
        ) is False
        assert channel.pending_count == 1          # 条目仍在，本人还能回

    async def test_second_delivery_is_refused(self):
        channel = DeviceChannel(timeout_seconds=5.0)
        user_id = uuid.uuid4()
        request = self._request()
        channel.register(request, user_id=user_id)
        outcome = DeviceOutcome(status=STATUS_OK)

        assert channel.deliver(request.request_id, user_id=user_id, outcome=outcome)
        assert not channel.deliver(request.request_id, user_id=user_id, outcome=outcome)

    async def test_timeout_status_cannot_be_delivered(self):
        """``timeout`` 只能由服务端产生（挂起期满）——协议边界上也要挡住。

        schemas 层的 ``Literal`` 是第一道（端点上表现为 422），这里是第二道，
        防的是绕过端点直接调通道的调用方（以及将来别的上行入口）。
        """
        channel = DeviceChannel(timeout_seconds=5.0)
        user_id = uuid.uuid4()
        request = self._request()
        channel.register(request, user_id=user_id)
        assert not channel.deliver(
            request.request_id, user_id=user_id,
            outcome=DeviceOutcome(status=STATUS_TIMEOUT),
        )
        assert channel.pending_count == 1

    async def test_unknown_request_id_is_refused(self):
        channel = DeviceChannel(timeout_seconds=5.0)
        assert channel.deliver(
            "dev-nope", user_id=uuid.uuid4(), outcome=DeviceOutcome(status=STATUS_OK),
        ) is False

    async def test_wait_times_out_and_deregisters(self):
        channel = DeviceChannel(timeout_seconds=0.05)
        request = self._request()
        channel.register(request, user_id=uuid.uuid4())
        assert await channel.wait(request.request_id) == DeviceOutcome(
            status=STATUS_TIMEOUT,
        )
        assert channel.pending_count == 0

    async def test_wait_returns_timeout_for_unknown_id(self):
        """到不了（调用点紧接着 register），但纵深防御不抛异常。"""
        channel = DeviceChannel(timeout_seconds=5.0)
        assert await channel.wait("dev-nope") == DeviceOutcome(status=STATUS_TIMEOUT)

    async def test_singleton_timeout_comes_from_config(self):
        from src.common.config import get_config

        set_config(get_config().model_copy(update={"ai_device_timeout_seconds": 7.5}))
        try:
            assert D.device_channel().timeout_seconds == 7.5
            assert D.device_channel() is D.device_channel()      # 单例
        finally:
            reset_config()
            D._channel = None


# ── 6. 闸门：关闭时与 P1 逐字相同 ─────────────────────────────────────────


class TestGating:
    def test_without_channel_device_tools_are_not_injected(self):
        """没通道 = 没执行者：把能力摆在模型面前只会换来一次白等的超时。"""
        loop, _ = _loop(ScriptedProvider([_text("hi")]))
        assert [t["function"]["name"] for t in loop._openai_tools] == [
            t.name for t in tools_for_domains(DOMAINS)
        ]

    def test_with_channel_device_tools_are_injected(self):
        loop, _ = _loop(
            ScriptedProvider([_text("hi")]),
            device=DeviceChannel(timeout_seconds=5.0),
        )
        assert [t["function"]["name"] for t in loop._openai_tools] == [
            *[t.name for t in tools_for_domains(DOMAINS)],
            *[t.name for t in D.DEVICE_TOOLS],
        ]

    async def test_hallucinated_device_tool_is_unavailable_without_channel(self):
        provider = ScriptedProvider([
            _tool_turn(_call("device_haptic", {"kind": "success"})),
            _text("做不了。"),
        ])
        loop, observer = _loop(provider)
        result = await loop.run("震动一下")

        assert _content_of(result)["code"] == "tool_unavailable"
        assert observer.device_requests == []


# ── 7. 上行端点 ───────────────────────────────────────────────────────────


@pytest_asyncio.fixture()
async def api_env():
    """临时文件库 + 两个 guest（本人 / 他人）。设备端点只需要鉴权与挂起表。"""
    from src.common.database import get_engine, get_session_factory, reset_engine
    from src.main import app
    from src.models.base import Base
    from src.modules.ai.service import _ai_rate_limits
    from src.modules.auth.dependencies import _rate_limits

    reset_config()
    _rate_limits.clear()
    _ai_rate_limits.clear()

    db_dir = Path(tempfile.mkdtemp(prefix="aozora_test_device_"))
    cfg = Settings(
        database_url=f"sqlite+aiosqlite:///{(db_dir / 'test.db').as_posix()}",
        secret_key="test-secret",
        env="test",
        api_prefix="",
        ai_agent_enabled=True,
        ai_device_channel_enabled=True,
        ai_device_timeout_seconds=5.0,
    )
    set_config(cfg)
    reset_engine()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    transport = httpx.ASGITransport(app=app)
    env = SimpleNamespace(cfg=cfg, factory=get_session_factory())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async def _guest(
            device_id: str, nickname: str,
        ) -> tuple[uuid.UUID, dict[str, str]]:
            response = await client.post(
                "/auth/guest", json={"device_id": device_id, "nickname": nickname},
            )
            assert response.status_code == 200, response.text
            body = response.json()
            return (
                uuid.UUID(body["user_id"]),
                {"Authorization": f"Bearer {body['tokens']['access_token']}"},
            )

        env.client = client
        env.user_id, env.headers = await _guest(DEVICE, "device-tester")
        _, env.other_headers = await _guest(OTHER_DEVICE, "someone-else")
        yield env

    reset_config()
    reset_engine()
    _rate_limits.clear()
    _ai_rate_limits.clear()
    shutil.rmtree(db_dir, ignore_errors=True)


class TestUplinkEndpoint:
    @staticmethod
    def _pending(user_id: uuid.UUID) -> str:
        """在**进程内单例**上登记一条挂起请求（与 loop 用的是同一张表）。"""
        request = build_request(get_device_tool("device_pick_image"), {}, step=1)
        D.device_channel().register(request, user_id=user_id)
        return request.request_id

    async def test_delivery_resumes_and_returns_accepted(self, api_env):
        request_id = self._pending(api_env.user_id)
        response = await api_env.client.post(
            "/ai/device-result",
            json={
                "request_id": request_id, "status": "ok",
                "data": {"mimeType": "image/jpeg"},
            },
            headers=api_env.headers,
        )

        assert response.status_code == 200, response.text
        assert response.json() == {"accepted": True}
        assert D.device_channel().pending_count == 0

    @pytest.mark.parametrize("status", ["ok", "denied", "unavailable", "failed"])
    async def test_every_deliverable_status_is_accepted(self, api_env, status):
        request_id = self._pending(api_env.user_id)
        response = await api_env.client.post(
            "/ai/device-result",
            json={"request_id": request_id, "status": status},
            headers=api_env.headers,
        )
        assert response.status_code == 200, response.text

    async def test_timeout_status_is_rejected_by_schema(self, api_env):
        """``timeout`` 是服务端产生的结局，客户端报它没有意义。"""
        request_id = self._pending(api_env.user_id)
        response = await api_env.client.post(
            "/ai/device-result",
            json={"request_id": request_id, "status": "timeout"},
            headers=api_env.headers,
        )
        assert response.status_code == 422
        assert D.device_channel().pending_count == 1

    async def test_unknown_request_id_is_404(self, api_env):
        response = await api_env.client.post(
            "/ai/device-result",
            json={"request_id": "dev-nope", "status": "ok"},
            headers=api_env.headers,
        )
        assert response.status_code == 404
        assert response.json()["code"] == "device_request_not_found"

    async def test_another_users_request_is_404_not_403(self, api_env):
        """403 等于承认「这个 id 存在但不是你的」。"""
        request_id = self._pending(api_env.user_id)
        response = await api_env.client.post(
            "/ai/device-result",
            json={"request_id": request_id, "status": "ok"},
            headers=api_env.other_headers,
        )
        assert response.status_code == 404
        assert response.json()["code"] == "device_request_not_found"
        assert D.device_channel().pending_count == 1     # 本人的请求没被动过

    async def test_second_delivery_is_404(self, api_env):
        """重试安全：已经回过一次之后再发一次，客户端按「不是错误」处理。"""
        request_id = self._pending(api_env.user_id)
        body = {"request_id": request_id, "status": "ok"}
        first = await api_env.client.post(
            "/ai/device-result", json=body, headers=api_env.headers,
        )
        second = await api_env.client.post(
            "/ai/device-result", json=body, headers=api_env.headers,
        )
        assert first.status_code == 200
        assert second.status_code == 404

    async def test_requires_authentication(self, api_env):
        request_id = self._pending(api_env.user_id)
        response = await api_env.client.post(
            "/ai/device-result", json={"request_id": request_id, "status": "ok"},
        )
        assert response.status_code == 401
        assert D.device_channel().pending_count == 1
