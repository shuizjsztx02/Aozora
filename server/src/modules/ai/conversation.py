"""T-M7-02f: 最小会话状态与配额双限。

服务端持有会话状态（架构 §6.3）——客户端只传 ``conversation_id`` + 本轮输入，
历史由本模块读出并投影成下一次 ``AgentLoop.run(history=…)`` 的输入。本卡只做
「够多轮上下文」的最小形态：**不做压缩、不做分叉、不做回放**（那些归 P3 的
T-M7-04 完整事件日志）。

三个概念，先分清
----------------

============  ==================================================================
Turn          一次用户输入跑完整个 loop。**配额与窗口都以它为单位**——不是
              「一次模型请求」：工具任务的第 2 步起恒为深档（02d 实测），
              按请求数算会让「轮数」随工具调用次数漂移。也**不是「一条 user
              消息」**：中途输入（``AgentLoop.submit()``）在下一个 Step 入口落
              进消息流，所以一轮里可以有多条 user 消息——按 user 消息切会把一
              轮切成两半，窗口起点落在轮中间时用户最初的提问就丢了（复审 P2-1，
              见 ``models/ai.py`` 的 ``turn`` 列）。
Event         会话日志里的一行，即 loop 消息流里的一条消息（``type`` 就是角色），
              带轮号 ``turn``。
窗口          最近 N 轮原文 + 字符双限（架构 §6.4）。超窗轮次**直接丢**——
              P1 不生成摘要，原文仍在日志里（P3 才做 ``replace`` 压缩）。
============  ==================================================================

三条不能破的约束
----------------

1. **不存 system 消息。** 系统提示由 loop 每轮按 ``ToolContext`` 现算（含当天
   日期与本账本对象名单），存下来换账本后就是过期数据，还会出现两条 system。
2. **assistant 消息必须带 ``reasoning_content`` 键**（空串可）。真实 API 探针
   实测缺键会让整轮 400，而「入口判快 → 第 2 步升深」这条最常见的路径，第 1 步
   本来就产不出推理内容。写入时补齐，不依赖调用方记得这条（§五 02d 记录）。
3. **只按 ``turn`` 整轮截断。** 一轮事件是原子写入的，同一个 ``turn`` 的事件
   因此永远同进同出，不会落在「带 ``tool_calls`` 的 assistant 消息」与其
   ``role:"tool"`` 结果之间——孤立结果会被 API 以 400 拒绝。

配额（R-PROD-15）
----------------

``ai_usage`` 上**一个 Turn 一条行**、带 ``conversation_id``，于是会话级「轮数 +
token」双限就是同一张表上的 ``COUNT(*)`` / ``SUM(tokens)`` 聚合——不引 Redis
（生产单 worker，架构 §12.2 已定表第 3 项）。超限沿用快路径的 429
``ai_quota_exceeded``，客户端据此降级到本地规则 NLU。
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import delete, func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.config import Settings, get_config
from src.common.errors import AppException
from src.models.ai import AIConversation, AIConversationEvent, AIUsage
from src.models.ledger import Book
from src.models.user import User

from .service import build_usage_row

logger = logging.getLogger(__name__)

#: 事件类型 = 消息角色。P1 只有这三种（``system`` 刻意不在其中，见模块 docstring）。
EVENT_USER = "user"
EVENT_ASSISTANT = "assistant"
EVENT_TOOL = "tool"
EVENT_TYPES = frozenset({EVENT_USER, EVENT_ASSISTANT, EVENT_TOOL})

#: 会话 id 本身是内部 ID，**不进历史正文**，只作为请求参数出现。
ERR_INVALID_CONVERSATION_ID = "invalid_conversation_id"
#: 不存在 / 不属于调用者一律报同一个码——跨用户不可分辨（security.md 的读取面口径）。
ERR_CONVERSATION_NOT_FOUND = "conversation_not_found"


class ConversationService:
    """会话索引、事件日志与配额。所有读路径都带用户归属校验。"""

    def __init__(self, db: AsyncSession, cfg: Settings | None = None) -> None:
        self._db = db
        self._cfg = cfg or get_config()

    # -- 会话索引 ---------------------------------------------------------

    async def resolve(
        self,
        user: User,
        book_id: uuid.UUID,
        conversation_id: str | uuid.UUID | None = None,
    ) -> AIConversation:
        """取会话：给了 id 就带归属校验地加载，没给就新建一个。

        归属校验是**读取面的唯一防线**：别人的 conversation_id 一律按「不存在」
        处理（404），不泄露它是否存在。

        会话跟随当前账本——请求带的 ``book_id`` 与索引不一致时以请求为准更新。
        P1 客户端没有「换账本就开新会话」的概念，让索引留着旧账本只会让它变成
        一句假话；换账本后系统提示的「现有对象」名单也要跟着换，否则模型会拿
        A 账本的分类名去 B 账本记账。
        """
        await self._verify_book(user, book_id)

        if conversation_id is None:
            conversation = AIConversation(user_id=user.id, book_id=book_id)
            self._db.add(conversation)
            await self._db.flush()
            return conversation

        try:
            conv_uuid = uuid.UUID(str(conversation_id))
        except ValueError:
            raise AppException(
                422, ERR_INVALID_CONVERSATION_ID, "Invalid conversation ID",
            )

        conversation = await self._db.scalar(
            select(AIConversation).where(
                AIConversation.id == conv_uuid,
                AIConversation.user_id == user.id,
            )
        )
        if conversation is None:
            raise AppException(
                404, ERR_CONVERSATION_NOT_FOUND, "Conversation not found",
            )
        if conversation.book_id != book_id:
            conversation.book_id = book_id
            await self._db.flush()
        return conversation

    async def _verify_book(self, user: User, book_id: uuid.UUID) -> None:
        """账本必须属于调用者（与 ``tools.load_tool_context`` 同口径、同错误码）。

        会话索引是新的一层读面：在上面写一个别人的 ``book_id``，等于给「指认
        他人账本」开了一个口子——P2 的会话列表一旦回显账本名就是实打实的泄漏。
        工具层同样不信任未经校验的账本 id，这里不重复它的信任假设。
        """
        owned = await self._db.scalar(
            select(Book.id).where(
                Book.id == book_id,
                Book.owner_id == user.id,
                Book.deleted_at.is_(None),
            )
        )
        if owned is None:
            raise AppException(404, "not_found", "Book not found")

    # -- 上下文窗口 -------------------------------------------------------

    async def history(self, conversation: AIConversation) -> list[dict[str, Any]]:
        """投影出模型上下文窗口（最近 N 轮原文，另受字符上限约束）。

        返回值可直接作为 ``AgentLoop.run(history=…)`` 的输入：不含 system，
        按 seq 有序，轮边界完整（整轮同进同出）。

        **按 ``turn`` 分组整取**——不是按 ``user`` 消息切：一轮里可以有多条 user
        消息（中途输入），按后者切会把一轮切成两半，窗口起点落在轮中间时用户
        最初的提问就丢了（复审 P2-1 实测：N=1 时只剩「补一句 + 回答」两句）。

        **字符超限时丢最早轮次**（架构 §6.4），但**至少保留最近一轮**——窗口
        空掉会让「追问」失去指代对象，那正是本卡要解决的痛点；宁可超一点预算
        也不能让上下文归零。
        """
        rows = (await self._db.scalars(
            select(AIConversationEvent)
            .where(AIConversationEvent.conversation_id == conversation.id)
            .order_by(AIConversationEvent.seq)
        )).all()
        if not rows:
            return []

        turns: list[list[dict[str, Any]]] = []
        current_turn: int | None = None
        for row in rows:
            if row.turn != current_turn:
                turns.append([])
                current_turn = row.turn
            turns[-1].append(dict(row.data))

        window = turns[-self._cfg.ai_agent_history_turns:]
        while len(window) > 1 and _window_chars(window) > self._cfg.ai_agent_history_chars:
            dropped = window.pop(0)
            logger.info(
                "agent history window truncated by char budget",
                extra={
                    "conversation_id": str(conversation.id),
                    "dropped_messages": len(dropped),
                    "outcome": "history_char_limit",
                },
            )

        return [message for turn in window for message in turn]

    # -- 写入 -------------------------------------------------------------

    async def append_turn(
        self, conversation: AIConversation, messages: Sequence[Mapping[str, Any]],
    ) -> int:
        """把一轮的消息追加进日志（一轮 = 一次 ``AgentResult.messages``）。

        ``messages`` 是 loop 给出的**本轮增量**（含本轮用户输入）——**只接受
        ``AgentResult.messages``**：把 ``history + delta`` 一起传进来会把历史
        重复写一遍；传一轮的中间片段则触发下面两条校验。校验在写入**前**一次性
        做完——半截写入会让下一轮的历史出现孤立 ``tool`` 消息，而那要到模型请求
        时才以 400 暴露，归因困难。校验失败抛的是 ``ValueError``（调用方编程
        错误，不是可降级的业务错误；loop 自身产出的 delta 结构上不可能触发）。

        返回写入的事件条数。
        """
        prepared = _prepare_events(messages)
        if not prepared:
            return 0

        # 轮号与序号都在写之前算好；两个并发请求可能算出同一组值，唯一索引会拒绝
        # 其中一个。用 **SAVEPOINT** 包住写入：撞车时只回滚这一次尝试（而不是调用方
        # 在这个 session 上的整个事务，那里还可能有别的未提交写入），重算后重试一次。
        # 用 Core insert 而不是 ``session.add()``——撞车后没有 ORM 对象需要从
        # 身份映射里摘除，重试路径因此是干净的。
        for attempt in (1, 2):
            next_seq, next_turn = await self._next_position(conversation)
            try:
                async with self._db.begin_nested():
                    await self._db.execute(
                        insert(AIConversationEvent),
                        [
                            {
                                "conversation_id": conversation.id,
                                # ``next_seq`` 已是本轮第一条的序号，故 offset 从 0 起。
                                "seq": next_seq + offset,
                                "turn": next_turn,
                                "type": event_type,
                                "data": data,
                            }
                            for offset, (event_type, data) in enumerate(prepared)
                        ],
                    )
            except IntegrityError:
                if attempt == 2:
                    raise
                logger.warning(
                    "conversation event seq race, retrying once",
                    extra={
                        "conversation_id": str(conversation.id),
                        "outcome": "seq_race_retry",
                    },
                )
                continue
            return len(prepared)

        raise AssertionError("unreachable: retry loop always returns or raises")

    async def _next_position(self, conversation: AIConversation) -> tuple[int, int]:
        """下一个 ``(seq, turn)``——都在会话内单调递增，从 1 开始。"""
        row = (await self._db.execute(
            select(
                func.coalesce(func.max(AIConversationEvent.seq), 0),
                func.coalesce(func.max(AIConversationEvent.turn), 0),
            ).where(AIConversationEvent.conversation_id == conversation.id)
        )).one()
        return int(row[0]) + 1, int(row[1]) + 1

    async def record_turn(
        self,
        user: User,
        conversation: AIConversation,
        *,
        provider: str,
        model: str,
        request_id: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        """记一轮的用量（一个 Turn 一条 ``ai_usage`` 行）。

        会话级配额与 guest 日配额共用这一张表：前者按 ``conversation_id`` 聚合，
        后者按 ``(user_id, created_at)`` 聚合，互不影响（快路径的行
        ``conversation_id`` 为 NULL）。
        """
        self._db.add(build_usage_row(
            user_id=user.id,
            provider=provider,
            model=model,
            request_id=request_id,
            tokens=prompt_tokens + completion_tokens,
            cfg=self._cfg,
            conversation_id=conversation.id,
        ))
        await self._db.flush()

    # -- 配额 -------------------------------------------------------------

    async def check_quota(self, conversation: AIConversation) -> None:
        """会话级「轮数 + token」双限。超限一律 429 ``ai_quota_exceeded``。

        与快路径的 guest 日配额**叠加**而非替代：日配额按用户/按天，本档按
        会话/按整个会话生命周期。两个数字都是**上限哨兵**——拦住失控的长会话，
        不是成本目标（成本口径见 02d：工具任务第 2 步起恒为深档）。

        轮数以 ``ai_usage`` 的**行数**为准（调用方一轮记一行），不取事件条数：
        一轮产生几条消息取决于模型调了几次工具，还可能没有 assistant 事件
        （provider 失败、空回答），拿它当轮数会让上限随消息数漂移。
        """
        if await self._db.scalar(
            select(func.count())
            .select_from(AIUsage)
            .where(AIUsage.conversation_id == conversation.id)
        ) >= self._cfg.ai_agent_conversation_max_turns:
            raise AppException(
                429, "ai_quota_exceeded",
                "This conversation has reached its turn limit, please start a new one",
            )

        used = await self._db.scalar(
            select(func.coalesce(func.sum(AIUsage.tokens), 0)).where(
                AIUsage.conversation_id == conversation.id,
            )
        )
        if used >= self._cfg.ai_agent_conversation_max_tokens:
            raise AppException(
                429, "ai_quota_exceeded",
                "This conversation has reached its token limit, please start a new one",
            )

    # -- 清理 -------------------------------------------------------------

    async def purge_user(self, user_id: uuid.UUID) -> int:
        """删掉该用户的全部会话与事件。返回删掉的会话数。

        **``ai_usage`` 不在这里删**：它是 guest 日配额的计数基座，随会话一起删
        等于「登出再登入可刷满配额」。库层面由 ``ON DELETE SET NULL`` 把外键置空
        （见 ``models/ai.py``），审计行留下。

        事件显式删而不靠级联：测试环境（SQLite）不强制外键，靠级联的清理在
        测试里看不出效果，而生产上它必须真的干净（security.md 的登出联动清理）。
        """
        conversation_ids = select(AIConversation.id).where(
            AIConversation.user_id == user_id,
        )
        await self._db.execute(
            delete(AIConversationEvent).where(
                AIConversationEvent.conversation_id.in_(conversation_ids),
            )
        )
        result = await self._db.execute(
            delete(AIConversation).where(AIConversation.user_id == user_id),
        )
        await self._db.flush()
        return result.rowcount or 0


# ---------------------------------------------------------------------------
# 事件与窗口的纯函数
# ---------------------------------------------------------------------------

def _prepare_events(
    messages: Sequence[Mapping[str, Any]],
) -> list[tuple[str, dict[str, Any]]]:
    """校验一轮消息并转成待写入的 ``(type, data)``。

    规则（模块 docstring 的三条约束在代码里的落点）：

    - ``system`` 消息**丢弃**（并告警）——它由 loop 现算，不该进历史；
    - 未知角色、或以 ``tool`` 开头（说明调用方切在了轮中间）**抛 ValueError**：
      这两种都是调用方的编程错误，静默写入会把问题推迟到下一轮的 400；
    - assistant 消息的 ``reasoning_content`` 规范化成字符串（缺键或 null → 空串）。
    """
    prepared: list[tuple[str, dict[str, Any]]] = []
    for message in messages:
        role = str(message.get("role", ""))
        if role == "system":
            logger.warning(
                "dropping system message from conversation log",
                extra={"outcome": "system_message_dropped"},
            )
            continue
        if role not in EVENT_TYPES:
            raise ValueError(f"unsupported conversation event role: {role!r}")
        if role == EVENT_TOOL and not prepared:
            raise ValueError("conversation turn must not start with a tool result")

        data = dict(message)
        if role == EVENT_ASSISTANT and not isinstance(data.get("reasoning_content"), str):
            # 缺键**或**值为 null 都补空串。API 要的是这个键在场且是字符串；
            # 值由调用方给的 ``None`` 到了下一轮就是一次 400。
            data["reasoning_content"] = ""
        prepared.append((role, data))
    return prepared


def _window_chars(window: Sequence[Sequence[dict[str, Any]]]) -> int:
    """窗口的字符量：按消息的序列化长度估。"""
    return sum(
        len(json.dumps(message, ensure_ascii=False))
        for turn in window
        for message in turn
    )
