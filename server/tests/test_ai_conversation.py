"""T-M7-02f: 最小会话状态与配额双限测试。

验收映射（docs/milestones/production-execution-plan.md T-M7-02f）：
- 多轮澄清（问 → 追问 → 简答「500」→ 正确解析） → TestMultiTurnClarification
- 会话级轮数 / token 超限返回 429                → TestConversationQuota
- 历史窗口无 UUID                                → TestHistoryWindow
- 登出 / 删号清理会话行                          → TestCleanup

全部用例走真实 DB（内存 SQLite）+ 真实工具注册表；模型侧用脚本化 provider，
无网络。窗口与配额的档位经 ``ConversationService(cfg=…)`` 注入，不依赖生产默认值。
"""

from __future__ import annotations

import importlib.util
import json
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.common.config import Settings
from src.common.errors import AppException
from src.models.ai import AIConversation, AIConversationEvent, AIUsage
from src.models.base import Base
from src.models.ledger import Book
from src.models.user import DeviceSession, User, UserStatus
from src.modules.ai import tools as T
from src.modules.ai.agent import AgentLoop, StopReason
from src.modules.ai.conversation import (
    ERR_CONVERSATION_NOT_FOUND,
    ERR_INVALID_CONVERSATION_ID,
    EVENT_ASSISTANT,
    EVENT_USER,
    ConversationService,
    _prepare_events,
)
from src.modules.ai.provider import ProviderResult, StreamChunk, ToolCall
from src.modules.auth.service import AuthService
from tests.fixtures_db import db_session, sample_data  # noqa: F401

TODAY = date(2026, 9, 18)
MODEL = "deepseek-flash"

#: UUID 形状（8-4-4-4-12）。历史窗口里出现任何一个都是内部 ID 泄漏。
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _cfg(**overrides: Any) -> Settings:
    """独立 Settings——窗口与配额档位由此注入，不吃生产默认值。"""
    base: dict[str, Any] = {
        "database_url": "sqlite+aiosqlite:///:memory:",
        "secret_key": "test-secret",
        "env": "test",
        "api_prefix": "",
    }
    base.update(overrides)
    return Settings(**base)


class _ScriptedProvider:
    """按序返回给定 ProviderResult 的 ToolCallingProvider。"""

    name = "scripted"

    def __init__(self, results: list[ProviderResult]) -> None:
        self.results = list(results)
        self.calls: list[list[dict[str, Any]]] = []

    async def complete_with_tools(
        self, messages, *, model, max_tokens, timeout, tools, reasoning,
    ) -> ProviderResult:
        # 深拷贝消息列表：loop 后续还会改动自己的 ``_messages``，这里存的必须是
        # 本次请求**实际发出**的载荷。
        self.calls.append([dict(m) for m in messages])
        assert self.results, "scripted provider exhausted"
        return self.results.pop(0)

    async def complete_with_tools_stream(
        self, messages, *, model, max_tokens, timeout, tools, reasoning,
    ):
        """流式形态（T-M7-02g-2）：loop 自本卡起只走这条路。

        复用非流式的取值逻辑（同一个 ``calls`` 记录与脚本消耗顺序），只在交付
        形态上分帧：推理一帧、正文一帧、收尾帧带 ``result``。
        """
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
    """内存 ToolContext：名字→id 映射由测试给出，全部「来自服务端」。"""
    return T.ToolContext(
        db=db,
        user_id=user_id,
        book_id=book_id,
        categories_by_kind={
            "expense": {"餐饮": str(uuid.uuid4()), "交通": str(uuid.uuid4())},
            "income": {"工资": str(uuid.uuid4())},
        },
        account_names={"招行卡": str(uuid.uuid4())},
        book_names={"日常": str(uuid.uuid4())},
    )


def _text(content: str, *, reasoning: str = "") -> ProviderResult:
    return ProviderResult(
        content=content, prompt_tokens=11, completion_tokens=13,
        reasoning_content=reasoning,
    )


def _tool_call(
    name: str, args: dict[str, Any], *, content: str = "",
) -> ProviderResult:
    return ProviderResult(
        content=content,
        prompt_tokens=11,
        completion_tokens=13,
        reasoning_content="",
        tool_calls=[ToolCall(id="", name=name, arguments=json.dumps(args, ensure_ascii=False))],
    )


def _msg(role: str, content: str, **extra: Any) -> dict[str, Any]:
    return {"role": role, "content": content, **extra}


def _assert_pairs(messages: list[dict[str, Any]]) -> None:
    """每条 ``role:"tool"`` 结果都能配到前面带 tool_calls 的 assistant 消息。"""
    pending: set[str] = set()
    for message in messages:
        role = message.get("role")
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                pending.add(call["id"])
        elif role == "tool":
            assert message["tool_call_id"] in pending, "orphan tool result in history"
            pending.discard(message["tool_call_id"])
    assert not pending, "tool call without a result in history"


async def _run_loop(
    provider: _ScriptedProvider,
    ctx: T.ToolContext,
    user_input: str,
    *,
    history: list[dict[str, Any]] | None = None,
):
    loop = AgentLoop(
        provider, ctx, model=MODEL, timeout=10.0, today=TODAY,  # type: ignore[arg-type]
    )
    return await loop.run(user_input, history=history or [])


# ── 会话索引与归属校验 ─────────────────────────────────────────────────────


class TestConversationResolve:
    async def test_new_conversation_is_created_and_bound_to_book(self, db_session, sample_data):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)

        conversation = await service.resolve(user, book.id)

        assert conversation.id is not None
        assert conversation.user_id == user.id
        assert conversation.book_id == book.id
        assert (await service.history(conversation)) == []

    async def test_existing_conversation_is_returned(self, db_session, sample_data):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        created = await service.resolve(user, book.id)

        again = await service.resolve(user, book.id, str(created.id))

        assert again.id == created.id

    async def test_other_users_conversation_is_not_found(self, db_session, sample_data):
        """跨用户一律 404（security.md：读取面带归属校验，不可分辨是否存在）。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        created = await service.resolve(user, book.id)

        intruder = User(id=uuid.uuid4(), nickname="intruder")
        db.add(intruder)
        await db.flush()
        intruder_book = Book(id=uuid.uuid4(), owner_id=intruder.id, name="别人的账本")
        db.add(intruder_book)
        await db.flush()

        with pytest.raises(AppException) as exc:
            await service.resolve(intruder, intruder_book.id, str(created.id))
        assert exc.value.status_code == 404
        assert exc.value.code == ERR_CONVERSATION_NOT_FOUND

    async def test_malformed_conversation_id_is_rejected(self, db_session, sample_data):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)

        with pytest.raises(AppException) as exc:
            await service.resolve(user, book.id, "not-a-uuid")
        assert exc.value.status_code == 422
        assert exc.value.code == ERR_INVALID_CONVERSATION_ID

    async def test_book_switch_updates_the_index(self, db_session, sample_data):
        """会话跟随当前账本——留着旧账本会让索引变成一句假话。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        created = await service.resolve(user, book.id)

        other = Book(id=uuid.uuid4(), owner_id=user.id, name="旅行")
        db.add(other)
        await db.flush()

        moved = await service.resolve(user, other.id, str(created.id))
        assert moved.book_id == other.id

    async def test_another_users_book_is_rejected(self, db_session, sample_data):
        """会话索引不许指认别人的账本（与 ``load_tool_context`` 同口径的 404）。"""
        db, book = db_session, sample_data["book"]
        service = ConversationService(db)

        intruder = User(id=uuid.uuid4(), nickname="intruder")
        db.add(intruder)
        await db.flush()

        with pytest.raises(AppException) as exc:
            await service.resolve(intruder, book.id)
        assert exc.value.status_code == 404
        assert exc.value.code == "not_found"

    async def test_deleted_book_is_rejected(self, db_session, sample_data):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        book.deleted_at = datetime.now(UTC)
        await db.flush()

        with pytest.raises(AppException) as exc:
            await service.resolve(user, book.id)
        assert exc.value.status_code == 404

    async def test_given_id_branch_also_checks_book_ownership(self, db_session, sample_data):
        """**给了 conversation_id 的那条分支**同样要校验账本归属。

        会话归属校验本身会把「别人的会话」挡在门外，所以那条路径天然看不出账本
        校验有没有做（复审覆盖缺口 M8：原用例只走 `conversation_id=None` 分支，
        把校验挪进那条分支里改坏，全部用例照样全绿）。这里的构造是「自己的会话
        + 别人的账本」——会话查得到，唯一能拦下它的就是账本校验。
        """
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        other = User(id=uuid.uuid4(), nickname="other")
        db.add(other)
        await db.flush()
        other_book = Book(id=uuid.uuid4(), owner_id=other.id, name="别人的账本")
        db.add(other_book)
        await db.flush()

        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)

        with pytest.raises(AppException) as exc:
            await service.resolve(user, other_book.id, str(conversation.id))
        assert exc.value.status_code == 404
        assert exc.value.code == "not_found"


# ── 事件日志 ───────────────────────────────────────────────────────────────


class TestEventLog:
    async def test_turn_is_appended_with_monotonic_seq(self, db_session, sample_data):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)

        assert await service.append_turn(conversation, [
            _msg("user", "第一轮"),
            _msg("assistant", "好的", reasoning_content=""),
        ]) == 2
        assert await service.append_turn(conversation, [_msg("user", "第二轮")]) == 1

        seqs = (await db.scalars(
            select(AIConversationEvent.seq)
            .where(AIConversationEvent.conversation_id == conversation.id)
            .order_by(AIConversationEvent.seq)
        )).all()
        assert list(seqs) == [1, 2, 3]

    async def test_seq_is_unique_per_conversation(self, db_session, sample_data):
        """并发写入撞车由唯一索引兜底（读历史按 seq 有序，重复会让顺序无意义）。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)
        await service.append_turn(conversation, [_msg("user", "一")])

        db.add(AIConversationEvent(
            conversation_id=conversation.id, seq=1, turn=1, type=EVENT_USER,
            data=_msg("user", "撞车"),
        ))
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()

    async def test_seq_race_is_retried_once(self, db_session, sample_data, monkeypatch):
        """并发写同一会话 → 唯一索引拒绝其中一个：**只回滚这一次尝试**并重算重试，
        而不是把调用方在这个 session 上的整个事务掀掉（复审 P3-1）。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)
        await service.append_turn(conversation, [_msg("user", "第一轮")])

        real_position = service._next_position
        calls = {"count": 0}

        async def stale_once(conv):
            calls["count"] += 1
            if calls["count"] == 1:
                return (1, 1)  # 陈旧位置：与已有事件撞车
            return await real_position(conv)

        monkeypatch.setattr(service, "_next_position", stale_once)

        assert await service.append_turn(conversation, [_msg("user", "第二轮")]) == 1

        assert calls["count"] == 2, "撞车后必须重算一次"
        rows = (await db.execute(
            select(AIConversationEvent.seq, AIConversationEvent.turn)
            .where(AIConversationEvent.conversation_id == conversation.id)
            .order_by(AIConversationEvent.seq)
        )).all()
        # 失败的那次尝试一行都不能留下，且序号不跳号。
        assert list(rows) == [(1, 1), (2, 2)]

    async def test_system_message_is_never_persisted(self, db_session, sample_data):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)

        stored = await service.append_turn(conversation, [
            _msg("system", "你是 Aozora 的助手"),
            _msg("user", "你好"),
            _msg("assistant", "你好呀", reasoning_content=""),
        ])

        assert stored == 2
        types = (await db.scalars(
            select(AIConversationEvent.type)
            .where(AIConversationEvent.conversation_id == conversation.id)
        )).all()
        assert set(types) == {EVENT_USER, EVENT_ASSISTANT}

    def test_prepare_events_defaults_reasoning_content(self):
        """assistant 消息必须带 ``reasoning_content`` 键（空串可）——缺键会让整轮 400。"""
        prepared = _prepare_events([_msg("assistant", "好的")])
        assert prepared[0][1]["reasoning_content"] == ""

    def test_prepare_events_normalizes_null_reasoning_content(self):
        """键在场但值为 null 同样补空串——到了下一轮还是一次 400。"""
        prepared = _prepare_events([
            {"role": "assistant", "content": "好的", "reasoning_content": None},
        ])
        assert prepared[0][1]["reasoning_content"] == ""

    def test_prepare_events_drops_system_and_keeps_order(self):
        prepared = _prepare_events([
            _msg("system", "s"),
            _msg("user", "u"),
            _msg("assistant", "a"),
        ])
        assert [role for role, _ in prepared] == [EVENT_USER, EVENT_ASSISTANT]

    def test_prepare_events_rejects_unknown_role(self):
        with pytest.raises(ValueError):
            _prepare_events([_msg("developer", "nope")])

    def test_prepare_events_rejects_turn_starting_with_tool_result(self):
        """以 tool 开头 = 调用方切在了轮中间，写下去就是孤立结果。"""
        with pytest.raises(ValueError):
            _prepare_events([_msg("tool", "{}", tool_call_id="call_0_0")])


# ── 上下文窗口 ─────────────────────────────────────────────────────────────


def _turn_messages(index: int) -> list[dict[str, Any]]:
    """一轮「用户问 → 模型调工具 → 工具结果 → 模型回答」的完整消息。"""
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


class TestHistoryWindow:
    async def _seed(self, db, user, book, turns: int, service: ConversationService):
        conversation = await service.resolve(user, book.id)
        for index in range(1, turns + 1):
            await service.append_turn(conversation, _turn_messages(index))
        return conversation

    async def test_window_keeps_the_last_n_turns(self, db_session, sample_data):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=2))
        conversation = await self._seed(db, user, book, 4, service)

        history = await service.history(conversation)

        assert len(history) == 8  # 2 轮 × 4 条
        assert history[0]["content"] == "第3轮问题"
        assert history[-1]["content"] == "第4轮回答"
        _assert_pairs(history)

    async def test_window_never_splits_a_tool_batch(self, db_session, sample_data):
        """截断只按轮边界——孤立 tool 结果会被 API 以 400 拒绝。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=3))
        conversation = await self._seed(db, user, book, 5, service)

        history = await service.history(conversation)

        _assert_pairs(history)
        assert history[0]["role"] == "user"

    async def test_window_drops_earliest_turns_under_char_budget(self, db_session, sample_data):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(
            db, _cfg(ai_agent_history_turns=8, ai_agent_history_chars=120),
        )
        conversation = await service.resolve(user, book.id)
        await service.append_turn(conversation, [_msg("user", "甲" * 100)])
        await service.append_turn(conversation, [_msg("user", "乙" * 100)])

        history = await service.history(conversation)

        assert [m["content"] for m in history] == ["乙" * 100]

    async def test_window_keeps_at_least_one_turn(self, db_session, sample_data):
        """窗口空掉会让「追问」失去指代对象——宁可超预算也不让上下文归零。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(
            db, _cfg(ai_agent_history_turns=8, ai_agent_history_chars=1),
        )
        conversation = await service.resolve(user, book.id)
        await service.append_turn(conversation, [_msg("user", "甲" * 100)])
        await service.append_turn(conversation, [_msg("user", "乙" * 100)])

        history = await service.history(conversation)

        assert [m["content"] for m in history] == ["乙" * 100]

    async def test_char_budget_is_measured_over_the_whole_window(
        self, db_session, sample_data,
    ):
        """预算量的是**整个窗口**，不是最后一轮。

        3 轮 × 20 字（每条序列化 51 字符）、预算 90：整窗口 153 > 90 → 只留 1 轮。
        若实现只量最后一轮（51 ≤ 90），三轮会全留下——这正是复审报告的覆盖缺口
        M15（原用例只有 2 轮，两种口径下结果相同，分不出来）。
        """
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(
            db, _cfg(ai_agent_history_turns=8, ai_agent_history_chars=90),
        )
        conversation = await service.resolve(user, book.id)
        for text in ("甲" * 20, "乙" * 20, "丙" * 20):
            await service.append_turn(conversation, [_msg("user", text)])

        history = await service.history(conversation)

        assert [m["content"] for m in history] == ["丙" * 20]

    async def test_mid_turn_user_input_stays_in_one_turn(self, db_session, sample_data):
        """一轮里的多条 user 消息（中途输入）必须整轮同进同出。

        复审 P2-1：窗口原按「每条 user 消息起始」切轮，而 ``AgentLoop.submit()``
        的中途输入在下一个 Step 入口落进消息流，一次 ``run`` 的批次里因此可以有
        多条 user 消息。按 user 切会把一轮切成两半——N=1 时窗口只剩「补一句 +
        回答」，用户最初的提问与整批工具结果全丢，而那正是本卡要解决的追问指代
        场景。
        """
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_history_turns=1))
        conversation = await service.resolve(user, book.id)
        await service.append_turn(conversation, [
            _msg("user", "帮我记一笔"),
            {
                "role": "assistant", "content": "", "reasoning_content": "",
                "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "transaction_add", "arguments": "{}"},
                }],
            },
            _msg("tool", "{}", tool_call_id="call_1"),
            _msg("user", "补充说明：12块"),
            _msg("assistant", "好了", reasoning_content=""),
        ])

        history = await service.history(conversation)

        assert [m.get("content") for m in history] == [
            "帮我记一笔", "", "{}", "补充说明：12块", "好了",
        ]
        _assert_pairs(history)

    async def test_window_contains_no_uuid(self, db_session, sample_data):
        """历史窗口只含自然语言——出现任何一个 UUID 都是内部 ID 泄漏。

        这里的会话里**确实存在**服务端解析出的 categoryId（写工具的提案草案
        含它，执行时必需），它被 ``agent._tool_result_content`` 剥掉之后才进
        消息流。把那次剔除改回去，本用例立即变红。
        """
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)
        ctx = _ctx(db, user.id, book.id)
        provider = _ScriptedProvider([
            _tool_call("transaction_add", {
                "type": "expense", "amountCents": 1250,
                "categoryName": "餐饮", "bizDate": "2026-09-18",
            }),
            _text("已经拟好一笔待确认。"),
        ])

        result = await _run_loop(provider, ctx, "记一笔午饭12块")
        assert result.proposals, "写工具应当产出提案草案"
        assert result.proposals[0]["transactions"][0]["categoryId"], "草案本身含 id"
        await service.append_turn(conversation, result.messages)

        history = await service.history(conversation)

        assert history, "断言前提：窗口非空"
        assert not _UUID_RE.search(json.dumps(history, ensure_ascii=False)), (
            "历史窗口里出现了 UUID"
        )
        assert all(m.get("role") != "system" for m in history)


# ── 多轮澄清（本卡的核心验收） ─────────────────────────────────────────────


class TestMultiTurnClarification:
    async def test_short_follow_up_answer_is_resolved_from_history(self, db_session, sample_data):
        """问 → 追问 → 简答「500」：第二轮必须带着第一轮的问句去见模型。

        「正确解析」的机制就在这里——「500」本身不含任何信息，模型只有看见
        上一轮的「这笔要记多少钱？」才能把它接上。所以断言两件事：
        (a) 第二轮请求的载荷里有第一轮的问答；(b) 产出的提案金额是 500。
        """
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)
        ctx = _ctx(db, user.id, book.id)

        # 第一轮：信息不足，模型只反问，不调工具。
        first = _ScriptedProvider([_text("这笔要记多少钱？")])
        first_result = await _run_loop(first, ctx, "帮我记一笔")
        assert first_result.stop_reason == StopReason.COMPLETED
        assert not first_result.proposals
        await service.append_turn(conversation, first_result.messages)
        await service.record_turn(
            user, conversation, provider=first.name, model=MODEL,
            request_id="req-1", prompt_tokens=first_result.prompt_tokens,
            completion_tokens=first_result.completion_tokens,
        )

        # 第二轮：用户只回一个「500」。
        history = await service.history(conversation)
        assert history == [
            {"role": "user", "content": "帮我记一笔"},
            {"role": "assistant", "content": "这笔要记多少钱？", "reasoning_content": ""},
        ]
        second = _ScriptedProvider([
            _tool_call("transaction_add", {
                "type": "expense", "amountCents": 500,
                "categoryName": "餐饮", "bizDate": "2026-09-18",
            }),
            _text("好，拟好了一笔 5 元的支出待你确认。"),
        ])
        second_result = await _run_loop(second, ctx, "500", history=history)

        sent = second.calls[0]
        assert {"role": "user", "content": "帮我记一笔"} in sent
        assert any(
            m.get("role") == "assistant" and m.get("content") == "这笔要记多少钱？"
            for m in sent
        ), "第二轮请求必须带上第一轮的问句"
        assert sent[-1] == {"role": "user", "content": "500"}

        proposal = second_result.proposals[0]
        assert proposal["transactions"][0]["amountCents"] == 500

        # 两轮都落进同一会话的日志，第三轮才能继续。
        await service.append_turn(conversation, second_result.messages)
        final = await service.history(conversation)
        assert [m["content"] for m in final][-1] == "好，拟好了一笔 5 元的支出待你确认。"
        assert not _UUID_RE.search(json.dumps(final, ensure_ascii=False))


# ── 会话级配额双限 ─────────────────────────────────────────────────────────


class TestConversationQuota:
    async def _seed_usage(self, db, user, conversation, *, rows: int, tokens: int):
        for index in range(rows):
            db.add(AIUsage(
                user_id=user.id, conversation_id=conversation.id,
                provider="p", model=MODEL, tokens=tokens, cost=0,
                request_id=f"req-{index}",
            ))
        await db.flush()

    async def test_turn_limit_exceeded_raises_429(self, db_session, sample_data):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_conversation_max_turns=3))
        conversation = await service.resolve(user, book.id)
        await self._seed_usage(db, user, conversation, rows=3, tokens=1)

        with pytest.raises(AppException) as exc:
            await service.check_quota(conversation)
        assert exc.value.status_code == 429
        assert exc.value.code == "ai_quota_exceeded"

    async def test_token_limit_exceeded_raises_429(self, db_session, sample_data):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_conversation_max_tokens=1000))
        conversation = await service.resolve(user, book.id)
        await self._seed_usage(db, user, conversation, rows=2, tokens=500)

        with pytest.raises(AppException) as exc:
            await service.check_quota(conversation)
        assert exc.value.status_code == 429
        assert exc.value.code == "ai_quota_exceeded"

    async def test_within_limits_passes(self, db_session, sample_data):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(
            ai_agent_conversation_max_turns=3, ai_agent_conversation_max_tokens=1000,
        ))
        conversation = await service.resolve(user, book.id)
        await self._seed_usage(db, user, conversation, rows=2, tokens=499)

        await service.check_quota(conversation)

    async def test_fast_path_usage_rows_do_not_count(self, db_session, sample_data):
        """快路径的行 ``conversation_id`` 为 NULL——不占会话配额。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db, _cfg(ai_agent_conversation_max_turns=1))
        conversation = await service.resolve(user, book.id)
        for index in range(5):
            db.add(AIUsage(
                user_id=user.id, conversation_id=None, provider="p", model="qwen",
                tokens=10, cost=0, request_id=f"fast-{index}",
            ))
        await db.flush()

        await service.check_quota(conversation)

    async def test_record_turn_writes_one_row_with_conversation_id(self, db_session, sample_data):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)

        await service.record_turn(
            user, conversation, provider="deepseek", model=MODEL,
            request_id="req-1", prompt_tokens=100, completion_tokens=20,
        )

        row = (await db.scalars(
            select(AIUsage).where(AIUsage.conversation_id == conversation.id)
        )).one()
        assert row.tokens == 120


# ── 登出 / 删号清理 ────────────────────────────────────────────────────────


def _load_cleanup_module(name: str):
    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / "infra" / "cleanup" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestCleanup:
    async def test_logout_purges_conversations_and_events(self, db_session):
        db = db_session
        auth = AuthService(db)
        guest, _, refresh = await auth.create_guest(device_id="dev-1")
        own_book = Book(id=uuid.uuid4(), owner_id=guest.id, name="访客账本")
        db.add(own_book)
        await db.flush()

        service = ConversationService(db)
        conversation = await service.resolve(guest, own_book.id)
        await service.append_turn(conversation, [_msg("user", "你好")])
        await service.record_turn(
            guest, conversation, provider="p", model=MODEL, request_id="r",
            prompt_tokens=3, completion_tokens=4,
        )
        assert (await db.scalar(
            select(func.count()).select_from(AIConversationEvent)
        )) == 1

        await auth.logout(refresh, "dev-1")

        assert (await db.scalar(
            select(func.count()).select_from(AIConversation)
            .where(AIConversation.user_id == guest.id)
        )) == 0
        assert (await db.scalar(
            select(func.count()).select_from(AIConversationEvent)
        )) == 0
        # 用量审计行**必须留下**——它是 guest 日配额的计数基座，随会话一起删
        # 等于「登出再登入可刷满配额」。
        assert (await db.scalar(
            select(func.count()).select_from(AIUsage)
            .where(AIUsage.user_id == guest.id)
        )) == 1

    async def test_logout_without_a_matching_session_deletes_nothing(self, db_session, sample_data):
        """没有命中的设备会话 → 不知道是谁 → 一行都不删（幂等登出不该清库）。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        await service.resolve(user, book.id)

        await AuthService(db).logout("no-such-refresh-token", "dev-1")

        assert (await db.scalar(
            select(func.count()).select_from(AIConversation)
        )) == 1

    async def test_ai_usage_conversation_fk_is_set_null(self):
        """库层面的策略也要钉住（SQLite 测试环境不强制外键，行为测不出来）。"""
        fk = next(iter(Base.metadata.tables["ai_usage"].columns[
            "conversation_id"
        ].foreign_keys))
        assert fk.ondelete == "SET NULL"

    async def test_conversation_event_fk_is_cascade(self):
        fk = next(iter(Base.metadata.tables[
            "ai_conversation_events"
        ].columns["conversation_id"].foreign_keys))
        assert fk.ondelete == "CASCADE"

    async def test_account_deletion_purges_conversation_events(self, db_session, sample_data):
        """T-M7-02f：事件表进入删号清理清单（此前只有会话表）。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        user.status = UserStatus.FROZEN
        user.frozen_at = datetime.now(UTC) - timedelta(days=8)
        db.add(DeviceSession(
            user_id=user.id, device_id="dev-1",
            refresh_token_hash="hash-1",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        ))
        await db.flush()

        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)
        await service.append_turn(conversation, [_msg("user", "你好")])
        await db.commit()
        assert (await db.scalar(
            select(func.count()).select_from(AIConversationEvent)
        )) == 1, "断言前提：清理前事件确实存在"

        cleanup = _load_cleanup_module("account-deletion")
        assert await cleanup.delete_frozen_users(db) == 1

        assert (await db.scalar(
            select(func.count()).select_from(AIConversationEvent)
        )) == 0

    async def test_purge_user_reports_conversation_count(self, db_session, sample_data):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        await service.resolve(user, book.id)
        await service.resolve(user, book.id)

        assert await service.purge_user(user.id) == 2

    async def test_purge_user_leaves_other_users_rows_alone(self, db_session, sample_data):
        """清理按 ``user_id`` 过滤——**别的用户的会话与事件一行都不能动**。

        复审覆盖缺口 M4/M5：把 `purge_user` 里的用户过滤去掉（删全表）当时**全部
        用例照样全绿**，也就是说「登出把整个库的会话清空」这种事故没有任何用例
        拦得住。这里用两个用户的真实数据把它钉住。
        """
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        other = User(id=uuid.uuid4(), nickname="other")
        db.add(other)
        await db.flush()
        other_book = Book(id=uuid.uuid4(), owner_id=other.id, name="别人的账本")
        db.add(other_book)
        await db.flush()

        service = ConversationService(db)
        mine = await service.resolve(user, book.id)
        await service.append_turn(mine, [_msg("user", "我的")])
        theirs = await service.resolve(other, other_book.id)
        await service.append_turn(theirs, [_msg("user", "他的")])

        assert await service.purge_user(user.id) == 1

        assert (await db.scalar(
            select(func.count()).select_from(AIConversation)
        )) == 1
        remaining = (await db.scalars(select(AIConversationEvent.data))).all()
        assert len(remaining) == 1
        assert remaining[0]["content"] == "他的"
