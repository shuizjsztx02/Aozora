"""T-M7-05g: 域路由（决定这一轮注入哪些工具域）的用例。

分三层：

1. **词表与纯函数**（:func:`routing.route_domains`）——确定性、零 IO、不碰 loop；
2. **loop 接线**——注入面收窄、隔离边界、与入口推理分级互不干扰；
3. **帧层**——``phase: routing`` 在真实 SSE 链路上可见，在
   ``test_agent_stream.py``（只有那里有真链路）。

验收映射（``docs/milestones/production-execution-plan.md`` T-M7-05g）：

- 路由失败兜底策略有用例 → :class:`TestFallback`
- **不得与入口推理分级混为一谈**（架构 §12.2 易混淆点）
  → :class:`TestNotTheSameAsReasoningLevel`
- 简单输入不因此增加模型调用 → :class:`TestNoExtraModelCall`
- 误路由率 / 漏注入率实测记录 → 跑出来的，不是断言出来的：
  ``cd server && python scripts/probe_tool_selection.py --offline``
  （58 条用例的漏注入 / 误路由 / 兜底比例，口径见 R-PROD-17）
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from copy import deepcopy
from datetime import date
from typing import Any

import pytest

from src.modules.ai import routing
from src.modules.ai import tools as T
from src.modules.ai.agent import (
    ERR_TOOL_UNAVAILABLE,
    AgentLoop,
    classify_reasoning,
)
from src.modules.ai.devices import DeviceChannel
from src.modules.ai.provider import (
    ProviderResult,
    ReasoningLevel,
    StreamChunk,
    ToolCall,
)

TODAY = date(2026, 9, 18)
MODEL = "deepseek-flash"

#: 单域输入（预算域只有读工具，v1 车道下 5 个）。
_BUDGET_ONLY = "给餐饮设个月度预算 1500"
#: 无信号输入（闲聊）——兜底路径的代表。
_NO_SIGNAL = "你好"


# ── 桩 ─────────────────────────────────────────────────────────────────────


class _FakeDb:
    """只读工具的极简假 DB（本文件不执行工具，桩只需存在）。"""

    async def execute(self, _query: Any) -> Any:
        return self._result()

    async def scalars(self, _query: Any) -> Any:
        return self._result()

    def _result(self) -> Any:
        class _Result:
            def all(self) -> list[Any]:
                return []

            def scalars(self) -> _Result:
                return self

        return _Result()


def _ctx() -> T.ToolContext:
    return T.ToolContext(
        db=_FakeDb(),
        user_id=uuid.uuid4(),
        book_id=uuid.uuid4(),
        categories_by_kind={
            "expense": {"餐饮": str(uuid.uuid4())},
            "income": {"工资": str(uuid.uuid4())},
        },
        account_names={"招行卡": str(uuid.uuid4())},
        book_names={"日常": str(uuid.uuid4())},
    )


def _text(content: str = "好。") -> ProviderResult:
    return ProviderResult(
        content=content, prompt_tokens=5, completion_tokens=7,
    )


def _call(name: str, args: dict[str, Any] | None = None) -> ToolCall:
    return ToolCall(
        id="c1", name=name, arguments=json.dumps(args or {}, ensure_ascii=False),
    )


def _tool_turn(*calls: ToolCall) -> ProviderResult:
    return ProviderResult(
        content="", prompt_tokens=5, completion_tokens=7, tool_calls=calls,
    )


class _Provider:
    """脚本化假 provider：第 n 次调用返回 ``script[n]``（用尽则回一句文本）。"""

    name = "scripted"

    def __init__(self, script: list[ProviderResult] | None = None) -> None:
        self.script = list(script or [])
        self.calls: list[dict[str, Any]] = []

    async def complete_with_tools(
        self, messages: Any, *, model: str, max_tokens: int, timeout: float,
        tools: list[dict], tool_choice: str | None = None,
        reasoning: ReasoningLevel = ReasoningLevel.FAST,
    ) -> ProviderResult:
        record = {
            "messages": deepcopy(messages),
            "tools": list(tools),
            "reasoning": reasoning,
        }
        self.calls.append(record)
        index = len(self.calls) - 1
        return self.script[index] if index < len(self.script) else _text()

    async def complete_with_tools_stream(
        self, messages: Any, **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        result = await self.complete_with_tools(messages, **kwargs)
        if result.content:
            yield StreamChunk(delta=result.content)
        yield StreamChunk(result=result)


class _RecordingObserver:
    """记下 loop 的每一次进度回调（钩子齐全——``_notify`` 用 ``getattr`` 取）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(self, hook: str, **kwargs: Any) -> None:
        self.calls.append((hook, kwargs))

    def routed(self, *, domains: Any, matched: bool) -> None:
        self._record("routed", domains=tuple(domains), matched=matched)

    def step_started(self, *, step: int, level: ReasoningLevel) -> None:
        self._record("step_started", step=step, level=level)

    def step_finished(
        self, *, step: int, prompt_tokens: int, completion_tokens: int,
    ) -> None:
        self._record("step_finished", step=step)

    def tool_started(self, *, step: int, name: str) -> None:
        self._record("tool_started", step=step, name=name)

    def tool_finished(self, *, step: int, name: str, outcome: Any) -> None:
        self._record("tool_finished", step=step, name=name)

    def delta(self, *, step: int, text: str) -> None:
        self._record("delta", step=step, text=text)

    def reasoning_delta(self, *, step: int, text: str) -> None:
        self._record("reasoning_delta", step=step, text=text)

    def device_request(self, *, step: int, payload: Any) -> None:
        self._record("device_request", step=step, payload=dict(payload))

    # -- 断言辅助 -------------------------------------------------------

    @property
    def hooks(self) -> list[str]:
        return [hook for hook, _ in self.calls]

    @property
    def routed_calls(self) -> list[dict[str, Any]]:
        return [kwargs for hook, kwargs in self.calls if hook == "routed"]


def _loop(
    provider: Any,
    *,
    observer: _RecordingObserver | None = None,
    **kwargs: Any,
) -> tuple[AgentLoop, _RecordingObserver]:
    observer = observer or _RecordingObserver()
    loop = AgentLoop(
        provider, _ctx(), model=MODEL, timeout=10.0, today=TODAY,
        observer=observer, **kwargs,
    )
    return loop, observer


def _injected(loop: AgentLoop) -> list[str]:
    return [t["function"]["name"] for t in loop._openai_tools]  # noqa: SLF001


def _lane_names(domains: Any, contract_version: int = 1) -> list[str]:
    return [
        tool.name
        for tool in T.tools_for_domains(domains, contract_version=contract_version)
    ]


def _tool_messages(result: Any) -> list[dict[str, Any]]:
    return [m for m in result.messages if m.get("role") == "tool"]


def _first_tool_payload(result: Any) -> dict[str, Any]:
    return json.loads(_tool_messages(result)[0]["content"])


# ── 1. 词表与纯函数 ────────────────────────────────────────────────────────


class TestLexicon:
    def test_a_noun_routes_to_its_domain(self):
        decision = routing.route_domains(_BUDGET_ONLY)

        assert decision.domains == (T.DOMAIN_BUDGET,)
        assert decision.matched is True
        assert "预算" in decision.signals

    def test_several_nouns_route_to_several_domains(self):
        decision = routing.route_domains("看看账户和预算")

        assert decision.domains == (T.DOMAIN_ACCOUNT, T.DOMAIN_BUDGET)
        assert decision.matched is True

    def test_domains_come_back_in_canonical_order(self):
        """同一组域在帧载荷、日志与用例里必须逐字可比（与命中顺序无关）。"""
        forward = routing.route_domains("先说预算，再说账户")
        backward = routing.route_domains("先说账户，再说预算")

        assert forward.domains == backward.domains == (
            T.DOMAIN_ACCOUNT, T.DOMAIN_BUDGET,
        )

    def test_a_shared_signal_covers_both_domains(self):
        """**近义工具分属两域时必须都注入**：``transaction_summary`` 与
        ``stats_monthly`` 在「这个月花了多少」上都是正确答案，只给一域就是一次
        漏注入（能力消失）。"""
        decision = routing.route_domains("这个月我一共花了多少")

        assert T.DOMAIN_TRANSACTION in decision.domains
        assert T.DOMAIN_ANALYSIS in decision.domains

    def test_a_bare_verb_does_not_route(self):
        """「删」跨七个域（删交易 / 删预算 / 归档账户…）——单字动词不许猜，
        猜错的方向正是漏注入。没有名词时按兜底处理。"""
        decision = routing.route_domains("删了吧")

        assert decision.matched is False
        assert decision.domains == T.DOMAINS

    def test_latin_signal_ignores_case(self):
        decision = routing.route_domains("把日常账本导出成 CSV")

        assert T.DOMAIN_EXPORT in decision.domains
        assert routing.route_domains("CSV").domains == routing.route_domains("csv").domains
        assert routing.route_domains("csv").domains == (T.DOMAIN_EXPORT,)

    def test_every_domain_has_at_least_one_signal(self):
        """**新增域时必须同时补词**（05i 的 help 域就是这么加进来的）。

        漏配的后果是静默的——那一域只能靠兜底注入，功能不消失、只是选择面变宽，
        因此这里把它变成一次可见的失败。范式同
        ``test_agent_stream.test_every_registered_tool_has_progress_text``。
        """
        assert routing.unrouteable_domains() == frozenset(), (
            "这些域没有任何路由信号："
            f"{sorted(routing.unrouteable_domains())}——在 routing._SIGNALS 里补词"
        )

    def test_signals_only_name_known_domains(self):
        """词表里的域名必须都在注册表里（防打错字：一个拼错的域名既不报错、
        也永远不会被 ``tools_for_domains`` 认出来）。"""
        named = {domain for _, targets in routing._ALL_SIGNALS for domain in targets}  # noqa: SLF001

        assert named <= set(T.DOMAINS)
        assert named == set(T.DOMAINS), "每个域都该有信号（与上一条同源）"


class TestProfileSignals:
    """T19-3（REQ-74 决策 #7）：画像类措辞必须把 ``meta`` 域带进注入面。

    这一组存在的理由是一条**实测出来的能力消失**：``DOMAIN_META`` 原先只有
    「默认」一个信号，于是「以后叫我爹」只命中 security + reminder —— meta 域被
    排除在注入面外，模型手里根本没有 ``meta_set``，只能回一句「你自己去设置页
    填」（附件 #147 的原文）。漏注入的代价是功能凭空消失，故按模块头「宁可多
    注入」补词。
    """

    def test_addressing_words_inject_meta_alongside_their_own_domains(self):
        """「叫我」是**并列**补 meta，不是从 reminder / security 挪走（决策 #7）：
        并集语义下三个域都注入，由模型按语义选。挪走的代价是「提醒我交房租」
        丢掉 reminder 域——一次真的漏注入。"""
        decision = routing.route_domains("以后叫我爹")

        assert T.DOMAIN_META in decision.domains
        assert T.DOMAIN_REMINDER in decision.domains
        assert T.DOMAIN_SECURITY in decision.domains

    @pytest.mark.parametrize(
        "text",
        [
            "我的说话偏好是温柔一点",
            "把我的用户画像清空",
            "帮我整理一下用户设定",
            "记住我的记账习惯是月底结算",
            "语气温柔一点",
            "以后称呼我为老板",
            "帮我改一下我的消费偏好",
        ],
    )
    def test_profile_phrasings_route_to_meta(self, text):
        """画像表单的 7 个字段各有自然说法，逐条钉住（补词时漏一条就是一次
        「模型没有 meta_set」的静默退化）。"""
        assert T.DOMAIN_META in routing.route_domains(text).domains

    @pytest.mark.parametrize("text", ["我更偏好用现金", "我习惯月底结账"])
    def test_a_bare_preference_word_does_not_narrow_to_meta(self, text):
        """泛词（「偏好」「习惯」本身）**不进词表**——这是这一组最容易踩的坑。

        它们出现时输入未必与设定有关（「我更偏好用现金」是在说记账方式），而
        **收窄**成 meta 单域会把记账工具一起收走，正是模块头明令避免的漏注入
        方向。所以词表只收复合词（「消费偏好」「记账习惯」），这类输入仍走
        兜底全量。"""
        decision = routing.route_domains(text)

        assert decision.matched is False
        assert decision.domains == T.DOMAINS


# ── 2. 兜底 ────────────────────────────────────────────────────────────────


class TestFallback:
    """**宁可多注入，不可少注入**：不确定的输入一律退回全量（= P1 的注入面）。"""

    def test_chitchat_falls_back_to_every_domain(self):
        decision = routing.route_domains(_NO_SIGNAL)

        assert decision.domains == T.DOMAINS
        assert decision.matched is False
        assert decision.signals == ()

    def test_a_pronoun_follow_up_falls_back(self):
        """指代性追问（「那改成 6000 吧」）命中不了任何信号——**这正是它不能
        收窄的理由**：收窄了就等于把上一轮的能力收走了。"""
        decision = routing.route_domains("那改成 6000 吧")

        assert decision.domains == T.DOMAINS
        assert decision.matched is False

    @pytest.mark.parametrize("text", ["", "   ", "\n"])
    def test_blank_input_falls_back(self, text: str):
        assert routing.route_domains(text).domains == T.DOMAINS

    def test_the_fallback_flag_separates_miss_from_wide_hit(self):
        """``matched=False`` 与「命中了全部域」是两件事：前者说明词表没覆盖这句
        输入（生产上值得观察的比例），后者说明这句话真的横跨很多东西。"""
        miss = routing.route_domains(_NO_SIGNAL)
        wide = routing.route_domains("账户、预算、账本、分类、存钱、定时、角色都看看")

        # 前者是兜底、后者是命中——组装的域集合可以相同，标志位必须不同。
        assert miss.matched is False
        assert wide.matched is True


# ── 3. loop 接线：注入面 ───────────────────────────────────────────────────


class TestInjection:
    async def test_a_route_narrows_the_injected_tools(self):
        provider = _Provider([_text()])
        loop, _ = _loop(provider)

        await loop.run(_BUDGET_ONLY)

        assert _injected(loop) == _lane_names([T.DOMAIN_BUDGET])
        # 模型请求里发出去的**就是**这一份（载荷与可执行集合同源）
        assert [t["function"]["name"] for t in provider.calls[0]["tools"]] == (
            _injected(loop)
        )

    async def test_an_unmatched_input_keeps_the_full_injection(self):
        """无信号 → 与 P1 逐字相同的注入面（这是「兜底不退化成能力缺失」的证据）。"""
        provider = _Provider([_text()])
        loop, _ = _loop(provider)

        await loop.run(_NO_SIGNAL)

        assert _injected(loop) == _lane_names(T.DOMAINS)

    async def test_a_tool_outside_the_route_is_not_executable(self):
        """**隔离边界**：路由收窄了注入面，模型若报出域外的工具名（幻觉或
        「多走一步」）必须被拒——否则收窄只是省了 token，没省下误导。"""
        provider = _Provider([
            _tool_turn(_call("stats_monthly", {})),
            _text("没有这个能力。"),
        ])
        loop, _ = _loop(provider)

        result = await loop.run(_BUDGET_ONLY)

        payload = _first_tool_payload(result)
        assert payload["code"] == ERR_TOOL_UNAVAILABLE
        assert result.tool_calls == 0

    async def test_explicit_tools_skip_routing(self):
        """显式给了 ``tools`` 的调用方（用例 / 探针的对照臂）**不被路由接管**，
        也**不会**收到 ``routed`` 回调——那时没有做过选择。"""
        provider = _Provider([_text()])
        only = [T.get_tool("account_list")]
        loop, observer = _loop(provider, tools=only)

        await loop.run(_BUDGET_ONLY)

        assert _injected(loop) == ["account_list"]
        assert observer.routed_calls == []

    async def test_device_tools_are_injected_whatever_the_route(self):
        """设备能力不在域里（它是一张独立的短清单）：路由收窄服务端工具，
        设备能力照常注入——05e 的挂起/恢复语义因此与域路由正交。"""
        provider = _Provider([_text()])
        loop, _ = _loop(provider, device=DeviceChannel(timeout_seconds=5.0))

        await loop.run(_BUDGET_ONLY)

        names = _injected(loop)
        assert "budget_list" in names
        # M8：haptic/widget 改由服务端确定性下发，不再出现在注入面；
        # 通道仍注入它真正需要模型选择的设备能力（如分享）。
        assert "device_share_text" in names
        assert "device_haptic" not in names
        assert "stats_monthly" not in names, "域外的服务端工具仍被收窄"


# ── 4. 与入口推理分级是两件事（架构 §12.2 易混淆点）────────────────────────


class TestNotTheSameAsReasoningLevel:
    async def test_the_two_decisions_move_independently(self):
        """**深档但单域** vs **浅档但多域**——这一对输入证明两个判定轴各自的
        取值不受另一个影响。合并实现（比如「多动作就该多注入域」）在这两条上
        立刻说不通。"""
        deep = "记一笔午饭 12 块，然后再记一笔打车 30"
        shallow = "看看账户和预算"

        assert classify_reasoning(deep) is ReasoningLevel.DEEP
        assert routing.route_domains(deep).domains == (T.DOMAIN_TRANSACTION,)

        assert classify_reasoning(shallow) is ReasoningLevel.FAST
        assert routing.route_domains(shallow).domains == (
            T.DOMAIN_ACCOUNT, T.DOMAIN_BUDGET,
        )

    async def test_routing_does_not_touch_the_reasoning_level(self):
        """loop 里两件事各按各的判据走：``phase.reasoning`` 报的是推理档位，
        它不因为注入面收窄而改变。"""
        provider = _Provider([_text()])
        loop, observer = _loop(provider)

        await loop.run(_BUDGET_ONLY)

        levels = [kwargs["level"] for hook, kwargs in observer.calls
                  if hook == "step_started"]
        assert levels == [classify_reasoning(_BUDGET_ONLY)]


# ── 5. 不额外调用模型 ──────────────────────────────────────────────────────


class TestNoExtraModelCall:
    async def test_routing_adds_no_model_call(self):
        """架构 §12.2 已定第 2 项：简单输入的快路径由 loop 自然产生——域选择
        **不许**换来一次分类器调用（那会让每一轮都多付一次钱，且它自己也会错）。"""
        provider = _Provider([_text()])
        loop, _ = _loop(provider)

        await loop.run(_BUDGET_ONLY)

        assert len(provider.calls) == 1, "一次输入 = 一次模型请求"

    def test_the_router_is_synchronous(self):
        """路由是**同步纯函数**——没有 await 点，因此不可能夹带 IO（网络 / DB /
        模型）。这一条是上一条的结构性保证，不依赖桩的计数。"""
        decision = routing.route_domains(_BUDGET_ONLY)

        assert not hasattr(decision, "__await__")


# ── 6. 观察者钩子 ──────────────────────────────────────────────────────────


class TestObserverHook:
    async def test_routed_fires_once_before_the_first_step(self):
        provider = _Provider([_text()])
        loop, observer = _loop(provider)

        await loop.run(_BUDGET_ONLY)

        assert observer.hooks.count("routed") == 1
        assert observer.hooks.index("routed") < observer.hooks.index("step_started")

    async def test_routed_carries_the_domains_and_the_fallback_flag(self):
        provider = _Provider([_text()])
        loop, observer = _loop(provider)

        await loop.run(_BUDGET_ONLY)
        assert observer.routed_calls == [
            {"domains": (T.DOMAIN_BUDGET,), "matched": True},
        ]

        fallback_provider = _Provider([_text()])
        fallback, fallback_observer = _loop(fallback_provider)
        await fallback.run(_NO_SIGNAL)
        assert fallback_observer.routed_calls == [
            {"domains": tuple(T.DOMAINS), "matched": False},
        ]
