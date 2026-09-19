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
Event         会话日志里的一行，带轮号 ``turn``。
窗口          最近 N 轮原文 + 字符双限（架构 §6.4）。超窗轮次**直接丢**——
              P1 不生成摘要，原文仍在日志里（P3 才做 ``replace`` 压缩）。
============  ==================================================================

事件分两档（T-M7-04a，架构 §6.2）
---------------------------------

**Surface 事件**（``SURFACE_EVENT_TYPES``）一一对应 loop 消息流里的一条消息，
``data`` 就是那条 API 消息字典，**只有它们进模型上下文**（投影 A）。**结构性
事件**（``STRUCTURAL_EVENT_TYPES``：审批 / 设备 / 错误 / 配额 / 安全 / 摘要替换）
是「发生过什么」的留档，只进投影 B（UI）与投影 C（审计）。分档的**单一来源**是
那两个常量：:meth:`ConversationService.history` 按前者过滤，写入时按前者派生
``ignorable`` 列——读侧信常量、不信数据列，两者的一致性由测试钉死。

于是「结构性事件不占 token」是**代码结构上的事实**而不是一句设计承诺：它们连
``history()`` 的返回值都进不去。这也是 P1 的 R-PROD-15 残余（当时全部事件都进
上下文）的关闭点。

**与下行 SSE 的八类事件是两个不同的枚举**（``stream.EventType``）：值有同名
（``error`` / ``device_request``），但一个是协议帧、一个是日志行，不得混用；
``confirm`` 帧对应的日志类型是 ``approval_request``。

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

会话列表、重命名与删除（T-M7-03a）
---------------------------------

列表只回**本人的、未归档的**会话，按 ``last_active_at`` 倒序（还没写过轮次的
用 ``created_at`` 兜）。``title`` 由首轮用户输入派生（:data:`TITLE_MAX_CHARS`
截断），用户可经 :meth:`ConversationService.rename` 改写；重命名**不动**活跃
时间——列表顺序不该因为改个名字就跳。

**删除 = 归档行 + 删事件正文**，两半各有各的理由：行留着（``archived_at`` 置位）
是因为 ``ai_usage.conversation_id`` 指着它，硬删会把审计行置空；且 P3 的保留期
（04d）本就只删事件、不删会话行，两处同构。正文则没有留的理由——用户主动删除
后服务端不该再持有它。删除后旧 ``conversation_id`` 与跨用户 id 拿到**同一个
404**，客户端把它当作「已经不是我的会话」（网络重试因此安全，见下）。

**登出粒度——P1 挂账 1 的结论（2026-09-19，P2 复核）：保持按用户粒度清，不引入
设备维度。** 会话是**用户级**资产：服务端列表对同一账号的每台设备是同一份，而
``ai_conversations`` 没有设备列、``/ai/agent`` 也不带 ``device_id``——为它加一层
设备维度等于同时改请求契约与客户端桥（02h 已冻结），而 P2 并不做跨设备正文同步
（正文同步是 P3 的 04e 决策门）。代价是多设备下 A 机登出会让 B 机的服务端会话
消失：B 机下一轮带旧 id 会拿到 404 ``conversation_not_found``，**客户端的降级
契约是「丢弃该 id、开新会话」，不得把它当错误呈现**（交 03b / 03d 落地）；本地
投影缓存仍在，历史依然可见。真正按设备清理要等 04e 的决策门。
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
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

#: **Surface 事件**——一一对应 loop 消息流里的一条消息，``data`` 就是那条 API
#: 消息字典，**只有它们进模型上下文**（架构 §6.2 投影 A）。``system`` 刻意不在
#: 其中（见模块 docstring）。``assistant`` 一条覆盖「模型回复」与「工具调用」两个
#: 语义——``tool_calls`` 挂在同一条消息的 ``data`` 里，不另立事件。
EVENT_USER = "user"
EVENT_ASSISTANT = "assistant"
EVENT_TOOL = "tool"
SURFACE_EVENT_TYPES = frozenset({EVENT_USER, EVENT_ASSISTANT, EVENT_TOOL})

#: **结构性事件**——留档，只进投影 B（UI）与投影 C（审计），不占 token。
#: 各类型的生产者状态见架构 §6.2 的表；方括号里是当前的真实生产者。
EVENT_APPROVAL_REQUEST = "approval_request"  # [待接] 审批请求（确认卡留档）
EVENT_APPROVAL_RESULT = "approval_result"    # [待接] 审批结果（写操作回执）
EVENT_DEVICE_REQUEST = "device_request"      # [无] 设备指令；通道归 P4
EVENT_ERROR = "error"                        # [待接] 一轮失败；随 04b 逐事件落库
EVENT_REPLACE = "replace"                    # [04c] 长历史被摘要替换（原文保留）
EVENT_QUOTA = "quota"                        # [record_turn] 每轮用量，与消息同轮
EVENT_SECURITY = "security"                  # [待接] 结论归 04d 的访问面审计
STRUCTURAL_EVENT_TYPES = frozenset({
    EVENT_APPROVAL_REQUEST,
    EVENT_APPROVAL_RESULT,
    EVENT_DEVICE_REQUEST,
    EVENT_ERROR,
    EVENT_REPLACE,
    EVENT_QUOTA,
    EVENT_SECURITY,
})

#: 类型全集。合法 ``type`` 只能是它里面的值（写入侧校验）。
#: **列宽**：``type`` 是 ``varchar(32)``——实测量过 ``varchar(16)`` 正好是全集里
#: 最长的 ``approval_request`` 的顶格长度、零余量（PG 17.11，17 字符即被拒）。
EVENT_TYPES = SURFACE_EVENT_TYPES | STRUCTURAL_EVENT_TYPES

#: 自动标题的字符上限（**不含**截断处的省略号）。一行的长度就够——它在侧栏 /
#: 抽屉里占一行，长了也只能截断显示。用户重命名不受这个数字约束（那是
#: ``schemas.AIConversationRenameRequest`` 的事，上限对齐列宽）。
TITLE_MAX_CHARS = 40

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

        conversation = await self.load_owned(user, conversation_id)
        if conversation.book_id != book_id:
            conversation.book_id = book_id
            await self._db.flush()
        return conversation

    async def load_owned(
        self, user: User, conversation_id: str | uuid.UUID,
    ) -> AIConversation:
        """按 id 取**本人的、未归档的**会话；此外一律 404。

        三种情况共用一个码，不可分辨：不存在、是别人的、已被归档（删除）。
        对客户端而言第三种只意味着「这个 id 不是我的会话了」——网络重试删除 /
        重发一轮因此都是安全的（P2 挂账 1 的降级契约，见模块 docstring）。
        """
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
                AIConversation.archived_at.is_(None),
            )
        )
        if conversation is None:
            raise AppException(
                404, ERR_CONVERSATION_NOT_FOUND, "Conversation not found",
            )
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

    # -- 会话列表与生命周期 -----------------------------------------------

    async def list_conversations(self, user: User) -> list[AIConversation]:
        """本人的未归档会话，按活跃时间倒序。

        ``last_active_at`` 还是 NULL 的会话（建了行、一轮都没写成的）用
        ``created_at`` 兜底——先后必须确定，否则客户端列表会在两次拉取之间抖动。
        ``id`` 是最后的 tie-breaker：同一时刻建的两个会话（测试里是常态）也要
        有稳定次序。
        """
        return list(await self._db.scalars(
            select(AIConversation)
            .where(
                AIConversation.user_id == user.id,
                AIConversation.archived_at.is_(None),
            )
            .order_by(
                func.coalesce(
                    AIConversation.last_active_at, AIConversation.created_at,
                ).desc(),
                AIConversation.created_at.desc(),
                AIConversation.id.desc(),
            )
        ))

    async def rename(self, conversation: AIConversation, title: str) -> None:
        """改写标题。**不动 ``last_active_at``**——改个名字不该让会话在列表里跳位。"""
        conversation.title = title.strip()
        await self._db.flush()

    async def delete(self, conversation: AIConversation) -> None:
        """归档会话并清掉它的事件正文。

        行留下、正文删掉，两半的理由见模块 docstring。事件**显式删而不靠级联**
        ——与 :meth:`purge_user` 同口径：测试环境（SQLite）不强制外键，靠级联的
        清理在测试里看不出效果，而生产上它必须真的干净。

        幂等性由 :meth:`load_owned` 保证：归档之后它就取不到这一行，再删一次
        是同一个 404（客户端重试因此安全）。
        """
        await self._db.execute(
            delete(AIConversationEvent).where(
                AIConversationEvent.conversation_id == conversation.id,
            )
        )
        conversation.archived_at = datetime.now(UTC)
        await self._db.flush()

    async def events(
        self, conversation: AIConversation, after_seq: int | None = None,
    ) -> list[AIConversationEvent]:
        """按 ``seq`` 升序读会话事件；给了 ``after_seq`` 就只取其后（增量读取）。

        **投影 B / C 的读面：原样返回全部事件**——不做 Surface 过滤（那是
        :meth:`history` 的事），也不改写 ``type`` / ``data``。结构性事件因此
        和消息一样读得到，UI 与审计两侧要的信息都在。``data`` 与
        :meth:`history` 是同一个数据面：工具结果里的内部 ID 在 ``agent`` 侧
        就被剥掉了。

        ``after_seq`` 的语义是**严格大于**：客户端拿上一批最后一条的 ``seq``
        再拉一次，不会重复收到它（断线重连的续读就靠这条）。
        """
        stmt = select(AIConversationEvent).where(
            AIConversationEvent.conversation_id == conversation.id,
        )
        if after_seq is not None:
            stmt = stmt.where(AIConversationEvent.seq > after_seq)
        return list(await self._db.scalars(stmt.order_by(AIConversationEvent.seq)))

    # -- 上下文窗口 -------------------------------------------------------

    async def history(self, conversation: AIConversation) -> list[dict[str, Any]]:
        """投影出模型上下文窗口（**只含 Surface 事件**，最近 N 轮，另受字符上限约束）。

        返回值可直接作为 ``AgentLoop.run(history=…)`` 的输入：不含 system，
        按 seq 有序，轮边界完整（整轮同进同出）。

        **只取 Surface 事件**（T-M7-04a）——白名单下推到 SQL，结构性事件根本
        不取出来：它们没有上限（每轮一条 quota、将来每个提案一条审批），全取
        出来只为丢掉是纯浪费。这就是「结构性事件不占 token」在代码里的落点。

        **按 ``turn`` 分组整取**——不是按 ``user`` 消息切：一轮里可以有多条 user
        消息（中途输入），按后者切会把一轮切成两半，窗口起点落在轮中间时用户
        最初的提问就丢了（复审 P2-1 实测：N=1 时只剩「补一句 + 回答」两句）。
        分组因此只能看见 Surface 事件，**只有结构性事件的轮次整个不出现**——
        那正是想要的（它没有可进上下文的内容）。

        **字符超限时丢最早轮次**（架构 §6.4），但**至少保留最近一轮**——窗口
        空掉会让「追问」失去指代对象，那正是本卡要解决的痛点；宁可超一点预算
        也不能让上下文归零。
        """
        rows = (await self._db.scalars(
            select(AIConversationEvent)
            .where(
                AIConversationEvent.conversation_id == conversation.id,
                AIConversationEvent.type.in_(SURFACE_EVENT_TYPES),
            )
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

        写成功后顺手更新会话索引（:meth:`_note_activity`）：标题在空的时候由首轮
        输入派生一次，活跃时间每次刷新——列表的排序与标题因此不用另开一条写路径。
        """
        prepared = _prepare_events(messages)
        if not prepared:
            return 0

        written = await self._insert_events(conversation, prepared)
        self._note_activity(conversation, messages)
        await self._db.flush()
        return written

    async def append_events(
        self,
        conversation: AIConversation,
        events: Sequence[tuple[str, Mapping[str, Any]]],
        *,
        same_turn: bool = False,
    ) -> int:
        """追加一批**结构性事件**（配额 / 审批 / 设备 / 错误 / 安全 / 摘要替换）。

        ``events`` 是 ``(type, data)`` 序列，``type`` 必须在
        :data:`STRUCTURAL_EVENT_TYPES` 里——**Surface 类型禁止从这条通道写入**：
        它们的 ``data`` 必须是 API 消息字典（:meth:`append_turn` 的产出），从这里
        塞一条形状不对的进去，下一轮就是模型侧 400。校验因此是白名单而不是
        「在全集里」（:func:`_prepare_structural_events`）。

        ``same_turn``：这批事件属于**当前这一轮**（如 :meth:`record_turn` 的用量
        事件，紧跟 :meth:`append_turn` 之后）时不占新轮号——另立轮号会在日志里
        留下「有轮号、没内容」的空轮。默认自成一轮（失败轮的用量、04b 的错误）。

        返回写入的事件条数。
        """
        prepared = _prepare_structural_events(events)
        if not prepared:
            return 0

        written = await self._insert_events(conversation, prepared, same_turn=same_turn)
        # 结构性事件也是「写入事件」——列表按最后一次写入排序，它同样算数。
        conversation.last_active_at = datetime.now(UTC)
        await self._db.flush()
        return written

    async def _insert_events(
        self,
        conversation: AIConversation,
        prepared: Sequence[tuple[str, dict[str, Any]]],
        *,
        same_turn: bool = False,
    ) -> int:
        """把一批已校验的事件写进日志：分配 ``(seq, turn)``、撞车重试一次。

        ``time`` 由本层统一取**写入时刻**（``datetime.now(UTC)``），同批事件因此
        共用同一个时间戳。它与 ``created_at`` **不等价**：后者是 ``server_default
        now()``，而 PG 的 ``now()`` 是**事务**时刻——一轮 loop 跑完才落库，dev 实测
        二者相差数秒。要「这一轮何时开始」看 ``created_at``，要「事件何时写下」看
        ``time``。04b 改为逐事件落库时，这个值改由调用方按事件实际发生的时刻给出。

        ``ignorable`` 按 :data:`SURFACE_EVENT_TYPES` **派生**而不是由调用方给：
        它与 :meth:`history` 的过滤条件必须同源，否则会出现「标了可忽略却进了
        上下文」的漂移。
        """
        now = datetime.now(UTC)
        # 轮号与序号都在写之前算好；两个并发请求可能算出同一组值，唯一索引会拒绝
        # 其中一个。用 **SAVEPOINT** 包住写入：撞车时只回滚这一次尝试（而不是调用方
        # 在这个 session 上的整个事务，那里还可能有别的未提交写入），重算后重试一次。
        # 用 Core insert 而不是 ``session.add()``——撞车后没有 ORM 对象需要从
        # 身份映射里摘除，重试路径因此是干净的。
        for attempt in (1, 2):
            next_seq, next_turn = await self._next_position(
                conversation, same_turn=same_turn,
            )
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
                                "time": now,
                                "ignorable": event_type not in SURFACE_EVENT_TYPES,
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

    def _note_activity(
        self, conversation: AIConversation, messages: Sequence[Mapping[str, Any]],
    ) -> None:
        """会话索引跟进这一轮：派生标题（只在还空着时）+ 刷新活跃时间。

        标题**只派生一次**——用户改过名之后，后面的轮次不该把名字覆盖回去
        （``rename`` 的结果会被吃掉）。活跃时间每次都刷，它就是列表的排序键。
        """
        if not conversation.title:
            conversation.title = _derive_title(messages)
        conversation.last_active_at = datetime.now(UTC)

    async def _next_position(
        self, conversation: AIConversation, *, same_turn: bool = False,
    ) -> tuple[int, int]:
        """下一个 ``(seq, turn)``——``seq`` 恒为会话内单调递增，从 1 开始。

        ``same_turn=True`` 时**不占新轮号**，挂在本会话最后一批事件的那个
        ``turn`` 上：本轮的结构性事件（如用量）属于这一轮。会话里还没有任何
        事件时（``turn`` 仍是 0）退回 +1 —— 轮号从 1 开始，不会出现 0。
        """
        row = (await self._db.execute(
            select(
                func.coalesce(func.max(AIConversationEvent.seq), 0),
                func.coalesce(func.max(AIConversationEvent.turn), 0),
            ).where(AIConversationEvent.conversation_id == conversation.id)
        )).one()
        next_seq = int(row[0]) + 1
        last_turn = int(row[1])
        if same_turn and last_turn > 0:
            return next_seq, last_turn
        return next_seq, last_turn + 1

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
        same_turn: bool = False,
    ) -> None:
        """记一轮的用量：一条 ``ai_usage`` 行 + 一条 ``quota`` 事件。

        会话级配额与 guest 日配额共用这一张表：前者按 ``conversation_id`` 聚合，
        后者按 ``(user_id, created_at)`` 聚合，互不影响（快路径的行
        ``conversation_id`` 为 NULL）。

        **``quota`` 事件是投影 C 的会话内用量留痕**（T-M7-04a）。``ai_usage``
        是配额的**计数基座**，它必须活得比会话长（``ON DELETE SET NULL``，登出
        再登入不能把配额刷回满额），因此不能承担「这一轮花了多少」的会话内视图；
        事件日志可以，且会随会话一起被保留期清理。

        ``same_turn`` 由调用方声明**本轮的消息刚写进日志**（成功轮）——用量事件
        因此与消息同轮，不另占轮号；失败轮没有消息事件，它自成一轮（那正是它
        的轮号）。
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
        await self.append_events(
            conversation,
            [(
                EVENT_QUOTA,
                {
                    "provider": provider,
                    "model": model,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "tokens": prompt_tokens + completion_tokens,
                },
            )],
            same_turn=same_turn,
        )

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

    **角色白名单是 Surface 子集**，不是 :data:`EVENT_TYPES`：结构性事件的
    ``data`` 不是消息字典，把 ``approval_request`` 之类当角色放行等于写一条
    进不了上下文、却又不满足消息契约的日志行（T-M7-04a）。
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
        if role not in SURFACE_EVENT_TYPES:
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


def _prepare_structural_events(
    events: Sequence[tuple[str, Mapping[str, Any]]],
) -> list[tuple[str, dict[str, Any]]]:
    """校验一批结构性事件并转成待写入的 ``(type, data)``。

    **白名单是 :data:`STRUCTURAL_EVENT_TYPES`，不是全集**：Surface 类型只能由
    :func:`_prepare_events` 从 loop 消息流派生——它们的 ``data`` 必须是 API
    消息字典。从这条通道写一条形状不对的 ``user`` / ``assistant`` 进日志，
    要到下一轮模型请求时才以 400 暴露，归因困难。

    ``data`` 浅拷贝一份：调用方后续改动自己的字典不该影响已排队的写入。
    """
    prepared: list[tuple[str, dict[str, Any]]] = []
    for event_type, data in events:
        if event_type not in STRUCTURAL_EVENT_TYPES:
            raise ValueError(
                f"not a structural conversation event type: {event_type!r}",
            )
        prepared.append((event_type, dict(data)))
    return prepared


def _window_chars(window: Sequence[Sequence[dict[str, Any]]]) -> int:
    """窗口的字符量：按消息的序列化长度估。

    入参是 :meth:`ConversationService.history` 已经滤过的那一版窗口——里面
    **只有 Surface 事件**。这个数因此就是模型上下文的字符预算本身：结构性事件
    既不进窗口，也不参与这里的计算（T-M7-04a 之前它们两样都占）。
    """
    return sum(
        len(json.dumps(message, ensure_ascii=False))
        for turn in window
        for message in turn
    )


def _derive_title(messages: Sequence[Mapping[str, Any]]) -> str:
    """首轮消息 → 会话标题：第一条 user 消息压成单行，截断到 :data:`TITLE_MAX_CHARS`。

    压单行是 ``" ".join(text.split())`` ——换行与连续空白一起收拾掉，标题在列表里
    只占一行。取不到 user 消息时返回空串：列表用兜底文案渲染，不编一个假标题。
    """
    for message in messages:
        if str(message.get("role", "")) != EVENT_USER:
            continue
        text = " ".join(str(message.get("content") or "").split())
        if text:
            return text[:TITLE_MAX_CHARS] + ("…" if len(text) > TITLE_MAX_CHARS else "")
    return ""
