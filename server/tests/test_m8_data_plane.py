"""M8-03：运营后台数据面基线测试。

验收映射（`docs/milestones/T-M8-admin-console-plan.md` §三 8-03）：

- 检索面只收 user/assistant 正文，不含 tool 结果与推理 → ``TestSearchText``
- 上限截断保留尾部                                         → ``TestSearchText``
- 用户删除会话 / 保留期清理后检索面同步消失（反向验证）      → ``TestSearchText``
- 分叉从新会话自己的前缀派生                               → ``TestSearchText``
- 事件带 ``request_id``，历史行为 NULL                     → ``TestRequestId``
- 新表（admin_accounts / admin_audit_log / ai_proposal_links）可建可写 → ``TestSchema``

全部用例走真实 DB（内存 SQLite）+ 真实写入/清理路径，无网络、无模型。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from src.models.admin import AdminAccount, AdminAuditLog
from src.models.ai import AIConversation, AIConversationEvent, AIProposalLink
from src.modules.ai.conversation import (
    SEARCH_TEXT_MAX_CHARS,
    SEARCH_TEXT_TRUNCATED_MARK,
    ConversationService,
    _truncate_search_text,
    rebuild_search_text,
)
from src.modules.ai.retention import purge_expired_events
from tests.fixtures_db import db_session, sample_data  # noqa: F401


def _msg(role: str, content: str, **extra: Any) -> dict[str, Any]:
    return {"role": role, "content": content, **extra}


async def _events(db: Any, conversation: AIConversation) -> list[AIConversationEvent]:
    return list((await db.scalars(
        select(AIConversationEvent)
        .where(AIConversationEvent.conversation_id == conversation.id)
        .order_by(AIConversationEvent.seq)
    )).all())


class TestSearchText:
    async def test_collects_user_and_assistant_only(self, db_session, sample_data):
        """检索面是**人话**：tool 结果是 JSON、推理是模型内部内容，都不该被搜到。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)

        await service.append_turn(conversation, [
            _msg("user", "这个月外卖花了多少"),
            _msg(
                "assistant", "我查一下",
                reasoning_content="内部推理：应该调用统计工具",
                tool_calls=[{"id": "c1", "type": "function", "function": {
                    "name": "query_stats", "arguments": "{}",
                }}],
            ),
            _msg("tool", '{"totalCents": 124050}', tool_call_id="c1"),
        ])

        text = conversation.search_text
        assert "这个月外卖花了多少" in text
        assert "我查一下" in text
        # 推理与工具结果不进检索面
        assert "内部推理" not in text
        assert "totalCents" not in text

    async def test_truncation_keeps_tail_with_marker(self):
        """超出上限保留**尾部**并留截断标记——最近说的话更可能被检索。"""
        text = _truncate_search_text("头" + "尾" * (SEARCH_TEXT_MAX_CHARS + 100))

        assert len(text) == SEARCH_TEXT_MAX_CHARS
        assert text.startswith(SEARCH_TEXT_TRUNCATED_MARK)
        assert text.endswith("尾")

    async def test_appended_batches_accumulate(self, db_session, sample_data):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)

        await service.append_turn(conversation, [_msg("user", "第一句")])
        await service.append_turn(conversation, [_msg("assistant", "第二句")])

        assert "第一句" in conversation.search_text
        assert "第二句" in conversation.search_text

    async def test_delete_clears_search_text(self, db_session, sample_data):
        """用户删除会话后，检索面不能留着已被删掉的正文（与摘要同一口径）。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)
        await service.append_turn(conversation, [_msg("user", "要删掉的内容")])
        assert "要删掉的内容" in conversation.search_text

        await service.delete(conversation)

        assert conversation.search_text == ""
        assert (await _events(db, conversation)) == []

    async def test_fork_derives_from_its_own_prefix(self, db_session, sample_data):
        """分叉会话的检索面只能含**它自己复制到的那段前缀**。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)
        await service.append_turn(
            conversation, [_msg("user", "前缀里的问题")], request_id="req-1",
        )
        await service.append_turn(
            conversation, [_msg("user", "前缀之外的问题")], request_id="req-2",
        )

        forked, cut = await service.fork(conversation, 1)

        assert cut == 1
        assert "前缀里的问题" in forked.search_text
        assert "前缀之外的问题" not in forked.search_text
        # request_id 随事件原样复制
        assert [e.request_id for e in await _events(db, forked)] == ["req-1"]

    async def test_retention_rebuild_drops_deleted_text(self, db_session, sample_data):
        """反向验证：删之前断言含、删之后断言不含。

        保留期只删事件行；检索面是正文的派生副本，必须同步重算——否则后台搜索
        会命中一段已经被清掉的对话，等于给保留期开后门。
        """
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)
        now = datetime.now(UTC)
        long_ago = now - timedelta(days=40)

        await service.append_turn(
            conversation, [_msg("user", "四十天前的问题")], times=[long_ago],
        )
        await service.append_turn(conversation, [_msg("user", "最近的问题")])

        # 删除之前：两条都在
        assert "四十天前的问题" in conversation.search_text
        assert "最近的问题" in conversation.search_text

        await purge_expired_events(db, now - timedelta(days=30))
        await db.refresh(conversation)

        assert "四十天前的问题" not in conversation.search_text
        assert "最近的问题" in conversation.search_text

    async def test_rebuild_is_idempotent(self, db_session, sample_data):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)
        await service.append_turn(conversation, [_msg("user", "幂等问题")])

        await rebuild_search_text(db, [conversation.id])
        first = conversation.search_text
        await rebuild_search_text(db, [conversation.id])
        await db.refresh(conversation)

        assert first == conversation.search_text
        assert "幂等问题" in first


class TestRequestId:
    async def test_events_carry_request_id(self, db_session, sample_data):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)

        await service.append_turn(
            conversation,
            [
                _msg("user", "问一句"),
                _msg("assistant", "答一句", reasoning_content=""),
            ],
            request_id="req-abc",
        )

        events = await _events(db, conversation)
        assert [e.request_id for e in events] == ["req-abc", "req-abc"]

    async def test_omitted_request_id_is_null(self, db_session, sample_data):
        """老调用点与历史行同形：不传就是 NULL，不猜。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)

        await service.append_turn(conversation, [_msg("user", "无请求 id")])

        events = await _events(db, conversation)
        assert events[0].request_id is None

    async def test_structural_event_carries_request_id(self, db_session, sample_data):
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        service = ConversationService(db)
        conversation = await service.resolve(user, book.id)

        await service.append_events(
            conversation, [("quota", {"provider": "p", "model": "m", "tokens": 1})],
            request_id="req-q",
        )

        events = await _events(db, conversation)
        assert events[0].type == "quota"
        assert events[0].request_id == "req-q"


class TestSchema:
    async def test_admin_tables_are_created_and_writable(self, db_session, sample_data):
        """新表的模型定义必须真的进了 ``Base.metadata``（否则 SQLite 测试建不出表）。"""
        db, user, book = db_session, sample_data["user"], sample_data["book"]
        admin = AdminAccount(username="ops", password_hash="hash")
        db.add(admin)
        await db.flush()

        conversation = AIConversation(user_id=user.id, book_id=book.id, title="t")
        db.add(conversation)
        await db.flush()

        db.add(AdminAuditLog(
            admin_id=admin.id, action="login", target_user_id=user.id,
            detail={"ip": "127.0.0.1"},
        ))
        db.add(AIProposalLink(
            user_id=user.id, proposal_id="prop-1",
            conversation_id=conversation.id, turn=1,
        ))
        await db.flush()

        assert (await db.scalar(
            select(AdminAuditLog).where(AdminAuditLog.action == "login")
        )) is not None
        assert (await db.scalar(
            select(AIProposalLink).where(AIProposalLink.proposal_id == "prop-1")
        )) is not None
