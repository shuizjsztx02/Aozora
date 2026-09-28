"""T-M7-05h: Skill —— 按需加载的域知识。

**为什么需要它**（P4 出口标准「新增一个表单能力不改 prompt 主体」）
----------------------------------------------------------------
Agent 的模型可见面有两处：工具 ``description``（``tools.py``，一个工具能说什么）
与系统提示（``agent._AGENT_SYSTEM_PROMPT``，全局行为准则）。工具数量到 54 个、
14 个域之后，**跨工具的规则**没地方放：

- 写进某个工具的 description → 它讲的是**别的**工具的事（``scheduled_create``
  的文案里说「别用 transaction_add」既不属它、也会被下一个工具的文案重复一遍）；
- 写进系统提示 → 它对**每一轮**都生效，而绝大多数轮次根本用不到（记账 App 的
  绝大多数输入是一句话记一笔），常驻的代价是每一轮都要付的输入 token。

Skill 是第三条路：**一段服务端持有的域知识，只在这一轮真的用得上时才进系统提示**。

与工具域的配合（架构 §7.2 的「域是注入单位」）
--------------------------------------------
一个 Skill 绑定一到多个**工具域**（``Skill.domains``），命中判据也落在域上——
域既是工具的分组单位，也是「这一轮在干什么」的最小可判定粒度。域集合有两个来源
（取并集，见 :func:`AgentLoop._load_skills`）：

1. **收窄的注入面**（前瞻式）：本轮的注入面若比「本车道的全部域」小，说明有人
   （05g 的域路由）已经认出这一轮要做什么了——那就把那些域的 Skill 一并带上，
   **从第一个 Step 就在**。词表没认出来而退回全量注入时**不带任何 Skill**：
   「所有域都可能」等于没有命中，这时候加载等于把域知识又变回常驻。
2. **实际用过的域**（懒加载）：本轮有工具调用落在某个域上 → 从下一个 Step 起
   带上它的 Skill。这是**不依赖域路由的兜底**：多步任务（也正是需要域知识的
   那一类）第一步通常是查询，第二步才写——Skill 恰好赶在写之前到位。

两条都不命中时，系统提示与 05b–05d **逐字相同**（``agent._system_message`` 在
``_skill_domains`` 为空时不追加任何内容），快路径的 prompt 与 token 成本一字未动。

预算
----
与 ``prompts.py`` 的注入段落同一把尺子（**字符**，不是 token）：单个 Skill 的正文
≤ :data:`MAX_SKILL_CHARS`，一次注入的合计 ≤ :data:`MAX_SKILL_SECTION_CHARS`。
超限的处置是**整条不进**（不截断）——半句规则比没有规则更误导，与 04c 的摘要口径
一致。单条上限在**导入期**就拦（写长了当场炸），运行期的合计上限只是兜底。

复用 ``PromptTemplate`` 作载体（``prompts.py`` 的机制）
-------------------------------------------------------
Skill 的正文与 task 的 system prompt 是同一类东西：服务端持有、按 id 版本化、
改它不用发版。因此正文装在一个 ``PromptTemplate`` 里（``task_id`` ＝
``skill.<id>``）。**它不会被当成一次模型请求发出去**，所以 ``output_format`` /
``max_tokens`` / ``model`` 三个字段在 Skill 上没有意义——一律取默认值，也没有
任何代码读它们。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .prompts import PromptTemplate
from .tools import (
    DOMAIN_CATEGORY,
    DOMAIN_SAVINGS,
    DOMAIN_SCHEDULED,
    DOMAINS,
)

#: 单个 Skill 正文的字符上限。400 是「三条以内的具体规则」的量：Skill 该回答的是
#: 「这个域里我该怎么动手」，不是把工具文档再抄一遍（工具 description 已经是模型
#: 可见的唯一语义来源，抄第二份＝两处会漂移的真相）。
MAX_SKILL_CHARS = 400

#: 一次注入的合计上限（含标题行）。到 400×3 的量级即封顶——再多就说明该拆域了。
MAX_SKILL_SECTION_CHARS = 1200

#: 域知识段的抬头。措辞按「模型读了会怎么做」写（与 ``_AGENT_SYSTEM_PROMPT`` 同款
#: 口径）：说明这是**这一轮**的补充规则，与上面的全局准则配套、不冲突。
SECTION_HEADER = "以下是本轮涉及的功能域的补充规则："


@dataclass(frozen=True, slots=True)
class Skill:
    """一段按需加载的域知识。

    ``domains`` 是**命中面**：列在里面的任一域进入本轮，这条 Skill 就进系统提示。
    可以绑多个域——设备执行类规则（同一件事横跨 meta / persona）就该只写一份。
    """

    skill_id: str
    domains: tuple[str, ...]
    title: str
    template: PromptTemplate

    @property
    def body(self) -> str:
        return self.template.system_prompt


def _skill(
    skill_id: str,
    domains: tuple[str, ...],
    title: str,
    body: str,
) -> Skill:
    return Skill(
        skill_id=skill_id,
        domains=domains,
        title=title,
        template=PromptTemplate(task_id=f"skill.{skill_id}", system_prompt=body),
    )


# ── 注册表 ─────────────────────────────────────────────────────────────
#
# 每条都是一条**跨工具**的规则：单个工具的 description 里放不下（它讲的是别的
# 工具），而系统提示里放不起（绝大多数轮次用不到）。因此判据是「这句话是不是
# 同时涉及两个以上工具、而且只在动手那一刻才需要」。
#
# **顺序即预算的处置顺序**（合计超限时先到先得、后面的整条不进），因此新加的
# Skill 一律追加在末尾。

#: 定时记账：规则 vs 这一笔。用户说「以后每月自动记房租」时模型很容易顺手记一笔，
#: 反过来也容易把「记一笔房租」做成一条规则——两个方向的错都在**两个工具之间**，
#: 谁的 description 都盖不住。
_SCHEDULED = _skill(
    "scheduled",
    (DOMAIN_SCHEDULED,),
    "定时记账规则与「记一笔」是两件事",
    "- 用户说「以后 / 每天 / 每月自动记…」才建规则（scheduled_create）；"
    "说「记一笔房租」是现在这一笔（transaction_add）。\n"
    "- 建规则**不会**立刻产生一笔记录：不要为了「补上这个月」再调一次 "
    "transaction_add。\n"
    "- 改规则（含暂停）用 scheduled_update；改**已经生成**的那一笔才用 "
    "transaction_update。\n"
    "- 规则属于**当前账本**：用户问的规则不在当前账本时，先确认是不是换了账本，"
    "别断言「没有这条规则」。",
)

#: 存钱目标：进度与账本是两套数据。往目标里存钱时顺手记一笔支出，会把同一笔钱
#: 记两遍（存钱进度 + 支出统计）；这条规则横跨 savings 与 transaction 两域。
_SAVINGS = _skill(
    "savings",
    (DOMAIN_SAVINGS,),
    "存钱目标与账本是两套数据",
    "- 往目标里存钱只调 savings_update 的 addAmountCents（服务端累加），**不要**"
    "再调 transaction_add 记一笔支出或转账——那会把同一笔钱记两遍。\n"
    "- 反过来，用户说「记一笔」时不要动存钱目标。",
)

#: T20-6（REQ-80）：分类的层级与维护。**「记账时怎么选分类」不在这里**——那三条
#: 规则（优先二级 / 必选一个 / 都是先记上再问）写在系统提示里，因为记账句未必
#: 路由得到 category 域（「打车 25」命不中任何信号 → 兜底全量 → 本模块按既定
#: 口径**不带任何 Skill**），把选分类的规则放这儿等于在最需要它的轮次缺席。
#:
#: 这条管的是**另一件事**：用户真的在谈分类本身（建 / 改 / 挂到哪一级）时的规则，
#: 横跨 ``category_create`` / ``category_update`` / ``category_list`` 三个工具，
#: 而每个工具的 description 只讲得清自己那一段。
_CATEGORY = _skill(
    "category",
    (DOMAIN_CATEGORY,),
    "分类的层级与维护",
    "- 分类只有**两级**：一级（如「交通」）与其下的二级（公交 / 打车 / 地铁…）。"
    "建二级要用 parentName 挂到对应的一级下面，不要平铺成又一个一级。\n"
    "- 用户说「加一个分类」时先看名单里有没有语义相同的（含二级）：能挂到某个"
    "一级下就用它作 parentName，别建一个近义的新一级。\n"
    "- 改已有分类用 category_update；不要「建一个新的、把旧的留着」。\n"
    "- 记账时觉得分类都不合适，只在回复里问一句，**不要**顺手 category_create。",
)

SKILLS: tuple[Skill, ...] = (_SCHEDULED, _SAVINGS, _CATEGORY)

#: 域 → 该域上的 Skill（注册表顺序）。域是命中面，因此一个 Skill 出现在它绑定的
#: 每一个域下；:func:`skills_for_domains` 负责去重。
_BY_DOMAIN: dict[str, tuple[Skill, ...]] = {
    domain: tuple(s for s in SKILLS if domain in s.domains) for domain in DOMAINS
}


def _validate_skills() -> None:
    """导入期校验：注册表的笔误必须**当场炸**，而不是静默退化成「没有 Skill」。

    静默失败在这里尤其危险：Skill 没命中与 Skill 不存在在运行期长得一模一样
    （都表现为「模型没拿到那条规则」），而唯一的观察面是模型的行为。

    **与域路由的守护刻意不同**（``routing.unrouteable_domains()`` 只由用例钉住、
    不在导入期报错）：那条守的是**覆盖度**（「有域还没补词」是一个正当的中间状态），
    这条守的是**正确性**（域写错永远不可能是有意的）。爆炸半径因此是清醒的选择：
    笔误会让服务端起不来，而不是让一条规则无声消失——后者没有任何人会发现。
    """
    seen: set[str] = set()
    for skill in SKILLS:
        if skill.skill_id in seen:
            raise ValueError(f"duplicate skill id: {skill.skill_id}")
        seen.add(skill.skill_id)
        if not skill.domains:
            raise ValueError(f"skill {skill.skill_id} has no domain")
        for domain in skill.domains:
            # 域写错就是永远不会命中（且没人会发现），与 §7.2 的域清单强绑定。
            if domain not in DOMAINS:
                raise ValueError(f"skill {skill.skill_id}: unknown domain {domain!r}")
        if not skill.body.strip():
            raise ValueError(f"skill {skill.skill_id} has an empty body")
        if len(skill.body) > MAX_SKILL_CHARS:
            raise ValueError(
                f"skill {skill.skill_id}: body is {len(skill.body)} chars (> {MAX_SKILL_CHARS})",
            )


_validate_skills()


def skills_for_domains(domains: Iterable[str]) -> tuple[Skill, ...]:
    """按域取 Skill（与 ``tools.tools_for_domains`` 同形），按注册表顺序去重。

    未知域贡献 0 条而不是报错——域集合的来源（域路由、注入面）不该因为一个笔误
    让整轮对话失败（与 ``tools_for_domains`` 同款降级）。
    """
    wanted = set(domains)
    if not wanted:
        return ()
    return tuple(skill for skill in SKILLS if any(d in wanted for d in skill.domains))


def render_skill_section(skills: Sequence[Skill]) -> str:
    """把命中的 Skill 渲染成系统提示尾部的一段；**空集 → ``""``**（逐字不变）。

    合计超限时**整条丢弃**、后面的也不再进（先到先得，顺序＝注册表顺序）：
    截断会把一条规则拦腰砍断，而模型读到的半句规则与整句的语义相反时（「不要用
    transaction_add」被砍成「不要用」）比没有更糟。
    """
    if not skills:
        return ""
    blocks: list[str] = []
    # 抬头也计入预算——上限量的是**注入进提示的那段文本**的总量。
    used = len(SECTION_HEADER) + 1
    for skill in skills:
        block = f"· {skill.title}\n{skill.body}"
        if used + len(block) > MAX_SKILL_SECTION_CHARS:
            break
        blocks.append(block)
        used += len(block) + 1
    if not blocks:
        return ""
    return SECTION_HEADER + "\n" + "\n".join(blocks)


__all__ = [
    "MAX_SKILL_CHARS",
    "MAX_SKILL_SECTION_CHARS",
    "SECTION_HEADER",
    "SKILLS",
    "Skill",
    "render_skill_section",
    "skills_for_domains",
]
