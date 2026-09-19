"""T-M7-04a: 事件日志完整化与 Surface 子集测试。

验收映射（docs/milestones/production-execution-plan.md T-M7-04a）：
- 实测「结构性事件不占 token」          → TestStructuralEventsCostNoTokens
- Surface 子集逐类用例                  → TestSurfaceSubset
- 事件类型全集可写可读（含长度边界）    → TestEventTypeUniverse
- 非 Surface 事件不进 ``messages``      → TestSurfaceSubset / TestStructuralEventsCostNoTokens
- 投影 B 读取仍返回全部事件             → TestProjectionB
- 配额与安全事件入日志（投影 C）        → TestQuotaEvent
- 既有 test_ai_conversation.py 不回归   → 同基线，

「不占 token」为什么用**请求载荷**实测而不是设计推论：``history()`` 的返回值是
``AgentLoop.run(history=…)`` 的直接输入，脚本化 provider 又原样记录每次请求**实际
发出**的载荷——两份载荷逐字节相同，token 就必然相同（token 是载荷的函数）。无网络
环境下拿不到模型侧 usage，这是能拿到的最硬证据；字符量按 ``_window_chars`` 的口径
（``json.dumps(ensure_ascii=False)`` 的长度）给出，作为 token 的可测代理。

全部用例走真实 DB（内存 SQLite）+ 真实 loop；模型侧用脚本化 provider，无网络。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import select

from src.common.config import Settings
from src.models.ai import AIConversationEvent
from src.modules.ai import tools as T
from src.modules.ai.agent import AgentLoop
from src.modules.ai.conversation import (
    EVENT_APPROVAL_REQUEST,
    EVENT_APPROVAL_RESULT,
    EVENT_ASSISTANT,
    EVENT_DEVICE_REQUEST,
    EVENT_ERROR,
    EVENT_QUOTA,
    EVENT_REPLACE,
    EVENT_SECURITY,
    EVENT_TOOL,
    EVENT_TYPES,
    EVENT_USER,
    STRUCTURAL_EVENT_TYPES,
    SURFACE_EVENT_TYPES,
    ConversationService,
    _prepare_structural_events,
)
from src.modules.ai.provider import ProviderResult, StreamChunk
from tests.fixtures_db import db_session, sample_data  # noqa: F401

TODAY = date(2026, 9, 18)
MODEL = "deepseek-flash"

#: 列宽上限——从模型元数据读，不硬编码（迁移改了列宽这里跟着走）。
TYPE_COLUMN_LENGTH = AIConversationEvent.__table__.c.type.type.length


def _cfg(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": "sqlite+aiosqlite:///:memory:",
        "secret_key": "test-secret",
        "env": "test",
        "api_prefix": "",
    }
    base.update(overrides)
    return Settings(**base)


class _ScriptedProvider:
    """按序返回给定 ProviderResult，并记录每次请求**实际发出**的载荷。"""

    name = "scripted"

    def __init__(self, results: list[ProviderResult]) -> None:
        self.results = list(results)
        self.calls: list[list[dict[str, Any]]] = []

    async def complete_with_tools(
        self, messages, *, model, max_tokens, timeout, tools, reasoning,
    ) -> ProviderResult:
        self.calls.append([dict(m) for m in messages])
        assert self.results, "scripted provider exhausted"
        return self.results.pop(0)

    async def complete_with_tools_stream(
        self, messages, *, model, max_tokens, timeout, tools, reasoning,
    ):
        result = await self.complete_with_tools(
            messages, model=model, max_tokens=max_tokens, timeout=timeout,
            tools=tools, reasoning=reasoning,
        )
        if result.reasoning_content:
            yield StreamChunk(reasoning_delta=result.reasoning_content)
        if result.content:
            yield StreamChunk(delta=result.content)
        yield StreamChunk(result=result)


def _ctx(db: Any, user_id: uuid.UUID, book_id: uuid.UUID) -> T.ToolContext:
    return T.ToolContext(
        db=db,
        user_id=user_id,
        book_id=book_id,
        categories_by_kind={"expense": {"餐饮": str(uuid.uuid4())}},
        account_names={"招行卡": str(uuid.uuid4())},
        book_names={"日常": str(uuid.uuid4())},
    )


def _text(content: str) -> ProviderResult:
    return ProviderResult(
        content=content, prompt_tokens=11, completion_tokens=13, reasoning_content="",
    )


def _msg(role: str, content: str, **extra: Any) -> dict[str, Any]:
    return {"role": role, "content": content, **extra}


def _turn_messages(label: str) -> list[dict[str, Any]]:
    """一轮「用户问 → 模型调工具 → 工具结果 → 模型回答」的完整消息。"""
    call_id = f"call_{label}"
    return [
        _msg("user", f"{label}的问题"),
        {
            "role": "assistant", "content": "", "reasoning_content": "",
            "tool_calls": [{
                "id": call_id, "type": "function",
                "function": {"name": "transaction_list", "arguments": "{}"},
            }],
        },
        _msg("tool", json.dumps({"items": []}), tool_call_id=call_id),
        _msg("assistant", f"{label}的回答", reasoning_content=""),
    ]


def _payload_chars(messages: list[dict[str, Any]]) -> int:
    """载荷的字符量——``conversation._window_chars`` 的同一口径。"""
    return len(json.dumps(messages, ensure_ascii=False))


def _payload_json(messages: list[dict[str, Any]]) -> str:
    return json.dumps(messages, ensure_ascii=False, sort_keys=True)


async def _events(db, conversation) -> list[AIConversationEvent]:
    return list(await db.scalars(
        select(AIConversationEvent)
        .where(AIConversationEvent.conversation_id == conversation.id)
        .order_by(AIConversationEvent.seq)
    ))


async def _run_second_turn(
    history: list[dict[str, Any]], ctx: T.ToolContext, user_input: str = "第二轮",
) -> list[dict[str, Any]]:
    """带着给定窗口跑一轮 loop，返回**这一次请求实际发出的载荷**。"""
    provider = _ScriptedProvider([_text("好。")])
    loop = AgentLoop(
        provider, ctx, model=MODEL, timeout=10.0, today=TODAY,  # type: ignore[arg-type]
    )
    await loop.run(user_input, history=history)
    return provider.calls[0]


# ── 结构化事件的构造（覆盖全部结构性类型） ─────────────────────────────────

def _structural_sample(event_type: str) -> dict[str, Any]:
    """逐类型的合法载荷。内容是占位符——它们不进模型上下文，形状不重要。"""
    return {EVENT_APPROVAL_REQUEST: {"proposal_id": "p-1", "actions": ["transaction_add"]},
            EVENT_APPROVAL_RESULT: {"proposal_id": "p-1", "approved": True},
            EVENT_DEVICE_REQUEST: {"command": "share", "payload": {}},
            EVENT_ERROR: {"code": "agent_failed"},
            EVENT_REPLACE: {"summary": "前面几轮聊的是记账"},
            EVENT_QUOTA: {"provider": "deepseek", "model": MODEL, "tokens": 24},
            EVENT_SECURITY: {"reason": "cross_user_access"}}[event_type]


async def _seed_with_structural(db, user, book, event_types, *, same_turn=True):
    """一轮消息 + 一批结构性事件；返回 ``(service, conversation)``。"""
    service = ConversationService(db)
    conversation = await service.resolve(user, book.id)
    await service.append_turn(conversation, _turn_messages("第一轮"))
    if event_types:
        await service.append_events(
            conversation,
            [(event_type, _structural_sample(event_type)) for event_type in event_types],
            same_turn=same_turn,
        )
    return service, conversation


# ── 验收：实测「结构性事件不占 token」 ─────────────────────────────────────


class TestStructuralEventsCostNoTokens:
    async def test_payload_is_byte_identical_with_and_without_structural_events(
        self, db_session, sample_data,
    ):
        """同一轮的两种形态：带 3 条结构性事件 / 不带。

        两份请求载荷必须**逐字节相同**——token 是载荷的函数，载荷相同则 token
        相同。这就是「结构性事件不占 token」的实测，不接受设计推论。
        """
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        message_turn = _turn_messages("第一轮")

        plain = ConversationService(db)
        plain_conv = await plain.resolve(user, book.id)
        await plain.append_turn(plain_conv, message_turn)

        loaded = ConversationService(db)
        loaded_conv = await loaded.resolve(user, book.id)
        await loaded.append_turn(loaded_conv, message_turn)
        # 三条结构性事件，载荷都**不小**——省下的不是「几条空事件」。
        await loaded.append_events(loaded_conv, [
            (EVENT_QUOTA, {"provider": "deepseek", "model": MODEL,
                           "prompt_tokens": 900, "completion_tokens": 300,
                           "tokens": 1200}),
            (EVENT_APPROVAL_REQUEST, {"proposal_id": "p-1",
                                      "note": "这一笔是上周的餐费，要记吗？" * 4}),
            (EVENT_DEVICE_REQUEST, {"command": "share", "payload": {"text": "分享" * 20}}),
        ], same_turn=True)

        ctx = _ctx(db, user.id, book.id)
        plain_payload = await _run_second_turn(await plain.history(plain_conv), ctx)
        loaded_payload = await _run_second_turn(await loaded.history(loaded_conv), ctx)

        assert _payload_json(loaded_payload) == _payload_json(plain_payload)
        assert _payload_chars(loaded_payload) == _payload_chars(plain_payload)

    async def test_control_structural_events_would_have_cost_this_much(
        self, db_session, sample_data,
    ):
        """对照组：**不过滤**（04a 之前的行为）时那批事件要占多少。

        把结构性事件的 ``data`` 直接拼进窗口喂给 loop——载荷明显变长。这条用例
        给出「省下了多少」的数字，也钉住「变长」这件事本身：如果哪天有人去掉
        ``history()`` 的白名单，上面那条用例会红，这条会给出差值。
        """
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service, conversation = await _seed_with_structural(
            db, user, book, [EVENT_QUOTA, EVENT_APPROVAL_REQUEST, EVENT_DEVICE_REQUEST],
        )
        ctx = _ctx(db, user.id, book.id)

        projected = await service.history(conversation)
        unfiltered = [
            dict(e.data) for e in await _events(db, conversation)
        ]  # 04a 之前：全部事件都进窗口

        filtered_chars = _payload_chars(await _run_second_turn(projected, ctx))
        unfiltered_chars = _payload_chars(await _run_second_turn(unfiltered, ctx))

        assert unfiltered_chars > filtered_chars, "结构性事件进上下文必然占 token"
        # 数字入档：省下的是这几条事件的全部载荷 + 它们带来的分隔符开销。
        assert len(unfiltered) == len(projected) + 3
        assert unfiltered_chars - filtered_chars > 100

    async def test_window_char_budget_counts_surface_events_only(
        self, db_session, sample_data,
    ):
        """字符预算同理：一堆结构性事件不会把窗口挤掉。

        先塞满结构性事件再跑窗口截断——若它们参与 ``_window_chars``，最后那轮
        会被挤出去（至少保留一轮的兜底也救不了「只剩一轮」以外的场景）。
        """
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=8))
        conversation = await service.resolve(user, book.id)
        await service.append_turn(conversation, [_msg("user", "第一轮")])
        await service.append_turn(conversation, [_msg("user", "第二轮")])
        await service.append_events(conversation, [
            (EVENT_REPLACE, {"summary": "很长的摘要" * 2000}),
        ])

        history = await service.history(conversation)

        assert [m["content"] for m in history] == ["第一轮", "第二轮"]


# ── 验收：Surface 子集逐类 ────────────────────────────────────────────────


class TestSurfaceSubset:
    def test_surface_and_structural_are_disjoint_and_cover_the_universe(self):
        assert SURFACE_EVENT_TYPES & STRUCTURAL_EVENT_TYPES == frozenset()
        assert SURFACE_EVENT_TYPES | STRUCTURAL_EVENT_TYPES == EVENT_TYPES
        assert SURFACE_EVENT_TYPES == {EVENT_USER, EVENT_ASSISTANT, EVENT_TOOL}

    @pytest.mark.parametrize("event_type", sorted(STRUCTURAL_EVENT_TYPES))
    async def test_each_structural_type_stays_out_of_messages(
        self, db_session, sample_data, event_type,
    ):
        """逐类：写进去读得到（投影 B），但**一条都不进** ``messages``。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service, conversation = await _seed_with_structural(db, user, book, [event_type])

        history = await service.history(conversation)

        assert [m["content"] for m in history] == [
            "第一轮的问题", "", '{"items": []}', "第一轮的回答",
        ]
        assert all(m["role"] in SURFACE_EVENT_TYPES for m in history)

    @pytest.mark.parametrize("event_type", sorted(SURFACE_EVENT_TYPES))
    async def test_each_surface_type_reaches_messages(
        self, db_session, sample_data, event_type,
    ):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)
        messages = _turn_messages("第一轮")

        await service.append_turn(conversation, messages)

        history = await service.history(conversation)
        roles = {m["role"] for m in history}
        assert event_type in roles, f"{event_type} 是 Surface 事件，必须进窗口"
        assert len(history) == len(messages)

    async def test_window_order_is_the_log_order(self, db_session, sample_data):
        """结构性事件插在中间也不打乱 Surface 的相对顺序（白名单是过滤不是重排）。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)
        await service.append_turn(conversation, [_msg("user", "一"), _msg("assistant", "二",
                                                                          reasoning_content="")])
        await service.append_events(conversation, [(EVENT_ERROR, {"code": "x"})])
        await service.append_turn(conversation, [_msg("user", "三")])
        await service.append_events(conversation, [(EVENT_ERROR, {"code": "y"})])

        history = await service.history(conversation)

        assert [m["content"] for m in history] == ["一", "二", "三"]

    async def test_a_turn_of_only_structural_events_never_enters_the_window(
        self, db_session, sample_data,
    ):
        """全是结构性事件的轮次（如失败轮的用量）不占窗口的 N 轮配额。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=1))
        conversation = await service.resolve(user, book.id)
        await service.append_turn(conversation, [_msg("user", "有内容的一轮")])
        # 失败轮：没有消息事件，只有一条用量事件，自成一轮。
        await service.append_events(conversation, [(EVENT_QUOTA, {"tokens": 7})])

        history = await service.history(conversation)

        assert [m["content"] for m in history] == ["有内容的一轮"]


# ── 验收：类型全集可写可读（含长度边界） ──────────────────────────────────


class TestEventTypeUniverse:
    def test_column_is_wide_enough_for_every_type(self):
        """长度边界：全集里最长的类型名必须放得进列。

        实测（PG 17.11）：``varchar(16)`` 按字符数计，``approval_request`` 恰为
        16 字符——顶格、零余量，17 字符即被拒。本卡把它加宽到 32。
        """
        longest = max(EVENT_TYPES, key=len)
        assert longest == EVENT_APPROVAL_REQUEST
        assert len(longest) == 16  # 项格长度——加宽的理由
        assert TYPE_COLUMN_LENGTH == 32
        assert len(longest) < TYPE_COLUMN_LENGTH

    @pytest.mark.parametrize("event_type", sorted(SURFACE_EVENT_TYPES))
    async def test_every_surface_type_round_trips(
        self, db_session, sample_data, event_type,
    ):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)
        await service.append_turn(conversation, _turn_messages("第一轮"))

        stored = {e.type for e in await _events(db, conversation)}
        assert stored == SURFACE_EVENT_TYPES
        assert event_type in stored

    async def test_every_structural_type_round_trips(self, db_session, sample_data):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service, conversation = await _seed_with_structural(
            db, user, book, sorted(STRUCTURAL_EVENT_TYPES),
        )

        stored = {e.type for e in await _events(db, conversation)}
        assert stored == STRUCTURAL_EVENT_TYPES | SURFACE_EVENT_TYPES

    async def test_longest_type_name_survives_a_round_trip(self, db_session, sample_data):
        """顶格的那个类型名真的写得进、读得出（列宽不只是常量上的数字）。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service, conversation = await _seed_with_structural(
            db, user, book, [EVENT_APPROVAL_REQUEST],
        )

        row = (await _events(db, conversation))[-1]
        assert row.type == EVENT_APPROVAL_REQUEST

    def test_unknown_type_is_rejected(self):
        with pytest.raises(ValueError):
            _prepare_structural_events([("summary", {})])


# ── 验收：投影 B 读取仍返回全部事件 ───────────────────────────────────────


class TestProjectionB:
    async def test_events_returns_surface_and_structural_alike(self, db_session, sample_data):
        """``events()`` 是 UI / 审计的读面——**不过滤**，与 ``history()`` 相反。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service, conversation = await _seed_with_structural(
            db, user, book, [EVENT_QUOTA, EVENT_APPROVAL_REQUEST],
        )

        events = await service.events(conversation)
        surface = await service.history(conversation)

        assert [e.type for e in events] == [
            EVENT_USER, EVENT_ASSISTANT, EVENT_TOOL, EVENT_ASSISTANT,
            EVENT_QUOTA, EVENT_APPROVAL_REQUEST,
        ]
        assert len(events) == len(surface) + 2
        # 结构性事件的载荷原样读得到（UI 拿它渲染卡片 / 审计拿它回溯）。
        assert events[-1].data["proposal_id"] == "p-1"

    async def test_after_seq_increments_over_structural_events_too(
        self, db_session, sample_data,
    ):
        """增量读取按 ``seq`` 走——中间夹着结构性事件也不跳号、不重复。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service, conversation = await _seed_with_structural(db, user, book, [EVENT_QUOTA])
        all_events = await service.events(conversation)

        tail = await service.events(conversation, after_seq=all_events[3].seq)

        assert [e.seq for e in tail] == [all_events[4].seq]
        assert tail[0].type == EVENT_QUOTA

    async def test_ignorable_column_matches_the_surface_constant(self, db_session, sample_data):
        """``ignorable`` 与 Surface 子集**同源**——写侧派生、读侧过滤，不得漂移。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service, conversation = await _seed_with_structural(
            db, user, book, sorted(STRUCTURAL_EVENT_TYPES),
        )

        rows = await _events(db, conversation)

        # 一轮消息是 4 条（assistant 出现两次），结构性事件每类型一条。
        assert len(rows) == len(_turn_messages("第一轮")) + len(STRUCTURAL_EVENT_TYPES)
        for row in rows:
            assert row.ignorable is (row.type not in SURFACE_EVENT_TYPES)


# ── 验收：配额与安全事件入日志（投影 C） ──────────────────────────────────


class TestQuotaEvent:
    async def test_record_turn_writes_a_quota_event_next_to_the_usage_row(
        self, db_session, sample_data,
    ):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)
        await service.append_turn(conversation, [_msg("user", "记账")])

        await service.record_turn(
            user, conversation, provider="deepseek", model=MODEL,
            request_id="req-1", prompt_tokens=100, completion_tokens=20,
            same_turn=True,
        )

        rows = await _events(db, conversation)
        quota = [e for e in rows if e.type == EVENT_QUOTA]
        assert len(quota) == 1
        assert quota[0].data == {
            "provider": "deepseek", "model": MODEL,
            "prompt_tokens": 100, "completion_tokens": 20, "tokens": 120,
        }
        assert quota[0].ignorable is True

    async def test_same_turn_keeps_the_quota_event_in_this_turn(
        self, db_session, sample_data,
    ):
        """``same_turn=True``：用量事件与消息同轮，不另占一个空轮号。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)
        await service.append_turn(conversation, [_msg("user", "记账")])

        await service.record_turn(
            user, conversation, provider="deepseek", model=MODEL,
            request_id="req-1", prompt_tokens=1, completion_tokens=1,
            same_turn=True,
        )

        assert {e.turn for e in await _events(db, conversation)} == {1}

    async def test_without_same_turn_it_gets_its_own_turn(self, db_session, sample_data):
        """失败轮没有消息事件——它的用量事件**自成一轮**（那正是它的轮号）。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)
        await service.append_turn(conversation, [_msg("user", "记账")])

        await service.record_turn(
            user, conversation, provider="deepseek", model=MODEL,
            request_id="req-1", prompt_tokens=1, completion_tokens=1,
        )

        rows = await _events(db, conversation)
        assert [(e.type, e.turn) for e in rows] == [
            (EVENT_USER, 1), (EVENT_QUOTA, 2),
        ]

    async def test_quota_event_does_not_enter_the_window(self, db_session, sample_data):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)
        await service.append_turn(conversation, [_msg("user", "记账")])
        await service.record_turn(
            user, conversation, provider="deepseek", model=MODEL,
            request_id="req-1", prompt_tokens=1, completion_tokens=1,
            same_turn=True,
        )

        history = await service.history(conversation)

        assert [m["content"] for m in history] == ["记账"]


class TestAppendEventsBoundary:
    """结构性写入口的边界——**禁止**从这里写 Surface 事件。"""

    @pytest.mark.parametrize("event_type", sorted(SURFACE_EVENT_TYPES))
    def test_surface_types_are_refused(self, event_type):
        """Surface 事件的 ``data`` 必须是 API 消息字典；塞一条形状不对的进去，
        要到下一轮模型请求时才以 400 暴露（归因困难），故在写入前就拒绝。
        """
        with pytest.raises(ValueError):
            _prepare_structural_events([(event_type, {"role": event_type, "content": "x"})])

    async def test_empty_batch_is_a_noop(self, db_session, sample_data):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)

        assert await service.append_events(conversation, []) == 0
        assert await _events(db, conversation) == []

    async def test_append_events_returns_the_written_count(self, db_session, sample_data):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service, conversation = await _seed_with_structural(
            db, user, book, [EVENT_QUOTA, EVENT_ERROR],
        )

        assert await service.append_events(
            conversation, [(EVENT_SECURITY, {"reason": "quota_rejected"})],
        ) == 1


class TestEventTime:
    async def test_time_is_written_and_shared_by_the_batch(self, db_session, sample_data):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service, conversation = await _seed_with_structural(
            db, user, book, [EVENT_QUOTA, EVENT_ERROR],
        )

        times = {e.time for e in await _events(db, conversation)}

        # 一轮原子写入 = 同一瞬间：消息批与随后的结构性事件批各一个时间戳。
        assert len(times) == 2
        assert all(isinstance(t, datetime) for t in times)

    async def test_server_default_fills_time_when_the_writer_omits_it(
        self, db_session, sample_data,
    ):
        """DB 兜底：ORM 直接构造（不给 ``time``）也能落库——``created_at`` 同口径。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)

        db.add(AIConversationEvent(
            conversation_id=conversation.id, seq=1, turn=1,
            type=EVENT_USER, data=_msg("user", "直接构造"),
        ))
        await db.flush()

        row = (await _events(db, conversation))[0]
        assert row.time is not None
        assert row.ignorable is False

    async def test_new_events_carry_a_timezone_aware_utc_stamp(
        self, db_session, sample_data,
    ):
        """``time`` 由写入方取 UTC——与 ``last_active_at`` 同一套时钟。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)
        before = datetime.now(UTC).replace(tzinfo=None)

        await service.append_turn(conversation, [_msg("user", "记账")])

        row = (await _events(db, conversation))[0]
        stored = row.time.replace(tzinfo=None)
        assert before <= stored <= datetime.now(UTC).replace(tzinfo=None)
