"""T-M7-04d: 会话事件日志的保留期。

``security.md`` 的会话日志有条件例外要求「保留期对齐 ``D-PROD-08``」。本模块是
那条约束的**全部落点**——口径（按整轮删、只删事件、不动会话行与 ``ai_usage``）
在 :func:`purge_expired_events`，调度在 :func:`retention_loop`，数值与开关见
``config.ai_conversation_retention_days`` / ``ai_conversation_retention_enabled``。

**为什么自成一模块，而不是挂在** ``ConversationService`` **上**：本模块是**批处理
生命周期**关切（全库扫一遍、按到期时间删行、随进程启停），与 ``conversation.py``
里的请求路径语义（解析会话、投影上下文、写入事件、按会话删除）不是同一类东西；
两者共用的事实只有「事件表长什么样」，而那是 ``models/ai.py`` 的职责。放在这里
还有一个直接好处：删除语句与它的调度、参数、口径同址，读一处就够。

与 ``modules/telemetry/retention.py`` 的三点异同
-----------------------------------------------

相同：进程内任务（随 API 生命周期启停）、纯函数 + ``*_loop`` 两段式（前者可测）、
开关默认关闭、**生产单 worker** 假设。

不同，且都是有意的：

1. **不挂 ``ENABLE_TELEMETRY``。** 那个开关是埋点用的，关掉它（prod compose 当前
   就是关的）不该顺带关掉一条隐私义务——保留期任务只看自己的开关。
2. **不做「每个 UTC 日只跑一次」的簿记。** 那个 ``last_run`` 标记是 gauge 语义
   逼出来的（同一天重复计算要覆盖而不是累加）；到期删除本身幂等（删过的行没有
   可匹配对象了），因此不需要日期簿记，进程重启也随时可以立刻补跑。
3. **不写 OTel 指标**，只落一条结构化日志。可见性由日志的 ``outcome`` 承担；
   要接仪表盘时再单独评估，别顺手发明一套指标名。

摘要是保留期的一部分（T-M7-04c）
--------------------------------

本模块因此多了一条 :func:`purge_expired_summaries`：``AIConversation.summary``
是窗口之外那段正文的压缩，正文删了它还留着，正文的保留期就被从后门绕过去了。
判定口径与理由见该函数。**两条清扫的顺序不能反**（先删事件、再判摘要）。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.common.config import Settings, get_config
from src.models.ai import AIConversation, AIConversationEvent

from .conversation import EVENT_REPLACE, _upto_turn

logger = logging.getLogger(__name__)

#: 扫描间隔。到期判定按天计，一小时一次足够；取值与 telemetry 的检查间隔一致。
CHECK_INTERVAL_SECONDS = 3600


async def purge_expired_events(db: AsyncSession, cutoff: datetime) -> int:
    """物理删除**到期轮次**的事件正文，返回删除的行数。

    ``cutoff`` 之前的整轮事件被删掉；窗口边界落在轮中间时**整轮保留**（偏保守
    的方向）。判定按 ``turn`` 分组取 ``max(time)`` 而不是逐条比 ``time``：一轮的
    事件**必须同进同出**，否则「带 ``tool_calls`` 的 assistant 消息」可能留下没有
    对应调用方的 ``tool`` 结果，下一轮模型请求直接 400（``conversation.py`` 模块
    docstring 的第 3 条约束、架构 §4.4 不变式 1）。这一点**不依赖 04a 的「一轮共用
    一个 ``time``」**——04b 改成逐事件落库后同一轮的时间戳各不相同，按轮取最大值
    仍然只删整轮。

    **只删事件行**：``ai_conversations`` 与 ``ai_usage`` 一行不动。删会话行会让
    ``ai_usage.conversation_id`` 被 ``ON DELETE SET NULL`` 置空，会话级「轮数 +
    token」双限因此可以被「等满一个保留期」刷回满额（02f 口径，见
    ``conversation.py`` 的模块 docstring）。

    幂等：删过之后再跑删 0 行。**不 commit**——事务边界归调用方。
    """
    expired_turns = (
        select(AIConversationEvent.conversation_id, AIConversationEvent.turn)
        .group_by(AIConversationEvent.conversation_id, AIConversationEvent.turn)
        .having(func.max(AIConversationEvent.time) < cutoff)
    )
    result = await db.execute(
        delete(AIConversationEvent).where(
            tuple_(
                AIConversationEvent.conversation_id, AIConversationEvent.turn,
            ).in_(expired_turns),
        )
    )
    await db.flush()
    return result.rowcount or 0


async def purge_expired_summaries(db: AsyncSession) -> int:
    """清掉**摘要覆盖的轮次已经不在日志里**的会话的 ``summary``，返回清空的行数。

    ``AIConversation.summary`` 是窗口之外那段正文的压缩（T-M7-04c）。正文到期被
    物理删掉之后摘要若继续留着，它仍会经系统提示进下一轮的上下文——**正文的保留期
    就从后门被绕过去了**。摘要是 04c 引入的产物，这条判定因此必须一起落地。

    **判定口径**：取该会话最后一条 ``replace`` 事件覆盖到的轮号 ``upto_turn``，
    这一轮在日志里**一条事件都不剩**时，摘要整条清空。三点理由：

    1. 与到期判定同一把尺子——:func:`purge_expired_events` 按
       (``conversation_id``, ``turn``) 分组删整轮，所以「``upto_turn`` 轮还在不在」
       就是「摘要覆盖的内容还在不在」。
    2. 偏保守的方向与「跨线轮整轮保留」一致：摘要活到它所覆盖的**最后一轮**到期
       为止；覆盖范围中段先到期的轮次，其内容最多多留一个保留窗口。反向（删掉
       会话里任何一行就清空摘要）会让活跃长会话在第一次到期清扫时就丢掉整段
       早期背景，代价与收益不成比例。
    3. 用户主动删除会话（``ConversationService.delete``）时事件也全没了，这条判定
       顺手覆盖那种情况——两条路径的结论因此一致，不必各写一套。

    **幂等**：清过的会话摘要已是空串，`WHERE summary != ''` 让第二次跑更新 0 行。
    **不 commit**——事务边界归调用方。
    """
    covered: dict[uuid.UUID, int] = {}
    for conversation_id, data in (await db.execute(
        select(AIConversationEvent.conversation_id, AIConversationEvent.data)
        .where(AIConversationEvent.type == EVENT_REPLACE)
        .order_by(AIConversationEvent.seq)
    )).all():
        # 一个会话可能有多条 ``replace``（每次窗口溢出写一条）。按 ``seq`` 升序
        # 覆盖，留下的是**最后一条**——摘要的覆盖范围只增不减。
        upto = _upto_turn(data)
        if upto:
            covered[conversation_id] = upto
    if not covered:
        return 0

    surviving = set(await db.scalars(
        select(AIConversationEvent.conversation_id).where(
            tuple_(
                AIConversationEvent.conversation_id, AIConversationEvent.turn,
            ).in_(list(covered.items())),
        )
    ))
    expired = [conversation_id for conversation_id in covered if conversation_id not in surviving]
    if not expired:
        return 0

    result = await db.execute(
        update(AIConversation)
        .where(AIConversation.id.in_(expired), AIConversation.summary != "")
        .values(summary="")
    )
    await db.flush()
    return result.rowcount or 0


async def run_retention_sweep(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    cfg: Settings | None = None,
) -> dict[str, int]:
    """跑一次到期清理：删除 ``now - retention_days`` 之前的整轮事件，并清掉因此
    失去依据的摘要。

    返回 ``{"retention_days": n, "deleted_events": m, "cleared_summaries": k}``
    （供日志与测试断言用）。**不 commit**——由调用方决定事务边界
    （:func:`retention_loop` 在成功后台提交）。
    """
    cfg = cfg or get_config()
    if now is None:
        now = datetime.now(UTC)
    days = cfg.ai_conversation_retention_days
    cutoff = now - timedelta(days=days)

    deleted = await purge_expired_events(db, cutoff)
    # 顺序不能反：摘要的判定看的是「那一轮还在不在」，先删事件才判得准。
    cleared = await purge_expired_summaries(db)
    logger.info(
        "Conversation retention sweep: %d event(s) expired, %d summary(ies) cleared",
        deleted, cleared,
        extra={
            "retention_days": days,
            "cutoff": cutoff.isoformat(),
            "deleted_events": deleted,
            "cleared_summaries": cleared,
            "outcome": "conversation_retention_sweep",
        },
    )
    return {
        "retention_days": days,
        "deleted_events": deleted,
        "cleared_summaries": cleared,
    }


async def retention_loop(
    factory: async_sessionmaker[AsyncSession],
    check_interval_seconds: int = CHECK_INTERVAL_SECONDS,
) -> None:
    """常驻驱动：启动后立刻扫一次，之后每 ``check_interval_seconds`` 重复。

    单次失败**不杀循环**（下个 tick 重试），与原范式一致；``CancelledError``
    照常上抛，关停路径才不会被吞掉。
    """
    while True:
        try:
            async with factory() as session:
                await run_retention_sweep(session)
                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Conversation retention sweep failed")
        await asyncio.sleep(check_interval_seconds)
