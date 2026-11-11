"""T-M7-02d: Agent loop 核心与推理分级测试。

验收映射（docs/milestones/production-execution-plan.md T-M7-02d）：
- 四条跨请求不变式各有用例    → TestPairingInvariant / TestMidTurnInput /
                                TestCancellation / TestResumableHistory
- 四条硬边界各有用例          → TestHardBoundaries
- 优雅终止含「已完成 / 未完成」→ TestGracefulTermination
- 三档判定规则各有用例        → TestReasoningClassification
- Step 升级在入口误判时生效   → TestStepUpgrade
- 「快」档显式传 thinking 关闭 → TestFastTierThinking

全部用例用假 provider（脚本化返回）+ 真实工具注册表跑，无网络、无数据库
（只读工具走 ``_FakeDb`` 桩，写工具与参数校验本就不需要 DB）。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from copy import deepcopy
from datetime import date
from typing import Any, Callable

import httpx
import pytest

from src.modules.ai import tools as T
from src.modules.ai.agent import (
    DEFAULT_MAX_TOKENS,
    ERR_TOOL_BUDGET,
    ERR_TOOL_FAILED,
    ERR_TOOL_UNAVAILABLE,
    ERR_WRITE_BUDGET,
    AgentLimits,
    AgentLoop,
    StopReason,
    classify_reasoning,
)
from src.modules.ai.provider import (
    DeepSeekProvider,
    ProviderError,
    ProviderResult,
    ReasoningLevel,
    StreamChunk,
    ToolCall,
)

TODAY = date(2026, 9, 18)
MODEL = "deepseek-flash"

_ADD_OK = {
    "type": "expense",
    "amountCents": 1250,
    "categoryName": "餐饮",
    "bizDate": "2026-09-18",
}


# ── 桩与夹具 ───────────────────────────────────────────────────────────────


class _FakeDb:
    """只读工具的极简假 DB：``execute()`` / ``scalars()`` 都返回给定行。"""

    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = rows or []

    async def execute(self, _query: Any) -> Any:
        return self._result()

    async def scalars(self, _query: Any) -> Any:
        return self._result()

    def _result(self) -> Any:
        rows = self._rows

        class _Result:
            def all(self) -> list[Any]:
                return rows

            def scalars(self) -> _Result:
                return self

        return _Result()


class _ExplodingDb:
    """任何查询都抛异常的假 DB——模拟真实链路里的数据库故障。"""

    async def execute(self, _query: Any) -> Any:
        raise RuntimeError("boom: database is gone")

    async def scalars(self, _query: Any) -> Any:
        raise RuntimeError("boom: database is gone")

    async def scalar(self, _query: Any) -> Any:
        raise RuntimeError("boom: database is gone")


def _ctx(**overrides: Any) -> T.ToolContext:
    """内存 ToolContext（写路径与参数校验不需要真 DB）。"""
    defaults: dict[str, Any] = dict(
        db=_FakeDb(),
        user_id=uuid.uuid4(),
        book_id=uuid.uuid4(),
        categories_by_kind={
            "expense": {"餐饮": str(uuid.uuid4()), "交通": str(uuid.uuid4())},
            "income": {"工资": str(uuid.uuid4())},
        },
        account_names={"招行卡": str(uuid.uuid4())},
        book_names={"日常": str(uuid.uuid4())},
    )
    defaults.update(overrides)
    return T.ToolContext(**defaults)


def _text(
    content: str,
    *,
    reasoning: str = "",
    prompt_tokens: int = 5,
    completion_tokens: int = 7,
) -> ProviderResult:
    return ProviderResult(
        content=content,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        reasoning_content=reasoning,
    )


def _call(name: str, args: dict[str, Any] | None = None, *, id: str = "") -> ToolCall:
    return ToolCall(id=id, name=name, arguments=json.dumps(args or {}, ensure_ascii=False))


def _tool_turn(*calls: ToolCall, content: str = "") -> ProviderResult:
    return ProviderResult(
        content=content, prompt_tokens=5, completion_tokens=7, tool_calls=calls,
    )


ScriptItem = ProviderResult | Exception | Callable[[dict[str, Any]], Any]


class ScriptedProvider:
    """脚本化假 provider：第 n 次调用返回 ``script[n]``。

    脚本项可以是 ``ProviderResult``、异常实例（抛出），或 ``(record) -> 结果``
    的可调用对象——后者用于「在模型请求进行中」触发 cancel / submit。
    """

    name = "scripted"

    def __init__(self, script: list[ScriptItem]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

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
        record = {
            "messages": deepcopy(messages),
            "tools": list(tools),
            "reasoning": reasoning,
            "model": model,
            "max_tokens": max_tokens,
            "timeout": timeout,
        }
        self.calls.append(record)
        item: Any = self.script[index] if index < len(self.script) else _text("（脚本已用尽）")
        if callable(item):
            item = item(record)
        if isinstance(item, Exception):
            raise item
        return item

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
        """流式形态（T-M7-02g-2）：loop 自本卡起只走这条路。

        脚本语义与非流式**逐字相同**（同一个调用序号、同一批异常与可调用项），
        差别只在交付形态：推理一帧、正文一帧、最后是带 ``result`` 的收尾帧。
        分片粒度不在这里——它按用例需要显式构造（见 ``test_agent_stream``），
        否则每条既有断言都要按 4 字符/帧改写。
        """
        result = await self.complete_with_tools(
            messages, model=model, max_tokens=max_tokens, timeout=timeout,
            tools=tools, tool_choice=tool_choice, reasoning=reasoning,
        )
        if result.reasoning_content:
            yield StreamChunk(reasoning_delta=result.reasoning_content)
        if result.content:
            yield StreamChunk(delta=result.content)
        yield StreamChunk(result=result)

    # -- 断言辅助 -------------------------------------------------------

    @property
    def levels(self) -> list[ReasoningLevel]:
        return [call["reasoning"] for call in self.calls]

    def sent(self, index: int) -> str:
        """第 index 次请求的全部消息文本（用于「不得出现某内容」类断言）。"""
        return json.dumps(self.calls[index]["messages"], ensure_ascii=False)


def _loop(provider: Any, *, ctx: T.ToolContext | None = None, **kwargs: Any) -> AgentLoop:
    params: dict[str, Any] = dict(
        model=MODEL, timeout=10.0, today=TODAY, ctx=ctx or _ctx(),
    )
    params.update(kwargs)
    return AgentLoop(provider, params.pop("ctx"), **params)


def _assistant_messages(messages: tuple[dict, ...]) -> list[dict]:
    return [m for m in messages if m.get("tool_calls")]


def _tool_messages(messages: tuple[dict, ...]) -> list[dict]:
    return [m for m in messages if m.get("role") == "tool"]


def _finish_turn() -> ProviderResult:
    """收尾请求的应答（脚本里最后一项）。"""
    return _text("已完成：查询了账户。未完成：其余步骤已达上限。")


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _sse_answer(
    *,
    content: str = "",
    reasoning: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
    prompt_tokens: int = 3,
    completion_tokens: int = 5,
) -> str:
    """一次流式应答的 SSE 正文（推理帧 → 正文 / 工具帧 → usage 尾块 → ``[DONE]``）。

    loop 自 02g-2 起走流式，因此「用**真实** ``DeepSeekProvider`` 断言真正发出的
    请求体」的那几条用例必须喂它 SSE——喂非流式的 JSON 体在流式路径下解析不出任何
    东西。形状照真实 provider：用量在**尾块**（``stream_options.include_usage``），
    工具调用的首片带 ``id`` 与 ``function.name``。
    """
    frames = []
    if reasoning:
        frames.append(_sse({"choices": [{"delta": {"reasoning_content": reasoning}}]}))
    if content:
        frames.append(_sse({"choices": [{"delta": {"content": content}}]}))
    if tool_calls:
        frames.append(_sse({"choices": [{"delta": {"tool_calls": tool_calls}}]}))
    frames.append(_sse({"choices": [], "usage": {
        "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
    }}))
    frames.append("data: [DONE]\n\n")
    return "".join(frames)


# ── 入口推理分级（架构 §4.6）────────────────────────────────────────────


class TestReasoningClassification:
    """入口分级是**纯函数**（字符串进、档位出，签名里没有 provider）——「不额外
    调用 LLM」由此在结构上成立，不需要（也无法）用断言证明，故不设空转用例。"""

    @pytest.mark.parametrize(
        "text",
        ["记一笔午饭 12 块", "这个月餐饮花了多少", "你好呀", "今天收入 500", "帮我看看还剩多少钱"],
    )
    def test_simple_intents_are_fast(self, text: str):
        assert classify_reasoning(text) is ReasoningLevel.FAST

    @pytest.mark.parametrize(
        "text",
        [
            "把上个月那笔餐饮改成交通",
            "删掉昨天那笔打车",
            "新建一个账本叫旅行",
            "把预算设置成 3000",
            "把默认账户换成招行卡",
        ],
    )
    def test_single_step_understanding_is_medium(self, text: str):
        """单步但要理解——编辑 / 删除 / 配置（架构 §4.6 档位表）。"""
        assert classify_reasoning(text) is ReasoningLevel.MEDIUM

    @pytest.mark.parametrize(
        "text",
        [
            "把上个月预算改成 3000，然后看看这个月花了多少",
            "先查一下这个月的支出，再帮我记一笔 50 块的餐饮",
            "记一笔午饭 12 块，顺便看看预算还剩多少",
            "早饭 12、午饭 25 分别记一下",
        ],
    )
    def test_multi_action_is_deep(self, text: str):
        assert classify_reasoning(text) is ReasoningLevel.DEEP

    def test_deep_wins_over_medium(self):
        assert classify_reasoning("把预算改成 3000，然后记一笔 12 块") is ReasoningLevel.DEEP


# ── 不变式 1：工具调用与结果配对 ──────────────────────────────────────────


class TestPairingInvariant:
    async def test_every_call_is_followed_by_exactly_one_result(self):
        provider = ScriptedProvider([
            _tool_turn(
                _call("account_list", {}, id="a"),
                _call("budget_summary", {"period": "2026-09"}, id="b"),
            ),
            _text("好了"),
        ])
        result = await _loop(provider).run("这个月花了多少")

        messages = list(result.messages)
        assistants = _assistant_messages(result.messages)
        assert len(assistants) == 1
        ids = [c["id"] for c in assistants[0]["tool_calls"]]
        assert ids == ["a", "b"]
        index = messages.index(assistants[0])
        paired = messages[index + 1:index + 1 + len(ids)]
        assert [m["tool_call_id"] for m in paired] == ids
        assert all(m["role"] == "tool" for m in paired)

    async def test_unknown_tool_still_gets_a_paired_result(self):
        """降级不破配对：未知工具名回一条错误结果，而不是抛异常或跳过。"""
        provider = ScriptedProvider([
            _tool_turn(_call("transaction.teleport", {}, id="x")),
            _text("抱歉，这个我做不到。"),
        ])
        result = await _loop(provider).run("帮我把钱传送到别处")

        paired = _tool_messages(result.messages)
        assert [m["tool_call_id"] for m in paired] == ["x"]
        assert '"code":"unknown_tool"' in paired[0]["content"].replace(" ", "")

    async def test_provider_may_omit_ids_and_the_loop_supplies_them(self):
        """provider 层刻意放行空 id（``_parse_tool_calls``），配对由 loop 补。"""
        provider = ScriptedProvider([
            _tool_turn(_call("account_list", {})),  # id=""
            _text("好了"),
        ])
        result = await _loop(provider).run("看看账户")

        assistant = _assistant_messages(result.messages)[0]
        supplied = assistant["tool_calls"][0]["id"]
        assert supplied
        assert _tool_messages(result.messages)[0]["tool_call_id"] == supplied

    async def test_malformed_arguments_get_a_paired_error(self):
        provider = ScriptedProvider([
            _tool_turn(ToolCall(id="m", name="account_list", arguments="{不是 JSON")),
            _text("我再试一次。"),
        ])
        result = await _loop(provider).run("看看账户")

        paired = _tool_messages(result.messages)
        assert [m["tool_call_id"] for m in paired] == ["m"]
        assert '"code":"invalid_arguments"' in paired[0]["content"].replace(" ", "")
        assert result.tool_calls == 1

    async def test_duplicate_tool_call_ids_are_renumbered(self):
        """同一个 id 挂两条结果 → 下一次请求里「一个调用对两条结果」，严格 provider 会 400。"""
        provider = ScriptedProvider([
            _tool_turn(
                _call("account_list", {}, id="dup"),
                _call("account_list", {}, id="dup"),
            ),
            _text("好了"),
        ])
        result = await _loop(provider).run("看看账户")

        ids = [m["tool_call_id"] for m in _tool_messages(result.messages)]
        assert len(ids) == 2 and len(set(ids)) == 2
        assert ids == [c["id"] for c in _assistant_messages(result.messages)[0]["tool_calls"]]

    async def test_tool_handler_exception_degrades_instead_of_exploding(self):
        """工具处理器会碰数据库，异常必须降级成配对错误，而不是炸穿整轮。

        ``invoke_tool`` 只保证「未知名 / 参数不合法」不抛，**不保护处理器本体**。
        """
        provider = ScriptedProvider([
            _tool_turn(_call("account_list", {}, id="a")),
            _text("抱歉，账本暂时读不到。"),
        ])
        result = await _loop(provider, ctx=_ctx(db=_ExplodingDb())).run("看看账户")

        assert result.stop_reason is StopReason.COMPLETED
        paired = _tool_messages(result.messages)
        assert [m["tool_call_id"] for m in paired] == ["a"], "配对不能断"
        payload = json.loads(paired[0]["content"])
        assert payload["code"] == ERR_TOOL_FAILED
        # 异常正文可能回显用户输入或库结构（security.md 脱敏口径），不得进模型可见范围
        assert "boom" not in provider.sent(1)

    async def test_calls_never_appear_without_results_across_steps(self):
        provider = ScriptedProvider([
            _tool_turn(_call("account_list", {}, id="a")),
            _tool_turn(_call("budget_list", {}, id="b")),
            _text("好了"),
        ])
        result = await _loop(provider).run("看看账户和预算")

        messages = list(result.messages)
        for index, message in enumerate(messages):
            if not message.get("tool_calls"):
                continue
            ids = [c["id"] for c in message["tool_calls"]]
            following = messages[index + 1:index + 1 + len(ids)]
            assert [m.get("tool_call_id") for m in following] == ids
        assert len(_tool_messages(result.messages)) == 2


# ── 不变式 2：中途输入进正确的下一步 ─────────────────────────────────────


class TestMidTurnInput:
    async def test_queued_input_lands_after_the_previous_steps_results(self):
        loop: AgentLoop

        def during_first_request(record: dict[str, Any]) -> ProviderResult:
            loop.submit("改成交通")
            return _tool_turn(_call("account_list", {}, id="a"))

        provider = ScriptedProvider([during_first_request, _text("已改成交通。")])
        loop = _loop(provider)
        result = await loop.run("把上个月那笔餐饮改一下")

        # 第二次请求里，用户中途输入必须排在第一步的工具结果之后
        second = provider.calls[1]["messages"]
        roles = [m["role"] for m in second]
        tool_index = max(i for i, m in enumerate(second) if m["role"] == "tool")
        user_index = max(i for i, m in enumerate(second) if m["role"] == "user")
        assert tool_index < user_index
        assert second[user_index]["content"] == "改成交通"
        assert roles.count("user") == 2  # 本轮输入 + 中途输入
        assert result.pending_input == ()

    async def test_queued_input_never_lands_inside_a_tool_batch(self):
        """输入进的是「下一步」，不是「已完成的 Step」。"""
        loop: AgentLoop

        def during_first_request(_record: dict[str, Any]) -> ProviderResult:
            loop.submit("顺便看看预算")
            return _tool_turn(
                _call("account_list", {}, id="a"),
                _call("budget_list", {}, id="b"),
            )

        provider = ScriptedProvider([during_first_request, _text("好了")])
        loop = _loop(provider)
        result = await loop.run("查一下")

        messages = list(result.messages)
        # a、b 两条结果相邻，中途输入排在它们之后
        assert [m["tool_call_id"] for m in _tool_messages(result.messages)] == ["a", "b"]
        last_tool = max(i for i, m in enumerate(messages) if m["role"] == "tool")
        queued = [i for i, m in enumerate(messages) if m["content"] == "顺便看看预算"]
        assert queued == [last_tool + 1]

    async def test_empty_input_is_ignored(self):
        """空串不入队——真要有下一步时，空串会变成一条空的 user 消息混进请求。

        （第一步必须**调工具**，否则回合在第 1 步就收尾，``_drain_queue`` 根本
        不会被执行，这个用例就成了空转——02d 复审实测过这一点。）
        """
        loop: AgentLoop

        def during_first_request(_record: dict[str, Any]) -> ProviderResult:
            loop.submit("")
            return _tool_turn(_call("account_list", {}, id="a"))

        provider = ScriptedProvider([during_first_request, _text("好了")])
        loop = _loop(provider)
        result = await loop.run("你好")

        assert result.pending_input == ()
        users = [
            m["content"] for m in provider.calls[1]["messages"] if m["role"] == "user"
        ]
        assert users == ["你好"], "空串不该变成一条空 user 消息混进请求"

    async def test_input_arriving_as_the_model_closes_still_gets_a_step(self):
        """模型已经收口（不再调工具）时才到达的输入，不能被当成「本轮做完了」。"""
        loop: AgentLoop

        def during_first_request(_record: dict[str, Any]) -> ProviderResult:
            loop.submit("再记一笔 20 块的交通")
            return _text("这个月你花了 1200 元。")

        provider = ScriptedProvider([during_first_request, _text("好，记上了。")])
        loop = _loop(provider)
        result = await loop.run("这个月花了多少")

        assert result.steps == 2, "收口那一步之后还要再走一步接住中途输入"
        assert result.pending_input == ()
        assert provider.calls[1]["messages"][-1]["content"] == "再记一笔 20 块的交通"
        assert result.content == "好，记上了。"

    async def test_input_arriving_at_a_hard_boundary_returns_to_the_caller(self):
        """撞边界时本轮已无下一步可承载 → 必须交回调用方，不能静默丢掉。"""
        loop: AgentLoop

        def during_first_request(_record: dict[str, Any]) -> ProviderResult:
            loop.submit("顺便看看预算")
            return _tool_turn(_call("account_list", {}, id="a"))

        provider = ScriptedProvider([during_first_request, _finish_turn()])
        loop = _loop(provider, limits=AgentLimits(max_steps=1))
        result = await loop.run("看看账户")

        assert result.stop_reason is StopReason.STEP_LIMIT
        assert result.pending_input == ("顺便看看预算",)
        assert all(
            m.get("content") != "顺便看看预算" for m in result.messages
        ), "它没有被处理，也不该伪装成已进历史"


# ── 不变式 3：取消收敛到一致状态 ─────────────────────────────────────────


class TestCancellation:
    async def test_cancel_stops_before_the_next_request(self):
        loop: AgentLoop

        def during_first_request(_record: dict[str, Any]) -> ProviderResult:
            loop.cancel()
            return _tool_turn(_call("account_list", {}, id="a"))

        provider = ScriptedProvider([during_first_request, _text("永远不会被请求")])
        loop = _loop(provider)
        result = await loop.run("看看账户")

        assert result.stop_reason is StopReason.CANCELLED
        assert len(provider.calls) == 1, "取消后不得再发起请求（含收尾请求）"
        assert result.steps == 1

    async def test_cancelled_turn_leaves_a_consistent_history(self):
        """取消不切断工具批次：调用与结果仍然配对（不变式 1 不被取消破坏）。"""
        loop: AgentLoop

        def during_first_request(_record: dict[str, Any]) -> ProviderResult:
            loop.cancel()
            return _tool_turn(
                _call("account_list", {}, id="a"),
                _call("unknown.tool", {}, id="b"),
            )

        provider = ScriptedProvider([during_first_request])
        loop = _loop(provider)
        result = await loop.run("看看账户")

        assistant = _assistant_messages(result.messages)[0]
        ids = [c["id"] for c in assistant["tool_calls"]]
        assert [m["tool_call_id"] for m in _tool_messages(result.messages)] == ids

    async def test_cancel_before_run_makes_no_request(self):
        provider = ScriptedProvider([_text("不该被调用")])
        loop = _loop(provider)
        loop.cancel()
        result = await loop.run("你好")

        assert result.stop_reason is StopReason.CANCELLED
        assert provider.calls == []
        assert result.content == ""


# ── 不变式 4：历史可恢复可分叉 ───────────────────────────────────────────


class TestResumableHistory:
    async def test_result_messages_resume_the_next_turn(self):
        first = _loop(ScriptedProvider([
            _tool_turn(_call("account_list", {}, id="a")),
            _text("账户查好了。"),
        ]))
        result = await first.run("看看账户")
        history = list(result.messages)

        provider = ScriptedProvider([_text("好，记上了。")])
        await _loop(provider).run("再记一笔午饭 12 块", history=history)

        sent = provider.calls[0]["messages"]
        assert sent[0]["role"] == "system"
        assert sent[1:1 + len(history)] == history

    async def test_delta_is_this_turn_only(self):
        """``result.messages`` 只含本轮新增：不含 system、不含传进来的历史。

        02f 按「接在 history 后面」持久化；delta 语义一旦回归，历史会被逐轮重复
        放大（system 与旧历史每轮翻倍），而 02d 复审实测「空历史下的用例」区分不出
        这个回归——必须**带非空历史**才测得到。
        """
        history = [
            {"role": "user", "content": "看看账户"},
            {"role": "assistant", "content": "看完了", "reasoning_content": ""},
        ]
        provider = ScriptedProvider([
            _tool_turn(_call("account_list", {}, id="a")),
            _text("好了"),
        ])
        result = await _loop(provider).run("再记一笔", history=history)

        assert [m["role"] for m in result.messages] == [
            "user", "assistant", "tool", "assistant",
        ]
        assert result.messages[0]["content"] == "再记一笔"
        assert all(m["role"] != "system" for m in result.messages)

    async def test_history_is_never_mutated(self):
        history = [
            {"role": "user", "content": "记一笔午饭 12 块"},
            {"role": "assistant", "content": "记好了。"},
        ]
        snapshot = deepcopy(history)
        provider = ScriptedProvider([_text("好。")])
        await _loop(provider).run("再来一笔", history=history)

        assert history == snapshot

    async def test_same_history_can_fork_into_identical_runs(self):
        """同一段历史喂给两个 loop 实例 → 两次请求逐字相同（可分叉）。"""
        history = [{"role": "user", "content": "看看账户"}, {"role": "assistant", "content": "看完了"}]
        first = ScriptedProvider([_text("好。")])
        second = ScriptedProvider([_text("好。")])
        await _loop(first).run("再记一笔", history=history)
        await _loop(second).run("再记一笔", history=history)

        assert first.calls[0]["messages"] == second.calls[0]["messages"]

    async def test_a_cancelled_turn_can_be_resumed(self):
        """取消后历史仍可续跑（架构 §4.4 不变式 3 与 4 的交叉点）。"""
        loop: AgentLoop

        def cancel_mid_request(_record: dict[str, Any]) -> ProviderResult:
            loop.cancel()
            return _tool_turn(_call("account_list", {}, id="a"))

        provider = ScriptedProvider([cancel_mid_request])
        loop = _loop(provider)
        cancelled = await loop.run("看看账户")
        assert cancelled.stop_reason is StopReason.CANCELLED

        resumed = ScriptedProvider([_text("账户里有 100 元。")])
        result = await _loop(resumed).run("继续", history=list(cancelled.messages))
        assert result.stop_reason is StopReason.COMPLETED
        assert result.content == "账户里有 100 元。"


# ── 四条硬边界 ───────────────────────────────────────────────────────────


class TestHardBoundaries:
    async def test_step_limit_is_eight(self):
        provider = ScriptedProvider(
            [_tool_turn(_call("account_list", {}, id=f"c{i}")) for i in range(12)]
            + [_finish_turn()]
        )
        result = await _loop(provider).run("一直查下去")

        assert result.stop_reason is StopReason.STEP_LIMIT
        assert result.steps == 8
        assert len(provider.calls) == 9, "8 个 Step + 1 次收尾请求，第 9 个 Step 不发"

    async def test_tool_call_budget_refuses_the_excess_with_paired_errors(self):
        limits = AgentLimits(max_tool_calls=1)
        provider = ScriptedProvider([
            _tool_turn(
                _call("account_list", {}, id="a"),
                _call("account_list", {}, id="b"),
                _call("account_list", {}, id="c"),
            ),
            _finish_turn(),
        ])
        result = await _loop(provider, limits=limits).run("查三次")

        assert result.stop_reason is StopReason.TOOL_CALL_LIMIT
        assert result.tool_calls == 1, "超出预算的调用不执行"
        paired = _tool_messages(result.messages)
        assert [m["tool_call_id"] for m in paired] == ["a", "b", "c"], "但必须配对"
        assert f'"code":"{ERR_TOOL_BUDGET}"' in paired[1]["content"].replace(" ", "")
        assert f'"code":"{ERR_TOOL_BUDGET}"' in paired[2]["content"].replace(" ", "")

    async def test_write_budget_stops_the_turn(self):
        limits = AgentLimits(max_writes=2)
        provider = ScriptedProvider([
            _tool_turn(_call("transaction_add", _ADD_OK, id="w1")),
            _tool_turn(_call("transaction_add", _ADD_OK, id="w2")),
            _tool_turn(_call("transaction_add", _ADD_OK, id="w3")),
            _finish_turn(),
        ])
        result = await _loop(provider, limits=limits).run("记三笔")

        assert result.stop_reason is StopReason.WRITE_LIMIT
        assert result.writes == 2
        assert len(result.proposals) == 2
        assert len(provider.calls) == 3, "第三步不再发起"

    async def test_write_budget_refuses_within_a_single_step(self):
        limits = AgentLimits(max_writes=1)
        provider = ScriptedProvider([
            _tool_turn(
                _call("transaction_add", _ADD_OK, id="w1"),
                _call("transaction_add", _ADD_OK, id="w2"),
                _call("transaction_add", _ADD_OK, id="w3"),
            ),
            _finish_turn(),
        ])
        result = await _loop(provider, limits=limits).run("一口气记三笔")

        assert result.writes == 1
        paired = _tool_messages(result.messages)
        assert [m["tool_call_id"] for m in paired] == ["w1", "w2", "w3"]
        assert f'"code":"{ERR_WRITE_BUDGET}"' in paired[1]["content"].replace(" ", "")

    async def test_batch_proposal_counts_as_one_write(self):
        """一个提案 = 一次写操作（批量提案虽含多笔，仍是原子的一个操作）。

        ``max_writes=1`` 时批量提案照样执行——若按笔数计，两笔就会顶掉预算。
        """
        limits = AgentLimits(max_writes=1)
        provider = ScriptedProvider([
            _tool_turn(_call("transaction_add_batch", {
                "transactions": [_ADD_OK, {**_ADD_OK, "amountCents": 2500}],
            }, id="b1")),
            _finish_turn(),
        ])
        result = await _loop(provider, limits=limits).run("记两笔")

        assert result.writes == 1
        assert result.proposals[0]["action"] == "add_tx_batch"
        assert len(result.proposals[0]["transactions"]) == 2
        # 写预算已用尽 → 下一步在入口即优雅终止（硬边界语义）
        assert result.stop_reason is StopReason.WRITE_LIMIT

    async def test_two_barren_steps_end_the_turn(self):
        bad = _tool_turn(_call("transaction_add", {"type": "expense"}, id="e"))
        provider = ScriptedProvider([bad, bad, bad, _finish_turn()])
        result = await _loop(provider).run("记一笔")

        assert result.stop_reason is StopReason.NO_PROGRESS
        assert result.steps == 2

    async def test_any_progress_resets_the_barren_counter(self):
        bad = _tool_turn(_call("transaction_add", {"type": "expense"}, id="e"))
        good = _tool_turn(_call("account_list", {}, id="g"))
        provider = ScriptedProvider([bad, good, bad, _text("好了")])
        # 输入同时点到两个域（记账 / 账户）——两次调用因此都在注入面内。本用例量的
        # 是硬边界的计数，不是域路由（05g），故用**跨域**的输入而不是显式 `tools=`。
        result = await _loop(provider).run("记一笔，再看看账户")

        assert result.stop_reason is StopReason.COMPLETED
        assert result.steps == 4

    async def test_a_step_with_one_failure_and_one_success_is_progress(self):
        """混合结果的 Step 必须清零计数——判定是「**任一**成功」，不是「全部成功」。

        模型最常见的形态就是一步里既有成功也有失败；若写成 ``all``，两个这样的
        Step 就会把回合提前掐死，而 02d 复审实测「``any`` → ``all``」当时无任何
        用例报警（现有用例的每个 Step 要么全错要么全对）。
        """
        mixed = _tool_turn(
            _call("transaction_add", {"type": "expense"}, id="bad"),
            _call("account_list", {}, id="good"),
        )
        provider = ScriptedProvider([mixed, mixed, _text("好了")])
        # 同上：跨域输入，两次调用都在注入面内（05g 的域路由）。
        result = await _loop(provider).run("记一笔，再看看账户")

        assert result.stop_reason is StopReason.COMPLETED
        assert result.steps == 3, "两个混合步之后仍应继续（无进展计数被清零）"

    async def test_limits_are_the_architecture_values(self):
        limits = AgentLimits()
        assert (limits.max_steps, limits.max_writes) == (8, 5)
        assert (limits.max_tool_calls, limits.max_no_progress_steps) == (20, 2)


# ── 优雅终止 ─────────────────────────────────────────────────────────────


class TestGracefulTermination:
    async def _limit_loop(self, tail: list[ScriptItem], **_kwargs: Any) -> tuple[Any, Any]:
        provider = ScriptedProvider(
            [_tool_turn(_call("account_list", {}, id=f"c{i}")) for i in range(8)] + tail
        )
        loop = _loop(provider, **_kwargs)
        return provider, await loop.run("一直查下去")

    async def test_wrap_up_asks_for_completed_and_pending_parts(self):
        provider, result = await self._limit_loop([_finish_turn()])

        assert result.stop_reason is StopReason.STEP_LIMIT
        wrap = provider.calls[-1]
        assert wrap["tools"] == [], "收尾请求不得带工具"
        instruction = wrap["messages"][-1]["content"]
        # 要求的是**字面标签**（真实 API 复测：只要求「两段」时模型不会写这两个词）
        assert "已完成：" in instruction and "未完成：" in instruction
        assert result.content == _finish_turn().content

    async def test_wrap_up_answer_is_appended_to_the_history(self):
        _provider, result = await self._limit_loop([_finish_turn()])
        assert result.messages[-1] == {
            "role": "assistant",
            "content": _finish_turn().content,
            # 收尾回答也要带该键：它会被持久化，下一轮 thinking 模式下缺键即 400。
            "reasoning_content": "",
        }

    async def test_provider_failure_falls_back_to_a_deterministic_summary(self):
        provider, result = await self._limit_loop([
            ProviderError(status=None, retryable=False, reason="timeout"),
        ])

        assert result.stop_reason is StopReason.STEP_LIMIT
        assert "已完成：" in result.content
        assert "未完成：" in result.content
        assert len(provider.calls) == 9  # 收尾请求发出过，只是失败了

    async def test_empty_wrap_up_answer_falls_back(self):
        _provider, result = await self._limit_loop([_tool_turn()])

        assert "已完成：" in result.content and "未完成：" in result.content
        assert "步骤数已达上限" in result.content

    async def test_fallback_reports_what_was_actually_done(self):
        _provider, result = await self._limit_loop([_tool_turn()])
        assert "本轮已执行 8 次数据操作" in result.content

    async def test_cancel_is_not_a_graceful_stop(self):
        """取消不触发收尾请求：用户已经离开，多一次模型往返没有意义。"""
        loop: AgentLoop

        def cancel_mid_request(_record: dict[str, Any]) -> ProviderResult:
            loop.cancel()
            return _tool_turn(_call("account_list", {}, id="a"))

        provider = ScriptedProvider([cancel_mid_request, _finish_turn()])
        loop = _loop(provider)
        result = await loop.run("看看账户")

        assert result.stop_reason is StopReason.CANCELLED
        assert len(provider.calls) == 1
        assert "已完成：" not in result.content


# ── 推理档位在 loop 内的注入与升级 ───────────────────────────────────────


class TestStepUpgrade:
    async def test_second_step_upgrades_to_deep(self):
        """入口判快、实际多步 → 第 2 个 Step 升到「深」档（入口误判不致命）。"""
        provider = ScriptedProvider([
            _tool_turn(_call("account_list", {}, id="a")),
            _text("这个月你还没有账户。"),
        ])
        result = await _loop(provider).run("这个月花了多少")

        assert provider.levels == [ReasoningLevel.FAST, ReasoningLevel.DEEP]
        assert result.stop_reason is StopReason.COMPLETED

    async def test_entry_deep_is_never_downgraded(self):
        provider = ScriptedProvider([
            _tool_turn(_call("account_list", {}, id="a")),
            _text("好了"),
        ])
        await _loop(provider).run("先查一下这个月的支出，再帮我记一笔 50 块的餐饮")

        assert provider.levels == [ReasoningLevel.DEEP, ReasoningLevel.DEEP]

    async def test_entry_medium_is_kept_for_the_first_step(self):
        provider = ScriptedProvider([_text("改好了。")])
        await _loop(provider).run("把预算改成 3000")

        assert provider.levels == [ReasoningLevel.MEDIUM]

    async def test_wrap_up_runs_at_deep(self):
        provider = ScriptedProvider(
            [_tool_turn(_call("account_list", {}, id="a"))] * 8 + [_finish_turn()]
        )
        await _loop(provider).run("一直查下去")

        assert provider.calls[-1]["reasoning"] is ReasoningLevel.DEEP


class TestFastTierThinking:
    async def test_fast_tier_sends_explicit_thinking_disabled(self):
        """「快」档必须显式传 ``disabled``——该模型 thinking 默认开启（02a 实测）。

        这里用真实 ``DeepSeekProvider`` + ``MockTransport``，断言的是真正发出
        去的请求体，而不是 loop 传了什么参数。
        """
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, text=_sse_answer(
                content="记好了。", prompt_tokens=11, completion_tokens=7,
            ))

        provider = DeepSeekProvider(
            "sk-test", "https://api.deepseek.com", transport=httpx.MockTransport(handler),
        )
        try:
            loop = _loop(provider)
            result = await loop.run("记一笔午饭 12 块")
        finally:
            await provider.aclose()

        assert result.stop_reason is StopReason.COMPLETED
        assert seen["body"]["thinking"] == {"type": "disabled"}
        assert "reasoning_effort" not in seen["body"]

    async def test_request_carries_the_v1_lane_tool_catalog(self):
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, text=_sse_answer(
                content="好", prompt_tokens=1, completion_tokens=1,
            ))

        provider = DeepSeekProvider(
            "sk-test", "https://api.deepseek.com", transport=httpx.MockTransport(handler),
        )
        try:
            await _loop(provider).run("账本")
        finally:
            await provider.aclose()

        names = [t["function"]["name"] for t in seen["body"]["tools"]]
        # 缺省车道是 v1（05b）；输入命中 book 域（M8 起未命中不再全量注入）
        assert names == [
            t.name for t in T.tools_for_domains(
                ["book"], contract_version=T.PROPOSAL_CONTRACT_V1,
            )
        ]
        assert "book_list" in names
        assert "book_delete" not in names, "v2-only 的写工具不进 v1 车道"
        assert seen["body"]["tool_choice"] == "auto"
        assert seen["body"]["max_tokens"] == DEFAULT_MAX_TOKENS

    async def test_max_tokens_leaves_room_for_reasoning(self):
        """02a 实测：预算被 reasoning 吃光会让 content 变空串，故不能照搬快路径的 512。"""
        assert DEFAULT_MAX_TOKENS > 512


def _rule_faithful_handler(request: httpx.Request) -> httpx.Response:
    """忠实模拟真实 API 的 thinking 规则（2026-09-18 真实探针实测）。

    规则：请求带 ``thinking: enabled`` 时，**历史里每一条 assistant 消息都必须
    带 ``reasoning_content`` 键**（空串可接受），否则 400。

    为什么要在 mock 里建模这条：02b/02c 的教训——mock 不建模 API 的真实约束，
    这类 400 在单测里**结构上不可能被发现**（02c 的工具名点号 400 即同类）。
    应答按 SSE 形态给（loop 自 02g-2 起走流式）。
    """
    body = json.loads(request.content)
    enabled = (body.get("thinking") or {}).get("type") == "enabled"
    if enabled:
        for message in body["messages"]:
            if message.get("role") == "assistant" and "reasoning_content" not in message:
                return httpx.Response(400, json={"error": {
                    "message": "The `reasoning_content` in the thinking mode "
                               "must be passed back to the API.",
                    "type": "invalid_request_error",
                }})
    last = body["messages"][-1]
    if last.get("role") == "tool":
        content, tool_calls = "记好了。", None
    else:
        content = ""
        tool_calls = [{
            "index": 0, "id": "call_1", "type": "function",
            "function": {"name": "account_list", "arguments": "{}"},
        }]
    return httpx.Response(200, text=_sse_answer(
        content=content,
        reasoning="先列账户再回答。" if enabled else "",
        tool_calls=tool_calls,
    ))


class TestReasoningEchoContract:
    """assistant 消息必须始终带 ``reasoning_content`` 键（真实 API 的硬要求）。

    触发路径是最常见的那条：**入口判快（第 1 步 thinking 关闭）→ 第 2 步升深**。
    第 1 步本来就产不出推理内容，若因此省略该键，第 2 步（thinking 开启）会被
    真实 API 以 400 拒绝——整轮对话失败，且只有真实调用才暴露得出来。
    """

    async def test_fast_entry_then_deep_step_is_not_rejected(self):
        provider = DeepSeekProvider(
            "sk-test", "https://api.deepseek.com",
            transport=httpx.MockTransport(_rule_faithful_handler),
        )
        try:
            result = await _loop(provider).run("记一笔午饭 12 块")
        finally:
            await provider.aclose()

        assert result.stop_reason is StopReason.COMPLETED
        assert result.content == "记好了。"
        assert result.steps == 2

    async def test_every_assistant_message_carries_the_key(self):
        provider = ScriptedProvider([
            _tool_turn(_call("account_list", {}, id="a")),  # 无推理内容
            _text("好了"),
        ])
        result = await _loop(provider).run("看看账户")

        assistants = [m for m in result.messages if m["role"] == "assistant"]
        assert assistants
        for message in assistants:
            assert "reasoning_content" in message

    async def test_wrap_up_answer_also_carries_the_key(self):
        provider = ScriptedProvider(
            [_tool_turn(_call("account_list", {}, id=f"c{i}")) for i in range(8)]
            + [_text("已完成：查完了。未完成：没有别的了。", reasoning="总结一下。")]
        )
        result = await _loop(provider).run("一直查下去")

        last = result.messages[-1]
        assert last["role"] == "assistant"
        assert last["reasoning_content"] == "总结一下。"

    async def test_history_without_the_key_is_normalized(self):
        """02f 持久化的形状不受本模块控制：缺键的 assistant 消息补齐空串。"""
        history = [
            {"role": "user", "content": "看看账户"},
            {"role": "assistant", "content": "看完了"},
        ]
        provider = ScriptedProvider([_text("好。")])
        await _loop(provider).run("再来一笔", history=history)

        sent = provider.calls[0]["messages"]
        assert sent[2]["reasoning_content"] == ""
        # 调用方手里那份不被改动（不变式 4）
        assert "reasoning_content" not in history[1]

    async def test_existing_reasoning_is_preserved_not_overwritten(self):
        history = [
            {"role": "assistant", "content": "看完了", "reasoning_content": "原来的推理"},
        ]
        provider = ScriptedProvider([_text("好。")])
        await _loop(provider).run("再来一笔", history=history)

        assert provider.calls[0]["messages"][1]["reasoning_content"] == "原来的推理"


# ── 写工具交接（02c → 02e）──────────────────────────────────────────────


class TestProposalHandoff:
    async def test_proposal_ids_never_reach_the_model(self):
        """02c 的硬约束：草案含 id，但回喂模型前必须剥掉（铁律 1）。"""
        ctx = _ctx()
        category_id = ctx.categories_by_kind["expense"]["餐饮"]
        provider = ScriptedProvider([
            _tool_turn(_call("transaction_add", _ADD_OK, id="w1")),
            _text("记好了。"),
        ])
        await _loop(provider, ctx=ctx).run("记一笔午饭 12 块")

        sent = provider.sent(1)
        assert category_id not in sent
        assert "categoryId" not in sent

    async def test_proposal_is_surfaced_intact_for_the_assembler(self):
        """草案本身**保留** id——ActionProposal 契约要求，执行时必需（02e 用）。"""
        ctx = _ctx()
        category_id = ctx.categories_by_kind["expense"]["餐饮"]
        provider = ScriptedProvider([
            _tool_turn(_call("transaction_add", _ADD_OK, id="w1")),
            _text("记好了。"),
        ])
        result = await _loop(provider, ctx=ctx).run("记一笔午饭 12 块")

        assert result.writes == 1
        draft = result.proposals[0]
        assert draft["action"] == "add_tx"
        assert draft["transactions"][0]["categoryId"] == category_id
        assert draft["message"]

    async def test_governance_fields_are_filled_by_the_assembler(self):
        """治理字段由组装器补齐（02d 时它们还不存在，02e 才接线）。

        ``proposalId`` 必须服务端生成——它同时是执行的幂等键；``confidence`` 必须
        确定性推导、不采信模型自报；``requiresConfirmation`` 为 True（交回调用方
        的提案一律要确认）。逐一取值的覆盖见 ``test_ai_agent_approval.py``。
        """
        provider = ScriptedProvider([
            _tool_turn(_call("transaction_add", _ADD_OK, id="w1")),
            _text("记好了。"),
        ])
        result = await _loop(provider).run("记一笔午饭 12 块")

        proposal = result.proposals[0]
        assert proposal["proposalId"].startswith("agent-")
        assert 0 <= proposal["confidence"] <= 1
        assert proposal["requiresConfirmation"] is True

    async def test_tool_result_tells_the_model_the_write_is_pending(self):
        """不执行写入，也不能让模型以为写完了（架构 §7.3 的确认门在 02e）。"""
        provider = ScriptedProvider([
            _tool_turn(_call("transaction_add", _ADD_OK, id="w1")),
            _text("记好了。"),
        ])
        result = await _loop(provider).run("记一笔午饭 12 块")

        payload = json.loads(_tool_messages(result.messages)[0]["content"])
        assert payload["status"] == "proposal_pending"
        assert payload["action"] == "add_tx"

    async def test_failed_write_does_not_count_towards_the_write_budget(self):
        """参数不合法的写调用没有产出提案，不该顶掉写预算。

        ``max_writes=1``：第一步失败（预算仍为 0）→ 第二步的写才被放行。若失败
        也计数，第二步在入口就会终止，proposals 会是空的。
        """
        limits = AgentLimits(max_writes=1)
        provider = ScriptedProvider([
            _tool_turn(_call("transaction_add", {"type": "expense"}, id="bad")),
            _tool_turn(_call("transaction_add", _ADD_OK, id="ok")),
            _finish_turn(),
        ])
        result = await _loop(provider, limits=limits).run("记一笔")

        assert result.writes == 1
        assert len(result.proposals) == 1
        assert result.stop_reason is StopReason.WRITE_LIMIT
        assert result.steps == 2


# ── 收尾行为与提示词 ─────────────────────────────────────────────────────


class TestTurnTermination:
    async def test_text_answer_completes_the_turn(self):
        provider = ScriptedProvider([_text("你这个月花了 1200 元。")])
        result = await _loop(provider).run("这个月花了多少")

        assert result.stop_reason is StopReason.COMPLETED
        assert result.content == "你这个月花了 1200 元。"
        assert result.steps == 1
        assert result.messages[-1]["role"] == "assistant"

    async def test_blank_answer_is_reported_as_empty(self):
        """02a 实测：thinking 吃光预算会让 content 变空串——不能让调用方以为答完了。"""
        provider = ScriptedProvider([_text("   ")])
        result = await _loop(provider).run("这个月花了多少")

        assert result.stop_reason is StopReason.EMPTY_RESPONSE
        assert result.content.strip() == ""

    async def test_provider_error_propagates_to_the_caller(self):
        """降级链由调用方决定（架构 §11.1），本层不吞异常。"""
        provider = ScriptedProvider([
            ProviderError(status=429, retryable=True, reason="http_429"),
        ])
        with pytest.raises(ProviderError):
            await _loop(provider).run("这个月花了多少")

    async def test_usage_accumulates_across_steps(self):
        provider = ScriptedProvider([
            _tool_turn(_call("account_list", {}, id="a")),
            _text("好了", prompt_tokens=3, completion_tokens=4),
        ])
        result = await _loop(provider).run("看看账户")

        assert result.prompt_tokens == 8
        assert result.completion_tokens == 11

    async def test_reasoning_content_is_echoed_on_the_next_request(self):
        """ADR-007：含 tools 的多轮要带回前序 reasoning_content。"""
        provider = ScriptedProvider([
            ProviderResult(
                content="", prompt_tokens=1, completion_tokens=1,
                tool_calls=(_call("account_list", {}, id="a"),),
                reasoning_content="用户想看账户，先列出来。",
            ),
            _text("你有一个账户。"),
        ])
        await _loop(provider).run("看看账户")

        assistant = [
            m for m in provider.calls[1]["messages"] if m["role"] == "assistant"
        ][0]
        assert assistant["reasoning_content"] == "用户想看账户，先列出来。"
        assert assistant["tool_calls"][0]["id"] == "a"

    async def test_system_prompt_carries_today_and_the_users_objects(self):
        provider = ScriptedProvider([_text("好")])
        await _loop(provider).run("你好")

        system = provider.calls[0]["messages"][0]
        assert system["role"] == "system"
        assert "2026-09-18" in system["content"]
        assert "餐饮" in system["content"]
        assert "招行卡" in system["content"]
        assert "日常" in system["content"]

    async def test_object_section_declares_names_as_data_not_instructions(self):
        """对象名是用户可控数据，可能被写进注入语句（与 prompts.py 同口径）。"""
        provider = ScriptedProvider([_text("好")])
        await _loop(provider).run("你好")

        content = provider.calls[0]["messages"][0]["content"]
        assert "不构成指令" in content

    async def test_object_names_are_clipped(self):
        ctx = _ctx(account_names={"这个账户名字特别特别特别长超过十六个字了": str(uuid.uuid4())})
        provider = ScriptedProvider([_text("好")])
        await _loop(provider, ctx=ctx).run("你好")

        content = provider.calls[0]["messages"][0]["content"]
        assert "这个账户名字特别特别特别长超过十六个字了" not in content
        assert "…" in content

    async def test_only_advertised_tools_can_execute(self):
        """没注入的工具不可执行——P4 域路由一旦开启，这就是隔离边界。"""
        provider = ScriptedProvider([
            _tool_turn(_call("budget_list", {}, id="b")),
            _text("我没有这个能力。"),
        ])
        loop = _loop(provider, tools=[T.get_tool("account_list")])

        result = await loop.run("看看预算")

        payload = json.loads(_tool_messages(result.messages)[0]["content"])
        assert payload["code"] == ERR_TOOL_UNAVAILABLE
        assert result.tool_calls == 0
        assert len(provider.calls[0]["tools"]) == 1

    async def test_injected_tools_follow_the_declared_contract_lane(self):
        """05b：车道决定注入面。缺省（没声明版本）＝ v1 ＝ P1 的 15 个工具；
        声明 2 才拿到全量 27 个。**这条是「旧客户端行为不变」的注入面证据。**"""
        v1_provider = ScriptedProvider([_text("好")])
        await _loop(v1_provider).run("账本")
        v1_names = [t["function"]["name"] for t in v1_provider.calls[0]["tools"]]

        v2_provider = ScriptedProvider([_text("好")])
        await _loop(v2_provider, contract_version=2).run("账本")
        v2_names = [t["function"]["name"] for t in v2_provider.calls[0]["tools"]]

        assert v1_names == [
            t.name for t in T.tools_for_domains(
                ["book"], contract_version=T.PROPOSAL_CONTRACT_V1,
            )
        ]
        assert v2_names == [
            t.name for t in T.tools_for_domains(["book"], contract_version=2)
        ]
        assert len(v2_names) > len(v1_names)


class TestToolPayloadHygiene:
    async def test_read_results_are_forwarded_as_json(self):
        provider = ScriptedProvider([
            _tool_turn(_call("account_list", {}, id="a")),
            _text("好了"),
        ])
        result = await _loop(provider).run("看看账户")

        payload = json.loads(_tool_messages(result.messages)[0]["content"])
        assert payload == {"accounts": [], "total": 0}

    async def test_id_shaped_keys_are_stripped_from_nested_results(self):
        """纵深防御：读工具目前不含 id，注入的 id 键也必须在下发前消失。

        键名覆盖 02d 复审列出的逃逸形态（``ID`` / ``bookID`` / ``ids`` / ``uuid``
        / ``ref``），同时不能误伤 ``valid`` / ``paid`` 这类正常字段。
        """
        from src.modules.ai.agent import _without_ids

        u = "11111111-2222-3333-4444-555555555555"
        payload = {
            "items": [{
                "name": "餐饮", "categoryId": u, "book_id": u, "id": u,
                "ID": u, "bookID": u, "ref": u,
                "valid": True, "paid": True,
            }],
            "accountId": u, "ids": [u], "uuid": u,
        }
        assert _without_ids(payload) == {
            "items": [{"name": "餐饮", "valid": True, "paid": True}],
        }


# ── 流式增量（T-M7-02g-2）────────────────────────────────────────────────


class _RecordingObserver:
    """把 loop 的每一次进度回调原样记下来（协议 6 个钩子，全部显式实现）。

    **必须显式**：``AgentLoop._notify`` 用 ``getattr`` 取钩子、异常一律吞掉，
    少写一个方法只会让那类回调静默消失——用例把「钩子被调到」当作断言对象，
    所以这里宁可啰嗦也不靠 ``__getattr__`` 兜。
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(self, hook: str, **kwargs: Any) -> None:
        self.calls.append((hook, kwargs))

    def step_started(self, *, step: int, level: ReasoningLevel) -> None:
        self._record("step_started", step=step, level=level)

    def step_finished(
        self, *, step: int, prompt_tokens: int, completion_tokens: int,
    ) -> None:
        self._record(
            "step_finished", step=step,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        )

    def tool_started(self, *, step: int, name: str) -> None:
        self._record("tool_started", step=step, name=name)

    def tool_finished(self, *, step: int, name: str, outcome: T.ToolOutcome) -> None:
        self._record("tool_finished", step=step, name=name, outcome=outcome)

    def delta(self, *, step: int, text: str) -> None:
        self._record("delta", step=step, text=text)

    def reasoning_delta(self, *, step: int, text: str) -> None:
        self._record("reasoning_delta", step=step, text=text)

    # -- 断言辅助 -------------------------------------------------------

    @property
    def increments(self) -> list[tuple[str, str]]:
        """``(通道, 文本)`` 序列，只保留两个增量钩子。"""
        return [
            (hook, kwargs["text"])
            for hook, kwargs in self.calls
            if hook in ("delta", "reasoning_delta")
        ]


class _IncrementOnlyProvider:
    """违约的假 provider：只给增量，不给带 ``result`` 的收尾帧。"""

    name = "increment-only"

    def __init__(self, *increments: StreamChunk) -> None:
        self._increments = increments

    async def complete_with_tools_stream(self, messages, **kwargs):
        for chunk in self._increments:
            yield chunk


class TestStreamingIncrements:
    """模型请求走 token 级流式，正文与推理**分通道**外送（架构 §8.5）。"""

    async def test_answer_and_reasoning_use_separate_hooks(self):
        """推理不进正文通道——两者在客户端渲染到不同位置，合并就没法分了。"""
        observer = _RecordingObserver()
        provider = ScriptedProvider([
            _text("你好呀！", reasoning="用户只是打招呼，直接回。"),
        ])

        await _loop(provider, observer=observer).run("你好")

        assert observer.increments == [
            ("reasoning_delta", "用户只是打招呼，直接回。"),
            ("delta", "你好呀！"),
        ]
        assert all(kwargs["step"] == 1 for hook, kwargs in observer.calls
                   if hook in ("delta", "reasoning_delta"))

    async def test_blank_answer_emits_no_increment(self):
        """空回答（thinking 吃光预算的形态）不该发一个空气泡——原因由
        ``stop_reason`` 交代，与 02g 的帧层同口径。"""
        observer = _RecordingObserver()

        await _loop(ScriptedProvider([_text("")]), observer=observer).run("你好")

        assert observer.increments == []

    async def test_wrap_up_text_also_reaches_the_delta_hook(self):
        """撞边界时的收尾文案**不是流式产出的**，但必须照样送到客户端。

        收尾请求刻意不流式（失败会退回确定性兜底，流式会让客户端看到
        「半截 + 兜底」拼接），所以它由 ``_wrap_up`` 自己发一帧——漏掉的话
        客户端会拿到一个空气泡加 ``done``。
        """
        observer = _RecordingObserver()
        provider = ScriptedProvider([
            _tool_turn(_call("budget_list", {})),
            _finish_turn(),
        ])

        result = await _loop(
            provider, observer=observer, limits=AgentLimits(max_steps=1),
        ).run("看看预算")

        assert result.stop_reason is StopReason.STEP_LIMIT
        assert ("delta", result.content) in observer.increments
        assert "已完成：" in result.content  # 收尾文案（模型给的或兜底的）

    async def test_every_step_reports_its_own_increments(self):
        """两次模型请求各自带自己的 ``step``——客户端据此把增量归到正确的步骤。"""
        observer = _RecordingObserver()
        provider = ScriptedProvider([
            _tool_turn(_call("budget_list", {}), content="我先查一下。"),
            _text("这个月还没有设置预算。", reasoning="汇总一下再答。"),
        ])

        await _loop(provider, observer=observer).run("看看预算")

        assert [(hook, kwargs["step"]) for hook, kwargs in observer.calls
                if hook in ("delta", "reasoning_delta")] == [
            ("delta", 1),
            ("reasoning_delta", 2),
            ("delta", 2),
        ]

    async def test_stream_without_a_result_chunk_is_a_bad_response(self):
        """provider 契约是「增量之后**恰好一个**带 ``result`` 的收尾帧」。

        收不到就是实现违约。此时增量已经全部交出去了，因此报**不可重试**——
        重发会把这些内容再送一遍（与 02b 的流式重试规则同口径）。
        """
        provider = _IncrementOnlyProvider(StreamChunk(delta="半句"))

        with pytest.raises(ProviderError) as exc_info:
            await _loop(provider).run("你好")

        assert exc_info.value.reason == "bad_response"
        assert exc_info.value.retryable is False
