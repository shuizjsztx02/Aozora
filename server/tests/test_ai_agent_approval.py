"""T-M7-02e: 写工具安全路径（ActionProposal + 审批四值）测试。

验收映射（`docs/milestones/production-execution-plan.md` T-M7-02e / P1 计划 §三 02e）：

- 模型输出 UUID 被拒             → TestForgedIdentifiers
- 三条铁律各有用例               → TestIronLaws
- 审批四值 + `unavailable` 视为拒绝 → TestApprovalFourValues
- `allowed-once` 执行后即失效     → TestOneShotGrant
- 单 Step 高置信自动执行         → TestApprovalPolicy
- 多步场景写操作必出确认卡       → TestApprovalPolicy / TestApprovalFourValues

全部用例用假 provider + 假审批通道 + 假执行通道跑，**无网络、无数据库**。
``ServiceProposalExecutor`` 对 ``AIService.execute_proposal`` 的接线由参数透传
断言钉住——执行器自身的语义（verdict / TOCTOU / 幂等）归它自己的用例，本卡
一行不改（职责边界：只做接线）。

审批只在 **Step ≥ 2** 才会被问到：Step 1 写出的提案若能自动执行就直接执行
（快路径策略）。因此四值用例统一用「先读一步、第二步才写」的脚本，这也正是
生产里会走到审批的形态。
"""

from __future__ import annotations

import inspect
import json
import uuid
from copy import deepcopy
from datetime import date
from typing import Any

from src.modules.ai import tools as T
from src.modules.ai.agent import (
    ERR_APPROVAL_CANCELLED,
    ERR_APPROVAL_REJECTED,
    ERR_APPROVAL_UNAVAILABLE,
    ERR_EXECUTION_FAILED,
    ERR_PROPOSAL_REJECTED,
    AgentLoop,
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
    ServiceProposalExecutor,
    StopReason,
    assemble_proposal,
    confirm_reason,
    validation_context_for,
)
from src.modules.ai.provider import ProviderResult, ReasoningLevel, ToolCall
from src.modules.ai.service import AIService

TODAY = date(2026, 9, 18)
MODEL = "deepseek-flash"

_ADD_OK: dict[str, Any] = {
    "type": "expense",
    "amountCents": 1250,
    "categoryName": "餐饮",
    "bizDate": "2026-09-18",
}


# ── 桩与夹具 ───────────────────────────────────────────────────────────────


class _FakeDb:
    """只读工具的极简假 DB（与 `test_ai_agent.py` 同形；写路径不需要真库）。"""

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


def _ctx(**overrides: Any) -> T.ToolContext:
    """内存 ToolContext。**只填名字→id 那一套映射**——校验上下文的派生必须容忍
    反向映射缺席（真实 ``load_tool_context`` 两套都填，手搓上下文常常只填一套）。
    """
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


def _text(content: str) -> ProviderResult:
    return ProviderResult(content=content, prompt_tokens=5, completion_tokens=7)


def _call(name: str, args: dict[str, Any] | None = None, *, id: str = "") -> ToolCall:
    return ToolCall(id=id, name=name, arguments=json.dumps(args or {}, ensure_ascii=False))


def _tool_turn(*calls: ToolCall, content: str = "") -> ProviderResult:
    return ProviderResult(
        content=content, prompt_tokens=5, completion_tokens=7, tool_calls=calls,
    )


class ScriptedProvider:
    """脚本化假 provider：第 n 次调用返回 ``script[n]``（脚本用尽则返回纯文本）。"""

    name = "scripted"

    def __init__(self, script: list[ProviderResult]) -> None:
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
        self.calls.append({"messages": deepcopy(messages), "tools": list(tools)})
        if index < len(self.script):
            return self.script[index]
        return _text("（脚本已用尽）")


class FakeApprover:
    """假审批通道：按剧本给结论，并记录每一次征询。"""

    def __init__(self, *decisions: Any, journal: list[str] | None = None) -> None:
        self.decisions = list(decisions)
        self.requests: list[ApprovalRequest] = []
        self._journal = journal if journal is not None else []

    async def __call__(self, request: ApprovalRequest) -> Any:
        self._journal.append("ask")
        self.requests.append(request)
        if self.decisions:
            return self.decisions.pop(0)
        return ApprovalDecision.UNAVAILABLE


class FakeExecutor:
    """假执行通道：返回 ``execute_proposal`` 形状的回执，并记录每次调用。"""

    def __init__(
        self, *, journal: list[str] | None = None, error: Exception | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._journal = journal if journal is not None else []
        self._error = error

    async def __call__(
        self, proposal: Any, *, confirmation_token: str,
    ) -> dict[str, Any]:
        self._journal.append("execute")
        self.calls.append({
            "proposal": dict(proposal),
            "confirmation_token": confirmation_token,
        })
        if self._error is not None:
            raise self._error
        return {
            "transaction_ids": [
                str(uuid.uuid4()) for _ in proposal.get("transactions") or []
            ],
            "proposal_id": proposal.get("proposalId"),
            "undo_window_seconds": 5,
        }


def _loop(
    provider: Any,
    *,
    ctx: T.ToolContext | None = None,
    approver: Any = None,
    executor: Any = None,
) -> AgentLoop:
    return AgentLoop(
        provider,
        ctx or _ctx(),
        model=MODEL,
        timeout=10.0,
        today=TODAY,
        approver=approver,
        executor=executor,
    )


def _tool_messages(messages: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    return [dict(m) for m in messages if m.get("role") == "tool"]


def _payload(message: dict[str, Any]) -> dict[str, Any]:
    return json.loads(str(message["content"]))


def _payload_for(messages: tuple[dict[str, Any], ...], call_id: str) -> dict[str, Any]:
    """取某一次工具调用的回喂内容（多步脚本里不能只看第一条）。"""
    for message in _tool_messages(messages):
        if message.get("tool_call_id") == call_id:
            return _payload(message)
    raise AssertionError(f"没有配对的工具结果：{call_id}")


def _multi_step_write(*calls: ToolCall) -> list[ProviderResult]:
    """「先读一步、第二步才写」——生产里唯一会走到审批的形态。"""
    return [
        _tool_turn(_call("account_list", {}, id="r1")),
        _tool_turn(*calls),
        _text("好了。"),
    ]


# ── 模型输出 UUID 被拒 ─────────────────────────────────────────────────────


class TestForgedIdentifiers:
    """铁律 1：模型永不输出 UUID——名→id 只由确定性代码解析。"""

    async def test_uuid_as_category_name_is_refused(self):
        """模型把真 id 当分类名回填（幻觉 id 的最直接形态）→ 工具层解析失败。"""
        ctx = _ctx()
        category_id = ctx.categories_by_kind["expense"]["餐饮"]
        executor = FakeExecutor()
        provider = ScriptedProvider([
            _tool_turn(_call(
                "transaction_add", {**_ADD_OK, "categoryName": category_id}, id="w1",
            )),
            _text("好的。"),
        ])
        result = await _loop(provider, ctx=ctx, executor=executor).run("记一笔")

        assert result.proposals == ()
        assert result.executions == ()
        assert executor.calls == [], "id 没解析出来，什么都别执行"
        content = _tool_messages(result.messages)[0]["content"]
        assert _payload_for(result.messages, "w1")["code"] == "unknown_category"
        # 回喂内容里出现的那串 UUID 是**模型自己写进参数的**（02c 的降级契约要把
        # 出错的值回显给模型才能自纠），不是服务端解析出的 id：名→id 只在
        # ``_build_tx_item`` 的确定性解析里发生，解析失败的路径上一个 id 都没产生。
        assert "categoryId" not in content

    async def test_foreign_category_id_in_a_draft_is_rejected(self):
        """草案带着**不属于本账本**的 id → 校验判幻觉：既不执行也不挂起。

        02c 保证正常路径下 id 出自服务端上下文；本用例守的是纵深防御——将来若有
        新写工具、或有人把客户端传来的 id 透传进草案，这里是唯一会拦住它的地方。
        """
        loop = _loop(ScriptedProvider([]))
        foreign = str(uuid.uuid4())
        outcome = await loop._handle_proposal({
            "action": "add_tx",
            "transactions": [{
                "type": "expense",
                "amountCents": 100,
                "categoryId": foreign,
                "bizDate": "2026-09-18",
                "note": "",
            }],
            "message": "记一笔",
            "warnings": [],
        })

        assert outcome.kind == "error"
        assert outcome.data["code"] == ERR_PROPOSAL_REJECTED
        assert loop._proposals == [], "被拒的提案不得挂起给客户端"
        assert foreign not in json.dumps(outcome.data, ensure_ascii=False)

    async def test_draft_governance_fields_are_never_trusted(self):
        """草案自带的治理字段一律作废——**覆写**而非「缺省才填」。

        草案给的是对抗性取值：想自己定提案号（幂等键）、想把置信度压到自动执行
        门槛之下（好让执行器去要确认口令）、想直接声明「不需要确认」。
        """
        proposal = assemble_proposal({
            "action": "add_tx",
            "transactions": [],
            "message": "x",
            "warnings": [],
            "proposalId": "attacker-chosen",
            "confidence": 0,
            "requiresConfirmation": False,
        })

        assert proposal["proposalId"] != "attacker-chosen"
        assert proposal["proposalId"].startswith("agent-")
        assert proposal["confidence"] == 1.0
        assert proposal["requiresConfirmation"] is True


# ── 三条铁律 ───────────────────────────────────────────────────────────────


class TestIronLaws:
    async def test_model_never_sees_any_id_on_the_pending_path(self):
        """挂起路径：回喂模型的提案摘要里没有任何 id 键与 id 值。"""
        ctx = _ctx()
        category_id = ctx.categories_by_kind["expense"]["餐饮"]
        provider = ScriptedProvider([
            _tool_turn(_call("transaction_add", _ADD_OK, id="w1")),
            _text("请确认。"),
        ])
        result = await _loop(provider, ctx=ctx).run("记一笔午饭 12 块")

        content = _tool_messages(result.messages)[0]["content"]
        assert category_id not in content
        for key in ("categoryId", "accountId", "proposalId"):
            assert key not in content
        assert _payload_for(result.messages, "w1")["status"] == "proposal_pending"

    async def test_model_never_sees_any_id_on_the_executed_path(self):
        """执行路径同样干净——事务 id 只进 ``executions``（给调用方做撤销）。"""
        ctx = _ctx()
        category_id = ctx.categories_by_kind["expense"]["餐饮"]
        provider = ScriptedProvider([
            _tool_turn(_call("transaction_add", _ADD_OK, id="w1")),
            _text("记好了。"),
        ])
        executor = FakeExecutor()
        result = await _loop(provider, ctx=ctx, executor=executor).run("记一笔午饭 12 块")

        content = _tool_messages(result.messages)[0]["content"]
        assert category_id not in content
        for tx_id in result.executions[0]["transaction_ids"]:
            assert tx_id not in content
        assert _payload_for(result.messages, "w1")["status"] == "executed"

    def test_confidence_is_computed_not_trusted(self):
        """铁律 2：置信度由确定性代码推导——工具带 ``warnings`` 就降档。"""
        clean = assemble_proposal({"action": "add_tx", "message": "x", "warnings": []})
        warned = assemble_proposal({
            "action": "add_tx", "message": "x", "warnings": ["金额和上次差很多"],
        })

        assert clean["confidence"] == 1.0
        assert warned["confidence"] < clean["confidence"]

    def test_threshold_matches_the_executor_verdict(self):
        """与 ``AIService.validate_proposal`` 的判定**对拍**：门槛不许两处漂移。

        低置信提案在执行器那里得到 ``rejected_low_confidence``（必须带确认口令），
        在审批策略里同样必须强制确认——否则 agent 路径会自动执行掉一份执行器
        认为「要人确认」的提案。
        """
        ctx = _ctx()
        vctx = validation_context_for(ctx)
        item = {
            "type": "expense",
            "amountCents": 1250,
            "categoryId": ctx.categories_by_kind["expense"]["餐饮"],
            "bizDate": "2026-09-18",
            "note": "",
        }
        base = {"action": "add_tx", "transactions": [item], "message": "x"}
        low = assemble_proposal({**base, "warnings": ["不确定"]})
        high = assemble_proposal({**base, "warnings": []})

        assert AIService.validate_proposal(low, vctx)["verdict"] == "rejected_low_confidence"
        assert confirm_reason(step=1, confidence=low["confidence"]) == "low_confidence"

        assert AIService.validate_proposal(high, vctx)["verdict"] == "accepted"
        assert confirm_reason(step=1, confidence=high["confidence"]) is None

    async def test_no_executor_means_no_write_at_all(self):
        """铁律 3：没有执行通道，写库一次也不会发生（提案只挂起）。"""
        provider = ScriptedProvider([
            _tool_turn(_call("transaction_add", _ADD_OK, id="w1")),
            _text("请确认。"),
        ])
        result = await _loop(provider).run("记一笔午饭 12 块")

        assert result.executions == ()
        assert len(result.proposals) == 1
        assert result.proposals[0]["requiresConfirmation"] is True

    def test_validation_context_excludes_ids_the_context_never_offered(self):
        """派生集合只认上下文自己给过的 id（并集），外来的不在其中。"""
        ctx = _ctx()
        vctx = validation_context_for(ctx)

        assert set(ctx.categories_by_kind["expense"].values()) <= vctx.category_ids
        assert set(ctx.account_names.values()) <= vctx.account_ids
        assert str(uuid.uuid4()) not in vctx.category_ids


# ── 审批四值 ───────────────────────────────────────────────────────────────


class TestApprovalFourValues:
    async def test_allowed_once_executes_exactly_once(self):
        journal: list[str] = []
        approver = FakeApprover(ApprovalDecision.ALLOWED_ONCE, journal=journal)
        executor = FakeExecutor(journal=journal)
        provider = ScriptedProvider(_multi_step_write(
            _call("transaction_add", _ADD_OK, id="w1"),
        ))
        result = await _loop(provider, approver=approver, executor=executor).run(
            "先看看账户，再记一笔午饭 12 块",
        )

        assert journal == ["ask", "execute"], "先问后执行，中间不留空档"
        assert len(executor.calls) == 1
        assert executor.calls[0]["confirmation_token"].startswith("allowed-once:")
        assert len(result.executions) == 1
        assert result.proposals == (), "执行掉的提案不再交回调用方要确认"
        assert _payload_for(result.messages, "w1")["status"] == "executed"

    async def test_rejected_never_executes_and_tells_the_model_why(self):
        approver = FakeApprover(ApprovalDecision.REJECTED)
        executor = FakeExecutor()
        provider = ScriptedProvider(_multi_step_write(
            _call("transaction_add", _ADD_OK, id="w1"),
        ))
        result = await _loop(provider, approver=approver, executor=executor).run(
            "先看看账户，再记一笔午饭 12 块",
        )

        assert executor.calls == []
        assert result.executions == ()
        assert result.proposals == (), "被拒的提案不挂起（用户已经表过态）"
        assert _payload_for(result.messages, "w1")["code"] == ERR_APPROVAL_REJECTED
        assert result.stop_reason is not StopReason.CANCELLED, "拒绝不等于中止整轮"

    async def test_cancelled_stops_the_turn(self):
        approver = FakeApprover(ApprovalDecision.CANCELLED)
        executor = FakeExecutor()
        provider = ScriptedProvider(_multi_step_write(
            _call("transaction_add", _ADD_OK, id="w1"),
        ))
        result = await _loop(provider, approver=approver, executor=executor).run(
            "先看看账户，再记一笔午饭 12 块",
        )

        assert executor.calls == []
        assert result.stop_reason is StopReason.CANCELLED
        assert len(provider.calls) == 2, "中止后不再发起模型请求（含收尾请求）"
        paired = _tool_messages(result.messages)
        assert [m["tool_call_id"] for m in paired] == ["r1", "w1"], "批次仍配对完整"
        assert _payload(paired[1])["code"] == ERR_APPROVAL_CANCELLED

    async def test_unavailable_is_treated_as_rejection(self):
        """拿不到授权（超时 / 离线 / 界面不可用）→ 视为拒绝，一次都不执行。"""
        approver = FakeApprover(ApprovalDecision.UNAVAILABLE)
        executor = FakeExecutor()
        provider = ScriptedProvider(_multi_step_write(
            _call("transaction_add", _ADD_OK, id="w1"),
        ))
        result = await _loop(provider, approver=approver, executor=executor).run(
            "先看看账户，再记一笔午饭 12 块",
        )

        assert executor.calls == []
        assert result.executions == ()
        code = _payload_for(result.messages, "w1")["code"]
        assert code == ERR_APPROVAL_UNAVAILABLE

    async def test_garbage_decision_is_unavailable(self):
        """通道给出的任何无法识别的值（含 ``None``）一律按 unavailable 处理。"""
        for raw in (None, "", "ALLOWED-ONCE", "yes", 1, {"decision": "allowed-once"}):
            approver = FakeApprover(raw)
            executor = FakeExecutor()
            provider = ScriptedProvider(_multi_step_write(
                _call("transaction_add", _ADD_OK, id="w1"),
            ))
            result = await _loop(provider, approver=approver, executor=executor).run(
                "先看看账户，再记一笔午饭 12 块",
            )
            assert executor.calls == [], f"{raw!r} 不该放行"
            assert result.executions == ()

    async def test_approval_channel_failure_is_unavailable(self):
        class _Broken:
            async def __call__(self, request: ApprovalRequest) -> Any:
                raise RuntimeError("channel down")

        executor = FakeExecutor()
        provider = ScriptedProvider(_multi_step_write(
            _call("transaction_add", _ADD_OK, id="w1"),
        ))
        result = await _loop(provider, approver=_Broken(), executor=executor).run(
            "先看看账户，再记一笔午饭 12 块",
        )

        assert executor.calls == []
        assert _payload_for(result.messages, "w1")["code"] == ERR_APPROVAL_UNAVAILABLE

    async def test_cancelled_batch_does_not_execute_the_rest(self):
        """一次中止之后，同一步里剩下的写调用不再征询、更不执行。"""
        approver = FakeApprover(ApprovalDecision.CANCELLED, ApprovalDecision.ALLOWED_ONCE)
        executor = FakeExecutor()
        provider = ScriptedProvider(_multi_step_write(
            _call("transaction_add", _ADD_OK, id="w1"),
            _call("transaction_add", {**_ADD_OK, "amountCents": 800}, id="w2"),
        ))
        result = await _loop(provider, approver=approver, executor=executor).run(
            "先看看账户，再记两笔",
        )

        assert len(approver.requests) == 1, "中止后不再打扰用户"
        assert executor.calls == []
        paired = _tool_messages(result.messages)
        assert [m["tool_call_id"] for m in paired] == ["r1", "w1", "w2"]
        assert _payload(paired[2])["code"] == ERR_APPROVAL_CANCELLED


# ── allowed-once 的一次性 ──────────────────────────────────────────────────


class TestOneShotGrant:
    async def test_the_same_proposal_is_never_approved_twice(self):
        """同一个提案号重复求批准 → 第二次直接 unavailable，且不再打扰用户。"""
        gate = ApprovalGate(FakeApprover(ApprovalDecision.ALLOWED_ONCE))
        request = ApprovalRequest(
            proposal={"proposalId": "agent-abc"}, step=2, reason="multi_step",
        )

        first = await gate.ask(request)
        second = await gate.ask(request)

        assert first is ApprovalDecision.ALLOWED_ONCE
        assert second is ApprovalDecision.UNAVAILABLE
        assert len(gate._approver.requests) == 1

    async def test_a_grant_does_not_authorize_other_proposals(self):
        """授权不落到任何长期位置：换一份提案就得重新问一次。"""
        approver = FakeApprover(ApprovalDecision.ALLOWED_ONCE)
        gate = ApprovalGate(approver)

        first = await gate.ask(ApprovalRequest(
            proposal={"proposalId": "agent-a"}, step=2, reason="multi_step",
        ))
        second = await gate.ask(ApprovalRequest(
            proposal={"proposalId": "agent-b"}, step=2, reason="multi_step",
        ))

        assert first is ApprovalDecision.ALLOWED_ONCE
        assert second is ApprovalDecision.UNAVAILABLE, (
            "第一份的批准不能顺延给第二份——没有「记住选择」这类隐式授权"
        )
        assert len(approver.requests) == 2

    async def test_an_unnumbered_proposal_is_never_approved(self):
        """没有提案号（未经组装）的提案一律不放行——门只认组装器发过号的提案。"""
        approver = FakeApprover(ApprovalDecision.ALLOWED_ONCE)
        gate = ApprovalGate(approver)

        decision = await gate.ask(ApprovalRequest(
            proposal={"action": "add_tx"}, step=2, reason="multi_step",
        ))

        assert decision is ApprovalDecision.UNAVAILABLE
        assert approver.requests == []

    def test_only_allowed_once_grants(self):
        assert ApprovalDecision.ALLOWED_ONCE.grants is True
        for value in (ApprovalDecision.REJECTED, ApprovalDecision.CANCELLED,
                      ApprovalDecision.UNAVAILABLE):
            assert value.grants is False, f"{value} 不是放行值"


# ── 审批策略 ───────────────────────────────────────────────────────────────


class TestApprovalPolicy:
    async def test_single_step_high_confidence_executes_without_asking(self):
        """单 Step + 高置信：沿用快路径策略——自动执行 + 撤销窗，不打扰用户。"""
        approver = FakeApprover(ApprovalDecision.REJECTED)
        executor = FakeExecutor()
        provider = ScriptedProvider([
            _tool_turn(_call("transaction_add", _ADD_OK, id="w1")),
            _text("记好了。"),
        ])
        result = await _loop(provider, approver=approver, executor=executor).run(
            "记一笔午饭 12 块",
        )

        assert approver.requests == [], "高置信单发不出确认卡"
        assert len(executor.calls) == 1
        assert executor.calls[0]["confirmation_token"] == "", "自动执行不带确认口令"
        assert result.executions[0]["undo_window_seconds"] == 5

    async def test_multi_step_write_always_asks_for_confirmation(self):
        """多步 loop（≥2 Step）里的写操作一律强制确认——模型先查后写。"""
        approver = FakeApprover(ApprovalDecision.ALLOWED_ONCE)
        executor = FakeExecutor()
        provider = ScriptedProvider(_multi_step_write(
            _call("transaction_add", _ADD_OK, id="w1"),
        ))
        result = await _loop(provider, approver=approver, executor=executor).run(
            "先看看账户，再记一笔午饭 12 块",
        )

        assert len(approver.requests) == 1
        assert approver.requests[0].step == 2
        assert approver.requests[0].reason == "multi_step"
        assert approver.requests[0].proposal["requiresConfirmation"] is True
        assert result.proposals == () and len(result.executions) == 1

    def test_policy_reasons(self):
        assert confirm_reason(step=1, confidence=1.0) is None
        assert confirm_reason(step=2, confidence=1.0) == "multi_step"
        assert confirm_reason(step=3, confidence=1.0) == "multi_step"
        assert confirm_reason(step=1, confidence=0.8) == "low_confidence"

    async def test_low_confidence_forces_confirmation_even_in_one_step(self):
        """低置信草案（工具带了 ``warnings``）→ 单 Step 也强制确认，不自动执行。

        工具层现在只在 ``warnings`` 非空时降置信度，而 P1 的工具还没有生产者
        （归 02c/P4）；本用例直接驱动写路径分支，把策略这一条钉住。
        """
        approver = FakeApprover(ApprovalDecision.REJECTED)
        executor = FakeExecutor()
        ctx = _ctx()
        loop = _loop(ScriptedProvider([]), ctx=ctx, approver=approver, executor=executor)
        loop._steps = 1

        outcome = await loop._handle_proposal({
            "action": "add_tx",
            "transactions": [{
                "type": "expense",
                "amountCents": 1250,
                "categoryId": ctx.categories_by_kind["expense"]["餐饮"],
                "bizDate": "2026-09-18",
                "note": "",
            }],
            "message": "记一笔",
            "warnings": ["金额和上次差很多"],
        })

        assert executor.calls == [], "低置信不走自动执行"
        assert len(approver.requests) == 1
        assert approver.requests[0].reason == "low_confidence"
        assert outcome.kind == "error"
        assert outcome.data["code"] == ERR_APPROVAL_REJECTED


# ── 执行通道接线 ───────────────────────────────────────────────────────────


class TestServiceProposalExecutor:
    async def test_it_passes_through_to_aiservice_execute_proposal(self):
        """只做透传：提案、账本、请求号、确认口令原样交给 ``execute_proposal``。"""
        captured: dict[str, Any] = {}

        class _StubService:
            async def execute_proposal(self, user, request_id, proposal, book_id,
                                       confirmation_token):
                captured.update({
                    "user": user,
                    "request_id": request_id,
                    "proposal": proposal,
                    "book_id": book_id,
                    "confirmation_token": confirmation_token,
                })
                return {"transaction_ids": ["t1"], "proposal_id": "p", "undo_window_seconds": 5}

        book_id = uuid.uuid4()
        user = object()
        executor = ServiceProposalExecutor(
            _StubService(), user=user, request_id="req-1", book_id=book_id,
        )
        proposal = {"proposalId": "p", "action": "add_tx", "transactions": []}

        receipt = await executor(proposal, confirmation_token="allowed-once:p")

        assert captured["user"] is user
        assert captured["request_id"] == "req-1"
        assert captured["book_id"] == book_id
        assert captured["confirmation_token"] == "allowed-once:p"
        assert captured["proposal"] == proposal
        assert receipt["undo_window_seconds"] == 5

    def test_positional_order_matches_the_real_executor(self):
        """钉住真实签名：适配器是**按位置**调用的，stub 用同名形参接不住错位。

        执行器的签名一旦变动（或顺序被调换），位置调用会把 ``book_id`` 当
        ``request_id`` 送进去——stub 全绿，生产上写进错的账本。这条断言让签名
        漂移在 CI 里就红，而不是等到真机联调。
        """
        params = list(inspect.signature(AIService.execute_proposal).parameters)
        assert params[1:6] == [
            "user", "request_id", "proposal", "book_id", "confirmation_token",
        ]

    async def test_execution_failure_degrades_instead_of_raising(self):
        """执行失败的两种来源（TOCTOU 否决 / DB 故障）都降级成配对的错误结果。"""
        executor = FakeExecutor(error=RuntimeError("db gone"))
        provider = ScriptedProvider([
            _tool_turn(_call("transaction_add", _ADD_OK, id="w1")),
            _text("没记上。"),
        ])
        result = await _loop(provider, executor=executor).run("记一笔午饭 12 块")

        assert result.executions == ()
        payload = _payload_for(result.messages, "w1")
        assert payload["code"] == ERR_EXECUTION_FAILED
        assert result.stop_reason is StopReason.COMPLETED, "单次执行失败不掀掉整轮"
