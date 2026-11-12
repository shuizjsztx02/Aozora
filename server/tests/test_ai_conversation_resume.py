"""T-M7-04b: 会话恢复与分叉（含轮中途落库）测试。

验收映射（``docs/milestones/production-execution-plan.md`` T-M7-04b）：

- 写入粒度由「一轮原子」改为**逐事件**   → :class:`TestPerEventPersistence`
- 断连后按 ``conversation_id`` + ``seq`` 续跑（服务端）→ :class:`TestResume`
- **轮中途落库后断连仍有完整历史**       → :class:`TestResume`
- 分叉会话独立续跑 / 原会话不变          → ``test_ai_conversations_api.TestForkConversation``
- **不变式 1 不因半截落库被破坏**        → :class:`TestPairingInvariant`
- ``StopReason.CANCELLED`` 可达性复审     → :class:`TestCancelledReachability`

分叉的 HTTP 面（路由、错误码、跨用户 404）在 ``test_ai_conversations_api.py``——
那里已有真实鉴权与全套会话端点，本文件只做**服务端语义**：写入的粒度与提交点、
恢复时模型实际看到什么、配对不变式的边界、以及取消的可达性结论。

**「轮中途落库」怎么证**：用一个包住真 ``TurnWriter`` 的观察者，在**每次写入之后**
查一遍库并记下当时日志里有什么、事务是否已提交（``in_transaction()`` 为假 = 真的
提交了，不是攒到轮末）。断言因此落在「loop 还没跑完时库里已经有了」上，而不是
「跑完之后库里有」——后者 P1 的整轮写入也能通过。

全部用例走真实 DB（内存 SQLite）+ 真实 loop；模型侧用脚本化 provider，无网络。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from src.models.ai import AIConversationEvent
from src.modules.ai import tools as T
from src.modules.ai.agent import (
    AgentLoop,
    ApprovalDecision,
    ApprovalRequest,
    StopReason,
)
from src.modules.ai.conversation import (
    EVENT_ASSISTANT,
    EVENT_TOOL,
    EVENT_USAGE,
    EVENT_USER,
    ConversationService,
    TurnWriter,
    _paired_prefix_len,
)
from src.modules.ai.provider import ProviderError, ProviderResult, StreamChunk
from tests.fixtures_db import db_session, sample_data  # noqa: F401

TODAY = date(2026, 9, 18)
MODEL = "deepseek-flash"
CATEGORY = "餐饮"


# ── 脚本化 provider（与 test_ai_conversation_log.py 同形）─────────────────


class _ScriptedProvider:
    """按序返回给定结果/异常，并记录每次请求**实际发出**的载荷。"""

    name = "scripted"

    def __init__(self, results: list[Any]) -> None:
        self.results = list(results)
        self.calls: list[list[dict[str, Any]]] = []

    async def complete_with_tools(
        self, messages, *, model, max_tokens, timeout, tools, reasoning,
    ) -> ProviderResult:
        self.calls.append([dict(m) for m in messages])
        assert self.results, "scripted provider exhausted"
        item = self.results.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def complete_with_tools_stream(
        self, messages, *, model, max_tokens, timeout, tools, reasoning,
    ):
        result = await self.complete_with_tools(
            messages, model=model, max_tokens=max_tokens, timeout=timeout,
            tools=tools, reasoning=reasoning,
        )
        if result.content:
            yield StreamChunk(delta=result.content)
        yield StreamChunk(result=result)


def _text(content: str, *, prompt: int = 5, completion: int = 7) -> ProviderResult:
    return ProviderResult(
        content=content, prompt_tokens=prompt, completion_tokens=completion,
        reasoning_content="",
    )


def _call(name: str, args: dict[str, Any] | None = None, *, id: str = "call_1") -> Any:
    from src.modules.ai.provider import ToolCall

    return ToolCall(
        id=id, name=name, arguments=json.dumps(args or {}, ensure_ascii=False),
    )


def _tool_turn(*calls: Any, content: str = "") -> ProviderResult:
    return ProviderResult(
        content=content, prompt_tokens=5, completion_tokens=7, tool_calls=calls,
    )


def _ctx(db: Any, user_id: uuid.UUID, book_id: uuid.UUID, category_id: uuid.UUID) -> T.ToolContext:
    return T.ToolContext(
        db=db,
        user_id=user_id,
        book_id=book_id,
        categories_by_kind={"expense": {CATEGORY: str(category_id)}},
        account_names={"招行卡": str(uuid.uuid4())},
        book_names={"日常": str(uuid.uuid4())},
    )


# ── 观察者：包住真写入器，每次写入后看一眼库里 ────────────────────────────


class _RecordingWriter:
    """真 ``TurnWriter`` 的观察者：写完之后查一遍库，记下**此刻**有什么。

    ``committed`` 取 ``AsyncSession.in_transaction()`` 的**反**：写完之后它必须是
    假，才说明这一批真的提交了（而不是攒在事务里等轮末）。这正是「逐事件落库」
    与「一轮原子写入」的分水岭。
    """

    def __init__(self, writer: TurnWriter, db: Any, conversation: Any) -> None:
        self._writer = writer
        self._db = db
        self._conversation = conversation
        self.snapshots: list[tuple[tuple[str, ...], bool]] = []
        self.stamps: list[tuple[tuple[str, ...], list[datetime]]] = []

    async def __call__(
        self, messages: Sequence[tuple[dict[str, Any], datetime]],
        *, opens_turn: bool = False,
    ) -> None:
        self.stamps.append((
            tuple(str(m.get("role")) for m, _ in messages),
            [stamp for _, stamp in messages],
        ))
        await self._writer(messages, opens_turn=opens_turn)
        await self._snapshot()

    async def structural(
        self, event_type: str, data: Mapping[str, Any], *, same_turn: bool = False,
    ) -> None:
        await self._writer.structural(event_type, data, same_turn=same_turn)
        await self._snapshot()

    async def _snapshot(self) -> None:
        # **先**看事务状态再看数据：观察者自己那条 SELECT 会开一个新事务，
        # 顺序反了会把「已提交」读成「在事务里」，这条断言就永远是假的。
        committed = not self._db.in_transaction()
        types = (await self._db.scalars(
            select(AIConversationEvent.type)
            .where(AIConversationEvent.conversation_id == self._conversation.id)
            .order_by(AIConversationEvent.seq)
        )).all()
        self.snapshots.append((tuple(types), committed))


async def _rows(db: Any, conversation: Any) -> list[AIConversationEvent]:
    return list((await db.scalars(
        select(AIConversationEvent)
        .where(AIConversationEvent.conversation_id == conversation.id)
        .order_by(AIConversationEvent.seq)
    )).all())


# ── 1. 逐事件落库 ─────────────────────────────────────────────────────────


class TestPerEventPersistence:
    async def test_each_batch_is_committed_while_the_turn_is_still_running(
        self, db_session, sample_data,
    ):
        """验收：**轮中途落库**。写入发生在 loop 还没跑完的时候，且每次都真的提交。

        P1 的整轮原子写入在这里只有两个快照（用户输入 + 整轮），且都在跑完之后；
        本卡要求逐批可见：用户输入一批、每个配对组一批、收尾答案一批。
        """
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)
        writer = _RecordingWriter(
            TurnWriter(service, conversation), db, conversation,
        )
        provider = _ScriptedProvider([
            _tool_turn(_call("transaction_list")),
            _text("没有记录。"),
        ])
        loop = AgentLoop(
            provider, _ctx(db, user.id, book.id, sample_data["cat_expense"].id),
            model=MODEL, timeout=5.0, today=TODAY, sink=writer,
        )

        result = await loop.run("看看流水")

        assert result.stop_reason is StopReason.COMPLETED
        # 每批落库前都提交：用户输入 / 第 1 步用量 / 第 1 步配对组（assistant + tool）
        # / 第 2 步用量 / 收尾答案。M8-12 的 ``usage`` 走 ``structural`` 通道，
        # 同样是「写完即提交」。
        assert writer.snapshots == [
            ((EVENT_USER,), True),
            ((EVENT_USER, EVENT_USAGE), True),
            ((EVENT_USER, EVENT_USAGE, EVENT_ASSISTANT, EVENT_TOOL), True),
            (
                (EVENT_USER, EVENT_USAGE, EVENT_ASSISTANT, EVENT_TOOL, EVENT_USAGE),
                True,
            ),
            (
                (
                    EVENT_USER, EVENT_USAGE, EVENT_ASSISTANT, EVENT_TOOL,
                    EVENT_USAGE, EVENT_ASSISTANT,
                ),
                True,
            ),
        ]
        # 每次写完都已提交（`in_transaction()` 为假）——不是攒到轮末一次提交
        assert all(committed for _, committed in writer.snapshots)

    async def test_a_turn_writes_many_batches_but_only_one_turn_number(
        self, db_session, sample_data,
    ):
        """一轮可以写好几批，但 ``turn`` 只有一个值——它是窗口截断与保留期的分组
        依据（见 ``models/ai.py``）。批量细分不该把一轮切成好几轮。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)
        writer = TurnWriter(service, conversation)
        provider = _ScriptedProvider([
            _tool_turn(_call("transaction_list")),
            _text("没有记录。"),
        ])
        loop = AgentLoop(
            provider, _ctx(db, user.id, book.id, sample_data["cat_expense"].id),
            model=MODEL, timeout=5.0, today=TODAY, sink=writer,
        )

        await loop.run("看看流水")

        rows = await _rows(db, conversation)
        assert [row.type for row in rows] == [
            EVENT_USER, EVENT_USAGE, EVENT_ASSISTANT, EVENT_TOOL,
            EVENT_USAGE, EVENT_ASSISTANT,
        ]
        assert {row.turn for row in rows} == {1}
        assert [row.seq for row in rows] == [1, 2, 3, 4, 5, 6]

    async def test_events_carry_the_moment_they_were_produced(
        self, db_session, sample_data,
    ):
        """``time`` 由 loop 在**产出那一刻**取（``agent.MessageStamp``），不是写入方
        统一取「此刻」——同一批里的 assistant 与它的工具结果之间隔着整段工具执行，
        共用一个时间戳会把那段间隔抹平。

        服务端落库时逐条照搬这些时刻（下面直接给两个可区分的值来钉死这条管道）。
        """
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)
        writer = _RecordingWriter(
            TurnWriter(service, conversation), db, conversation,
        )
        provider = _ScriptedProvider([
            _tool_turn(_call("transaction_list")),
            _text("没有记录。"),
        ])
        loop = AgentLoop(
            provider, _ctx(db, user.id, book.id, sample_data["cat_expense"].id),
            model=MODEL, timeout=5.0, today=TODAY, sink=writer,
        )

        await loop.run("看看流水")

        # 每条消息各带一个时刻，且**按产出顺序非递减**（assistant 在工具执行之前
        # 产出，工具结果在它之后）
        batch = writer.stamps[1]
        assert batch[0] == (EVENT_ASSISTANT, EVENT_TOOL)
        assert len(batch[1]) == 2
        assert batch[1][0] <= batch[1][1]
        # 落库侧逐条照搬：给两个可区分的值，读回来必须还是那两个
        early = datetime(2026, 9, 18, 3, 0, tzinfo=UTC)
        late = early + timedelta(seconds=7)
        await service.append_turn(
            conversation,
            [{"role": "user", "content": "第二批"}],
            times=[late],
        )
        last = (await _rows(db, conversation))[-1]
        assert last.time.replace(tzinfo=UTC) == late
        assert last.time.replace(tzinfo=UTC) != early

    async def test_mismatched_times_are_rejected(self, db_session, sample_data):
        """长度对不上必须**当场报错**：给错位的时间戳比不给更糟——它会静默地
        张冠李戴（``_prepare_events`` 丢 system 消息就会造成这种错位）。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)

        with pytest.raises(ValueError):
            await service.append_turn(
                conversation,
                [{"role": "user", "content": "甲"}, {"role": "assistant", "content": "乙"}],
                times=[datetime.now(UTC)],
            )


# ── 2. 恢复 ───────────────────────────────────────────────────────────────


class TestResume:
    async def test_next_turn_sees_the_steps_the_interrupted_turn_completed(
        self, db_session, sample_data,
    ):
        """验收：**断连（此处以 provider 失败代表）后按 ``conversation_id`` 续跑**。

        P1 的整轮原子写入下，第 1 步跑完就挂掉的轮次**一条都不落库**，下一轮的历史
        里连用户问过什么都不在；04b 之后第 1 步的配对组已经提交，下一轮因此知道
        「问过了、查过了、拿到的是什么」——这就是「接着跑」在服务端的全部含义。
        """
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        ctx = _ctx(db, user.id, book.id, sample_data["cat_expense"].id)
        conversation = await service.resolve(user, book.id)

        # 第一轮：第 1 步查流水成功，第 2 步 provider 挂掉
        failing = _ScriptedProvider([
            _tool_turn(_call("transaction_list")),
            ProviderError(status=503, retryable=False, reason="transport"),
        ])
        with pytest.raises(ProviderError):
            await AgentLoop(
                failing, ctx, model=MODEL, timeout=5.0, today=TODAY,
                sink=TurnWriter(service, conversation),
            ).run("看看流水")

        interrupted = await _rows(db, conversation)
        # 已经完成的第 1 步留在日志里（含那条带 tool_calls 的 assistant 与它的用量）
        assert [row.type for row in interrupted] == [
            EVENT_USER, EVENT_USAGE, EVENT_ASSISTANT, EVENT_TOOL,
        ]
        resume_point = await service.max_seq(conversation)
        assert resume_point == 4

        # 第二轮：带同一个 conversation_id（服务端侧即「用同一个会话再跑一轮」）
        history = await service.history(conversation)
        assert [m["role"] for m in history] == ["user", "assistant", "tool"]
        assert history[0]["content"] == "看看流水"

        provider = _ScriptedProvider([_text("接着看：没有记录。")])
        result = await AgentLoop(
            provider, ctx, model=MODEL, timeout=5.0, today=TODAY,
            sink=TurnWriter(service, conversation),
        ).run("接着看", history=history)

        # 模型**实际发出**的请求里带着上一轮跑完的部分（不是从零开始）
        assert [m["role"] for m in provider.calls[0]] == [
            "system", "user", "assistant", "tool", "user",
        ]
        assert provider.calls[0][-1]["content"] == "接着看"
        # 新一轮自占一个轮号（被打断那一轮已经占了 1）
        rows = await _rows(db, conversation)
        assert [(row.turn, row.type) for row in rows] == [
            (1, EVENT_USER), (1, EVENT_USAGE), (1, EVENT_ASSISTANT), (1, EVENT_TOOL),
            (2, EVENT_USER), (2, EVENT_USAGE), (2, EVENT_ASSISTANT),
        ]
        assert result.stop_reason is StopReason.COMPLETED
        assert await service.max_seq(conversation) == 7

    async def test_after_seq_hands_the_client_exactly_what_it_missed(
        self, db_session, sample_data,
    ):
        """客户端侧入口：``after_seq`` 是**严格大于**——断连重连时拿上一批最后一条
        的 ``seq`` 再拉一次，既补齐断连期间落下的事件，也不重复收到已有的。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)
        provider = _ScriptedProvider([_text("好的。")])
        result = await AgentLoop(
            provider, _ctx(db, user.id, book.id, sample_data["cat_expense"].id),
            model=MODEL, timeout=5.0, today=TODAY,
            sink=TurnWriter(service, conversation),
        ).run("你好")

        rows = await _rows(db, conversation)
        seen = rows[0].seq  # 客户端只收到了第一条（断连）
        tail = await service.events(conversation, after_seq=seen)
        assert [row.seq for row in tail] == [row.seq for row in rows[1:]]
        # 幂等：同一个 after_seq 再拉一次得到同一批
        assert await service.events(conversation, after_seq=seen) == tail
        assert result.stop_reason is StopReason.COMPLETED


# ── 3. 配对不变式 ─────────────────────────────────────────────────────────


class TestPairingInvariant:
    async def test_a_batch_is_never_torn_between_call_and_result(
        self, db_session, sample_data,
    ):
        """验收：**不变式 1 不因半截落库被破坏**。

        在一个工具批次中间把写入器打断（模拟落库失败 / 进程被杀），日志里剩下的
        必须是**上一个完整批**——不能出现「有 ``tool_calls`` 没有结果」的尾巴。
        """
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)
        real = TurnWriter(service, conversation)
        writes = {"count": 0}

        class _DiesOnTheToolBatch:
            async def __call__(self, messages, *, opens_turn=False):
                writes["count"] += 1
                if writes["count"] == 2:  # 第 2 批 = 配对组
                    raise RuntimeError("commit failed mid batch")
                await real(messages, opens_turn=opens_turn)

            async def structural(self, event_type, data, *, same_turn=False):
                await real.structural(event_type, data, same_turn=same_turn)

        provider = _ScriptedProvider([
            _tool_turn(_call("transaction_list")),
            _text("没有记录。"),
        ])
        with pytest.raises(RuntimeError):
            await AgentLoop(
                provider, _ctx(db, user.id, book.id, sample_data["cat_expense"].id),
                model=MODEL, timeout=5.0, today=TODAY, sink=_DiesOnTheToolBatch(),
            ).run("看看流水")

        rows = await _rows(db, conversation)
        # 第 1 步的用量已经写完，配对组在落库时被打断——尾巴仍是用户输入 + 用量，
        # **没有**悬空的调用或结果。
        assert [row.type for row in rows] == [EVENT_USER, EVENT_USAGE]
        assert _paired_prefix_len([(row.type, row.data) for row in rows]) == len(rows)
        # 恢复路径上模型拿到的历史同样是干净的（不变式 1 在恢复路径上成立）
        assert [m["role"] for m in await service.history(conversation)] == ["user"]

    async def test_history_trims_an_unpaired_tail_written_by_someone_else(
        self, db_session, sample_data,
    ):
        """读侧的兜底：日志尾巴若停在配对组中间（**别的写入方**——分叉切点、并发
        写同一会话的交叉、将来绕过 sink 的写路径），模型上下文只取到上一个安全点。

        正常写入路径下这一条是空转（批的边界就是配对组的边界，上一条用例钉住）；
        它防的是「写路径一定写得对」这个假设本身——模型上下文里出现孤立的
        ``tool_calls`` 会被 API 以 400 拒绝，而那个 400 在下一轮才炸、归因困难。
        """
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)
        db.add(AIConversationEvent(
            conversation_id=conversation.id, seq=1, turn=1, type=EVENT_USER,
            data={"role": "user", "content": "看看流水"},
        ))
        db.add(AIConversationEvent(
            conversation_id=conversation.id, seq=2, turn=1, type=EVENT_ASSISTANT,
            data={
                "role": "assistant", "content": "", "reasoning_content": "",
                "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "transaction_list", "arguments": "{}"},
                }],
            },
        ))
        await db.flush()

        assert [m["role"] for m in await service.history(conversation)] == ["user"]


class TestPairedPrefix:
    """:func:`_paired_prefix_len` 是配对不变式唯一的判据，分叉与读侧都用它。"""

    @staticmethod
    def _events(*items: tuple[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        return [(event_type, data) for event_type, data in items]

    def test_complete_group_returns_the_whole_prefix(self):
        events = self._events(
            (EVENT_USER, {"role": "user", "content": "看看流水"}),
            (EVENT_ASSISTANT, {"role": "assistant", "tool_calls": [{"id": "c1"}]}),
            (EVENT_TOOL, {"role": "tool", "tool_call_id": "c1", "content": "{}"}),
        )
        assert _paired_prefix_len(events) == 3

    def test_dangling_call_falls_back_to_the_last_safe_point(self):
        events = self._events(
            (EVENT_USER, {"role": "user", "content": "看看流水"}),
            (EVENT_ASSISTANT, {"role": "assistant", "tool_calls": [{"id": "c1"}]}),
        )
        assert _paired_prefix_len(events) == 1

    def test_partial_tool_batch_is_not_a_safe_point(self):
        """一个 Step 里的**全部**调用都要有结果才算配对完整。"""
        events = self._events(
            (EVENT_USER, {"role": "user", "content": "看看流水"}),
            (EVENT_ASSISTANT, {"role": "assistant", "tool_calls": [
                {"id": "c1"}, {"id": "c2"},
            ]}),
            (EVENT_TOOL, {"role": "tool", "tool_call_id": "c1", "content": "{}"}),
        )
        assert _paired_prefix_len(events) == 1

    def test_assistant_without_calls_closes_the_group(self):
        events = self._events(
            (EVENT_USER, {"role": "user", "content": "你好"}),
            (EVENT_ASSISTANT, {"role": "assistant", "content": "你好呀"}),
        )
        assert _paired_prefix_len(events) == 2

    def test_structural_events_do_not_change_the_pairing_state(self):
        """结构性事件（用量等）落在组的哪一侧都不影响判定——它们不是消息。"""
        events = self._events(
            (EVENT_USER, {"role": "user", "content": "你好"}),
            ("quota", {"tokens": 12}),
            (EVENT_ASSISTANT, {"role": "assistant", "content": "你好呀"}),
        )
        assert _paired_prefix_len(events) == 3

    def test_empty_prefix_is_safe(self):
        assert _paired_prefix_len([]) == 0


# ── 4. StopReason.CANCELLED 可达性复审 ───────────────────────────────────


class TestCancelledReachability:
    """P1 记的是「``CANCELLED`` 不可达；P2 的 03e 定义主动取消后应可达」。复审的
    结论是**分两级**：

    - **loop 层可达**（下面两条用例钉住）：显式 ``cancel()``、以及审批四值里的
      ``cancelled``（用户中止整个任务）。
    - **生产入口（``/ai/agent``）不可达**，且这是刻意的：服务端没有独立的取消
      端点，「断连即取消」走的是**取消请求任务**，loop 的 Step 入口检查点根本不会
      被走到——客户端断开时连一个 ``done``/``cancelled`` 帧都收不到（那条路径由
      ``test_agent_stream`` 的断连用例钉住：只剩已经落库的事件）。03e 已把「保持
      断连即取消」定为结论，04b 的逐事件落库没有重新打开它：断连时能留下的只是
      **已经产出的事件**，与「loop 自己收敛到 CANCELLED」是两回事。
    """

    async def test_explicit_cancel_stops_before_any_model_call(
        self, db_session, sample_data,
    ):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)
        provider = _ScriptedProvider([_text("不会被调用")])
        loop = AgentLoop(
            provider, _ctx(db, user.id, book.id, sample_data["cat_expense"].id),
            model=MODEL, timeout=5.0, today=TODAY,
            sink=TurnWriter(service, conversation),
        )
        loop.cancel()

        result = await loop.run("你好")

        assert result.stop_reason is StopReason.CANCELLED
        assert provider.calls == []
        # 取消不写收尾文案（`_GRACEFUL_STOPS` 不含它）：模型根本没被叫起来，
        # 没有可交代的「未完成」——日志里只剩那条用户输入。
        assert [row.type for row in await _rows(db, conversation)] == [EVENT_USER]

    async def test_approval_cancelled_ends_the_turn_without_executing(
        self, db_session, sample_data,
    ):
        """审批四值的 ``cancelled`` → 置位取消标志 → 下一个 Step 入口收敛为
        ``CANCELLED``；**零写**，且工具批次仍然配对完整（不变式 1 与 3 同时成立）。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)
        executed: list[Any] = []

        async def _approver(request: ApprovalRequest) -> ApprovalDecision:
            return ApprovalDecision.CANCELLED

        async def _executor(proposal: Any, *, confirmation_token: str) -> Any:
            executed.append(proposal)
            raise AssertionError("取消之后不该有任何执行")

        provider = _ScriptedProvider([
            _tool_turn(_call("transaction_list")),
            _tool_turn(_call("transaction_add", {
                "type": "expense", "amountCents": 1250,
                "categoryName": CATEGORY, "bizDate": "2026-09-18",
            })),
            _text("不该走到这里"),
        ])
        result = await AgentLoop(
            provider, _ctx(db, user.id, book.id, sample_data["cat_expense"].id),
            model=MODEL, timeout=5.0, today=TODAY,
            approver=_approver, executor=_executor,
            sink=TurnWriter(service, conversation),
        ).run("记一笔午饭")

        assert result.stop_reason is StopReason.CANCELLED
        assert executed == []
        assert result.proposals == ()
        # 配对完整：那条带 tool_calls 的 assistant 有对应的 tool 结果（不变式 1）
        rows = await _rows(db, conversation)
        assert [row.type for row in rows] == [
            EVENT_USER, EVENT_USAGE, EVENT_ASSISTANT, EVENT_TOOL,
            EVENT_USAGE, EVENT_ASSISTANT, EVENT_TOOL,
        ]
        assert _paired_prefix_len([(row.type, row.data) for row in rows]) == len(rows)
        # 取消不回一次收尾请求（02d 决策 8）
        assert len(provider.calls) == 2
