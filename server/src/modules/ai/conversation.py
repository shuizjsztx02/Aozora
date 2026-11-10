"""T-M7-02f: 最小会话状态与用量记账。

服务端持有会话状态（架构 §6.3）——客户端只传 ``conversation_id`` + 本轮输入，
历史由本模块读出并投影成下一次 ``AgentLoop.run(history=…)`` 的输入。P1 只做了
「够多轮上下文」的最小形态；P3 在此之上补齐**完整事件日志与 Surface 子集**
（04a）、**逐事件落库、恢复与分叉**（04b）、**摘要压缩与预算**（04c）。

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
窗口          最近 N 轮原文 + 字符双限（架构 §6.4）。**窗口之外的轮次由摘要
              承载**（04c）——原文仍在日志里，只是不再进模型上下文。
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
3. **配对组同进同出。** 带 ``tool_calls`` 的 assistant 消息与它的全部
   ``role:"tool"`` 结果写在**同一个落库批**里（04b 的 ``agent.MessageSink``），
   :meth:`ConversationService.history` 按 ``turn`` 整轮截断、读侧再裁掉可能半截的
   尾巴（:func:`_paired_prefix_len`）。孤立的调用或结果会被 API 以 400 拒绝，而
   那个 400 要到下一轮请求才炸、归因困难——所以三道防线都要在。

用量记账（T-M7-02f；会话级双限已由 T20-9 / REQ-82 移除）
------------------------------------------------------

``ai_usage`` 上**一个 Turn 一条行**、带 ``conversation_id``——它仍是**日配额**
（``service.check_daily_quota``，按用户/按天）与审计的计数基座，故本模块照旧
每轮写一行（:meth:`ConversationService.record_turn`）。

**会话级「轮数 + token」双限（R-PROD-15 的原缓解手段）在 T20-9 被删掉**：用户
与 AI 的长期会话属正常用法，被 429 拦在中途是把「用了很久」当成故障——而配置项
带 ``gt=0``、没有关闭语义，设成极大值只是把缺陷藏起来（每轮仍跑两次聚合查询，
下一个人调小它缺陷原样回来）。成本护栏只剩两条真实存在的：**摘要压缩**
（:meth:`ConversationService.summarize_overflow`，窗口之外的正文不进上下文）与
**guest 日配额**。``conversation_id`` 列与每轮写入因此都不能动——删它会同时打掉
日配额与审计。

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

保留期（T-M7-04d）
------------------

事件正文的保留期**不在本模块**——口径（按整轮删、只删事件、不动会话行与
``ai_usage``）、参数与调度都在 ``modules/ai/retention.py``，边界依据是
``security.md``「会话事件日志的保留期」。这里只留一句指针，免得下游读者以为
「谁删事件」的答案只有 :meth:`ConversationService.delete` 与
:meth:`ConversationService.purge_user`。

逐事件落库、恢复与分叉（T-M7-04b）
----------------------------------

P1 是**一轮原子写入**（02f 决策 5）：整轮消息攒在 ``AgentResult.messages`` 里，
loop 跑完才一次写进日志。代价是流式路径中途断连 = 整轮回滚，用户看完了答案而
历史里什么都没有（02g-2 决策 5 的已知代价）。04b 把写入粒度改为**逐事件**：loop
每产出一个**落库批**就经 :class:`TurnWriter` 写一次、**提交一次**。

**批的边界就是配对组的边界**（``agent.MessageSink``）：带 ``tool_calls`` 的
assistant 消息与它的全部 ``role="tool"`` 结果同批同提交。于是「半截落库」在结构
上不可能——断连留下的尾巴永远是完整的组，不变式 1（调用与结果配对）不需要任何
读侧修补就成立。批内每条事件各带自己的 ``time``（由 loop 在产出那一刻取，见
``agent.AgentLoop._append``），不再整批共用一个时间戳。

**恢复**——按 ``conversation_id`` 续跑：日志即权威，消费方两个：

- :meth:`ConversationService.history` 是 **loop 侧入口**（就是它，没有第二个）：
  客户端带同一个 ``conversation_id`` 再发一轮，本轮历史由它读出——被中断那一轮
  **已经落库的步骤全部在场**。它的尾部保证干净（见下），所以恢复路径上模型不会
  拿到孤立的 ``tool_calls``。
- ``GET /ai/conversations/{id}/events?after_seq=``（03a 交付）是 **客户端侧入口**：
  断连后按 ``seq`` 增量续读，补齐本地投影落下的部分（严格大于，不重复）；
  ``done.seq``（04b 新增）给出断连前最后一次成功轮次的游标。

**分叉**——:meth:`ConversationService.fork` 从某个 ``seq`` 起开一个新会话，原会话
一行不动。前缀按**事件行**复制（不是让客户端把消息数组传上来——架构 §6.2「用户
回到某轮重说不需要复制消息数组」说的是后者），``seq`` 会被向下取到最近的**完整
配对组边界**（:func:`_paired_prefix_len`）：切在组中间会复制出一个悬空的
``tool_calls``，那正是本卡要保住的不变式。新会话是**独立**的——它有自己的 ``seq``
空间与生命周期，父会话被删除或到期清理都不影响它（代价是前缀存了两份；换来的是
04d 的保留期对分叉无需特判）。

摘要压缩（T-M7-04c）
--------------------

**触发条件是「窗口溢出」，不与 token / 时间并列**（架构 §12.2 未决 #3 的结论，
理由见 :meth:`ConversationService.summarize_overflow`）：窗口本身就是按轮数裁的，
「有轮次落在窗口之外」就是「需要摘要」的定义本身。压缩的产物是一条 ``replace``
事件（**原文一行不删**，仍可按 ``seq`` 取到、仍进投影 B / C）加上
``AIConversation.summary`` 列。

**摘要怎么进上下文**：不在 ``history()`` 的返回值里，而是经
``AgentLoop.run(summary=…)`` 拼进**系统提示**的一段。两个理由：① 它不对应日志里
的任何一条 Surface 事件，塞进 messages 数组等于让窗口里多出一条无来源的消息；
② ``[system] + history`` 这个数组形状是 02d 以来被真实链路验过的，不动它就不会
多出「两条 system」这类只在真实 provider 上才暴露的问题。

**摘要的读取面**：``summary`` 列是**投影**，权威仍在日志——:meth:`fork` 因此从
新会话自己的前缀里重新派生它，而不是复制父会话的列。保留期（04d）删掉正文之后
摘要也必须跟着消失，那条判定在 ``retention.purge_expired_summaries``。
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.config import Settings, get_config
from src.common.errors import AppException
from src.models.ai import AIConversation, AIConversationEvent
from src.models.ledger import Book
from src.models.user import User

from .prompts import CONVERSATION_SUMMARY
from .service import build_usage_row

if TYPE_CHECKING:
    # 只为标注：`agent` 是 loop 那一层，本模块不在运行期依赖它（`MessageStamp`
    # 的形状即「消息字典 + 产出时刻」，见 `agent.MessageStamp`）。
    from .agent import MessageStamp

    # 同上：摘要生成只用到 `complete` 这一条通路，`DeepSeekProvider` 同时满足
    # `LLMProvider` 与 `ToolCallingProvider`，这里按前者标注（04c）。
    from .provider import LLMProvider

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
EVENT_DEVICE_REQUEST = "device_request"      # [不接] 设备指令：由配对的 tool 结果留档（ADR-010 决策 5）
EVENT_ERROR = "error"                        # [stream._record_failure] 04b
EVENT_REPLACE = "replace"                    # [04c] 长历史被摘要替换（原文保留）
EVENT_QUOTA = "quota"                        # [record_turn] 每轮用量，与消息同轮
EVENT_SECURITY = "security"                  # [不接] 访问面审计结论：见 security.md（04d）
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

#: M8-03：会话检索文本（``AIConversation.search_text``）的字符上限。
#: 超出时保留**尾部**——最近说的话比开场白更可能被拿来检索；列首留一个
#: 截断标记。初值 4096（容量与检索质量实测后调整）。
SEARCH_TEXT_MAX_CHARS = 4096
SEARCH_TEXT_TRUNCATED_MARK = "…"

#: UUID 形状的内部 ID。摘要里出现任何一个都违反架构 §6.3 的反幻觉约束
#: （「历史与摘要只含自然语言，不得出现 UUID / 内部 ID」）。口径与
#: ``tests/test_ai_conversation.py`` 的窗口用例同一条正则——**同一个形状，
#: 两处断言**：那边管窗口，这边管摘要。
_INTERNAL_ID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

#: 送进摘要提示时每条消息的截断上限。用户输入本身就受单轮 500 字符的约束
#: （架构 §6.4），会超的是助手的长回答——摘要是要**压缩**它，不是复述它，
#: 单条给几百字符足够模型抓到结论。
_SUMMARY_MESSAGE_CHARS = 400

#: 会话 id 本身是内部 ID，**不进历史正文**，只作为请求参数出现。
ERR_INVALID_CONVERSATION_ID = "invalid_conversation_id"
#: 不存在 / 不属于调用者一律报同一个码——跨用户不可分辨（security.md 的读取面口径）。
ERR_CONVERSATION_NOT_FOUND = "conversation_not_found"
#: 分叉点不在这个会话的事件范围内（T-M7-04b）。与 422 的入参错误同档：``seq`` 是
#: 客户端从事件读面拿到的，给出一个超出范围的值是调用方的错，不是「会话没了」。
ERR_INVALID_FORK_SEQ = "invalid_fork_seq"


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

        **摘要一并清空**（04c）：``summary`` 是那些被删轮次的压缩，正文都没了还
        留着它，等于给「用户删掉的对话」开了一扇后门——它仍会进下一轮的系统提示。
        保留期那条路径上的同一判定在 ``retention.purge_expired_summaries``。

        幂等性由 :meth:`load_owned` 保证：归档之后它就取不到这一行，再删一次
        是同一个 404（客户端重试因此安全）。
        """
        await self._db.execute(
            delete(AIConversationEvent).where(
                AIConversationEvent.conversation_id == conversation.id,
            )
        )
        conversation.summary = ""
        # M8-03：检索面是正文的派生副本。正文删了它还留着，等于把用户主动
        # 删除的对话从后台的搜索入口里留了个后门（与摘要列同一口径）。
        conversation.search_text = ""
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

        **这就是 loop 侧的恢复入口（T-M7-04b）。** 断连之后「接着跑」不需要别的
        入口：客户端带同一个 ``conversation_id`` 再发一轮，本轮历史就是从这里读
        出来的——被中断那一轮**已经落库的步骤全部在场**，模型因此知道做到哪儿了，
        而不是从零开始（P1 的一轮原子写入下，那些步骤一条都不在）。

        **尾部保证干净**（:func:`_paired_prefix_len`）：日志末尾若停在配对组中间，
        这里只取到上一个安全点。正常写入路径下这是空转——落库批的边界就是配对组
        的边界（:class:`TurnWriter`），尾巴不可能半截；它防的是**别的写入方**：
        分叉的前缀切点、并发写同一会话的交叉、以及将来任何绕过 sink 的写路径。
        模型上下文里出现孤立的 ``tool_calls`` 会被 API 以 400 拒绝，这条底线不该
        只靠「写路径写得对」。
        """
        turns = await self._surface_turns(conversation)
        window = self._window(conversation, turns)
        return [message for turn, messages in window for message in messages]

    async def _surface_turns(
        self, conversation: AIConversation,
    ) -> list[tuple[int, list[dict[str, Any]]]]:
        """读 **Surface 事件**并按 ``turn`` 分组，返回 ``[(turn, messages), …]``。

        **两个消费方共用**（T-M7-04c）：:meth:`history` 投影上下文窗口，
        :meth:`summarize_overflow` 决定哪些轮次要进摘要。共用是刻意的——压缩要
        覆盖的正是「模型看不见的那部分」，两处若各算各的分组，迟早漂移成
        「窗口外」与「摘要覆盖」两张对不上的表。

        白名单下推到 SQL（04a）：结构性事件根本不取出来，它们没有上限（每轮一条
        quota、将来每个提案一条审批），取出来只为丢掉是纯浪费。

        尾部裁到配对组边界（:func:`_paired_prefix_len`）也在这里做：日志末尾若停在
        配对组中间，只取到上一个安全点。正常写入路径下这是空转——落库批的边界就是
        配对组的边界（:class:`TurnWriter`），尾巴不可能半截；它防的是**别的写入
        方**：分叉的前缀切点、并发写同一会话的交叉、以及将来任何绕过 sink 的写
        路径。模型上下文里出现孤立的 ``tool_calls`` 会被 API 以 400 拒绝，这条
        底线不该只靠「写路径写得对」。
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

        paired = _paired_prefix_len([(row.type, row.data) for row in rows])
        if paired < len(rows):
            logger.warning(
                "conversation log ends mid pairing group, trimming tail",
                extra={
                    "conversation_id": str(conversation.id),
                    "dropped_events": len(rows) - paired,
                    "outcome": "unpaired_tail_trimmed",
                },
            )
            rows = rows[:paired]

        turns: list[tuple[int, list[dict[str, Any]]]] = []
        for row in rows:
            if not turns or turns[-1][0] != row.turn:
                turns.append((row.turn, []))
            turns[-1][1].append(dict(row.data))
        return turns

    def _window(
        self,
        conversation: AIConversation,
        turns: Sequence[tuple[int, list[dict[str, Any]]]],
    ) -> list[tuple[int, list[dict[str, Any]]]]:
        """窗口选择：最近 N 轮 + 字符双限（架构 §6.4），返回 ``(turn, messages)``。

        带上轮号是 04c 的需要：压缩问的是「哪些轮次落在窗口之外」，只有消息列表
        答不了这个问题。
        """
        window = list(turns[-self._cfg.ai_agent_history_turns:])
        while len(window) > 1 and _window_chars(
            [messages for _, messages in window]
        ) > self._cfg.ai_agent_history_chars:
            dropped = window.pop(0)
            logger.info(
                "agent history window truncated by char budget",
                extra={
                    "conversation_id": str(conversation.id),
                    "dropped_messages": len(dropped[1]),
                    "outcome": "history_char_limit",
                },
            )
        return window

    # -- 摘要压缩（04c）---------------------------------------------------

    async def summarize_overflow(
        self, conversation: AIConversation, provider: LLMProvider,
    ) -> bool:
        """把**已经落在窗口之外**的轮次压缩进摘要，写一条 ``replace`` 事件。

        返回是否真的产出了新摘要（供调用方记日志）；**任何失败都返回 False 且不
        抛**。

        **触发条件就是「窗口溢出」，不另立 token / 时间尺子**（架构 §12.2 未决 #3
        的结论）。三条理由：① 窗口本身就按轮数裁，「有轮次落在窗口之外」就是
        「需要摘要」的定义本身，另立一把尺子只会让触发点与窗口脱节；② token 在
        **写入侧拿不到**（要 provider 返回 usage 才知道），按它触发意味着压缩永远
        滞后一轮，而那正是窗口最挤的时候；③ 时间更没有依据——会话可能一周才被
        拾起一次，按时间触发会把刚说过的话摘要掉。

        **覆盖范围由窗口算出来，不由调用方给**：``_window`` 正是 :meth:`history`
        用的那一个，所以「摘要覆盖的」与「模型看不见的」是同一件事，不是两处各
        自维护的近似。

        **增量**：上一次压缩覆盖到 ``upto_turn``（从最后一条 ``replace`` 事件读，
        **不从列读**——列是投影），本次只摘要它之后、窗口之前的轮次，并把上一版
        摘要一并交给模型合并。摘要因此始终是「第 1 轮到窗口起点」的一整段。

        **原文一行不删**：压缩只是**新增**一条事件，被摘要的 ``user`` /
        ``assistant`` / ``tool`` 行原样留在日志里，``GET /ai/conversations/{id}/
        events`` 仍读得到（审计用例钉死这一点）。这是架构 §6.2「压缩无损」的定义。

        ``same_turn=True``：压缩是**本轮的收尾维护**，不占新轮号——另立一个只含
        ``replace`` 的轮次会在日志里留下一个没有对话内容的轮号。
        """
        turns = await self._surface_turns(conversation)
        if not turns:
            return False
        window = self._window(conversation, turns)
        if not window:
            return False

        covered = await self._covered_turn(conversation)
        window_start = window[0][0]
        overflow = [
            (turn, messages) for turn, messages in turns
            if covered < turn < window_start
        ]
        if not overflow:
            return False

        try:
            summary = await self._generate_summary(
                provider, conversation.summary, overflow,
            )
        except Exception as exc:  # noqa: BLE001 — 摘要失败不阻塞本轮
            logger.warning(
                "conversation summary generation failed",
                extra={
                    "conversation_id": str(conversation.id),
                    "error": type(exc).__name__,
                    "outcome": "summary_generation_failed",
                },
            )
            return False
        if summary is None:
            logger.warning(
                "conversation summary rejected",
                extra={
                    "conversation_id": str(conversation.id),
                    "limit": self._cfg.ai_agent_summary_chars,
                    "outcome": "summary_rejected",
                },
            )
            return False

        await self.append_events(conversation, [(
            EVENT_REPLACE,
            {
                "summary": summary,
                "upto_turn": overflow[-1][0],
                "turns": len(overflow),
            },
        )], same_turn=True)
        conversation.summary = summary
        await self._db.flush()
        logger.info(
            "conversation history compressed into summary",
            extra={
                "conversation_id": str(conversation.id),
                "upto_turn": overflow[-1][0],
                "turns": len(overflow),
                "outcome": "history_summarized",
            },
        )
        return True

    async def _covered_turn(self, conversation: AIConversation) -> int:
        """上一次压缩覆盖到的轮号（一条 ``replace`` 事件都没有时为 0）。"""
        data = await self._db.scalar(
            select(AIConversationEvent.data)
            .where(
                AIConversationEvent.conversation_id == conversation.id,
                AIConversationEvent.type == EVENT_REPLACE,
            )
            .order_by(AIConversationEvent.seq.desc())
            .limit(1)
        )
        return _upto_turn(data)

    async def _generate_summary(
        self,
        provider: LLMProvider,
        previous: str,
        overflow: Sequence[tuple[int, list[dict[str, Any]]]],
    ) -> str | None:
        """调一次 provider 产摘要；**不可用返回 None**（调用方什么都不写）。

        超时用单步超时（``ai_agent_step_timeout_seconds``）：这是一次单发补全，
        与 loop 里的一次模型请求同类，没有理由另设一个数字。
        """
        result = await provider.complete(
            [
                {"role": "system", "content": CONVERSATION_SUMMARY.system_prompt},
                {
                    "role": "user",
                    "content": _render_summary_input(previous, overflow),
                },
            ],
            model=self._cfg.ai_agent_provider_model,
            max_tokens=CONVERSATION_SUMMARY.max_tokens,
            timeout=self._cfg.ai_agent_step_timeout_seconds,
        )
        return _usable_summary(result.content, self._cfg.ai_agent_summary_chars)

    # -- 恢复与分叉（04b）-------------------------------------------------

    async def max_seq(self, conversation: AIConversation) -> int:
        """会话当前的最大 ``seq``（**没有任何事件时为 0**）。

        它就是「接着这里往下跑」的游标。消费方两个：``done`` 帧把它交给客户端
        （断连重连时用作 ``after_seq`` 的起点），:meth:`fork` 用它校验分叉点。

        **不是事件条数**：保留期（04d）会删掉最早的整轮，``seq`` 因此可能不从 1
        开始、也不再连续——它是会话内的序号，不是计数。
        """
        return int(await self._db.scalar(
            select(func.coalesce(func.max(AIConversationEvent.seq), 0)).where(
                AIConversationEvent.conversation_id == conversation.id,
            )
        ) or 0)

    async def commit(self) -> None:
        """提交当前事务——**逐事件落库的提交点**（T-M7-04b）。

        P1 只在轮末提交一次（``stream._execute_turn``），断连即整轮回滚；04b 把
        提交点下推到落库批的边界，由 :class:`TurnWriter` 每批调用一次。
        """
        await self._db.commit()

    async def fork(
        self, conversation: AIConversation, upto_seq: int,
    ) -> tuple[AIConversation, int]:
        """从 ``upto_seq`` 起分叉出新会话，返回 ``(新会话, 实际切点 seq)``。

        **原会话一行不动**：不写它、不归档它、不动它的 ``last_active_at``。分叉出去
        的是一个**独立**的会话——自己的 ``seq`` 空间（前缀按原值复制，新事件接在
        后面）、自己的生命周期（父会话被删除或到期清理都不影响它）。

        **切点向下取整到完整配对组**（:func:`_paired_prefix_len`）：客户端给的
        ``seq`` 可能落在某条 ``assistant`` 与它的 ``tool`` 结果之间，照切会复制出
        一个悬空的 ``tool_calls``——那正是不变式 1 禁止的形状。返回**实际**切点，
        调用方据此知道复制到哪儿，而不是以为就是自己给的那个数。

        ``upto_seq`` 超出事件范围（小于最小 ``seq``，或大于最大 ``seq``）报 422
        ``invalid_fork_seq``：它来自客户端的读面，越界说明调用方算错了；静默夹到
        边界只会让它以为分叉点是自己给的那一个。
        """
        events = await self.events(conversation)
        if not events or not events[0].seq <= upto_seq <= events[-1].seq:
            raise AppException(
                422, ERR_INVALID_FORK_SEQ,
                "Fork point is outside this conversation's event range",
            )
        prefix = [event for event in events if event.seq <= upto_seq]
        cut = _paired_prefix_len([(event.type, event.data) for event in prefix])
        if cut < 1:
            # 前缀里连一条完整事件都没有（第一条就是悬空的调用）：没有可复制的
            # 历史，分叉没有意义。
            raise AppException(
                422, ERR_INVALID_FORK_SEQ,
                "Fork point leaves nothing complete to copy",
            )

        forked = AIConversation(
            user_id=conversation.user_id,
            book_id=conversation.book_id,
            title=conversation.title,
            # 摘要从**新会话自己的前缀**重新派生，而不是复制父会话的列（04c）：
            # 切点可能落在父会话最后一条 ``replace`` 之前，那时复制列会让新会话
            # 带着一段「描述它没有的历史」的摘要。派生则天然一致——前缀里有
            # 哪条 ``replace``，摘要就停在哪一轮。
            summary=_replace_summary_in(prefix[:cut]),
            # M8-03：检索面同样从**新会话自己的前缀**派生（与摘要同一条理由）——
            # 复制父会话的列会让新会话带着一段它并不拥有的历史。
            search_text=_compose_events_search_text(prefix[:cut]),
        )
        self._db.add(forked)
        await self._db.flush()
        await self._db.execute(
            insert(AIConversationEvent),
            [
                {
                    "conversation_id": forked.id,
                    # 前缀原样搬：``seq`` 与 ``turn`` 都是**会话内**编号，原值复制
                    # 在新会话里正好接着往下数，无需重编（重编还会让客户端手里
                    # 那个「上一批最后一条的 seq」对不上）。
                    "seq": event.seq,
                    "turn": event.turn,
                    "type": event.type,
                    "data": event.data,
                    "time": event.time,
                    "ignorable": event.ignorable,
                    "request_id": event.request_id,
                }
                for event in prefix[:cut]
            ],
        )
        # 列表按活跃时间倒序——分叉是一次**新**动作，它该出现在列表上方；父会话的
        # ``last_active_at`` 不动（那会改到原会话，而本方法承诺原会话一行不动）。
        forked.last_active_at = datetime.now(UTC)
        await self._db.flush()
        return forked, prefix[cut - 1].seq

    # -- 写入 -------------------------------------------------------------

    async def append_turn(
        self, conversation: AIConversation, messages: Sequence[Mapping[str, Any]],
        *, times: Sequence[datetime] | None = None, same_turn: bool = False,
        request_id: str | None = None,
    ) -> int:
        """把一批消息追加进日志（整轮 = 一次 ``AgentResult.messages``，或 04b 的
        一个**落库批**）。

        ``messages`` 是 loop 给出的**本次增量**（04b 之后不再等同于整轮）——**只
        接受 loop 产出的消息**：把 ``history + delta`` 一起传进来会把历史重复写
        一遍；传一轮的中间片段则触发下面两条校验。校验在写入**前**一次性做完
        ——半截写入会让下一轮的历史出现孤立 ``tool`` 消息，而那要到模型请求时才
        以 400 暴露，归因困难。校验失败抛的是 ``ValueError``（调用方编程错误，
        不是可降级的业务错误；loop 自身产出的 delta 结构上不可能触发）。

        ``times`` 是每条消息**产出**的时刻（``agent.MessageStamp``，T-M7-04b）：
        逐事件落库的调用方按它给事件时间；不给时整批共用「此刻」，即 P1 的整轮
        写入口径（也用于测试与老调用点）。长度必须与 ``messages`` 一一对应。

        ``same_turn`` 见 :meth:`append_events`：一轮可以写好几批（每个配对组一批），
        但**轮号只有一个**——第一批分配新轮号，其余各批挂上去。

        返回写入的事件条数。

        写成功后顺手更新会话索引（:meth:`_note_activity`）：标题在空的时候由首轮
        输入派生一次，活跃时间每次刷新——列表的排序与标题因此不用另开一条写路径。
        """
        prepared = _prepare_events(messages)
        if not prepared:
            return 0

        written = await self._insert_events(
            conversation, prepared, same_turn=same_turn, times=times,
            request_id=request_id,
        )
        self._note_activity(conversation, messages)
        await self._db.flush()
        return written

    async def append_events(
        self,
        conversation: AIConversation,
        events: Sequence[tuple[str, Mapping[str, Any]]],
        *,
        same_turn: bool = False,
        request_id: str | None = None,
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

        written = await self._insert_events(
            conversation, prepared, same_turn=same_turn, request_id=request_id,
        )
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
        times: Sequence[datetime] | None = None,
        request_id: str | None = None,
    ) -> int:
        """把一批已校验的事件写进日志：分配 ``(seq, turn)``、撞车重试一次。

        ``times`` 逐条给出事件的**产出**时刻（T-M7-04b 的逐事件落库路径）；不给时
        整批共用「此刻」。它与 ``created_at`` **不等价**：后者是 ``server_default
        now()`` = PG 的 ``now()`` = **事务**时刻。逐事件落库之后一次提交覆盖一个
        落库批，两个时间戳因此贴得很近——但那仍是巧合而不是等式：``time`` 回答的
        是「这条事件什么时候发生」（loop 产出它的那一刻），``created_at`` 回答的
        是「这个事务什么时候开的」。批内两条事件（如 assistant 与它之后跑完的
        工具结果）之间隔着整段工具执行，只给一个时间戳会把那段间隔抹平。

        ``ignorable`` 按 :data:`SURFACE_EVENT_TYPES` **派生**而不是由调用方给：
        它与 :meth:`history` 的过滤条件必须同源，否则会出现「标了可忽略却进了
        上下文」的漂移。
        """
        if times is None:
            times = [datetime.now(UTC)] * len(prepared)
        elif len(times) != len(prepared):
            # 只能来自调用方：`_prepare_events` 会丢掉 system 消息，那一刻长度就
            # 对不上了。给错位的时间戳比不给更糟——它会静默地张冠李戴。
            raise ValueError("event times must align one-to-one with messages")
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
                                "time": event_time,
                                "ignorable": event_type not in SURFACE_EVENT_TYPES,
                                # M8-03：同一轮的每一批都带同一个 request_id；
                                # 历史行由列默认值 NULL 兜（不做近似回填）。
                                "request_id": request_id,
                            }
                            for offset, ((event_type, data), event_time) in enumerate(
                                zip(prepared, times, strict=True)
                            )
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
        # M8-03：检索面与正文同寿。只在 Surface 写入路径累积；结构性事件
        # （quota / error / 审批）不进检索面。
        _append_search_text(conversation, messages)

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
            request_id=request_id,
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


class TurnWriter:
    """一轮的**逐事件写入器**（T-M7-04b）——loop 的 ``agent.MessageSink`` 在
    服务端的落点。

    与 :meth:`ConversationService.append_turn` 只差一件事：**每写一批就提交**。
    P1 的「一轮原子写入」把整轮压进一次提交，于是流式路径中途断连 = 全部回滚、
    日志里什么都没有（02g-2 决策 5 的已知代价）。逐批提交把窗口缩到「当前这一
    批」，而批的边界就是配对组的边界（``agent.MessageSink``）——断连留下的因此
    永远是完整的组，不变式 1 不需要任何读侧修补就成立。

    提交点同时是「写失败要穿透」的落点：``commit`` 抛异常时异常向上穿过 loop，
    由端点按本轮失败处理（``stream._execute_turn``）。**不吞**——静默丢事件会把
    「这一轮已经存下来了」变成假话。

    ``same_turn`` 只对 :meth:`structural` 有意义：用量事件属于刚写完的那一轮。
    """

    def __init__(
        self, service: ConversationService, conversation: AIConversation,
        *, request_id: str | None = None,
    ) -> None:
        self._service = service
        self._conversation = conversation
        #: M8-03：本轮请求标识，随每一批事件落库（后台按它精确反查）。
        self._request_id = request_id

    async def __call__(
        self, messages: Sequence[MessageStamp], *, opens_turn: bool = False,
    ) -> None:
        """写一个落库批（loop 的消息 + 产出时刻）并提交。

        ``opens_turn`` 由 loop 声明「这一批开了一轮」（用户输入那批）：它分配新的
        轮号，其余各批（每个配对组、收尾文案）共用同一个——一轮的 ``turn`` 只能有
        一个值，那是窗口截断与保留期分组的依据（见 ``models/ai.py`` 的 ``turn``）。
        """
        written = await self._service.append_turn(
            self._conversation,
            [message for message, _ in messages],
            times=[stamp for _, stamp in messages],
            same_turn=not opens_turn,
            request_id=self._request_id,
        )
        if written:
            await self._service.commit()

    async def structural(
        self, event_type: str, data: Mapping[str, Any], *, same_turn: bool = False,
    ) -> None:
        """写一条结构性事件并提交（失败轮的 ``error``、成功轮的 ``quota`` 等）。"""
        written = await self._service.append_events(
            self._conversation, [(event_type, data)], same_turn=same_turn,
            request_id=self._request_id,
        )
        if written:
            await self._service.commit()


# ---------------------------------------------------------------------------
# 事件与窗口的纯函数
# ---------------------------------------------------------------------------

def _paired_prefix_len(
    events: Sequence[tuple[str, Mapping[str, Any]]],
) -> int:
    """最大的 ``k``，使 ``events[:k]`` 里**每个工具调用都配到了结果**。

    这是不变式 1（架构 §4.4）在**读侧**的判据，两个消费方：:meth:`ConversationService.history`
    用它把可能半截的日志尾巴裁掉（模型上下文里出现孤立的 ``tool_calls`` 会被 API
    以 400 拒绝），:meth:`ConversationService.fork` 用它把分叉点向下取整到安全切点。

    配对规则照搬 ``agent`` 的写侧口径：``assistant`` 的 ``data.tool_calls`` 开启
    一个配对组（组内**全部** id 都要有 ``tool`` 结果销账），不带 ``tool_calls`` 的
    ``assistant`` 关闭它。``user`` 与结构性事件不改变配对状态——它们落在组的哪一侧
    都不影响「有没有悬空的调用」这个判断。

    正常写入路径下它是空转（落库批的边界就是配对组的边界）：它防的是别的写入方
    ——分叉的前缀切点、并发写同一会话的交叉、以及将来任何绕过 sink 的写路径。
    """
    pending: set[str] = set()
    safe = 0
    for index, (event_type, data) in enumerate(events):
        if event_type == EVENT_ASSISTANT:
            calls = data.get("tool_calls") or []
            if not calls:
                pending.clear()
                safe = index + 1
                continue
            pending = {
                str(call.get("id", ""))
                for call in calls
                if isinstance(call, Mapping)
            }
        elif event_type == EVENT_TOOL and pending:
            pending.discard(str(data.get("tool_call_id", "")))
        if not pending:
            safe = index + 1
    return safe


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


def _compose_search_text(messages: Sequence[Mapping[str, Any]]) -> str:
    """从消息序列派生检索文本：**只收 user / assistant 的自然语言**（M8-03）。

    ``tool`` 结果是 JSON 结构、``reasoning_content`` 是模型内部推理，两者都不该
    让「按关键词找会话」命中——用户从没在对话里说过那些词。
    """
    parts: list[str] = []
    for message in messages:
        role = str(message.get("role", ""))
        if role not in (EVENT_USER, EVENT_ASSISTANT):
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            parts.append(content)
    return _truncate_search_text("\n".join(parts))


def _compose_events_search_text(
    events: Sequence[AIConversationEvent],
) -> str:
    """同 :func:`_compose_search_text`，输入是已落库的事件行（分叉用）。"""
    parts: list[str] = []
    for event in events:
        if event.type not in (EVENT_USER, EVENT_ASSISTANT):
            continue
        content = (event.data or {}).get("content")
        if isinstance(content, str) and content.strip():
            parts.append(content)
    return _truncate_search_text("\n".join(parts))


def _truncate_search_text(text: str) -> str:
    """上限截断，**保留尾部**：最近说的话比开场白更可能被检索。"""
    if len(text) <= SEARCH_TEXT_MAX_CHARS:
        return text
    keep = SEARCH_TEXT_MAX_CHARS - len(SEARCH_TEXT_TRUNCATED_MARK)
    return SEARCH_TEXT_TRUNCATED_MARK + text[-keep:]


def _append_search_text(
    conversation: AIConversation, messages: Sequence[Mapping[str, Any]],
) -> None:
    """把这一批 Surface 消息追加进会话检索面（与事件同事务）。"""
    chunk = _compose_search_text(messages)
    if not chunk:
        return
    current = conversation.search_text or ""
    combined = f"{current}\n{chunk}" if current else chunk
    conversation.search_text = _truncate_search_text(combined)


async def rebuild_search_text(
    db: AsyncSession, conversation_ids: Sequence[uuid.UUID],
) -> int:
    """按当前事件日志**重算**这些会话的检索文本，返回更新行数（M8-03）。

    保留期清理删掉事件之后必须调用：``search_text`` 是正文的派生副本，正文没了
    它也不能留——否则保留期从后台搜索入口被绕过去（与
    ``retention.purge_expired_summaries`` 的摘要同一口径）。与写入路径共用
    :func:`_truncate_search_text`，两处口径不漂移。
    """
    if not conversation_ids:
        return 0
    updated = 0
    for conversation_id in conversation_ids:
        rows = (await db.execute(
            select(AIConversationEvent.type, AIConversationEvent.data)
            .where(AIConversationEvent.conversation_id == conversation_id)
            .order_by(AIConversationEvent.seq)
        )).all()
        parts: list[str] = []
        for event_type, data in rows:
            if event_type not in (EVENT_USER, EVENT_ASSISTANT):
                continue
            content = (data or {}).get("content")
            if isinstance(content, str) and content.strip():
                parts.append(content)
        result = await db.execute(
            update(AIConversation)
            .where(AIConversation.id == conversation_id)
            .values(search_text=_truncate_search_text("\n".join(parts)))
        )
        updated += result.rowcount or 0
    await db.flush()
    return updated


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


def _upto_turn(data: object) -> int:
    """``replace`` 事件载荷里的覆盖轮号；形状不对一律当 0（＝没覆盖过）。

    **从事件读、不从列读**：``AIConversation.summary`` 是投影，日志才是权威
    （模块 docstring）。形状校验按「宁可重做一次摘要，不可漏摘要一批轮次」——
    把坏载荷当 0 的代价是多摘要几轮，不校验就会永久跳过它们。
    """
    if isinstance(data, Mapping):
        value = data.get("upto_turn")
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return 0


def _replace_summary_in(events: Sequence[AIConversationEvent]) -> str:
    """一段事件前缀里最后一条 ``replace`` 的摘要文本（没有则为空串）。

    分叉用它给新会话定摘要：前缀复制到哪儿，摘要就覆盖到哪儿（04c）。形状不对
    的载荷当没有——与 :func:`_upto_turn` 同一条口径：「宁可重做一次摘要，不可
    漏摘要一批轮次」。
    """
    for event in reversed(events):
        if event.type != EVENT_REPLACE:
            continue
        data = event.data
        if isinstance(data, Mapping) and isinstance(data.get("summary"), str):
            return str(data["summary"])
        return ""
    return ""


def _usable_summary(text: str, limit: int) -> str | None:
    """摘要的可用性校验：非空、不超字符上限、不含内部 ID。

    三条各自对应一条硬约束——空摘要等于没压缩；``limit`` 是架构 §6.4 的摘要预算
    （``Settings.ai_agent_summary_chars``）；UUID 是 §6.3 的反幻觉约束（「历史与
    摘要只含自然语言，不得出现 UUID / 内部 ID」）。

    **任一不过整条退回**（返回 ``None``），调用方的动作是「什么都不写、沿用上一版
    摘要」——这正是 §6.4 写的降级方式（「超限 → 使用上一版摘要」）。不截断也不
    修补：半句摘要会被下一轮当成事实，比没有摘要更糟；改写模型的话只会引入新的
    事实错误。首次压缩就没有上一版——那种情况下降级为「不压缩」，即回到 P1 的
    行为（超窗轮次直接丢），本轮照常跑完。
    """
    summary = " ".join(str(text or "").split())
    if not summary or len(summary) > limit:
        return None
    if _INTERNAL_ID_RE.search(summary):
        return None
    return summary


def _clip_summary_text(raw: object) -> str:
    """压成单行并截断——摘要提示里每条消息的字符上限（:data:`_SUMMARY_MESSAGE_CHARS`）。"""
    text = " ".join(str(raw or "").split())
    if len(text) > _SUMMARY_MESSAGE_CHARS:
        return text[:_SUMMARY_MESSAGE_CHARS] + "…"
    return text


def _render_summary_input(
    previous: str,
    overflow: Sequence[tuple[int, list[dict[str, Any]]]],
) -> str:
    """摘要提示的 user 段：上一版摘要（若有）+ 新增的对话轮次。

    **只渲染 ``user`` 与 ``assistant`` 的正文**：``tool`` 消息整条跳过，正文为空的
    ``assistant``（只有 ``tool_calls`` 的那种）也不出现。这是「摘要不含内部 ID」
    在**输入侧**的落点——工具消息是日志里唯一可能带 id 的地方（写工具的提案草案
    含服务端解析出的 id），不让它进提示，模型就没有可复述的来源；工具调用的过程
    本来也不该进摘要（模板规则 2：只记结论）。输出侧另有一道校验
    （:func:`_usable_summary`）——两侧都要在，输入侧的干净是设计，不是证明。
    """
    parts: list[str] = []
    if previous:
        parts.append(f"【已有摘要】\n{previous}")

    lines: list[str] = []
    for turn, messages in overflow:
        lines.append(f"（第 {turn} 轮）")
        for message in messages:
            role = str(message.get("role", ""))
            if role not in SURFACE_EVENT_TYPES or role == EVENT_TOOL:
                continue
            content = _clip_summary_text(message.get("content"))
            if not content:
                continue
            lines.append(f"{'用户' if role == EVENT_USER else '助手'}：{content}")
    parts.append("【新增对话】\n" + "\n".join(lines))
    return "\n\n".join(parts)


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
