"""M8-02：审批留档（``approval_request`` / ``approval_result``）验收测试。

验收映射（`docs/milestones/T-M8-admin-console-plan.md` §三 8-02）：

- ``confirm`` 帧写一条 ``approval_request``，与消息同 ``turn``、同事务落
  ``ai_proposal_links``  → ``TestApprovalRequest``
- ``/ai/execute`` 成功写 ``allowed-once`` + ``source="server"`` → ``TestServerResult``
- 重复上报覆盖最后一次、不追加行                          → ``TestClientResult``
- ``conversation_id`` 越权被拒                            → ``TestClientResult``
- 老客户端不调用上报端点时一切照旧                        → ``TestClientResult``

共用 agent 流的整套夹具（临时文件库 + 脚本化 provider + 真实端点）——不复刻一份，
否则两边的「一轮怎么跑」会慢慢漂开。全部用例无网络、无真实模型。
"""

from __future__ import annotations

import uuid
from typing import Any

from src.models.ai import AIConversationEvent, AIProposalLink
from src.models.ledger import Category
from tests.test_agent_stream import (  # noqa: F401 — stream_env 是复用夹具
    StreamEnv,
    _call,
    _rows,
    _text,
    _tool_turn,
    _tx_args,
    payload_of,
    stream_env,
)

# ── 小工具 ────────────────────────────────────────────────────────────────


async def _events(env: StreamEnv, conversation_id: uuid.UUID) -> list[Any]:
    rows = await _rows(env, AIConversationEvent, conversation_id=conversation_id)
    return sorted(rows, key=lambda event: event.seq)


def _conversation_id(body: str) -> uuid.UUID:
    return uuid.UUID(payload_of(body, "done")["conversation_id"])


async def _hung_turn(env: StreamEnv) -> tuple[dict[str, Any], uuid.UUID]:
    """跑一轮多步写入 → 挂起提案 + 确认帧；返回 ``(提案, 会话 id)``。"""
    env.provider.reset([
        _tool_turn(_call("category_list")),
        _tool_turn(_call("transaction_add", _tx_args())),
        _text("已经帮你拟好了。"),
    ])
    _, body = await env.turn("记一笔午饭 12 块")
    return payload_of(body, "confirm")["proposal"], _conversation_id(body)


async def _post_result(
    env: StreamEnv,
    *,
    conversation_id: uuid.UUID,
    proposal_id: str,
    result: str,
    headers: dict[str, str] | None = None,
    reason: str | None = None,
) -> Any:
    body: dict[str, Any] = {
        "proposalId": proposal_id,
        "conversationId": str(conversation_id),
        "result": result,
    }
    if reason is not None:
        body["reason"] = reason
    return await env.client.post(
        "/ai/approval-result", json=body, headers=headers or env.auth_headers,
    )


# ── 1. approval_request ───────────────────────────────────────────────────


class TestApprovalRequest:
    async def test_confirm_frame_archives_request_in_the_same_turn(
        self, stream_env: StreamEnv,
    ):
        """``confirm`` 帧产出一条 ``approval_request``，与消息同轮、载荷五项。

        载荷**不含完整 proposal 信封**（架构 §4.2：那份与 ``done.proposals``
        重复）；``op`` 是规范 op——v1 信封装的是别名 ``add_tx``，后台要按同一个
        键聚合两条车道。
        """
        proposal, conversation_id = await _hung_turn(stream_env)
        events = await _events(stream_env, conversation_id)

        archived = [event for event in events if event.type == "approval_request"]
        assert len(archived) == 1
        data = archived[0].data
        assert data == {
            "proposalId": proposal["proposalId"],
            "op": "transaction_add",
            "risk": "low",
            "confirmReason": "multi_step",
            "step": 2,
        }
        # 与消息同轮（另立轮号会在日志里留下一个只有审批事件、没有对话的轮次）
        message_turns = {event.turn for event in events if event.type == "user"}
        assert archived[0].turn in message_turns
        # 不写完整信封
        assert "transactions" not in data and "payload" not in data

    async def test_link_row_is_written_in_the_same_transaction(
        self, stream_env: StreamEnv,
    ):
        proposal, conversation_id = await _hung_turn(stream_env)
        archived = next(
            event for event in await _events(stream_env, conversation_id)
            if event.type == "approval_request"
        )

        links = await _rows(
            stream_env, AIProposalLink, proposal_id=proposal["proposalId"],
        )
        assert len(links) == 1
        link = links[0]
        assert link.user_id == stream_env.user_id
        assert link.conversation_id == conversation_id
        assert link.turn == archived.turn

    def test_payload_maps_v1_alias_to_canonical_op(self):
        """v1 的 ``add_tx_batch`` 与 v2 的 ``transaction_add`` 是同一个 op。"""
        from src.modules.ai.stream import _approval_request_payload

        data = _approval_request_payload(
            {"proposalId": "p1", "action": "add_tx_batch"},
            step=1, confirm_reason=None,
        )
        assert data["op"] == "transaction_add"
        assert data["risk"] == "low"
        assert data["confirmReason"] is None


# ── 2. 服务端确认 ─────────────────────────────────────────────────────────


class TestServerResult:
    async def test_execute_success_records_allowed_once(self, stream_env: StreamEnv):
        proposal, conversation_id = await _hung_turn(stream_env)

        response = await stream_env.client.post(
            "/ai/execute",
            json={
                "proposal": proposal,
                "book_id": stream_env.book_id,
                "confirmation_token": "allowed-once",
            },
            headers=stream_env.auth_headers,
        )
        assert response.status_code == 200

        results = [
            event for event in await _events(stream_env, conversation_id)
            if event.type == "approval_result"
        ]
        assert len(results) == 1
        assert results[0].data == {
            "proposalId": proposal["proposalId"],
            "result": "allowed-once",
            "source": "server",
        }
        link = (await _rows(
            stream_env, AIProposalLink, proposal_id=proposal["proposalId"],
        ))[0]
        assert results[0].turn == link.turn, "结论落回提案所在的那一轮"

    async def test_execute_replay_does_not_append_a_second_row(
        self, stream_env: StreamEnv,
    ):
        """``/ai/execute`` 的第二次调用命中提案幂等键——同一件事只记一次。"""
        proposal, conversation_id = await _hung_turn(stream_env)
        payload = {
            "proposal": proposal,
            "book_id": stream_env.book_id,
            "confirmation_token": "allowed-once",
        }
        for _ in range(2):
            response = await stream_env.client.post(
                "/ai/execute", json=payload, headers=stream_env.auth_headers,
            )
            assert response.status_code == 200

        results = [
            event for event in await _events(stream_env, conversation_id)
            if event.type == "approval_result"
        ]
        assert len(results) == 1

    async def test_execute_without_a_link_still_succeeds(
        self, stream_env: StreamEnv,
    ):
        """没有 ``ai_proposal_links`` 行（不是本服务端挂起的提案）时，执行照常，
        留档什么都不写——**不猜一个会话**。"""
        category = (await _rows(stream_env, Category))[0]
        proposal = {
            "proposalId": f"manual-{uuid.uuid4().hex}",
            "action": "add_tx",
            "confidence": 0.95,
            "requiresConfirmation": False,
            "transactions": [{
                "type": "expense",
                "amountCents": 1200,
                "categoryId": str(category.id),
                "bizDate": "2026-09-18",
                "note": "午饭",
            }],
            "message": "已记录",
            "warnings": [],
        }
        response = await stream_env.client.post(
            "/ai/execute",
            json={
                "proposal": proposal,
                "book_id": stream_env.book_id,
                "confirmation_token": "x",
            },
            headers=stream_env.auth_headers,
        )
        assert response.status_code == 200
        assert await _rows(
            stream_env, AIProposalLink, proposal_id=proposal["proposalId"],
        ) == []


# ── 3. 客户端上报 ─────────────────────────────────────────────────────────


class TestClientResult:
    async def test_repeat_upload_overwrites_instead_of_appending(
        self, stream_env: StreamEnv,
    ):
        """幂等 ``(user_id, proposal_id)``：重复上报覆盖最后一次、不追加行。"""
        _, body = await stream_env.turn("你好")
        conversation_id = _conversation_id(body)

        first = await _post_result(
            stream_env, conversation_id=conversation_id,
            proposal_id="prop-client-1", result="rejected", reason="用户拒绝",
        )
        assert first.status_code == 200
        rows = [
            event for event in await _events(stream_env, conversation_id)
            if event.type == "approval_result"
        ]
        assert len(rows) == 1
        assert rows[0].data == {
            "proposalId": "prop-client-1",
            "result": "rejected",
            "source": "client",
            "reason": "用户拒绝",
        }
        original_seq = rows[0].seq

        second = await _post_result(
            stream_env, conversation_id=conversation_id,
            proposal_id="prop-client-1", result="cancelled",
        )
        assert second.status_code == 200
        rows = [
            event for event in await _events(stream_env, conversation_id)
            if event.type == "approval_result"
        ]
        assert len(rows) == 1, "重复上报不追加行"
        assert rows[0].seq == original_seq, "覆盖的是同一条事件"
        assert rows[0].data["result"] == "cancelled"
        assert "reason" not in rows[0].data

    async def test_upload_lands_on_the_proposal_turn(self, stream_env: StreamEnv):
        """有 ``ai_proposal_links`` 行时，结论落回**提案那一轮**。

        设备类 op 是这条路径的真实形态：服务端只挂起、由客户端在设备上执行，
        四值只能靠客户端上报。
        """
        proposal, conversation_id = await _hung_turn(stream_env)

        response = await _post_result(
            stream_env, conversation_id=conversation_id,
            proposal_id=proposal["proposalId"], result="allowed-once",
        )
        assert response.status_code == 200

        rows = [
            event for event in await _events(stream_env, conversation_id)
            if event.type == "approval_result"
        ]
        assert len(rows) == 1
        assert rows[0].data["source"] == "client"
        assert rows[0].data["result"] == "allowed-once"
        link = (await _rows(
            stream_env, AIProposalLink, proposal_id=proposal["proposalId"],
        ))[0]
        assert rows[0].turn == link.turn

    async def test_foreign_conversation_is_rejected(self, stream_env: StreamEnv):
        _, body = await stream_env.turn("你好")
        conversation_id = _conversation_id(body)

        other = await stream_env.client.post(
            "/auth/guest", json={"device_id": "other-device", "nickname": "other"},
        )
        assert other.status_code == 200
        token = other.json()["tokens"]["access_token"]

        response = await _post_result(
            stream_env, conversation_id=conversation_id, proposal_id="prop-x",
            result="rejected", headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404
        assert response.json()["code"] == "conversation_not_found"
        assert [
            event for event in await _events(stream_env, conversation_id)
            if event.type == "approval_result"
        ] == []

    async def test_allowed_once_upload_is_ignored_when_server_row_exists(
        self, stream_env: StreamEnv,
    ):
        """M8-02b：客户端四值统一上报时，``allowed-once`` 与 server 行冗余。

        执行路径已经记过既成事实；客户端这条既不改写它，也不追加第二行——
        回放页不该看到两条结论。
        """
        proposal, conversation_id = await _hung_turn(stream_env)
        executed = await stream_env.client.post(
            "/ai/execute",
            json={
                "proposal": proposal,
                "book_id": stream_env.book_id,
                "confirmation_token": "allowed-once",
            },
            headers=stream_env.auth_headers,
        )
        assert executed.status_code == 200

        response = await _post_result(
            stream_env, conversation_id=conversation_id,
            proposal_id=proposal["proposalId"], result="allowed-once",
        )
        assert response.status_code == 200

        results = [
            event for event in await _events(stream_env, conversation_id)
            if event.type == "approval_result"
        ]
        assert len(results) == 1
        assert results[0].data["source"] == "server"

    async def test_unknown_result_value_is_a_schema_error(
        self, stream_env: StreamEnv,
    ):
        """四值是封闭枚举——多一个值就是客户端写错了，不静默落库。"""
        _, body = await stream_env.turn("你好")
        response = await _post_result(
            stream_env, conversation_id=_conversation_id(body),
            proposal_id="prop-y", result="probably-fine",
        )
        assert response.status_code == 422


# ── 4. 老客户端 ───────────────────────────────────────────────────────────


class TestLegacyClient:
    async def test_a_client_that_never_uploads_leaves_no_result_rows(
        self, stream_env: StreamEnv,
    ):
        """老客户端不调用上报端点：一轮照常收尾，日志里只有 ``approval_request``。

        「没上报」是后台的第四种展示态，**不是拒绝**——服务端不得替用户补一个结论。
        """
        proposal, conversation_id = await _hung_turn(stream_env)
        events = await _events(stream_env, conversation_id)

        assert [e.type for e in events].count("approval_request") == 1
        assert [e.type for e in events].count("approval_result") == 0
        # 提案本身仍然在 done.proposals 里（链路没被本卡改动）
        assert proposal["proposalId"].startswith("agent-")
