"""T-M7-04c: 摘要压缩与预算（``replace``）测试。

验收映射（docs/milestones/production-execution-plan.md T-M7-04c）：
- 压缩触发条件（窗口溢出，不是 token / 时间） → TestCompressionTrigger
- **压缩后原文仍可查**（审计）                  → TestReplaceEvent
- 摘要**不含 ID**、≤800 字符                    → TestSummaryValidation
- 摘要失败**回落上一版且不阻塞本轮**            → TestSummaryValidation / TestCompressionFailure
- 摘要**注入**下一轮上下文                      → TestSummaryInContext
- 压缩前后「结构性事件不占 token」结论一致      → TestSummaryInContext
- 多轮澄清在压缩后仍正确（信息确实被带下去）    → TestSummaryInContext
- 摘要与分叉 / 删除 / 保留期的联动              → TestSummaryLifecycle

全部用例走真实 DB（内存 SQLite）+ 真实 ``ConversationService``；摘要侧用脚本化
provider，无网络。窗口、字符上限与摘要上限一律经 ``Settings(...)`` 注入，不吃
生产默认值。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update

from src.common.config import Settings
from src.models.ai import AIConversation, AIConversationEvent
from src.modules.ai.agent import AgentLoop
from src.modules.ai.conversation import (
    EVENT_REPLACE,
    EVENT_USAGE,
    EVENT_USER,
    ConversationService,
)
from src.modules.ai.provider import ProviderError, ProviderResult, StreamChunk
from tests.fixtures_db import db_session, sample_data  # noqa: F401

TODAY = datetime(2026, 9, 19, tzinfo=UTC).date()
MODEL = "deepseek-flash"
NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def _cfg(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": "sqlite+aiosqlite:///:memory:",
        "secret_key": "test-secret",
        "env": "test",
        "api_prefix": "",
        "ai_agent_provider_model": MODEL,
    }
    base.update(overrides)
    return Settings(**base)


class _SummaryProvider:
    """只实现 ``complete`` 的摘要 provider——压缩通路用到的那一条接口。

    ``reply`` 是固定的答复文本（异常实例即「provider 报错」，``None`` = 返回空串）。
    它**刻意不模拟真实模型**：这里要钉的是调用方对答复的处置（校验、降级、注入），
    不是模型的措辞。
    """

    name = "scripted"

    def __init__(self, reply: Any = "用户此前问过几笔账，已经记好了。") -> None:
        self.reply = reply
        self.calls: list[list[dict[str, str]]] = []

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        max_tokens: int,
        timeout: float,
    ) -> ProviderResult:
        self.calls.append([dict(m) for m in messages])
        if isinstance(self.reply, Exception):
            raise self.reply
        return ProviderResult(
            content="" if self.reply is None else str(self.reply),
            prompt_tokens=3,
            completion_tokens=5,
        )

    @property
    def prompt(self) -> str:
        """最后一次摘要请求的 user 段（提示里喂进去了什么）。"""
        return self.calls[-1][1]["content"]


class _LoopProvider:
    """跑一轮 loop 用的最小 provider：一次请求、一段回答、不做工具调用。"""

    name = "scripted"

    def __init__(self, content: str = "好。") -> None:
        self.content = content
        self.calls: list[list[dict[str, Any]]] = []

    async def complete_with_tools(
        self, messages, *, model, max_tokens, timeout, tools, reasoning,
    ) -> ProviderResult:
        self.calls.append([dict(m) for m in messages])
        return ProviderResult(content=self.content, prompt_tokens=11, completion_tokens=13)

    async def complete_with_tools_stream(
        self, messages, *, model, max_tokens, timeout, tools, reasoning,
    ):
        result = await self.complete_with_tools(
            messages, model=model, max_tokens=max_tokens, timeout=timeout,
            tools=tools, reasoning=reasoning,
        )
        yield StreamChunk(delta=result.content)
        yield StreamChunk(result=result)


def _assert_pairs(messages: list[dict[str, Any]]) -> None:
    """配对组完整性：每个 ``tool_calls`` 的 id 都有配对的 ``tool`` 结果。

    与 ``test_ai_conversation.py`` 的同名断言同一口径（不变式 1）——压缩不该让
    窗口里的配对关系变样。
    """
    pending: set[str] = set()
    for message in messages:
        role = message.get("role")
        if role == "assistant":
            calls = message.get("tool_calls") or []
            pending = {str(c["id"]) for c in calls} if calls else set()
        elif role == "tool":
            assert message.get("tool_call_id") in pending
            pending.discard(str(message.get("tool_call_id")))
    assert not pending


def _msg(role: str, content: str, **extra: Any) -> dict[str, Any]:
    return {"role": role, "content": content, **extra}


def _simple_turn(index: int) -> list[dict[str, Any]]:
    """不含工具调用的两句话一轮——多数用例用它（短、好断言）。"""
    return [
        _msg("user", f"第{index}轮问题"),
        _msg("assistant", f"第{index}轮回答", reasoning_content=""),
    ]


def _tool_turn(index: int) -> list[dict[str, Any]]:
    """一轮「问 → 调工具 → 工具结果 → 回答」——配对组用例用它。"""
    call_id = f"call_{index}"
    return [
        _msg("user", f"第{index}轮问题"),
        {
            "role": "assistant", "content": "", "reasoning_content": "",
            "tool_calls": [{
                "id": call_id, "type": "function",
                "function": {"name": "transaction_list", "arguments": "{}"},
            }],
        },
        _msg("tool", json.dumps({"items": []}), tool_call_id=call_id),
        _msg("assistant", f"第{index}轮回答", reasoning_content=""),
    ]


async def _seed(
    service: ConversationService,
    user: Any,
    book: Any,
    turns: int,
    *,
    factory: Any = _simple_turn,
) -> AIConversation:
    conversation = await service.resolve(user, book.id)
    for index in range(1, turns + 1):
        await service.append_turn(conversation, factory(index))
    return conversation


async def _events(db: Any, conversation: AIConversation) -> list[AIConversationEvent]:
    return list(await db.scalars(
        select(AIConversationEvent)
        .where(AIConversationEvent.conversation_id == conversation.id)
        .order_by(AIConversationEvent.seq)
    ))


async def _replaces(db: Any, conversation: AIConversation) -> list[AIConversationEvent]:
    return [e for e in await _events(db, conversation) if e.type == EVENT_REPLACE]


async def _age_turn(
    db: Any, conversation: AIConversation, turn: int, when: datetime,
) -> None:
    """把某一轮事件的时间拨到过去——保留期用例造「到期」用。"""
    await db.execute(
        update(AIConversationEvent)
        .where(
            AIConversationEvent.conversation_id == conversation.id,
            AIConversationEvent.turn == turn,
        )
        .values(time=when)
    )
    await db.flush()


# ---------------------------------------------------------------------------
# 触发条件：窗口溢出（不是 token、不是时间）
# ---------------------------------------------------------------------------

class TestCompressionTrigger:
    async def test_window_sized_conversation_is_not_compressed(
        self, db_session, sample_data,
    ):
        """轮数正好装满窗口 → 没有任何轮次落在窗口外 → 不产生摘要。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=2))
        conversation = await _seed(service, user, book, 2)
        provider = _SummaryProvider()

        assert await service.summarize_overflow(conversation, provider) is False
        assert provider.calls == []
        assert conversation.summary == ""
        assert await _replaces(db, conversation) == []

    async def test_overflowing_turn_is_compressed(self, db_session, sample_data):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=2))
        conversation = await _seed(service, user, book, 3)
        provider = _SummaryProvider("用户在问记账，助手已经答复。")

        assert await service.summarize_overflow(conversation, provider) is True

        replace = (await _replaces(db, conversation))[0]
        assert replace.data["upto_turn"] == 1
        assert replace.data["turns"] == 1
        assert replace.data["summary"] == "用户在问记账，助手已经答复。"
        assert conversation.summary == "用户在问记账，助手已经答复。"

    async def test_summary_covers_every_turn_outside_the_window(
        self, db_session, sample_data,
    ):
        """覆盖范围由窗口算出来：5 轮 / 窗口 2 → 摘要覆盖第 1~3 轮，一次做完。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=2))
        conversation = await _seed(service, user, book, 5)

        assert await service.summarize_overflow(conversation, _SummaryProvider()) is True

        window_start = (await service.history(conversation))[0]
        assert window_start["content"] == "第4轮问题"
        assert (await _replaces(db, conversation))[0].data["upto_turn"] == 3

    async def test_char_budget_overflow_is_compressed_too(self, db_session, sample_data):
        """字符双限挤出去的轮次同样进摘要——**与历史窗口同一把尺子**。

        轮数上限放着不管（8），只靠字符预算把窗口压到最近一轮：窗口外的两轮因此
        该被摘要。若压缩只看轮数，这两轮就永远进不了摘要、也永远回不到窗口。
        """
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(
            db, _cfg(ai_agent_history_turns=8, ai_agent_history_chars=120),
        )
        conversation = await service.resolve(user, book.id)
        for index in range(1, 4):
            await service.append_turn(conversation, [_msg("user", "字" * 100)])

        assert await service.summarize_overflow(conversation, _SummaryProvider()) is True

        assert (await _replaces(db, conversation))[0].data["upto_turn"] == 2

    async def test_compression_is_incremental_and_merges_the_previous_summary(
        self, db_session, sample_data,
    ):
        """第二次压缩只覆盖新增的窗口外轮次，并把上一版摘要一并交给模型合并。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=2))
        conversation = await _seed(service, user, book, 3)

        first = _SummaryProvider("第一版摘要")
        assert await service.summarize_overflow(conversation, first) is True

        await service.append_turn(conversation, _simple_turn(4))
        second = _SummaryProvider("第二版摘要")
        assert await service.summarize_overflow(conversation, second) is True

        # 只覆盖第 2 轮（第 1 轮上一版已经覆盖过），不是重做 1~2。
        assert (await _replaces(db, conversation))[-1].data["upto_turn"] == 2
        assert "【已有摘要】" in second.prompt
        assert "第一版摘要" in second.prompt
        assert "第2轮问题" in second.prompt
        # **第 1 轮的原文不能再进提示**：它已经在「已有摘要」里了，重摘一遍
        # 既浪费 token 又会让摘要越长越糊。边界写成 ``covered <= turn`` 时本行变红
        # ——只断言 ``upto_turn`` 分不出来（两种情况都是 2）。
        assert "第1轮问题" not in second.prompt

    async def test_empty_conversation_is_a_no_op(self, db_session, sample_data):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg())
        conversation = await service.resolve(user, book.id)

        assert await service.summarize_overflow(conversation, _SummaryProvider()) is False


# ---------------------------------------------------------------------------
# M8-12：压缩调用的分步用量（usage 事件，与 replace 同轮）
# ---------------------------------------------------------------------------


class TestSummaryUsage:
    async def test_compression_call_writes_a_usage_event(
        self, db_session, sample_data,
    ):
        """压缩也是一次模型调用——用量必须留痕，且与 ``replace`` 同轮。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=2))
        conversation = await _seed(service, user, book, 3)

        provider = _SummaryProvider()
        assert await service.summarize_overflow(
            conversation, provider, request_id="req-summary-1",
        ) is True

        usage = [e for e in await _events(db, conversation) if e.type == EVENT_USAGE]
        assert len(usage) == 1
        data = usage[0].data
        assert data["kind"] == "summary"
        assert data["step"] is None
        assert data["call"] is None
        assert data["level"] is None
        assert data["provider"] == "scripted"
        assert data["model"] == MODEL
        assert data["prompt_tokens"] == 3
        assert data["completion_tokens"] == 5
        assert data["tokens"] == 8
        assert data["tool_calls"] == []
        # M8-13：压缩是独立补全——输入全量记录，不参与 loop 的链式重建。
        request = data["request"]
        assert request["base_call"] is None
        assert request["system"] == provider.calls[-1][0]["content"]
        assert [m["role"] for m in request["append"]] == ["user"]
        assert request["tools"] == [] and request["tools_chars"] == 0
        assert usage[0].turn == (await _replaces(db, conversation))[0].turn
        assert usage[0].request_id == "req-summary-1"

    async def test_rejected_summary_still_records_the_call(
        self, db_session, sample_data,
    ):
        """摘要被拒 ≠ 没花钱：``usage`` 照写，``replace`` 不写（沿用上一版）。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=2))
        conversation = await _seed(service, user, book, 3)

        provider = _SummaryProvider(f"用户提到了 {uuid.uuid4()}，已经记好了。")
        assert await service.summarize_overflow(conversation, provider) is False

        usage = [e for e in await _events(db, conversation) if e.type == EVENT_USAGE]
        assert len(usage) == 1
        assert usage[0].data["kind"] == "summary"
        assert usage[0].data["tokens"] == 8
        assert await _replaces(db, conversation) == []
        assert conversation.summary == ""


# ---------------------------------------------------------------------------
# replace 事件：原文一行不删（架构 §6.2「压缩无损」）
# ---------------------------------------------------------------------------

class TestReplaceEvent:
    async def test_original_turns_survive_in_the_log(self, db_session, sample_data):
        """压缩后原文仍可查——这是 P3 出口标准里的审计项。

        ``events()``（投影 B / C 的读面）拿到的应该是**压缩前的全部事件 + 一条
        replace**。把压缩实现成「覆写」或「删原文」，本用例立即变红。
        """
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=2))
        conversation = await _seed(service, user, book, 3)
        before = [(e.type, e.data.get("content")) for e in await _events(db, conversation)]

        await service.summarize_overflow(conversation, _SummaryProvider())

        after = [(e.type, e.data.get("content")) for e in await _events(db, conversation)]
        assert after[:len(before)] == before
        # M8-12：压缩那次模型调用的 ``usage`` 与 ``replace`` 同轮追加在后。
        assert after[len(before):] == [(EVENT_USAGE, None), (EVENT_REPLACE, None)]
        assert [e for e in await _events(db, conversation) if e.type == EVENT_USER]

    async def test_compressed_turns_are_still_readable_by_seq(self, db_session, sample_data):
        """按 ``seq`` 增量读也要读得到被压缩的那一轮（客户端续读路径）。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=2))
        conversation = await _seed(service, user, book, 3)
        await service.summarize_overflow(conversation, _SummaryProvider())

        events = await service.events(conversation, after_seq=0)

        assert any(
            e.type == EVENT_USER and e.data.get("content") == "第1轮问题"
            for e in events
        )

    async def test_replace_event_is_structural(self, db_session, sample_data):
        """replace 是结构性事件——``ignorable`` 由 Surface 常量派生（04a 的口径）。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=2))
        conversation = await _seed(service, user, book, 3)

        await service.summarize_overflow(conversation, _SummaryProvider())

        assert (await _replaces(db, conversation))[0].ignorable is True

    async def test_replace_event_does_not_enter_the_window(self, db_session, sample_data):
        """压缩**不改变**模型看到的那段窗口——replace 连 ``history()`` 都进不去。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=2))
        conversation = await _seed(service, user, book, 3)
        before = await service.history(conversation)

        await service.summarize_overflow(conversation, _SummaryProvider())

        assert await service.history(conversation) == before


# ---------------------------------------------------------------------------
# 摘要的可用性校验与降级（架构 §6.4「超限 → 使用上一版摘要」）
# ---------------------------------------------------------------------------

class TestSummaryValidation:
    async def _compressed_once(self, db, user, book, service, summary: str):
        """先成功压缩一次，建立「上一版摘要」，返回 (会话, provider, 上一版)。"""
        conversation = await _seed(service, user, book, 3)
        await service.summarize_overflow(conversation, _SummaryProvider(summary))
        return conversation

    async def test_summary_over_the_limit_is_rejected(self, db_session, sample_data):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(
            db, _cfg(ai_agent_history_turns=2, ai_agent_summary_chars=20),
        )
        conversation = await _seed(service, user, book, 3)

        assert await service.summarize_overflow(
            conversation, _SummaryProvider("字" * 21),
        ) is False

        assert conversation.summary == ""
        assert await _replaces(db, conversation) == []

    async def test_summary_at_the_limit_is_accepted(self, db_session, sample_data):
        """边界是「不超过」——正好 20 字符的摘要通过（防 off-by-one）。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(
            db, _cfg(ai_agent_history_turns=2, ai_agent_summary_chars=20),
        )
        conversation = await _seed(service, user, book, 3)

        assert await service.summarize_overflow(
            conversation, _SummaryProvider("字" * 20),
        ) is True

        assert conversation.summary == "字" * 20

    async def test_over_limit_keeps_the_previous_summary(self, db_session, sample_data):
        """降级形态就是**什么都不写**：上一版摘要原样留着，本轮照常结束。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(
            db, _cfg(ai_agent_history_turns=2, ai_agent_summary_chars=20),
        )
        conversation = await self._compressed_once(db, user, book, service, "第一版摘要")
        await service.append_turn(conversation, _simple_turn(4))

        assert await service.summarize_overflow(
            conversation, _SummaryProvider("字" * 21),
        ) is False

        assert conversation.summary == "第一版摘要"
        assert len(await _replaces(db, conversation)) == 1

    async def test_summary_containing_an_internal_id_is_rejected(
        self, db_session, sample_data,
    ):
        """架构 §6.3：摘要**不得出现 UUID / 内部 ID**。输出侧另验一道。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=2))
        conversation = await _seed(service, user, book, 3)

        leaked = f"用户提到了分类 {uuid.uuid4()}，已经记好了。"
        assert await service.summarize_overflow(
            conversation, _SummaryProvider(leaked),
        ) is False

        assert conversation.summary == ""
        assert await _replaces(db, conversation) == []

    async def test_blank_summary_is_rejected(self, db_session, sample_data):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=2))
        conversation = await _seed(service, user, book, 3)

        assert await service.summarize_overflow(
            conversation, _SummaryProvider("  \n\t "),
        ) is False

    async def test_summary_is_folded_into_a_single_line(self, db_session, sample_data):
        """模型爱分段——存下来之前先压成单行（它要进系统提示里的一段）。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=2))
        conversation = await _seed(service, user, book, 3)

        await service.summarize_overflow(
            conversation, _SummaryProvider("用户问了记账。\n\n助手已经答复。"),
        )

        assert conversation.summary == "用户问了记账。 助手已经答复。"


class TestCompressionFailure:
    async def test_provider_error_is_swallowed_and_nothing_is_written(
        self, db_session, sample_data,
    ):
        """摘要失败**不抛**——调用方（轮末）据此判定「本轮照常结束」。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=2))
        conversation = await _seed(service, user, book, 3)

        provider = _SummaryProvider(ProviderError(status=500, retryable=True, reason="x"))

        assert await service.summarize_overflow(conversation, provider) is False
        assert conversation.summary == ""
        assert await _replaces(db, conversation) == []

    async def test_failure_after_a_successful_one_keeps_the_old_summary(
        self, db_session, sample_data,
    ):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=2))
        conversation = await _seed(service, user, book, 3)
        await service.summarize_overflow(conversation, _SummaryProvider("第一版摘要"))
        await service.append_turn(conversation, _simple_turn(4))

        provider = _SummaryProvider(ProviderError(status=503, retryable=True, reason="y"))
        assert await service.summarize_overflow(conversation, provider) is False

        assert conversation.summary == "第一版摘要"
        assert len(await _replaces(db, conversation)) == 1

    async def test_retry_after_a_failure_still_works(self, db_session, sample_data):
        """失败**不消耗**那批轮次：下一轮再压时它们还在待摘要的范围里。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=2))
        conversation = await _seed(service, user, book, 3)
        await service.summarize_overflow(
            conversation, _SummaryProvider(ProviderError(status=500, retryable=False, reason="z")),
        )

        retry = _SummaryProvider("重试成功")
        assert await service.summarize_overflow(conversation, retry) is True

        assert "第1轮问题" in retry.prompt
        assert conversation.summary == "重试成功"


# ---------------------------------------------------------------------------
# 摘要怎么进上下文：系统提示的一段（不是 messages 里的一条）
# ---------------------------------------------------------------------------

async def _run_loop(provider: _LoopProvider, history: list[dict[str, Any]], *, summary: str = ""):
    loop = AgentLoop(provider, _ctx(), model=MODEL, timeout=10.0, today=TODAY)  # type: ignore[arg-type]
    return await loop.run("继续", history=history, summary=summary)


def _ctx() -> Any:
    from src.modules.ai import tools as T

    return T.ToolContext(
        db=None,  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        book_id=uuid.uuid4(),
        categories_by_kind={},
        account_names={},
        book_names={},
    )


class TestSummaryInContext:
    async def test_summary_lands_in_the_system_prompt(self, db_session, sample_data):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=2))
        conversation = await _seed(service, user, book, 3)
        await service.summarize_overflow(conversation, _SummaryProvider("用户想攒钱买相机。"))

        provider = _LoopProvider()
        await _run_loop(provider, await service.history(conversation), summary=conversation.summary)

        system = provider.calls[0][0]
        assert system["role"] == "system"
        assert "用户想攒钱买相机。" in system["content"]

    async def test_summary_is_not_in_the_messages_array(self, db_session, sample_data):
        """摘要**只在 system 里**——它不对应任何一条 Surface 事件。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=2))
        conversation = await _seed(service, user, book, 3)
        await service.summarize_overflow(conversation, _SummaryProvider("用户想攒钱买相机。"))

        provider = _LoopProvider()
        await _run_loop(provider, await service.history(conversation), summary=conversation.summary)

        rest = provider.calls[0][1:]
        assert rest and all("用户想攒钱买相机。" not in json.dumps(m) for m in rest)

    async def test_no_summary_no_section(self):
        """没有摘要时系统提示里不该出现摘要抬头——它不是一段固定文案。"""
        provider = _LoopProvider()

        await _run_loop(provider, [], summary="")

        assert "摘要" not in provider.calls[0][0]["content"]

    async def test_structural_events_still_cost_no_tokens_after_compression(
        self, db_session, sample_data,
    ):
        """压缩前后「结构性事件不占 token」的结论一致（P3 出口标准）。

        04c 新增的 ``replace`` 也是结构性事件——它连 ``history()`` 的返回值都进不去。
        这里量的是**请求载荷**：压缩后跑一轮，载荷与压缩前逐字节相同（摘要另行经
        ``summary=`` 注入，在这条用例里传空，量的是窗口本身）。
        """
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=2))
        conversation = await _seed(service, user, book, 3)
        before_provider = _LoopProvider()
        await _run_loop(before_provider, await service.history(conversation))

        await service.summarize_overflow(conversation, _SummaryProvider())
        after_provider = _LoopProvider()
        await _run_loop(after_provider, await service.history(conversation))

        assert json.dumps(before_provider.calls[0], ensure_ascii=False, sort_keys=True) == (
            json.dumps(after_provider.calls[0], ensure_ascii=False, sort_keys=True)
        )

    async def test_clarification_context_survives_compression(
        self, db_session, sample_data,
    ):
        """多轮澄清在压缩后仍成立：**窗口（最近几轮原文）+ 摘要（更早的）**同时在。

        P3 验收列的「多轮澄清用例在压缩后仍正确」在服务端侧的落点就是这一条：
        压缩不夺走任何一侧——窗口照旧（含完整的工具配对组），更早的轮次进摘要提示。
        """
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=2))
        conversation = await _seed(service, user, book, 4, factory=_tool_turn)
        provider = _SummaryProvider("用户在问预算，助手已经答复过两次。")

        await service.summarize_overflow(conversation, provider)

        history = await service.history(conversation)
        _assert_pairs(history)
        assert history[0]["content"] == "第3轮问题"     # 窗口起点没有被压缩挪动
        assert "第1轮问题" in provider.prompt           # 更早的轮次确实被带下去了
        assert "第2轮问题" in provider.prompt
        assert conversation.summary == "用户在问预算，助手已经答复过两次。"

    async def test_overflowing_turns_are_fed_into_the_summary_prompt(
        self, db_session, sample_data,
    ):
        """多轮澄清为什么在压缩后仍然成立：信息是**喂进了摘要**才被带下去的。

        窗口外的轮次（第 1 轮）进提示，窗口内的（第 3 轮）不进——后者还在原文里。
        """
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=2))
        conversation = await _seed(service, user, book, 3)
        provider = _SummaryProvider()

        await service.summarize_overflow(conversation, provider)

        assert "第1轮问题" in provider.prompt
        assert "第1轮回答" in provider.prompt
        assert "第3轮问题" not in provider.prompt

    async def test_tool_messages_stay_out_of_the_summary_prompt(
        self, db_session, sample_data,
    ):
        """工具消息整条不进摘要提示——「摘要不含内部 ID」的输入侧落点。

        工具结果是日志里唯一可能带服务端解析出的 id 的地方（写工具的提案草案），
        不让它进提示，模型就没有可复述的来源。
        """
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=1))
        conversation = await _seed(service, user, book, 3, factory=_tool_turn)
        provider = _SummaryProvider()

        await service.summarize_overflow(conversation, provider)

        assert "tool_calls" not in provider.prompt
        assert "call_1" not in provider.prompt
        assert '"items"' not in provider.prompt
        # 结论仍然在：两侧的自然语言都在提示里。
        assert "第1轮问题" in provider.prompt
        assert "第1轮回答" in provider.prompt


# ---------------------------------------------------------------------------
# 摘要的生命周期：分叉 / 删除 / 保留期
# ---------------------------------------------------------------------------

class TestSummaryLifecycle:
    async def test_fork_derives_the_summary_from_its_own_prefix(
        self, db_session, sample_data,
    ):
        """分叉到**压缩之后**：新会话带着前缀里那条 replace 的摘要。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=2))
        conversation = await _seed(service, user, book, 3)
        await service.summarize_overflow(conversation, _SummaryProvider("前缀摘要"))

        forked, _ = await service.fork(conversation, await service.max_seq(conversation))

        assert forked.summary == "前缀摘要"

    async def test_fork_before_the_replace_gets_no_summary(self, db_session, sample_data):
        """分叉到**压缩之前**：新会话不能带着一段「描述它没有的历史」的摘要。

        这是「摘要从新会话自己的前缀派生，而不是复制父会话的列」的那条判定。
        复制列的话，本用例会红。
        """
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=2))
        conversation = await _seed(service, user, book, 3)
        cut = await service.max_seq(conversation)
        await service.summarize_overflow(conversation, _SummaryProvider("前缀摘要"))

        forked, actual = await service.fork(conversation, cut)

        assert actual == cut
        assert forked.summary == ""

    async def test_delete_clears_the_summary(self, db_session, sample_data):
        """用户删掉会话正文之后，摘要不能还留着——它会进下一轮的系统提示。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=2))
        conversation = await _seed(service, user, book, 3)
        await service.summarize_overflow(conversation, _SummaryProvider("前缀摘要"))

        await service.delete(conversation)

        assert conversation.summary == ""

    async def test_retention_clears_the_summary_once_its_last_turn_is_gone(
        self, db_session, sample_data,
    ):
        """保留期删掉正文之后摘要必须一起消失——否则保留期被从后门绕过去。"""
        from src.modules.ai import retention as retention_mod

        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=2))
        conversation = await _seed(service, user, book, 3)
        await service.summarize_overflow(conversation, _SummaryProvider("前缀摘要"))
        # 摘要覆盖到第 1 轮；把第 1 轮拨到 60 天前（窗口 30 天）。
        await _age_turn(db, conversation, 1, NOW - timedelta(days=60))

        result = await retention_mod.run_retention_sweep(
            db, now=NOW, cfg=_cfg(ai_conversation_retention_days=30),
        )

        assert result["deleted_events"] == 2
        assert result["cleared_summaries"] == 1
        assert conversation.summary == ""

    async def test_retention_keeps_the_summary_while_its_last_turn_survives(
        self, db_session, sample_data,
    ):
        """覆盖范围内**最后一轮**还在 → 摘要留着（偏保守的方向，与整轮保留一致）。"""
        from src.modules.ai import retention as retention_mod

        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=2))
        conversation = await _seed(service, user, book, 4)
        # 4 轮 / 窗口 2 → 摘要覆盖第 1~2 轮。只把**第 1 轮**拨老：覆盖范围的
        # 末轮（第 2 轮）还在，摘要因此留着。
        await service.summarize_overflow(conversation, _SummaryProvider("前缀摘要"))
        await _age_turn(db, conversation, 1, NOW - timedelta(days=60))

        result = await retention_mod.run_retention_sweep(
            db, now=NOW, cfg=_cfg(ai_conversation_retention_days=30),
        )

        assert result["deleted_events"] == 2
        assert result["cleared_summaries"] == 0
        assert conversation.summary == "前缀摘要"

    async def test_retention_sweep_is_idempotent_for_summaries(
        self, db_session, sample_data,
    ):
        from src.modules.ai import retention as retention_mod

        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=2))
        conversation = await _seed(service, user, book, 3)
        await service.summarize_overflow(conversation, _SummaryProvider("前缀摘要"))
        await _age_turn(db, conversation, 1, NOW - timedelta(days=60))
        cfg = _cfg(ai_conversation_retention_days=30)

        await retention_mod.run_retention_sweep(db, now=NOW, cfg=cfg)
        again = await retention_mod.run_retention_sweep(db, now=NOW, cfg=cfg)

        assert again == {"retention_days": 30, "deleted_events": 0, "cleared_summaries": 0}
