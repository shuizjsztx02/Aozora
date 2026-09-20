"""T-M7-05h: 域知识的 token 实测（「Skill 不常驻上下文」这条验收的取证）。

**为什么要有这个脚本**：本卡的验收里写着「token 实测」，而 token 数与字符数不是
同一件事（中文约 1 token ≈ 1.5 字，且随内容浮动）——真实数字只能从**真实调用**
里读（``prompt_tokens`` 是 provider 报的）。这正是 02c 的点号命名事故留下的教训：
MockTransport 不经过真实接口，测不出真实链路上的量与形状。

三个臂，**同一句用户输入、同一套工具载荷之外的变量各只差一处**：

1. **现状（全量注入 + 基础提示）** —— 05b–05d 的形态，也是绝大多数轮次的形态；
2. **＋定时域知识（工具载荷不变）** —— 与臂 1 只差一段域知识，因此两者之差
   **就是域知识本身的 token 成本**；
3. **收窄注入（定时域）+ 域知识** —— 生产中真正会发生的那一轮（域路由认出这一轮
   在做什么，于是只注入该域的工具、并把它的域知识一并带上）。

臂 2 的存在是为了把「域知识的成本」与「域路由省下的工具定义」**分开报**：它们
是两个独立的效果，混在一个数字里谁也说不清是谁的。**脚本不写库、不落任何状态**。

用法::

    cd server && python scripts/probe_skill_budget.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.config import get_config  # noqa: E402
from src.modules.ai import skills as S  # noqa: E402
from src.modules.ai import tools as T  # noqa: E402
from src.modules.ai.agent import AgentLoop  # noqa: E402
from src.modules.ai.provider import DeepSeekProvider  # noqa: E402

#: 与探针的其它脚本同一口径：名字真实、id 随机（工具层只认名字）。
_CATEGORIES = {
    "expense": {"餐饮": str(uuid.uuid4()), "交通": str(uuid.uuid4())},
    "income": {"工资": str(uuid.uuid4())},
}
_ACCOUNTS = {"招行卡": str(uuid.uuid4())}
_BOOKS = {"日常": str(uuid.uuid4())}

#: 一句**会命中定时域**的话：臂 3 的收窄注入以它为准（与生产同一条判据）。
UTTERANCE = "以后每个月 1 号自动记一笔房租 3000"

#: 臂 3 收窄到的域（域路由在生产里给的就是这个集合）。
NARROW_DOMAINS = (T.DOMAIN_SCHEDULED,)


class _FakeDb:
    """探针不需要真库：系统提示的渲染只读 ``ToolContext`` 里的名字。"""


def _ctx() -> T.ToolContext:
    return T.ToolContext(
        db=_FakeDb(),  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        book_id=uuid.uuid4(),
        categories_by_kind=_CATEGORIES,
        account_names=_ACCOUNTS,
        book_names=_BOOKS,
    )


def _base_prompt(domains: tuple[str, ...] | None) -> str:
    """基础提示（与 loop 真实产出的那一条逐字相同）。

    ``domains=None`` ＝ 全量注入（05b–05d 的形态）；给了域就是收窄后的注入面。
    """
    loop = AgentLoop(
        None,  # type: ignore[arg-type] — 只渲染提示，不跑 provider
        _ctx(),
        model="unused",
        timeout=1.0,
        today=date.today(),
        tools=T.tools_for_domains(domains or T.DOMAINS),
    )
    return loop._system_message()["content"]  # noqa: SLF001 — 探针要的就是真实提示


async def main() -> None:
    cfg = get_config()
    provider = DeepSeekProvider(
        cfg.ai_agent_provider_api_key,
        cfg.ai_agent_provider_base_url,
    )

    wide_tools = T.tools_for_domains(T.DOMAINS)
    narrow_tools = T.tools_for_domains(NARROW_DOMAINS)
    section = S.render_skill_section(S.skills_for_domains(NARROW_DOMAINS))
    base_wide = _base_prompt(None)
    base_narrow = _base_prompt(NARROW_DOMAINS)

    arms: list[tuple[str, str, list[T.Tool]]] = [
        ("① 全量注入 + 基础提示（现状）", base_wide, wide_tools),
        ("② 全量注入 + 定时域知识", f"{base_wide}\n\n{section}", wide_tools),
        ("③ 收窄注入 + 定时域知识（生产形态）", f"{base_narrow}\n\n{section}", narrow_tools),
    ]

    print(f"模型 {cfg.ai_agent_provider_model}／用户输入「{UTTERANCE}」")
    print(f"域知识段：{len(section)} 字符（域 {list(NARROW_DOMAINS)}）")
    print()
    print(f"{'臂':<34} {'工具数':>4} {'系统提示字符':>8} {'prompt_tokens':>14}")
    print("-" * 70)

    measured: list[int] = []
    for label, system, tools in arms:
        result = await provider.complete_with_tools(
            [{"role": "system", "content": system}, {"role": "user", "content": UTTERANCE}],
            model=cfg.ai_agent_provider_model,
            max_tokens=256,
            timeout=cfg.ai_agent_step_timeout_seconds,
            tools=T.to_openai_tools(tools),
        )
        measured.append(result.prompt_tokens)
        print(f"{label:<34} {len(tools):>4} {len(system):>8} {result.prompt_tokens:>14}")

    print()
    print(f"**域知识本身的成本**（臂 2 − 臂 1）：{measured[1] - measured[0]:+d} tokens")
    print(
        f"**收窄注入相对现状**（臂 3 − 臂 1）：{measured[2] - measured[0]:+d} tokens"
        f"（少注入 {len(wide_tools) - len(narrow_tools)} 个工具定义）",
    )
    print(
        "\n口径：tokens 由 provider 报的 prompt_tokens 读出，含工具定义与消息两部分；"
        "臂 2 与臂 1 的工具载荷相同，故两者之差只含域知识。",
    )


if __name__ == "__main__":
    asyncio.run(main())
