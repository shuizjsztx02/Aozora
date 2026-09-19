"""T-M2-02: AI-related models — AIConversation, AIUsage.

T-M7-02f 起 ``AIConversation`` 不再是占位表：它是 agent 会话的**索引**，事件
正文落在 ``ai_conversation_events``（同一文件），会话级配额落在 ``ai_usage``
的 ``conversation_id`` 列上。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import AuditMixin, Base


class AIConversation(Base, AuditMixin):
    """Agent 会话索引（T-M7-02f 激活；此前是 schema 占位）。

    一行 = 一个会话。正文不在这里——事件落在 :class:`AIConversationEvent`，
    模型上下文由那些事件**投影**出来（架构 §6.2：会话不是聊天记录，而是
    append-only 事件日志 + 派生投影）。

    ``summary`` 字段保留给 P3 的 ``replace`` 压缩（把超窗轮次替换为摘要，
    原文仍留在日志里）。**P1 不生成摘要，该列保持空串**。

    ``book_id`` 是会话绑定的账本——系统提示里的「现有对象」名单由它决定。
    ``ConversationService.resolve`` 在请求带的账本与索引不一致时**更新索引**
    （会话跟随当前账本）。**P1 不会因此失效已有历史**：换账本后窗口里仍是被
    换掉那个账本的名字对话，而系统提示已经换成新账本的名单——两者不一致。
    P1 客户端没有「会话进行中换账本」的入口，影响有限，故不做失效（复审 P3-3
    记下的残余）；P2 引入多会话/账本切换时要一并处理。

    T-M7-03a 起它同时是**会话列表的数据源**（架构 §6.1「服务端持有会话摘要」），
    因此补三列：

    - ``title``——列表上的会话名。默认取**首轮第一条用户输入**截断
      （``ConversationService.TITLE_MAX_CHARS``），用户可经 ``PATCH`` 改写。
      新会话、以及一次都没说成的轮次，都可能是空串（客户端展示兜底文案）。
    - ``last_active_at``——最后一次写入事件的时间。**列表按它倒序**。
      不复用 ``updated_at``：那个字段会被重命名、软删、换账本一并改动，列表顺序
      会因此莫名其妙地跳。新会话还没有轮次时为 ``NULL``，列表按 ``created_at`` 兜。
    - ``archived_at``——软删标记（与 ``Category`` / ``SavingsGoal`` 的 ``archived_at``
      同口径：「删除语义＝归档」）。``resolve`` / ``list_conversations`` 一律滤掉
      已归档行，于是删除后旧 ``conversation_id`` 与跨用户 id 得到**同一个 404**，
      不泄露它存在过。
    """

    __tablename__ = "ai_conversations"
    __table_args__ = (
        # T-M7-03a：列表查询的形状——本人的、未归档的、按活跃时间倒序。
        Index(
            "ix_ai_conversations_user_archived_active",
            "user_id", "archived_at", "last_active_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    book_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("books.id", ondelete="SET NULL"),
        nullable=True,
    )
    summary: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    title: Mapped[str] = mapped_column(
        String(120), default="", server_default="", nullable=False,
    )
    last_active_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )


class AIConversationEvent(Base):
    """会话事件日志（T-M7-04a 起为完整形态）。

    事件分两档（架构 §6.2「事件类型全集与 Surface 子集」）：**Surface 事件**
    （``user`` / ``assistant`` / ``tool``）一一对应 loop 消息流里的一条消息，
    ``data`` 就是那条 API 消息字典，是唯一进模型上下文（投影 A）的一档；
    **结构性事件**（审批 / 设备 / 错误 / 配额 / 安全 / 摘要替换）是留档，
    只进投影 B（UI）与投影 C（审计）。两档的划分落为 ``conversation.py`` 的
    ``SURFACE_EVENT_TYPES`` 单一来源常量——**读侧按它过滤，写侧按它派生
    ``ignorable``**，两者的一致性由测试钉死。

    固定语义（不要在这些点上顺手扩展）：

    - ``seq`` 是**会话内**单调递增的序号，从 1 开始，唯一索引钉死。
    - ``turn`` 是**会话内的轮号**，同样从 1 开始，**一轮的全部事件共用一个值**。
      T-M7-04b 起一轮分多次写入（每个配对组一批），但轮号只在第一批量分配——它仍
      是窗口截断与保留期分组的唯一依据：一轮里可以有**多条**
      ``user`` 消息（``AgentLoop.submit()`` 的中途输入在下一个 Step 入口落进消息
      流），所以「按 user 消息切轮」会把一轮切成两半——窗口起点落在轮中间时，
      用户最初的提问会被丢掉、只留下后补的那一句，而「追问指代」正是本卡要解决
      的问题。``turn`` 由写入方一次性分配，读取方只按它分组，不猜。
    - ``time`` 是**事件落进日志**的时刻（写法上由服务端取 ``datetime.now(UTC)``），
      ``created_at`` 是**事务开启**时刻（``server_default=now()``；PG 的 ``now()``
      是 ``transaction_timestamp()``，不是语句时刻）。两者**不等价**：``agent``
      一轮要把整个 loop 跑完才落库，实测二者相差数秒。要「这一轮从什么时候开始」
      看 ``created_at``，要「这条事件什么时候发生」看 ``time``——04b 逐事件落库
      后 ``time`` 改由各事件的实际发生时刻给出，两者的分工才完全展开。
    - ``data`` 是事件载荷。Surface 事件即消息字典（含 ``reasoning_content``；
      assistant 消息**必须**带该键，空串可——真实 API 探针实测缺键会让整轮 400，
      见 §五 02d 记录）；结构性事件的载荷随类型而定，**不进模型上下文**，因此
      可以含内部 ID（那是投影 B 让卡片可执行所需）。
    - ``type`` Surface 取值只有 ``user`` / ``assistant`` / ``tool``；**不存 system**
      ——系统提示由 loop 每轮按 ToolContext 现算（含当天日期与本账本对象名单），
      存下来会在换账本后变成过期数据，且会出现两条 system。
    - **落库批的边界是配对组的边界**（T-M7-04b）：loop 每产出一个批就写一次、提交
      一次，批内**要么**是单条消息（用户输入 / 不带工具调用的回答 / 收尾文案），
      **要么**是「带 ``tool_calls`` 的 assistant 消息 + 它的全部 ``role:"tool"``
      结果」。于是断连时留下的尾巴永远是完整的组——按 ``turn`` 整取（
      :meth:`ConversationService.history`）因此不会切断调用与结果。**结构性事件与
      消息同轮**（如每轮的 ``quota`` 事件），不另占轮号。
    """

    __tablename__ = "ai_conversation_events"
    __table_args__ = (
        # 会话内序号唯一：并发写入撞车时由数据库兜底（读历史要按 seq 有序）。
        Index(
            "ix_ai_conversation_events_conv_seq",
            "conversation_id", "seq",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # T-M7-02f 复审 P2-1：窗口截断的唯一依据，见类 docstring。
    turn: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # T-M7-04a：varchar(32)。实测量过——varchar(16) 正好是全集里最长的
    # ``approval_request`` 的顶格长度、零余量（PG 17.11，17 字符即被拒），
    # 故加倍留余量，免得后续加一个类型名就要再来一次迁移。
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # T-M7-04a：事件时间（写法上由服务端取 UTC 此刻）。``server_default`` 只是
    # 让 DB 层也能填（``created_at`` 同口径）——注意 PG 的 ``now()`` 是**事务**
    # 时刻，与本列由写入方取的时刻不是一回事，见类 docstring。
    time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    # T-M7-04a：结构性事件标记（写侧由 ``SURFACE_EVENT_TYPES`` 派生）。
    # 读侧过滤以常量为准，本列供投影 C 与按类型检索用（04c / 04d）。
    ignorable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


class AIUsage(Base):
    """Per-request AI usage record — append-only audit trail.

    T-M7-02f：一个 agent **Turn 一条行**（``tokens`` 为该轮全部 provider 调用
    之和），``conversation_id`` 落在这条行上——会话级「轮数 + token」双限因此
    是同一张表上的 COUNT / SUM 聚合查询（架构 §12.2 已定表第 3 项）。快路径
    的 interpret / report / parse-image 各行保持 ``conversation_id = NULL``。
    """

    __tablename__ = "ai_usage"
    __table_args__ = (
        # T-M4-01: guest daily-quota check counts rows by
        # (user_id, created_at >= day start) on every /ai/interpret call.
        Index("ix_ai_usage_user_created", "user_id", "created_at"),
        # T-M7-02f: conversation-level turn / token quota aggregates by
        # conversation_id over the conversation's lifetime.
        Index("ix_ai_usage_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # **SET NULL 而不是 CASCADE**：会话行会被登出清理删掉，而 ai_usage 是
    # guest 日配额的计数基座——若随会话一起删，用户只要登出再登入就能把当天
    # 的配额刷回满额。审计行必须活得比会话长。
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_conversations.id", ondelete="SET NULL"),
        nullable=True,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cost: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
