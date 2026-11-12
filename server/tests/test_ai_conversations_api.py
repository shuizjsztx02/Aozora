"""T-M7-03a: 会话索引服务端化与事件读取 API 测试。

验收映射（``docs/milestones/production-execution-plan.md`` T-M7-03a）：

- 列表只返回本人且按活跃排序      → :class:`TestListConversations`
- 重命名持久化、跨用户一律 404    → :class:`TestRenameConversation`
- 删除幂等且事件行级联清理        → :class:`TestDeleteConversation`
- ``events(after_seq)`` 增量读取  → :class:`TestConversationEvents`
- 登出 / 删号行为与结论一致       → :class:`TestLogoutGranularity`
- 标题派生（首轮输入）与不被覆盖  → :class:`TestTitles`

两条通道各管一段：**HTTP** 走真实鉴权与四个新端点；**DB** 直接造会话 / 事件行，
用例因此快而确定。只有需要「真实事件」的用例才走 ``/ai/agent`` 跑一轮——那里用
脚本化 provider，无网络。

临时库用 ``tempfile.mkdtemp`` 而不是 ``tmp_path``：本机 pytest basetemp 权限已坏
（见 ``pytest-basetemp-broken`` 记录），CI 上两者等价。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from src.common.config import Settings, reset_config, set_config
from src.models.ai import AIConversation, AIConversationEvent, AIUsage
from src.modules.ai.conversation import TITLE_MAX_CHARS
from src.modules.ai.provider import ProviderResult, StreamChunk

MODEL = "deepseek-flash"
DEVICE = "conv-api-device-1"
OTHER_DEVICE = "conv-api-device-2"
BOOK = "日常"

#: :meth:`ConversationEnv.seed` 的哨兵：区分「没传账本」与「显式不带账本」。
_DEFAULT = object()


# ── SSE 与脚本化 provider ─────────────────────────────────────────────────


def _frames(body: str) -> list[tuple[str, dict[str, Any]]]:
    """SSE 正文 → ``[(事件名, 载荷)]``。注释帧（heartbeat）整体忽略。"""
    frames: list[tuple[str, dict[str, Any]]] = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block or block.startswith(":"):
            continue
        name = block.split("\n", 1)[0].removeprefix("event: ").strip()
        frames.append((name, json.loads(block.split("data: ", 1)[1])))
    return frames


def _done(body: str) -> dict[str, Any]:
    """一轮的 ``done`` 载荷（会话 id、用量、停因都在里面）。"""
    return next(payload for name, payload in _frames(body) if name == "done")


class StubProvider:
    """固定应答的 agent provider。

    本卡不测 loop 本身，只要它产出**一轮真实事件**（``user`` + ``assistant``，
    带 seq / turn）。应答逐次取用，用尽后重复最后一条。
    """

    name = "stub"

    def __init__(self, replies: list[str] | None = None) -> None:
        self.replies = list(replies or ["好的，记下了。"])
        self.calls: list[list[dict[str, Any]]] = []

    def _next(self) -> ProviderResult:
        index = min(len(self.calls) - 1, len(self.replies) - 1)
        return ProviderResult(
            content=self.replies[index], prompt_tokens=5, completion_tokens=7,
        )

    async def complete_with_tools(self, messages: Any, **_: Any) -> ProviderResult:
        self.calls.append(messages)
        return self._next()

    async def complete_with_tools_stream(self, messages: Any, **_: Any):
        self.calls.append(messages)
        result = self._next()
        yield StreamChunk(delta=result.content)
        yield StreamChunk(result=result)


# ── 环境 ─────────────────────────────────────────────────────────────────


class ConversationEnv:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)

    # -- HTTP ----------------------------------------------------------

    async def list_(self, headers: dict[str, str] | None = None) -> httpx.Response:
        return await self.client.get(
            "/ai/conversations", headers=headers or self.auth_headers,
        )

    async def rename(
        self,
        conversation_id: str | uuid.UUID,
        title: str,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        return await self.client.patch(
            f"/ai/conversations/{conversation_id}",
            json={"title": title},
            headers=headers or self.auth_headers,
        )

    async def delete(
        self, conversation_id: str | uuid.UUID,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        return await self.client.delete(
            f"/ai/conversations/{conversation_id}",
            headers=headers or self.auth_headers,
        )

    async def events(
        self,
        conversation_id: str | uuid.UUID,
        after_seq: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        params = {} if after_seq is None else {"after_seq": after_seq}
        return await self.client.get(
            f"/ai/conversations/{conversation_id}/events",
            params=params,
            headers=headers or self.auth_headers,
        )

    async def turn(
        self, text: str, *, conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """跑一轮 agent 并返回 ``done`` 载荷（非 200 直接失败）。"""
        body: dict[str, Any] = {"input": text, "book_id": self.book_id}
        if conversation_id:
            body["conversation_id"] = str(conversation_id)
        response = await self.client.post(
            "/ai/agent", json=body, headers=self.auth_headers,
        )
        assert response.status_code == 200, response.text
        return _done(response.text)

    async def fork(
        self, conversation_id: str | uuid.UUID, seq: int,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        return await self.client.post(
            f"/ai/conversations/{conversation_id}/fork",
            json={"seq": seq},
            headers=headers or self.auth_headers,
        )

    async def logout(self) -> httpx.Response:
        return await self.client.post(
            "/auth/logout",
            json={"refresh_token": self.refresh_token, "device_id": DEVICE},
        )

    # -- 直接造数据 -----------------------------------------------------

    async def seed(
        self,
        *,
        user_id: uuid.UUID | None = None,
        title: str = "",
        book_id: Any = _DEFAULT,
        last_active_at: datetime | None = None,
        archived_at: datetime | None = None,
        messages: tuple[tuple[str, dict[str, Any]], ...] = (),
    ) -> uuid.UUID:
        """插一行会话（可选带事件）。事件 ``seq`` / ``turn`` 从 1 起、逐条递增。"""
        async with self.factory() as session:
            conversation = AIConversation(
                id=uuid.uuid4(),
                user_id=user_id or self.user_id,
                book_id=uuid.UUID(self.book_id) if book_id is _DEFAULT else book_id,
                title=title,
                last_active_at=last_active_at,
                archived_at=archived_at,
            )
            session.add(conversation)
            await session.flush()
            for offset, (event_type, data) in enumerate(messages, start=1):
                session.add(AIConversationEvent(
                    conversation_id=conversation.id,
                    seq=offset,
                    turn=offset,
                    type=event_type,
                    data=data,
                ))
            await session.commit()
            return conversation.id

    async def rows(self, model: Any, **filters: Any) -> list[Any]:
        async with self.factory() as session:
            stmt = select(model)
            for column, value in filters.items():
                stmt = stmt.where(getattr(model, column) == value)
            return list((await session.scalars(stmt)).all())


@pytest_asyncio.fixture()
async def env():
    """临时文件库 + 两个 guest（本人 / 他人）+ 一个账本 + 脚本化 provider。"""
    from src.common.database import get_engine, get_session_factory, reset_engine
    from src.main import app
    from src.models.base import Base
    from src.modules.ai.provider import get_agent_provider
    from src.modules.ai.service import _ai_rate_limits
    from src.modules.auth.dependencies import _rate_limits

    reset_config()
    _rate_limits.clear()
    _ai_rate_limits.clear()

    db_dir = Path(tempfile.mkdtemp(prefix="aozora_test_conv_api_"))
    cfg = Settings(
        database_url=f"sqlite+aiosqlite:///{(db_dir / 'test.db').as_posix()}",
        secret_key="test-secret",
        env="test",
        api_prefix="",
        ai_agent_enabled=True,
        ai_agent_provider_api_key=None,
        ai_agent_provider_model=MODEL,
        ai_daily_quota_guest=20,
    )
    set_config(cfg)
    reset_engine()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    provider = StubProvider()
    app.dependency_overrides[get_agent_provider] = lambda: provider

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async def _guest(device: str, nickname: str) -> dict[str, Any]:
            response = await client.post(
                "/auth/guest", json={"device_id": device, "nickname": nickname},
            )
            assert response.status_code == 200, response.text
            return response.json()

        mine = await _guest(DEVICE, "conv-tester")
        other = await _guest(OTHER_DEVICE, "other-tester")
        auth_headers = {"Authorization": f"Bearer {mine['tokens']['access_token']}"}
        other_headers = {"Authorization": f"Bearer {other['tokens']['access_token']}"}

        book = await client.post("/books", json={"name": BOOK}, headers=auth_headers)
        assert book.status_code == 201, book.text

        yield ConversationEnv(
            app=app,
            client=client,
            cfg=cfg,
            provider=provider,
            engine=engine,
            factory=get_session_factory(),
            user_id=uuid.UUID(mine["user_id"]),
            book_id=book.json()["id"],
            auth_headers=auth_headers,
            refresh_token=mine["tokens"]["refresh_token"],
            other_user_id=uuid.UUID(other["user_id"]),
            other_headers=other_headers,
        )

    app.dependency_overrides.clear()
    await engine.dispose()
    reset_config()
    reset_engine()
    _rate_limits.clear()
    _ai_rate_limits.clear()
    shutil.rmtree(db_dir, ignore_errors=True)


# ── 1. 列表 ───────────────────────────────────────────────────────────────


class TestListConversations:
    async def test_returns_own_conversations_only(self, env: ConversationEnv):
        mine = await env.seed(title="我的")
        theirs = await env.seed(
            user_id=env.other_user_id, book_id=None, title="别人的",
        )

        response = await env.list_()
        assert response.status_code == 200
        ids = [c["id"] for c in response.json()["conversations"]]
        assert ids == [str(mine)]
        assert str(theirs) not in ids

    async def test_orders_by_last_active_desc(self, env: ConversationEnv):
        old = await env.seed(title="旧", last_active_at=datetime(2026, 9, 17, tzinfo=UTC))
        new = await env.seed(title="新", last_active_at=datetime(2026, 9, 19, tzinfo=UTC))
        mid = await env.seed(title="中", last_active_at=datetime(2026, 9, 18, tzinfo=UTC))

        payload = (await env.list_()).json()
        assert [c["id"] for c in payload["conversations"]] == [
            str(new), str(mid), str(old),
        ]

    async def test_never_used_conversation_sorts_by_created_at(self, env: ConversationEnv):
        """建了行、一轮都没写成的会话 ``last_active_at`` 是 NULL——按 ``created_at``
        兜底排序，且响应里该字段一定有值（客户端不必处理 NULL）。"""
        fresh = await env.seed(title="新建")
        used = await env.seed(
            title="很久以前聊过", last_active_at=datetime(2020, 1, 1, tzinfo=UTC),
        )

        conversations = (await env.list_()).json()["conversations"]
        assert [c["id"] for c in conversations] == [str(fresh), str(used)]
        assert conversations[0]["last_active_at"] is not None

    async def test_hides_archived_conversations(self, env: ConversationEnv):
        kept = await env.seed(title="留着")
        await env.seed(title="删了", archived_at=datetime(2026, 9, 19, tzinfo=UTC))

        ids = [c["id"] for c in (await env.list_()).json()["conversations"]]
        assert ids == [str(kept)]

    async def test_summary_shape(self, env: ConversationEnv):
        conversation_id = await env.seed(title="带账本")

        item = (await env.list_()).json()["conversations"][0]
        assert item["id"] == str(conversation_id)
        assert item["title"] == "带账本"
        assert item["book_id"] == env.book_id
        assert item["created_at"] and item["last_active_at"]

    async def test_conversation_without_book_lists_with_null_book(self, env: ConversationEnv):
        await env.seed(title="无账本", book_id=None)
        assert (await env.list_()).json()["conversations"][0]["book_id"] is None

    async def test_empty_for_new_account(self, env: ConversationEnv):
        assert (await env.list_()).json()["conversations"] == []

    async def test_requires_authentication(self, env: ConversationEnv):
        assert (await env.client.get("/ai/conversations")).status_code == 401


# ── 2. 重命名 ─────────────────────────────────────────────────────────────


class TestRenameConversation:
    async def test_rename_persists(self, env: ConversationEnv):
        conversation_id = await env.seed(title="原标题")

        response = await env.rename(conversation_id, "新标题")
        assert response.status_code == 200
        assert response.json()["title"] == "新标题"

        # 落库了（不是只回显），且列表里也是新名字
        rows = await env.rows(AIConversation, id=conversation_id)
        assert rows[0].title == "新标题"
        assert (await env.list_()).json()["conversations"][0]["title"] == "新标题"

    async def test_rename_strips_surrounding_space(self, env: ConversationEnv):
        conversation_id = await env.seed()
        response = await env.rename(conversation_id, "  周末计划  ")
        assert response.json()["title"] == "周末计划"

    async def test_rename_does_not_move_conversation_in_list(self, env: ConversationEnv):
        """活跃时间是列表的排序键，重命名**不该**动它——否则改个名字会话就跳到最前。"""
        old = await env.seed(title="旧", last_active_at=datetime(2026, 9, 17, tzinfo=UTC))
        new = await env.seed(title="新", last_active_at=datetime(2026, 9, 19, tzinfo=UTC))
        before = [c["id"] for c in (await env.list_()).json()["conversations"]]
        assert before == [str(new), str(old)]

        await env.rename(old, "改个名")

        rows = await env.rows(AIConversation, id=old)
        assert rows[0].last_active_at.replace(tzinfo=UTC) == datetime(2026, 9, 17, tzinfo=UTC)
        assert [c["id"] for c in (await env.list_()).json()["conversations"]] == before

    async def test_other_users_conversation_is_404(self, env: ConversationEnv):
        theirs = await env.seed(user_id=env.other_user_id, book_id=None, title="别人的")

        response = await env.rename(theirs, "偷改")
        assert response.status_code == 404
        assert response.json()["code"] == "conversation_not_found"
        assert (await env.rows(AIConversation, id=theirs))[0].title == "别人的"

    async def test_archived_conversation_is_404(self, env: ConversationEnv):
        conversation_id = await env.seed(archived_at=datetime(2026, 9, 19, tzinfo=UTC))
        assert (await env.rename(conversation_id, "改名")).status_code == 404

    async def test_unknown_id_is_404(self, env: ConversationEnv):
        assert (await env.rename(uuid.uuid4(), "改名")).status_code == 404

    async def test_blank_title_is_422(self, env: ConversationEnv):
        conversation_id = await env.seed()
        assert (await env.rename(conversation_id, "   ")).status_code == 422

    async def test_too_long_title_is_422(self, env: ConversationEnv):
        conversation_id = await env.seed()
        assert (await env.rename(conversation_id, "标题" * 61)).status_code == 422


# ── 3. 删除 ───────────────────────────────────────────────────────────────


class TestDeleteConversation:
    async def test_delete_archives_row_and_clears_events(self, env: ConversationEnv):
        """行留下（审计链 / 配额基座的锚点），正文删掉（用户删了就不该再持有）。"""
        conversation_id = await env.seed(messages=(
            ("user", {"role": "user", "content": "你好"}),
            ("assistant", {
                "role": "assistant", "content": "你好呀", "reasoning_content": "",
            }),
        ))

        assert (await env.delete(conversation_id)).status_code == 204

        rows = await env.rows(AIConversation, id=conversation_id)
        assert len(rows) == 1 and rows[0].archived_at is not None
        assert await env.rows(AIConversationEvent, conversation_id=conversation_id) == []

    async def test_deleted_conversation_leaves_the_list(self, env: ConversationEnv):
        conversation_id = await env.seed(title="要删的")
        await env.delete(conversation_id)
        assert (await env.list_()).json()["conversations"] == []

    async def test_second_delete_is_404_and_changes_nothing(self, env: ConversationEnv):
        """幂等：重试拿到与「不是我的 id」同一个 404，且不再产生副作用。"""
        conversation_id = await env.seed()
        assert (await env.delete(conversation_id)).status_code == 204
        archived_at = (await env.rows(AIConversation, id=conversation_id))[0].archived_at

        response = await env.delete(conversation_id)
        assert response.status_code == 404
        assert response.json()["code"] == "conversation_not_found"
        assert (await env.rows(AIConversation, id=conversation_id))[0].archived_at == archived_at

    async def test_other_users_conversation_is_404_and_untouched(self, env: ConversationEnv):
        theirs = await env.seed(
            user_id=env.other_user_id, book_id=None,
            messages=(("user", {"role": "user", "content": "别人的"}),),
        )

        assert (await env.delete(theirs)).status_code == 404
        assert (await env.rows(AIConversation, id=theirs))[0].archived_at is None
        assert len(await env.rows(AIConversationEvent, conversation_id=theirs)) == 1

    async def test_deleted_id_is_rejected_by_agent(self, env: ConversationEnv):
        """删除后旧 id 不能再跑轮次——客户端据此丢弃它、开新会话。"""
        conversation_id = await env.seed()
        await env.delete(conversation_id)

        response = await env.client.post(
            "/ai/agent",
            json={
                "input": "还在吗",
                "book_id": env.book_id,
                "conversation_id": str(conversation_id),
            },
            headers=env.auth_headers,
        )
        assert response.status_code == 404
        assert response.json()["code"] == "conversation_not_found"

    async def test_delete_keeps_usage_rows(self, env: ConversationEnv):
        """会话行留下的理由之一：``ai_usage`` 是配额的计数基座，删除不该动它。"""
        done = await env.turn("记一笔早饭")
        conversation_id = uuid.UUID(done["conversation_id"])
        assert len(await env.rows(AIUsage, conversation_id=conversation_id)) == 1

        await env.delete(conversation_id)

        usage = await env.rows(AIUsage, conversation_id=conversation_id)
        assert len(usage) == 1


# ── 4. 事件读取 ───────────────────────────────────────────────────────────


class TestConversationEvents:
    async def test_events_round_trip_a_real_turn(self, env: ConversationEnv):
        done = await env.turn("午饭花了 12 块")
        conversation_id = done["conversation_id"]

        payload = (await env.events(conversation_id)).json()
        assert payload["conversation_id"] == conversation_id
        events = payload["events"]
        # 结构性事件与消息一样读得到（投影 B **不过滤**）：M8-12 起每次模型调用
        # 一条 ``usage``（这一步的用量），轮末再加一条 ``quota``（轮合计）——
        # 三者同轮（``same_turn``）。
        assert [e["seq"] for e in events] == [1, 2, 3, 4]
        assert [e["type"] for e in events] == ["user", "usage", "assistant", "quota"]
        assert [e["turn"] for e in events] == [1, 1, 1, 1]
        assert events[0]["data"] == {"role": "user", "content": "午饭花了 12 块"}
        assert events[2]["data"]["content"] == "好的，记下了。"
        assert events[1]["data"]["kind"] == "step"
        assert events[1]["data"]["step"] == 1
        # 客户端拿这些事件**重建投影**，所以形状就是契约：assistant 消息一定带
        # ``reasoning_content`` 键（02f 的写入面硬约束在读取面上同样成立）——缺键
        # 的消息会让下一次 thinking 请求整轮 400，客户端靠这个键把推理分流进折叠区。
        assert events[2]["data"]["reasoning_content"] == ""

    async def test_after_seq_reads_incrementally(self, env: ConversationEnv):
        done = await env.turn("第一轮")
        conversation_id = done["conversation_id"]
        first_batch = (await env.events(conversation_id)).json()["events"]
        last_seq = first_batch[-1]["seq"]

        await env.turn("第二轮", conversation_id=conversation_id)

        delta = (await env.events(conversation_id, after_seq=last_seq)).json()["events"]
        assert [e["seq"] for e in delta] == [
            last_seq + 1, last_seq + 2, last_seq + 3, last_seq + 4,
        ]
        assert all(e["turn"] == 2 for e in delta)
        # 严格大于：同一个 after_seq 再拉一次得到同一批，不重复
        assert (await env.events(conversation_id, after_seq=last_seq)).json()["events"] == delta

    async def test_full_read_returns_every_turn(self, env: ConversationEnv):
        done = await env.turn("第一轮")
        conversation_id = done["conversation_id"]
        await env.turn("第二轮", conversation_id=conversation_id)

        events = (await env.events(conversation_id)).json()["events"]
        # 两条消息 + 一条分步用量 + 一条同轮合计用量，每轮四行（M8-12）。
        assert [e["turn"] for e in events] == [1, 1, 1, 1, 2, 2, 2, 2]

    async def test_events_of_fresh_conversation_are_empty(self, env: ConversationEnv):
        conversation_id = await env.seed()
        payload = (await env.events(conversation_id)).json()
        assert payload["events"] == []

    async def test_other_users_events_are_404(self, env: ConversationEnv):
        theirs = await env.seed(
            user_id=env.other_user_id, book_id=None,
            messages=(("user", {"role": "user", "content": "秘密"}),),
        )

        response = await env.events(theirs)
        assert response.status_code == 404
        assert response.json()["code"] == "conversation_not_found"

    async def test_archived_conversation_events_are_404(self, env: ConversationEnv):
        conversation_id = await env.seed(archived_at=datetime(2026, 9, 19, tzinfo=UTC))
        assert (await env.events(conversation_id)).status_code == 404

    async def test_unknown_id_is_404(self, env: ConversationEnv):
        assert (await env.events(uuid.uuid4())).status_code == 404

    async def test_negative_after_seq_is_422(self, env: ConversationEnv):
        conversation_id = await env.seed()
        assert (await env.events(conversation_id, after_seq=-1)).status_code == 422


# ── 5. 标题 ───────────────────────────────────────────────────────────────


class TestTitles:
    async def test_title_derived_from_first_user_input(self, env: ConversationEnv):
        done = await env.turn("帮我看看这个月的预算")
        rows = await env.rows(AIConversation, id=uuid.UUID(done["conversation_id"]))
        assert rows[0].title == "帮我看看这个月的预算"

    async def test_long_input_is_truncated(self, env: ConversationEnv):
        text = "记一笔" + "很长的描述" * 20
        done = await env.turn(text)

        title = (await env.rows(
            AIConversation, id=uuid.UUID(done["conversation_id"]),
        ))[0].title
        assert title == text[:TITLE_MAX_CHARS] + "…"

    async def test_multiline_input_collapses_to_one_line(self, env: ConversationEnv):
        done = await env.turn("第一行\n第二行")
        title = (await env.rows(
            AIConversation, id=uuid.UUID(done["conversation_id"]),
        ))[0].title
        assert title == "第一行 第二行"

    async def test_title_comes_from_first_turn_only(self, env: ConversationEnv):
        done = await env.turn("第一轮说的话")
        conversation_id = done["conversation_id"]
        await env.turn("第二轮说的话", conversation_id=conversation_id)

        rows = await env.rows(AIConversation, id=uuid.UUID(conversation_id))
        assert rows[0].title == "第一轮说的话"

    async def test_rename_survives_next_turn(self, env: ConversationEnv):
        """派生只在标题**还空着**时发生，否则用户改的名字会被下一轮吃掉。"""
        done = await env.turn("第一轮说的话")
        conversation_id = done["conversation_id"]
        await env.rename(conversation_id, "我自己起的名字")

        await env.turn("第二轮", conversation_id=conversation_id)

        rows = await env.rows(AIConversation, id=uuid.UUID(conversation_id))
        assert rows[0].title == "我自己起的名字"

    async def test_turn_refreshes_last_active_and_list_order(self, env: ConversationEnv):
        done = await env.turn("第一轮")
        conversation_id = done["conversation_id"]
        idle = await env.seed(title="闲置", last_active_at=datetime(2026, 9, 18, tzinfo=UTC))
        assert [c["id"] for c in (await env.list_()).json()["conversations"]] == [
            str(conversation_id), str(idle),
        ]

        await env.turn("第二轮", conversation_id=conversation_id)

        rows = await env.rows(AIConversation, id=uuid.UUID(conversation_id))
        assert rows[0].last_active_at is not None


# ── 6. 登出粒度（P1 挂账 1 的落地） ───────────────────────────────────────


class TestLogoutGranularity:
    """P1 挂账 1 的结论：**保持按用户粒度清，不引入设备维度**（理由见
    ``conversation.py`` 模块 docstring）。

    代价是可观测的：多设备下另一台机器的服务端会话会消失，它拿到的是与「不是我
    的 id」同一个 404。客户端的降级契约是「丢弃该 id、开新会话」，本地投影缓存
    仍在——本类把这条契约**服务端这一侧**钉住。

    注：``/auth/logout`` 吊销的是 device session，access token 在过期前仍可用
    （``get_current_user`` 只看 JWT 与用户状态），所以下面能在登出后继续发请求
    ——这正是「另一台设备还在用」在服务端眼中的样子。
    """

    async def test_logout_purges_every_conversation_of_the_user(self, env: ConversationEnv):
        await env.seed(title="会话一")
        await env.seed(title="会话二")

        assert (await env.logout()).status_code == 204

        assert await env.rows(AIConversation, user_id=env.user_id) == []
        assert await env.rows(AIConversationEvent) == []

    async def test_stale_id_is_404_on_every_endpoint(self, env: ConversationEnv):
        conversation_id = await env.seed(title="登出前建的")
        await env.logout()

        assert (await env.rename(conversation_id, "改名")).status_code == 404
        assert (await env.delete(conversation_id)).status_code == 404
        assert (await env.events(conversation_id)).status_code == 404
        assert (await env.list_()).json()["conversations"] == []

    async def test_stale_id_is_404_on_agent(self, env: ConversationEnv):
        done = await env.turn("第一轮")
        conversation_id = done["conversation_id"]
        await env.logout()

        response = await env.client.post(
            "/ai/agent",
            json={"input": "继续", "book_id": env.book_id, "conversation_id": conversation_id},
            headers=env.auth_headers,
        )
        assert response.status_code == 404
        assert response.json()["code"] == "conversation_not_found"

    async def test_logout_leaves_other_users_conversations_alone(self, env: ConversationEnv):
        theirs = await env.seed(user_id=env.other_user_id, book_id=None)
        await env.logout()
        assert len(await env.rows(AIConversation, id=theirs)) == 1

    async def test_logout_keeps_usage_rows(self, env: ConversationEnv):
        """配额基座口径（02f 决策 1）在登出路径上同样成立。"""
        await env.turn("记一笔")
        await env.logout()
        assert len(await env.rows(AIUsage, user_id=env.user_id)) == 1


# ── 6b. 会话分叉（T-M7-04b）──────────────────────────────────────────────

def _tool_call_event(call_id: str) -> dict[str, Any]:
    """一条带 ``tool_calls`` 的 assistant 事件载荷（配对组的前半边）。"""
    return {
        "role": "assistant", "content": "", "reasoning_content": "",
        "tool_calls": [{
            "id": call_id, "type": "function",
            "function": {"name": "transaction_list", "arguments": "{}"},
        }],
    }


#: 一个完整配对组：``user`` → ``assistant``（带调用）→ ``tool``（结果）。
#: 切在第 2 条与第 3 条之间就是**半截**（悬空的 ``tool_calls``），切在第 3 条之后
#: 才安全——这是分叉端点唯一的边界条件。
_GROUP: tuple[tuple[str, dict[str, Any]], ...] = (
    ("user", {"role": "user", "content": "看看流水"}),
    ("assistant", _tool_call_event("call_1")),
    ("tool", {"role": "tool", "tool_call_id": "call_1", "content": "{}"}),
)


class TestForkConversation:
    async def test_copies_the_prefix_and_leaves_the_source_untouched(
        self, env: ConversationEnv,
    ):
        source = await env.seed(title="原始", messages=_GROUP)
        before = (await env.events(source)).json()["events"]

        response = await env.fork(source, 3)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["seq"] == 3
        forked = payload["conversation"]

        assert forked["id"] != str(source)
        assert forked["title"] == "原始"  # 标题跟着走，用户可再改名
        # 前缀逐字节相同（含 seq / turn：它们是会话内编号，原值复制正好接着数）
        assert (await env.events(forked["id"])).json()["events"] == before
        # **原会话一行不动**：事件一条不少、一条不改，也不动它的活跃时间
        assert (await env.events(source)).json()["events"] == before
        source_row = (await env.rows(AIConversation, id=source))[0]
        assert source_row.last_active_at is None
        assert source_row.archived_at is None
        # 分叉自己是一次新动作：它出现在列表上方（而不是继承父会话的活跃时间）
        assert (await env.rows(AIConversation, id=uuid.UUID(forked["id"])))[0]\
            .last_active_at is not None

    async def test_rounds_down_to_a_complete_pairing_group(
        self, env: ConversationEnv,
    ):
        """验收：**不变式 1 不因分叉被破坏**。切点落在配对组中间时向下取整到最近
        的完整组，并把**实际**切点回报给客户端（而不是它给的那个数）。"""
        source = await env.seed(messages=_GROUP)

        payload = (await env.fork(source, 2)).json()

        assert payload["seq"] == 1
        copied = (await env.events(payload["conversation"]["id"])).json()["events"]
        assert [e["type"] for e in copied] == ["user"]

    async def test_fork_point_outside_the_event_range_is_422(
        self, env: ConversationEnv,
    ):
        source = await env.seed(messages=_GROUP)

        assert (await env.fork(source, 0)).status_code == 422  # schema: ge=1
        response = await env.fork(source, 4)  # 只有 3 条事件
        assert response.status_code == 422
        assert response.json()["code"] == "invalid_fork_seq"
        # 空会话没有可复制的前缀
        empty = await env.seed()
        assert (await env.fork(empty, 1)).status_code == 422

    async def test_other_users_conversation_is_404(self, env: ConversationEnv):
        theirs = await env.seed(
            user_id=env.other_user_id, book_id=None, messages=_GROUP,
        )

        response = await env.fork(theirs, 3)
        assert response.status_code == 404
        assert response.json()["code"] == "conversation_not_found"

    async def test_archived_source_is_404(self, env: ConversationEnv):
        source = await env.seed(
            archived_at=datetime(2026, 9, 19, tzinfo=UTC), messages=_GROUP,
        )
        assert (await env.fork(source, 3)).status_code == 404

    async def test_forked_conversation_resumes_independently(
        self, env: ConversationEnv,
    ):
        """验收：**分叉会话独立续跑**。新会话带着复制来的前缀接着往下跑，原会话
        既不被搬走也不被改写。"""
        source = (await env.turn("第一轮"))["conversation_id"]
        source_events = (await env.events(source)).json()["events"]
        cut = source_events[-1]["seq"]

        forked = (await env.fork(source, cut)).json()["conversation"]

        env.provider.calls.clear()
        done = await env.turn("分叉后的一轮", conversation_id=forked["id"])

        # ① 模型看到的是**分叉会话**的历史：被复制的那一轮都在，外加本轮的输入
        sent = env.provider.calls[0]
        assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
        assert sent[1]["content"] == "第一轮"
        assert sent[3]["content"] == "分叉后的一轮"
        # ② 新轮自占一个轮号（前缀的轮号原样保留，新的接在后面）
        forked_events = (await env.events(forked["id"])).json()["events"]
        assert [e["turn"] for e in forked_events] == [
            *[e["turn"] for e in source_events], 2, 2, 2, 2,
        ]
        assert done["conversation_id"] == forked["id"]
        assert done["seq"] == forked_events[-1]["seq"]
        # ③ 原会话一行不动——分叉不是「搬家」
        assert (await env.events(source)).json()["events"] == source_events


# ── 7. 未登录 / 冻结等既有守卫 ────────────────────────────────────────────


class TestGuards:
    @pytest.mark.parametrize("method", ["patch", "delete"])
    async def test_write_endpoints_require_authentication(
        self, env: ConversationEnv, method: str,
    ):
        conversation_id = await env.seed()
        response = await getattr(env.client, method)(
            f"/ai/conversations/{conversation_id}", headers={},
            **({"json": {"title": "x"}} if method == "patch" else {}),
        )
        assert response.status_code in (401, 403)

    async def test_invalid_uuid_path_is_422(self, env: ConversationEnv):
        assert (await env.rename("not-a-uuid", "改名")).status_code == 422
        assert (await env.events("not-a-uuid")).status_code == 422
