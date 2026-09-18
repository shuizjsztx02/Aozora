"""T-M7-02g: agent 路径的 SSE 传输层（帧格式化 + 进度观察者 + 流生成器）。

架构依据 §8（流式协议设计）与 §11.3（灰度开关），风险登记 R-PROD-22。
本模块是 agent 循环（02d/02e）与 HTTP 之间唯一的粘合层：**不重复 loop 的任何
判定**（边界、审批、校验、执行都在 ``agent.py`` 里），只负责把 loop 的进度与
产物翻译成 SSE 帧。

零新依赖
--------
用 Starlette 原生 ``StreamingResponse`` + 下面约 30 行的 :func:`format_frame`
手写帧格式化，**不引 ``sse-starlette``**（卡面职责边界）。帧格式即 SSE 规范的
最小集::

    event: tool_call\\n
    data: {"tool":"budget_list",...}\\n
    \\n

``data`` 只有一行：``json.dumps`` 会把换行转义成 ``\\n``，因此载荷文本里即便
含换行也不会破坏帧边界。

八类事件与 P1 的生产者
----------------------
架构 §8.1 的八类事件全部由 :class:`EventType` 定义、由 :func:`format_frame`
格式化（各有用例），但 P1 的 loop **只产出其中六类**：

========================  ================================================
事件                      P1 生产者
========================  ================================================
``phase``                 loop 的 Step 入口（``thinking``）与工具批次入口
                          （``tool_calling``）。``routing`` 属域路由，P4 才有
``tool_call``             工具批次里每个调用**执行前**（名字 + 人类可读文案）
``tool_result``           同一次调用结束后（**摘要，不含原始数据**）
``confirm``               写提案**挂起**时（02e 决策 2），载荷为完整提案
``device_request``        **无生产者**——P1 没有设备类工具（域只有记账 / 预算 /
                          账户账本三个），设备能力通道归 P2（架构 §4.3）
``delta``                 助手正文与推理的增量，见下「delta 的粒度与两个通道」
``done``                  收尾（含 usage、stop_reason、提案与执行回执）
``error``                 中途失败的安全枚举码（见下「失败收尾」）
========================  ================================================

**确认卡走的是既有 v2 链路，不是流内回答**：本模块不接 ``AgentLoop`` 的
``approver`` 通道——02h 的桥命令里只有 ``ai.agent`` / ``ai.cancel``，没有
「上行审批」，客户端在流内无从回答（架构 §8.2 也把确认归到普通 HTTP 上行）。
因此挂起的提案随 ``confirm`` 帧送出、并原样列在 ``done.proposals`` 里，由客户端
照 02i 的既定设计在那之后走既有卡链路（``/ai/validate`` → ``/ai/execute``）。
``confirm`` 是**实时信号**，``done.proposals`` 是**权威清单**，按 ``proposalId``
去重即可。

各事件的载荷（02h / 02i 的编码依据）
------------------------------------
架构 §8.1 只规定到「载荷大意」，字段以本表为准。``step`` 是 1 起的 Step 序号
（步骤卡片分组用，与 ``confirm`` 的 ``step`` 同源）；带 ``*`` 的是 P1 对 §8.1
的**追加字段**，纯增量、客户端可忽略：

===============  ============================================================
事件             载荷字段
===============  ============================================================
``phase``        ``phase``（thinking / routing / tool_calling）、``step``、
                 ``reasoning``*（fast / medium / deep，仅供客户端显示「深度思考」）
``tool_call``    ``tool``、``step``、``message``（人类可读文案）
``tool_result``  ``tool``、``step``、``status``（ok / error）、
                 ``code``* / ``message``*（仅 ``status="error"``，安全枚举码）
``confirm``      ``step``、``proposal``（**完整 ActionProposal**，含服务端解析的
                 id——客户端要拿它回投 ``/ai/execute``）
``delta``        ``text``（增量）、``channel``（``content`` / ``reasoning``，
                 见上「delta 的粒度与两个通道」）、``step``
``done``         ``conversation_id``、``stop_reason``、``steps``、``tool_calls``、
                 ``writes``、``usage``{prompt_tokens, completion_tokens}、
                 ``proposals``、``executions``、``pending_input``
``error``        ``code``（ai_unavailable / agent_failed）、``message``、
                 ``conversation_id``（会话索引已提交，失败轮也能接着用）
``device_request``  P1 无生产者（见上表）
===============  ============================================================

``stop_reason`` 取 ``agent.StopReason`` 的枚举值（``completed`` /
``empty_response`` / ``step_limit`` / ``tool_call_limit`` / ``write_limit`` /
``no_progress``；``cancelled`` 在 P1 不可达——断连走的是连接取消，不发帧）。

delta 的粒度与两个通道（T-M7-02g-2）
-----------------------------------
loop 自 02g-2 起走 **token 级流式**（``agent.py`` 的 ``_stream_step``），``delta``
因此一轮会有几十上百帧，分**两个通道**（载荷的 ``channel``）：

- ``content`` —— 助手回答正文，客户端渲染为气泡（实测约 4 字符/帧）。
- ``reasoning`` —— 模型的自述推理（DeepSeek 的 ``reasoning_content``），客户端
  渲染为**灰字折叠区**，对标 DeepSeek App 的「深度思考」。

**推理通道不是锦上添花，是本卡存在的理由**：02g-2 的真实探针显示推理增量先于
正文增量到达且量大（深档带工具那一步 0.45 s 起有推理、全程 524 字符推理对
**0 字符正文**），而深档单步要跑 5–7 s——只送正文的话，最需要「有反应」的那一步
仍然长时间静默，收益接近于零。

帧泵把同一通道、同一 Step 的增量在 :data:`_DELTA_MERGE_SECONDS`（100 ms）内并成
一帧（实测帧粒度约 4 字符/帧，一轮三四百帧直连透传会压桥）。**收尾文本（撞硬
边界时的交代）不流式**，产出后作为一帧送出；客户端因此**不能**假设「一轮一帧」，
按追加渲染。协议细节见架构 §8.5。

SSE 的实时性仍由**步骤可见性**共同承担（``tool_call`` / ``tool_result``，02d
实测：深档单步 5–7 s，两步就超出 12 s）——它给的是「在做哪件事」，``delta`` 给
的是「正在说什么」，两者互补。

失败收尾（不是重发）
--------------------
provider 中途失败一律**报错收尾**，不重发请求——02b 的交接约定（首个 delta
转发之后的失败 ``retryable=False``，重发会让用户看到重复的半句话）。本模块
的 ``error`` 帧**不带 retryable 语义**：一轮 loop 可能已经执行过写操作，原地
重跑会重复落库，客户端的正确动作是回退快路径（单发）而不是重试 agent 路径。

心跳与缓冲
----------
- **心跳**（架构 §8.4）：空闲超过 ``ai_agent_heartbeat_seconds`` 就发一帧注释
  （``: ping``）。服务器未设 ``proxy_read_timeout``，用 nginx 默认 60 s——静默
  即断。心跳只在**没有事件可发**时出现，不占事件语义。
- **缓冲**：响应带 ``X-Accel-Buffering: no``（由 ``routes.py`` 设置），nginx
  自己关掉该响应的 ``proxy_buffering``，**不改服务器配置**（R-PROD-22 的决策）。
  该头被忽略时的退路是改 nginx，见该风险的残余项。

为什么是「工作协程 + 帧泵」两层
-------------------------------
流式响应的生成器在响应头返回之后仍在运行，**不能沿用请求作用域的
``Depends(get_db_session)``**（那时 session 已随请求关闭，架构 §8.4 第 3 项），
session 因此必须由本模块自持。但**生成器不是持有它的地方**：客户端断连时，
Starlette 取消的是外层请求任务（anyio 的取消是作用域级的），生成器 ``finally``
里的任何 ``await`` 都会立刻再抛一次 ``CancelledError``——session 于是永远关不
掉，连接不归还连接池（池满之后所有请求一起挂）。所以：

- :func:`_run_turn` —— 普通 ``asyncio.Task``，自建自关 session（``finally`` 里
  ``await close()``）。普通任务只被取消一次，``finally`` 的 await 因此照常执行。
- :func:`agent_event_stream` —— 生成器只做帧泵：排空队列、发心跳、格式化。
  收尾全是同步操作（取消工作协程），不需要 await。

工作协程被取消时**不再走 loop 的取消路径而直接中断**：用户已经走了，没有理由
再等一次最多 20 s 的在途模型请求。中断时 session 未提交即关闭 = 回滚，因此
半途的写操作不会落库（``execute_proposal`` 本身也是原子的）。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import AsyncIterator, Mapping
from datetime import date
from enum import StrEnum
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.common.config import Settings
from src.common.database import get_session_factory
from src.models.ai import AIConversation
from src.models.user import User

from .agent import (
    AgentLoop,
    AgentResult,
    ServiceProposalExecutor,
)
from .conversation import ConversationService
from .provider import ProviderError, ReasoningLevel, ToolCallingProvider
from .service import AIService
from .tools import ToolContext, ToolOutcome, load_tool_context

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 事件与帧
# ---------------------------------------------------------------------------

class EventType(StrEnum):
    """SSE 下行事件类型（架构 §8.1 的八类，一个不多一个不少）。"""

    PHASE = "phase"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    CONFIRM = "confirm"
    DEVICE_REQUEST = "device_request"
    DELTA = "delta"
    DONE = "done"
    ERROR = "error"


class Phase(StrEnum):
    """``phase`` 帧的阶段标识（架构 §8.1）。

    ``ROUTING`` 未被 P1 使用——域路由是 P4 的事（P1 三域全量注入，没有「先选
    域」这一步）。保留取值是为了让 02h/02i 的解析器一次写对。
    """

    THINKING = "thinking"
    ROUTING = "routing"
    TOOL_CALLING = "tool_calling"


class DeltaChannel(StrEnum):
    """``delta`` 帧的内容通道（架构 §8.5，T-M7-02g-2）。

    客户端据此分流，**两个通道不能混**：``REASONING`` 是模型的自述推理
    （DeepSeek 的 ``reasoning_content``），进灰字折叠区；``CONTENT`` 才是回答
    正文，进气泡。载荷里带 ``channel`` 而不是另立事件类型——八类事件是 02h 的
    解析依据，本卡只做纯增量。
    """

    CONTENT = "content"
    REASONING = "reasoning"


class StreamErrorCode(StrEnum):
    """``error`` 帧的安全枚举码（绝不透传异常正文）。

    与既有降级契约（APP-CONTEXT §6.4）同口径：``ai_unavailable`` 就是快路径
    503 的那个码，客户端据此回退本地规则 NLU。``agent_failed`` 是本路径特有的
    内部失败，客户端按「本轮没做成」处理。
    """

    AI_UNAVAILABLE = "ai_unavailable"
    AGENT_FAILED = "agent_failed"


_ERROR_MESSAGES: dict[StreamErrorCode, str] = {
    StreamErrorCode.AI_UNAVAILABLE: "AI service is temporarily unavailable",
    StreamErrorCode.AGENT_FAILED: "The assistant could not complete this turn",
}

#: 心跳注释帧。SSE 规范里以 ``:`` 开头的行是注释，客户端必须忽略——它只用于
#: 让 nginx 的 60 s 读超时看到流量。
HEARTBEAT_FRAME = ": ping\n\n"

#: delta 合并窗（秒）。实测帧粒度约 **4 字符/帧**（02g-2 探针：单步 138–164 帧），
#: 一轮三步就是 400–500 帧——直连透传会把客户端的桥调用量推高一个数量级。帧泵
#: 把**同一通道、同一 Step** 的增量在窗内并成一帧再发：帧数降一个数量级，首帧
#: 延迟最多只多这一窗。合并只改帧的**分法**不改内容——把一帧里的若干增量顺序
#: 拼接，与逐帧拼接逐字节相同，客户端按追加渲染即可。
#:
#: **放在服务端而不是客户端**：架构的立场是客户端瘦渲染（§2.1），一处解决对所有
#: 客户端生效，且桥层（02h）因此只需逐帧原样转发。
_DELTA_MERGE_SECONDS = 0.1


def format_frame(event: EventType | str, payload: Mapping[str, Any]) -> str:
    """一个 SSE 帧（``event:`` + 单行 ``data:`` + 空行）。

    ``ensure_ascii=False``：中文原样出，省带宽也便于联调时肉眼读帧；
    ``separators`` 去掉空格，一帧尽量小。两者都不影响可解析性。
    """
    data = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n"


# ---------------------------------------------------------------------------
# 工具进度文案（02c 留给本层的产物）
# ---------------------------------------------------------------------------

#: 工具名 → 步骤卡片上的人类可读文案（架构 §8.1 的 ``tool_call`` 载荷）。
#: **必须覆盖注册表里的每一个工具**——漏一个只会让卡片显示兜底文案，但
#: ``test_every_registered_tool_has_progress_text`` 把它钉死，免得新工具掉队。
#: 文案是给人看的一句话，不含参数、不含用户数据。
TOOL_PROGRESS: dict[str, str] = {
    "transaction_add": "正在记账…",
    "transaction_add_batch": "正在批量记账…",
    "transaction_list": "正在查询流水…",
    "transaction_recent": "正在查询最近的记录…",
    "transaction_summary": "正在汇总收支…",
    "budget_list": "正在查询预算…",
    "budget_status": "正在核算预算执行情况…",
    "budget_check": "正在检查这笔支出是否超预算…",
    "budget_summary": "正在汇总预算…",
    "budget_overspent": "正在检查超支的分类…",
    "account_list": "正在查询账户…",
    "account_balances": "正在核算账户余额…",
    "book_list": "正在查询账本…",
    "book_detail": "正在查看账本详情…",
    "category_list": "正在查询分类…",
}

#: 认不出工具名时的兜底（例如注册表加了新工具而文案表忘了跟）。
_FALLBACK_PROGRESS = "正在处理…"


def tool_progress(name: str) -> str:
    return TOOL_PROGRESS.get(name, _FALLBACK_PROGRESS)


def summarize_outcome(outcome: ToolOutcome) -> dict[str, Any]:
    """``tool_result`` 的载荷：**摘要，不含原始数据**（架构 §8.1）。

    读工具的结果是整块账本数据（还可能很大），它属于模型上下文，不属于给
    客户端看的帧——这里只给「成了没有」，失败时给安全枚举码与固定文案。文案
    取自工具层的固定错误表，**不含**模型可控的 ``argument`` 字段，也不含任何
    用户数据。
    """
    if outcome.kind == "error":
        return {
            "status": "error",
            "code": outcome.data.get("code"),
            "message": outcome.data.get("message"),
        }
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# 队列观察者
# ---------------------------------------------------------------------------

class QueueObserver:
    """把 ``AgentLoop`` 的进度回调翻译成帧、塞进队列。

    **方法全是同步的**：回调点都在 loop 的关键路径上，``put_nowait`` 不产生
    await，观察者因此不改变 loop 的时序，也不会成为取消点。队列无界，永不阻塞。

    回调抛异常由 loop 兜住（``AgentLoop._notify``），本类不需要自己防御。
    """

    def __init__(self, queue: asyncio.Queue[Any]) -> None:
        self._queue = queue
        #: 累计本轮的 token 用量。**失败路径也要用**：loop 抛异常时
        #: ``AgentResult`` 不存在，但已经发生的 provider 调用是计费的，仍要落
        #: 用量行（快路径的 ``_record_failure_usage`` 是同一口径）。
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def step_started(self, *, step: int, level: ReasoningLevel) -> None:
        self._queue.put_nowait((EventType.PHASE, {
            "phase": Phase.THINKING, "step": step, "reasoning": str(level),
        }))

    def step_finished(
        self, *, step: int, prompt_tokens: int, completion_tokens: int,
    ) -> None:
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens

    def tool_started(self, *, step: int, name: str) -> None:
        self._queue.put_nowait((EventType.PHASE, {
            "phase": Phase.TOOL_CALLING, "step": step,
        }))
        self._queue.put_nowait((EventType.TOOL_CALL, {
            "tool": name, "step": step, "message": tool_progress(name),
        }))

    def tool_finished(self, *, step: int, name: str, outcome: ToolOutcome) -> None:
        if outcome.kind == "proposal":
            # 挂起的提案（``kind="proposal"`` 只在挂起那条路上出现——自动执行
            # 与执行失败分别返回 ``kind="result"`` / ``"error"``）。载荷是完整
            # ActionProposal：客户端要拿它渲染确认卡并回投 ``/ai/execute``。
            self._queue.put_nowait((EventType.CONFIRM, {
                "step": step, "proposal": dict(outcome.data),
            }))
            return
        self._queue.put_nowait((EventType.TOOL_RESULT, {
            "tool": name, "step": step, **summarize_outcome(outcome),
        }))

    def delta(self, *, step: int, text: str) -> None:
        """助手**正文**的一个增量（T-M7-02g-2）。

        **不在观察者里攒**：回调点在 provider 的增量路径上，一次 Step 会来几十上
        百次，这里只如实入队；合并成整帧是帧泵的事（:data:`_DELTA_MERGE_SECONDS`）。
        """
        self._queue.put_nowait((EventType.DELTA, {
            "text": text, "channel": DeltaChannel.CONTENT, "step": step,
        }))

    def reasoning_delta(self, *, step: int, text: str) -> None:
        """**推理内容**的一个增量（T-M7-02g-2）。

        与正文分通道送出（载荷的 ``channel``），客户端渲染为灰字折叠区。
        """
        self._queue.put_nowait((EventType.DELTA, {
            "text": text, "channel": DeltaChannel.REASONING, "step": step,
        }))


# ---------------------------------------------------------------------------
# 帧泵（生成器）
# ---------------------------------------------------------------------------

#: 队列里的终止项。事件项一律是 ``(EventType, dict)``。
_TERMINAL = object()


async def agent_event_stream(
    *,
    provider: ToolCallingProvider,
    cfg: Settings,
    user: User,
    book_id: uuid.UUID,
    conversation_id: uuid.UUID,
    request_id: str,
    user_input: str,
    today: date,
) -> AsyncIterator[str]:
    """把一轮 agent 跑成 SSE 帧流。

    调用方（``routes.py``）已完成前置检查——开关、入参、会话与账本归属、日配额
    与会话配额——所以这里的一切失败都发生在响应头之后，一律以 ``error`` 帧收尾
    （HTTP 状态码此时已经发出，改不了了）。

    **session 由工作协程自建自关**、不从端点传进来：端点那份是请求作用域的短命
    session（用完即关），而流式生成器有可能**一次都没被迭代**（响应头发送就失败
    时 Starlette 不会开始取帧），把连接交给它等于留下一个永不归还的连接。
    """
    queue: asyncio.Queue[Any] = asyncio.Queue()
    worker = asyncio.create_task(_run_turn(
        queue=queue,
        provider=provider,
        cfg=cfg,
        user=user,
        book_id=book_id,
        conversation_id=conversation_id,
        request_id=request_id,
        user_input=user_input,
        today=today,
    ))
    pending: asyncio.Future[Any] | None = None
    #: 待发的 delta 缓冲与其冲刷时刻（见 :data:`_DELTA_MERGE_SECONDS`）。
    #: ``None`` = 没有待发增量。
    buffered: dict[str, Any] | None = None
    flush_at = 0.0
    #: 收到终止项 = 工作协程已经跑完并落库。**此时不取消它**——它正在收尾
    #: （关 session），取消会把「归还连接」打断在半路。
    normal_end = False
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(queue.get())
            # 有增量待发时只再等合并窗的**剩余**时间，到点就冲刷；否则按心跳等。
            wait_for = (
                cfg.ai_agent_heartbeat_seconds if buffered is None
                else max(0.0, flush_at - asyncio.get_running_loop().time())
            )
            done, _ = await asyncio.wait({pending}, timeout=wait_for)
            if not done:
                if buffered is not None:
                    yield format_frame(EventType.DELTA, buffered)
                    buffered = None
                    continue
                # 空闲：发心跳。``pending`` 刻意**不取消**——取消一个恰好已就绪
                # 的 ``Queue.get`` 有丢件的窗口，而这个协程本来就只等这一件事。
                if worker.done() and queue.empty():
                    # 兜底：工作协程已结束却没留下终止项（被取消的路径）。不加
                    # 这一条，这里会永远发心跳，客户端永远等不到收尾。
                    break
                yield HEARTBEAT_FRAME
                continue
            item = pending.result()
            pending = None
            # 非 delta 的一律先冲刷缓冲：**事件顺序即语义**，``done`` 跑到半截
            # 正文前面会让客户端提前收尾。
            if item is _TERMINAL or item[0] != EventType.DELTA:
                if buffered is not None:
                    yield format_frame(EventType.DELTA, buffered)
                    buffered = None
                if item is _TERMINAL:
                    normal_end = True
                    break
                event, payload = item
                yield format_frame(event, payload)
                continue
            _, payload = item
            now = asyncio.get_running_loop().time()
            if buffered is not None and (
                buffered["channel"] != payload["channel"]
                or buffered["step"] != payload["step"]
                or now >= flush_at
            ):
                # 通道或 Step 换了 / 窗已到期：旧的先走，另起一个。用时刻判窗而
                # 不是「等队列空」——增量是连续到达的，靠队列空判会把密集输出
                # 整段攒成一帧。
                yield format_frame(EventType.DELTA, buffered)
                buffered = None
            if buffered is None:
                buffered = dict(payload)
                flush_at = now + _DELTA_MERGE_SECONDS
            else:
                buffered["text"] += payload["text"]
    finally:
        # 收尾**全同步**：客户端断连时生成器处于取消态，任何 await 都会立刻再
        # 抛一次 CancelledError（anyio 的取消是作用域级的）。工作协程是普通
        # asyncio 任务，只被取消一次，因此仍能跑完自己的 ``finally``——关
        # session 即回滚未提交的写入。
        if pending is not None:
            pending.cancel()
        if not normal_end and not worker.done():
            worker.cancel()
        worker.add_done_callback(_discard_result)


async def _run_turn(
    *,
    queue: asyncio.Queue[Any],
    provider: ToolCallingProvider,
    cfg: Settings,
    user: User,
    book_id: uuid.UUID,
    conversation_id: uuid.UUID,
    request_id: str,
    user_input: str,
    today: date,
) -> None:
    """取得一轮的 session，跑完它，**保证**队列里留下一个终止项。

    终止项放在关 session 之后：帧泵收到它就意味着「这一轮已经存下来、连接也已经
    还回去了」，客户端看到的 ``done`` 与「记录已落库」因此同序。
    """
    observer = QueueObserver(queue)
    try:
        session = get_session_factory()()
    except Exception as exc:  # noqa: BLE001 — 拿不到连接 = 这一轮根本没开始
        queue.put_nowait((EventType.ERROR, _error_payload(exc, conversation_id)))
        queue.put_nowait(_TERMINAL)
        return
    try:
        await _execute_turn(
            queue=queue, session=session, observer=observer, provider=provider,
            cfg=cfg, user=user, book_id=book_id, conversation_id=conversation_id,
            request_id=request_id, user_input=user_input, today=today,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — 装载阶段失败，同样以 error 帧收尾
        logger.warning(
            "agent turn failed to start",
            extra={"error": type(exc).__name__, "outcome": "agent_turn_failed"},
        )
        queue.put_nowait((EventType.ERROR, _error_payload(exc, conversation_id)))
    finally:
        with contextlib.suppress(Exception):
            await session.close()
        queue.put_nowait(_TERMINAL)


async def _execute_turn(
    *,
    queue: asyncio.Queue[Any],
    session: AsyncSession,
    observer: QueueObserver,
    provider: ToolCallingProvider,
    cfg: Settings,
    user: User,
    book_id: uuid.UUID,
    conversation_id: uuid.UUID,
    request_id: str,
    user_input: str,
    today: date,
) -> None:
    """一轮的全部工作：装载 → 跑 loop → 落库 → 发尾帧。

    **落库时机**：正常结束时先 ``append_turn``（本轮消息进会话日志）+ 一次
    ``record_turn``（一条用量行，会话级配额与 guest 日配额共用这张表），提交成功
    后才发 ``done``——``done`` 因此意味着「这一轮已经存下来了」。

    ``delta`` **不再享有这个保证**（T-M7-02g-2）：它是 loop 跑的过程中实时送出的
    （这是流式的全部意义），所以「用户看完了答案但历史里什么都没有」的窗口从
    「正文与落库几乎同时」变成「整段正文期间」——断连即如此。这是 P1 的已知代价
    （02f 的一轮原子写入决定了不能在轮中途落库），真正的修法归 P3 的完整事件日志；
    架构 §8.5 已把客户端侧的契约写死（保留半截文本 + 标注失败）。

    失败**不写事件、不写正文**（02f 的一轮原子写入决定：流式路径不能在轮中途
    落库），但仍记一条用量行：已经发生的 provider 调用是计费的，不记就成了
    「靠让请求失败来绕过配额」。
    """
    conversations = ConversationService(session, cfg)
    conversation = await conversations.resolve(user, book_id, conversation_id)
    history = await conversations.history(conversation)
    ctx = await load_tool_context(session, user.id, book_id)

    try:
        result = await _build_loop(
            provider, ctx, cfg, observer, session, user, book_id,
            request_id=request_id, today=today,
        ).run(user_input, history=history)
    except Exception as exc:  # noqa: BLE001 — 运行期失败一律以 error 帧收尾
        logger.warning(
            "agent turn failed",
            extra={"error": type(exc).__name__, "outcome": "agent_turn_failed"},
        )
        await _record_failure_usage(
            session, conversations, user, conversation, provider, cfg, request_id,
            observer,
        )
        queue.put_nowait((EventType.ERROR, _error_payload(exc, conversation_id)))
        return

    try:
        await conversations.append_turn(conversation, result.messages)
        await conversations.record_turn(
            user,
            conversation,
            provider=provider.name,
            model=cfg.ai_agent_provider_model,
            request_id=request_id,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
        )
        await session.commit()
    except Exception as exc:  # noqa: BLE001 — 落库失败不能冒充成功
        logger.warning(
            "agent turn could not be persisted",
            extra={"error": type(exc).__name__, "outcome": "agent_persist_failed"},
        )
        queue.put_nowait((EventType.ERROR, _error_payload(exc, conversation_id)))
        return

    queue.put_nowait((EventType.DONE, _done_payload(result, conversation)))


def _build_loop(
    provider: ToolCallingProvider,
    ctx: ToolContext,
    cfg: Settings,
    observer: QueueObserver,
    session: AsyncSession,
    user: User,
    book_id: uuid.UUID,
    *,
    request_id: str,
    today: date,
) -> AgentLoop:
    """组装 loop：注入观察者与**执行通道**，但不接审批通道。

    只给执行通道、不给审批通道是 02e 定义的两个可选通道的直接用法：单 Step 高
    置信的写入照既定策略自动执行（撤销窗在回执里），多步写入因无人可问而挂起
    → ``confirm`` 帧 + ``done.proposals`` → 客户端走既有 v2 卡链路。
    """
    return AgentLoop(
        provider,
        ctx,
        model=cfg.ai_agent_provider_model,
        timeout=cfg.ai_agent_step_timeout_seconds,
        today=today,
        observer=observer,
        executor=ServiceProposalExecutor(
            AIService(session, provider, cfg),
            user=user, request_id=request_id, book_id=book_id,
        ),
    )


def _done_payload(result: AgentResult, conversation: AIConversation) -> dict[str, Any]:
    """``done`` 帧（架构 §8.1：结束标记含 usage）。

    除 usage 外带上调用方渲染所需的一切：会话 id（下一轮要用）、终止原因、计数、
    **待确认的提案**（确认卡的权威清单）与**已执行的写操作回执**（撤销窗要用事务
    id）。``pending_input`` 一并交回——P1 的端点不接收中途输入，该字段因此恒为
    空；保留它是因为 02d 的约定要求调用方消费，客户端若收到非空值应把这些话重新
    发一次（另起一轮），不能当作没说过。
    """
    return {
        "conversation_id": str(conversation.id),
        "stop_reason": str(result.stop_reason),
        "steps": result.steps,
        "tool_calls": result.tool_calls,
        "writes": result.writes,
        "usage": {
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
        },
        "proposals": [dict(p) for p in result.proposals],
        "executions": [dict(e) for e in result.executions],
        "pending_input": list(result.pending_input),
    }


def _error_payload(
    exc: BaseException, conversation_id: uuid.UUID,
) -> dict[str, Any]:
    """异常 → 安全枚举码。**只分类，不回显异常正文**（可能含用户原话或库结构）。

    带上 ``conversation_id``：会话索引行在前置检查里已经提交，失败轮虽不写正文，
    但会话本身是有效的——客户端据此可以继续这一轮对话，不必重开会话。
    """
    code = (
        StreamErrorCode.AI_UNAVAILABLE
        if isinstance(exc, ProviderError)
        else StreamErrorCode.AGENT_FAILED
    )
    return {
        "code": str(code),
        "message": _ERROR_MESSAGES[code],
        "conversation_id": str(conversation_id),
    }


async def _record_failure_usage(
    session: AsyncSession,
    conversations: ConversationService,
    user: User,
    conversation: AIConversation,
    provider: ToolCallingProvider,
    cfg: Settings,
    request_id: str,
    observer: QueueObserver,
) -> None:
    """失败轮的用量行。**尽力而为**：记不上也不能盖掉即将发出的 ``error`` 帧。"""
    if not observer.prompt_tokens and not observer.completion_tokens:
        return
    try:
        await conversations.record_turn(
            user,
            conversation,
            provider=provider.name,
            model=cfg.ai_agent_provider_model,
            request_id=request_id,
            prompt_tokens=observer.prompt_tokens,
            completion_tokens=observer.completion_tokens,
        )
        await session.commit()
    except Exception as exc:  # noqa: BLE001 — 审计行记不上不该改变用户看到的结果
        logger.warning(
            "agent failure usage row not recorded",
            extra={"error": type(exc).__name__, "outcome": "agent_usage_failed"},
        )


def _discard_result(task: asyncio.Task[Any]) -> None:
    """取走已结束任务的结果，免得 asyncio 打「exception never retrieved」。"""
    if not task.cancelled():
        with contextlib.suppress(Exception):
            task.exception()
