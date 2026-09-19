"""T-M7-04d: 会话事件日志保留期测试。

验收映射（`docs/milestones/T-M7-04-P3-implementation-plan.md` §三 04d）：
- 到期事件按策略删除、按**整轮**删（不变式 1 不被破坏） → TestPurgeExpiredEvents
- **不破坏 `AIUsage` 配额基座**（02f 的 `SET NULL` 口径）   → TestQuotaBaseIntact
- 只删事件、不删会话行（保留期 ≠ 删会话行）                 → TestConversationRowSurvives
- 长期不登出的用户其日志不再无限留存（任务形态 + 默认开关）  → TestRetentionDriver
- 读取面归属校验复审（03a 的四个端点）                      → TestOwnershipRecheck
- 保留期数值与开关的配置面                                 → `tests/test_config.py`

全部用例走真实 DB（内存 SQLite）+ 真实删除路径（`retention.purge_expired_events`，
经 `run_retention_sweep` 驱动）；读面断言（`history` / `events` / `load_owned`）用
真实 `ConversationService`。无网络、无模型。窗口与开关经 ``Settings(...)`` 注入，
不吃生产默认值。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

from src.common.config import Settings
from src.models.ai import AIConversation, AIConversationEvent, AIUsage
from src.models.user import User
from src.modules.ai import retention as retention_mod
from src.modules.ai.conversation import (
    EVENT_ASSISTANT,
    EVENT_TOOL,
    EVENT_USER,
    SURFACE_EVENT_TYPES,
    ConversationService,
)
from tests.fixtures_db import db_session, sample_data  # noqa: F401

#: 「现在」（UTC）与默认窗口。到期判定是 ``max(time) < now - days``。
NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
DAYS = 30
CUTOFF = NOW - timedelta(days=DAYS)

#: 明确早于 / 晚于 cutoff 的两个时刻（相隔一天，避开边界本身）。
LONG_AGO = CUTOFF - timedelta(days=1)
RECENT = CUTOFF + timedelta(days=1)


def _cfg(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": "sqlite+aiosqlite:///:memory:",
        "secret_key": "test-secret",
        "env": "test",
        "api_prefix": "",
        "ai_conversation_retention_days": DAYS,
    }
    base.update(overrides)
    return Settings(**base)


async def _conversation(session, data, *, title: str = "会话") -> AIConversation:
    conversation = AIConversation(
        user_id=data["user"].id, book_id=data["book"].id, title=title,
    )
    session.add(conversation)
    await session.flush()
    return conversation


def _event(
    conversation: AIConversation,
    *,
    seq: int,
    turn: int,
    at: datetime,
    type: str = EVENT_USER,
    data: dict[str, Any] | None = None,
) -> AIConversationEvent:
    """直接落一行事件——``time`` 由本层给定，正是保留期要判定的那个值。"""
    return AIConversationEvent(
        conversation_id=conversation.id,
        seq=seq,
        turn=turn,
        type=type,
        data=data if data is not None else {"role": type, "content": f"m{seq}"},
        time=at,
        ignorable=type not in SURFACE_EVENT_TYPES,
    )


async def _turn(
    session,
    conversation: AIConversation,
    *,
    turn: int,
    at: datetime,
    messages: list[tuple[str, dict[str, Any]]] | None = None,
) -> None:
    """落一「轮」：默认一条 user + 一条 assistant —— 消息与结构性事件各一条。"""
    base_seq = turn * 10
    rows = messages or [
        (EVENT_USER, {"role": "user", "content": f"t{turn} 问"}),
        (EVENT_ASSISTANT, {"role": "assistant", "content": f"t{turn} 答", "reasoning_content": ""}),
    ]
    session.add_all([
        _event(conversation, seq=base_seq + offset, turn=turn, at=at, type=t, data=d)
        for offset, (t, d) in enumerate(rows)
    ])
    await session.flush()


async def _events(session, conversation: AIConversation) -> list[AIConversationEvent]:
    return list(await session.scalars(
        select(AIConversationEvent)
        .where(AIConversationEvent.conversation_id == conversation.id)
        .order_by(AIConversationEvent.seq)
    ))


async def _sweep(session, cfg: Settings | None = None) -> dict[str, int]:
    return await retention_mod.run_retention_sweep(session, now=NOW, cfg=cfg or _cfg())


# ---------------------------------------------------------------------------
# 到期删除与整轮粒度
# ---------------------------------------------------------------------------

class TestPurgeExpiredEvents:
    async def test_expired_turn_is_deleted_fresh_turn_survives(
        self, db_session, sample_data,
    ):
        conv = await _conversation(db_session, sample_data)
        await _turn(db_session, conv, turn=1, at=LONG_AGO)
        await _turn(db_session, conv, turn=2, at=RECENT)

        result = await _sweep(db_session)

        remaining = await _events(db_session, conv)
        assert [e.turn for e in remaining] == [2, 2]
        assert result == {
            "retention_days": DAYS, "deleted_events": 2, "cleared_summaries": 0,
        }

    async def test_whole_conversation_expires_leaving_the_row(
        self, db_session, sample_data,
    ):
        """全部轮次都到期 → 事件一条不剩，但会话行还在（见 TestConversationRowSurvives）。"""
        conv = await _conversation(db_session, sample_data)
        await _turn(db_session, conv, turn=1, at=LONG_AGO)
        await _turn(db_session, conv, turn=2, at=LONG_AGO)

        deleted = (await _sweep(db_session))["deleted_events"]

        assert deleted == 4
        assert await _events(db_session, conv) == []

    async def test_turn_straddling_the_cutoff_is_kept_whole(
        self, db_session, sample_data,
    ):
        """窗口边界落在轮中间时**整轮保留**——保守方向，绝不把一轮切开。

        04b 改成逐事件落库后，同一轮的事件各带自己的 ``time``，跨线因此在真实
        数据上会出现；判定取 ``max(time)``，于是整轮留下。
        """
        conv = await _conversation(db_session, sample_data)
        db_session.add_all([
            # 第 1 轮：跨线（两条已过线、一条没过线）
            _event(conv, seq=10, turn=1, at=LONG_AGO,
                   data={"role": "user", "content": "更早"}),
            _event(conv, seq=11, turn=1, at=CUTOFF - timedelta(minutes=1),
                   data={"role": "user", "content": "线前"}),
            _event(conv, seq=12, turn=1, at=CUTOFF + timedelta(minutes=1),
                   type=EVENT_ASSISTANT,
                   data={"role": "assistant", "content": "线后", "reasoning_content": ""}),
            # 第 2 轮：整轮都在线之前
            _event(conv, seq=20, turn=2, at=LONG_AGO,
                   data={"role": "user", "content": "整轮到期"}),
            _event(conv, seq=21, turn=2, at=LONG_AGO, type=EVENT_ASSISTANT,
                   data={"role": "assistant", "content": "整轮到期", "reasoning_content": ""}),
        ])
        await db_session.flush()

        await _sweep(db_session)

        remaining = await _events(db_session, conv)
        assert [(e.turn, e.seq) for e in remaining] == [(1, 10), (1, 11), (1, 12)]

    async def test_tool_pairing_is_never_split(self, db_session, sample_data):
        """不变式 1：工具调用与结果同进同出，幸存事件流不以孤立 tool 结果开头。"""
        conv = await _conversation(db_session, sample_data)
        # 第 1 轮：普通问答，全到期
        await _turn(db_session, conv, turn=1, at=LONG_AGO)
        # 第 2 轮：带工具调用，全到期（assistant 的 tool_calls 与其 tool 结果同轮）
        await _turn(
            db_session, conv, turn=2, at=LONG_AGO,
            messages=[
                (EVENT_ASSISTANT, {
                    "role": "assistant", "content": "",
                    "reasoning_content": "",
                    "tool_calls": [{"id": "c1", "type": "function",
                                    "function": {"name": "tx_list", "arguments": "{}"}}],
                }),
                (EVENT_TOOL, {"role": "tool", "tool_call_id": "c1", "content": "[]"}),
            ],
        )
        # 第 3 轮：新鲜
        await _turn(db_session, conv, turn=3, at=RECENT)

        await _sweep(db_session)

        remaining = await _events(db_session, conv)
        assert {e.turn for e in remaining} == {3}
        assert remaining[0].type == EVENT_USER, "幸存事件流不得以孤立 tool 结果开头"

        # 模型上下文同样只含整轮
        service = ConversationService(db_session, _cfg())
        history = await service.history(conv)
        assert [m["role"] for m in history] == ["user", "assistant"]

    async def test_second_sweep_is_idempotent(self, db_session, sample_data):
        conv = await _conversation(db_session, sample_data)
        await _turn(db_session, conv, turn=1, at=LONG_AGO)
        await _turn(db_session, conv, turn=2, at=RECENT)

        first = await _sweep(db_session)
        second = await _sweep(db_session)

        assert first["deleted_events"] == 2
        assert second["deleted_events"] == 0
        assert len(await _events(db_session, conv)) == 2

    async def test_other_conversations_are_untouched(self, db_session, sample_data):
        expired = await _conversation(db_session, sample_data, title="旧会话")
        fresh = await _conversation(db_session, sample_data, title="新会话")
        await _turn(db_session, expired, turn=1, at=LONG_AGO)
        await _turn(db_session, fresh, turn=1, at=RECENT)

        await _sweep(db_session)

        assert await _events(db_session, expired) == []
        assert len(await _events(db_session, fresh)) == 2

    async def test_conversation_without_events_is_a_noop(self, db_session, sample_data):
        """建了行、一轮都没写成的会话（浏览器/联调常见）不该被清扫碰到。"""
        conv = await _conversation(db_session, sample_data)

        result = await _sweep(db_session)

        assert result["deleted_events"] == 0
        assert await _events(db_session, conv) == []

    async def test_boundary_is_strict(self, db_session, sample_data):
        """恰好等于 cutoff 的轮次保留（``max(time) < cutoff`` 是严格小于）。"""
        conv = await _conversation(db_session, sample_data)
        await _turn(db_session, conv, turn=1, at=CUTOFF)
        await _turn(db_session, conv, turn=2, at=CUTOFF - timedelta(microseconds=1))

        await _sweep(db_session)

        assert [e.turn for e in await _events(db_session, conv)] == [1, 1]

    async def test_purge_entry_point_returns_the_deleted_row_count(
        self, db_session, sample_data,
    ):
        """公开入口的直接契约：``purge_expired_events(db, cutoff)`` 返回删掉的**行数**。

        （``run_retention_sweep`` 的返回值是给日志/监控用的另一个形状；这一条钉的是
        删除函数本身——PG 取证脚本用的也是这个入口。）
        """
        conv = await _conversation(db_session, sample_data)
        await _turn(db_session, conv, turn=1, at=LONG_AGO)
        await _turn(db_session, conv, turn=2, at=RECENT)

        assert await retention_mod.purge_expired_events(db_session, CUTOFF) == 2
        assert await retention_mod.purge_expired_events(db_session, CUTOFF) == 0

    async def test_sweep_leaves_the_transaction_to_the_caller(
        self, db_session, sample_data,
    ):
        """清扫**不 commit**——事务边界归调度方（回滚即恢复，证明没偷偷提交）。"""
        conv = await _conversation(db_session, sample_data)
        await _turn(db_session, conv, turn=1, at=LONG_AGO)
        conv_id = conv.id
        await db_session.commit()

        await _sweep(db_session)
        await db_session.rollback()

        # rollback 会把 ORM 对象整体 expire（再摸属性就是同步 IO），故按 id 直接数。
        count = await db_session.scalar(
            select(func.count()).select_from(AIConversationEvent)
            .where(AIConversationEvent.conversation_id == conv_id)
        )
        assert count == 2

    async def test_configured_window_is_honoured(self, db_session, sample_data):
        """窗口取配置：365 天时 30 天前的事件不该被删。"""
        conv = await _conversation(db_session, sample_data)
        await _turn(db_session, conv, turn=1, at=LONG_AGO)

        result = await _sweep(db_session, _cfg(ai_conversation_retention_days=365))

        assert result == {
            "retention_days": 365, "deleted_events": 0, "cleared_summaries": 0,
        }
        assert len(await _events(db_session, conv)) == 2


# ---------------------------------------------------------------------------
# 配额基座（02f 的 SET NULL 口径）
# ---------------------------------------------------------------------------

class TestQuotaBaseIntact:
    async def _seed_usage(self, session, data, conv, *, turns: int = 3) -> None:
        session.add_all([
            AIUsage(
                user_id=data["user"].id,
                conversation_id=conv.id,
                provider="deepseek",
                model="deepseek-flash",
                tokens=1000,
                request_id=f"req-{i}",
            )
            for i in range(turns)
        ])
        await session.flush()

    async def test_usage_rows_survive_with_their_conversation_id(
        self, db_session, sample_data,
    ):
        conv = await _conversation(db_session, sample_data)
        await _turn(db_session, conv, turn=1, at=LONG_AGO)
        await self._seed_usage(db_session, sample_data, conv)

        await _sweep(db_session)

        rows = list(await db_session.scalars(
            select(AIUsage).where(AIUsage.user_id == sample_data["user"].id)
        ))
        assert len(rows) == 3
        # 关键：外键**没有**被置空——置空即会话级配额被绕过。
        assert all(r.conversation_id == conv.id for r in rows)

    async def test_conversation_quota_aggregate_is_unchanged(
        self, db_session, sample_data,
    ):
        """会话级「轮数 + token」双限在清扫前后完全一致（用户刷不回满额）。"""
        conv = await _conversation(db_session, sample_data)
        await _turn(db_session, conv, turn=1, at=LONG_AGO)
        await self._seed_usage(db_session, sample_data, conv)

        service = ConversationService(db_session, _cfg())
        before = (
            await db_session.scalar(
                select(func.count()).select_from(AIUsage)
                .where(AIUsage.conversation_id == conv.id)
            ),
            await db_session.scalar(
                select(func.coalesce(func.sum(AIUsage.tokens), 0))
                .where(AIUsage.conversation_id == conv.id)
            ),
        )

        await _sweep(db_session)

        after = (
            await db_session.scalar(
                select(func.count()).select_from(AIUsage)
                .where(AIUsage.conversation_id == conv.id)
            ),
            await db_session.scalar(
                select(func.coalesce(func.sum(AIUsage.tokens), 0))
                .where(AIUsage.conversation_id == conv.id)
            ),
        )
        assert before == after == (3, 3000)
        # 配额门也不该因为清扫而变化
        await service.check_quota(conv)

    async def test_expired_events_do_not_touch_other_users_usage(
        self, db_session, sample_data,
    ):
        conv = await _conversation(db_session, sample_data)
        await _turn(db_session, conv, turn=1, at=LONG_AGO)
        await self._seed_usage(db_session, sample_data, conv)

        other = AIUsage(
            user_id=sample_data["user"].id,
            conversation_id=None,           # 快路径的行（无会话）
            provider="dashscope",
            model="qwen3.7-flash",
            tokens=42,
            request_id="req-fast",
        )
        db_session.add(other)
        await db_session.flush()

        await _sweep(db_session)
        await db_session.refresh(other)

        assert other.tokens == 42
        assert other.conversation_id is None


# ---------------------------------------------------------------------------
# 会话行与读面
# ---------------------------------------------------------------------------

class TestConversationRowSurvives:
    async def test_row_is_still_readable_and_listed_after_expiry(
        self, db_session, sample_data,
    ):
        """保留期 ≠ 删会话行：行仍在列表里、仍可 load_owned（不是 404）。"""
        conv = await _conversation(db_session, sample_data, title="去年的会话")
        await _turn(db_session, conv, turn=1, at=LONG_AGO)

        await _sweep(db_session)

        service = ConversationService(db_session, _cfg())
        loaded = await service.load_owned(sample_data["user"], conv.id)
        assert loaded.id == conv.id
        assert loaded.title == "去年的会话"
        listed = await service.list_conversations(sample_data["user"])
        assert [c.id for c in listed] == [conv.id]

    async def test_event_read_returns_the_pruned_log_not_a_404(
        self, db_session, sample_data,
    ):
        """读面（投影重建源）到期后回**变短的**事件流，不是 404、也不是空。"""
        conv = await _conversation(db_session, sample_data)
        await _turn(db_session, conv, turn=1, at=LONG_AGO)
        await _turn(db_session, conv, turn=2, at=RECENT)

        await _sweep(db_session)

        service = ConversationService(db_session, _cfg())
        events = await service.events(await service.load_owned(sample_data["user"], conv.id))
        assert [e.turn for e in events] == [2, 2]
        # 增量读（断线重连续读）仍按 seq 严格大于工作
        assert [e.seq for e in await service.events(conv, after_seq=events[0].seq)] == [21]


# ---------------------------------------------------------------------------
# 调度形态
# ---------------------------------------------------------------------------

class _StubSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class _StubFactory:
    def __init__(self) -> None:
        self.session = _StubSession()

    def __call__(self):
        session = self.session

        class _Ctx:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()


class TestRetentionDriver:
    async def test_loop_sweeps_immediately_then_survives_failures(
        self, monkeypatch,
    ):
        """常驻循环：启动即扫一次；单次失败不杀循环（下个 tick 重试）。"""
        calls: list[int] = []

        async def fake_sweep(session, **kwargs):
            calls.append(len(calls))
            if len(calls) == 2:
                raise RuntimeError("boom")
            return {"retention_days": DAYS, "deleted_events": 0}

        monkeypatch.setattr(retention_mod, "run_retention_sweep", fake_sweep)
        factory = _StubFactory()
        task = asyncio.create_task(
            retention_mod.retention_loop(factory, check_interval_seconds=0),
        )
        try:
            for _ in range(200):
                if len(calls) >= 3:
                    break
                await asyncio.sleep(0.01)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert len(calls) >= 3, "失败后循环必须继续（原范式口径）"
        assert factory.session.commits >= 2

    async def test_loop_propagates_cancellation(self, monkeypatch):
        async def fake_sweep(session, **kwargs):
            return {"retention_days": DAYS, "deleted_events": 0}

        monkeypatch.setattr(retention_mod, "run_retention_sweep", fake_sweep)
        task = asyncio.create_task(
            retention_mod.retention_loop(_StubFactory(), check_interval_seconds=3600),
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    def test_enabled_flag_defaults_off(self):
        """默认关闭只是「测试进程不起后台任务」——部署侧必须显式开启。"""
        assert _cfg().ai_conversation_retention_enabled is False
        assert _cfg(ai_conversation_retention_enabled=True).ai_conversation_retention_enabled


# ---------------------------------------------------------------------------
# 与 04b 的交汇面：分叉复制的前缀带**父会话原 time**
# ---------------------------------------------------------------------------

class TestForkInteraction:
    """04b 的 `fork` 按原值复制前缀（含 ``time``），因此保留期对分叉**无需特判**：

    已过期的那部分前缀，在新会话里同样过期，随下一次清扫一起消失。**这是刻意的**——
    若复制时盖成 ``now``，分叉就成了「把 30 天前的正文洗成新的」的绕过手段，保留期
    承诺（服务端最多持有 30 天正文）会当场失效。代价写进 security.md 的口径：分叉的
    实际可用历史＝保留期窗口内那部分，对已过期的会话分叉得到的是「有标题、无正文」的
    会话（模型侧无上下文）。
    """

    async def test_fork_keeps_original_times_so_old_prefix_expires_too(
        self, db_session, sample_data,
    ):
        conv = await _conversation(db_session, sample_data)
        await _turn(db_session, conv, turn=1, at=LONG_AGO)   # seq 10/11，已过期
        await _turn(db_session, conv, turn=2, at=RECENT)     # seq 20/21，未过期
        service = ConversationService(db_session, _cfg())

        whole_fork, _ = await service.fork(conv, upto_seq=21)   # 前缀含两轮
        old_fork, _ = await service.fork(conv, upto_seq=11)     # 前缀只有已过期那轮

        await _sweep(db_session)

        # 连「整段都分叉」的那个也只留下窗口内的那一轮——前缀的原时间戳生效
        assert [e.turn for e in await _events(db_session, whole_fork)] == [2, 2]
        assert await _events(db_session, old_fork) == []
        # 保留期只删事件：两个分叉的**会话行**都还在（父会话也一样）
        for forked in (whole_fork, old_fork, conv):
            assert (await service.load_owned(sample_data["user"], forked.id)).id == forked.id
        # 模型上下文同理：过期前缀不进窗口
        assert [m["role"] for m in await service.history(whole_fork)] == ["user", "assistant"]

    async def test_a_fresh_fork_prefix_survives_intact(self, db_session, sample_data):
        """窗口内的前缀一分不少——分叉在保留期内是完整的。"""
        conv = await _conversation(db_session, sample_data)
        await _turn(db_session, conv, turn=1, at=RECENT)
        service = ConversationService(db_session, _cfg())

        forked, cut_seq = await service.fork(conv, upto_seq=11)

        await _sweep(db_session)

        assert cut_seq == 11
        assert [e.turn for e in await _events(db_session, forked)] == [1, 1]


# ---------------------------------------------------------------------------
# 归属校验复审（03a 四个端点）
# ---------------------------------------------------------------------------

class TestOwnershipRecheck:
    async def test_cross_user_read_of_a_pruned_conversation_is_still_404(
        self, db_session, sample_data,
    ):
        """本卡复审：清扫不改归属语义——别人的 id 仍是 404（不是 403、不是空列表）。"""
        conv = await _conversation(db_session, sample_data)
        await _turn(db_session, conv, turn=1, at=LONG_AGO)
        await _sweep(db_session)

        intruder = User(id=uuid.uuid4(), nickname="intruder")
        db_session.add(intruder)
        await db_session.flush()

        service = ConversationService(db_session, _cfg())
        from src.common.errors import AppException

        with pytest.raises(AppException) as err:
            await service.load_owned(intruder, conv.id)
        assert err.value.status_code == 404
        # 扫地也不该把别人的会话「扫进」我的列表
        assert await service.list_conversations(intruder) == []
