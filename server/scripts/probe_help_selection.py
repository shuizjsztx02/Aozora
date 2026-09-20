"""T-M7-05i: 产品帮助检索的**真实调用抽样**（R-PROD-17 的数据点）。

**为什么要有这个脚本**：``test_ai_tools_help.py`` 钉住的是语料与检索的**确定性**
那一半（同一句话永远给出同一组条目）；模型会不会在「怎么设置提醒」这类输入上
去调 ``help_search``，只能靠真实调用取证——MockTransport 不经过真实接口（02c
的点号命名事故就是这么漏掉的：单测能过、真实请求 400）。

口径
----
- **每个用例最多两次模型调用**，第一次取模型点名的工具；若它点的是
  ``help_search`` 就到此为止（它自己就是终点：结果直接回喂模型），否则回一份
  假读结果再问一次，把「先查后改」照原样走完。
- 系统提示与工具清单都走**真实链路**（``AgentLoop`` 自己拼的系统提示 +
  ``to_openai_tools``），上下文是内存里的假用户。
- **``--routing``（默认）= 生产形态**：每个用例按 ``routing.route_domains`` 只注入
  被选中的域——帮助域**在不在这一份里**是本卡最该量的一件事（域路由的组词原则是
  「名词指向哪类数据」，而 help 的信号是问法）。``--full`` 是全量注入的对照组。
- **两类用例分开记账**：
  - **该命中**（``CASES``）：明确的「怎么用 / 在哪」类产品问题，期望首调
    ``help_search``；
  - **不该抢**（``CASES_NEGATIVE``）：该由数据域工具完成的输入（「记一笔」
    「这个月花了多少」），期望**不出现** ``help_search``——帮助是补集，不是
    万能兜底。
- **兜底提示**：``help_search`` 没被调不代表用户拿不到答案（模型也可能直接答，
  或调了数据域工具），所以**「未中」在这里的代价是「答案没有事实来源」**，
  不是「功能不可用」——报表里要把这一点说清楚，别把它读成一次失败。

用法::

    cd server && python scripts/probe_help_selection.py            # 域路由（生产形态）
    cd server && python scripts/probe_help_selection.py --full     # 全量注入（对照组）
    cd server && python scripts/probe_help_selection.py --limit 4  # 冒烟
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.config import get_config  # noqa: E402
from src.modules.ai import routing  # noqa: E402
from src.modules.ai import tools as T  # noqa: E402
from src.modules.ai.agent import AgentLoop  # noqa: E402
from src.modules.ai.provider import (  # noqa: E402
    DeepSeekProvider,
    ReasoningLevel,
    ToolCallingProvider,
)

HELP = "help_search"

#: **(用户原话, 期望工具)**——产品用法类问题，期望首调 ``help_search``。
CASES: tuple[tuple[str, str], ...] = (
    ("怎么设置每日记账提醒？", HELP),
    ("桌面组件在哪配置？", HELP),
    ("换手机以后我的数据还在吗？", HELP),
    ("支持语音记账吗？", HELP),
    ("主题怎么换成深色？", HELP),
    ("预算沿用是什么意思？", HELP),
    ("存钱目标的进度怎么看？", HELP),
    ("怎么删除账号？", HELP),
    ("聊天里的确认卡为什么要确认？", HELP),
    ("隐私政策在哪看？", HELP),
)

#: **(用户原话, 期望工具)**——该由数据域工具完成，``help_search`` 不该被点。
CASES_NEGATIVE: tuple[tuple[str, str], ...] = (
    ("午饭 12.5", "transaction_add"),
    ("这个月花了多少", "stats_monthly"),
    ("我的账户都有哪些", "account_list"),
)

#: 结果可回喂的读工具（探针给它们编一份形状真实的假结果）。
_READ_TOOLS = frozenset({"help_search", "transaction_add", "stats_monthly", "account_list"})
_FAKE_READ = json.dumps({"status": "ok", "note": "（探针的假结果）"}, ensure_ascii=False)


class _NullProvider(ToolCallingProvider):
    """只为把 ``AgentLoop`` 拼系统提示这一步跑起来（本探针不调它）。"""

    async def complete_with_tools(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError

    async def complete_with_tools_stream(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError


def _tool_context() -> T.ToolContext:
    return T.ToolContext(
        db=None,  # 探针不执行工具
        user_id=uuid.uuid4(),
        book_id=uuid.uuid4(),
        categories_by_kind={"expense": {"餐饮": str(uuid.uuid4())}},
        account_names={"招行卡": str(uuid.uuid4())},
        book_names={"日常": str(uuid.uuid4())},
    )


async def _sequence(
    provider: DeepSeekProvider,
    *,
    system: str,
    tools: list[dict],
    model: str,
    utterance: str,
) -> list[str]:
    """真实调用**最多两步**，返回模型点名的工具序列。"""
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": utterance},
    ]
    names: list[str] = []
    for _ in range(2):
        result = await provider.complete_with_tools(
            messages, model=model, max_tokens=1024, timeout=60.0,
            tools=tools, tool_choice="auto", reasoning=ReasoningLevel.FAST,
        )
        if not result.tool_calls:
            break
        call = result.tool_calls[0]
        names.append(call.name)
        if call.name not in _READ_TOOLS:
            break
        messages.append({
            "role": "assistant",
            "content": result.content or "",
            "reasoning_content": result.reasoning_content or "",
            "tool_calls": [{
                "id": call.id or "call-1",
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }],
        })
        messages.append({
            "role": "tool", "tool_call_id": call.id or "call-1", "content": _FAKE_READ,
        })
    return names


async def main() -> int:
    parser = argparse.ArgumentParser(description="产品帮助检索的选择抽样（R-PROD-17）")
    parser.add_argument("--full", action="store_true", help="全量注入（对照组，默认走域路由）")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 个用例（冒烟用）")
    args = parser.parse_args()

    cfg = get_config()
    if not cfg.ai_agent_provider_api_key:
        print("缺少 AI_AGENT_PROVIDER_API_KEY（server/.env）", file=sys.stderr)
        return 2

    lane_tools = T.tools_for_domains(T.DOMAINS, contract_version=2)
    loop = AgentLoop(
        _NullProvider(), _tool_context(),
        model=cfg.ai_agent_provider_model, timeout=60.0,
        today=date(2026, 9, 20), tools=lane_tools, contract_version=2,
    )
    system = loop._system_message()["content"]  # noqa: SLF001 — 探针要的就是真实系统提示
    full_payload = T.to_openai_tools(lane_tools)

    cases: list[tuple[str, str, str]] = [
        *[("该命中", u, e) for u, e in CASES],
        *[("不该抢", u, e) for u, e in CASES_NEGATIVE],
    ]
    if args.limit:
        cases = cases[: args.limit]

    mode = "全量注入（对照组）" if args.full else "域路由（生产形态）"
    print(f"车道 v2 / {mode}：全量 {len(lane_tools)} 个工具，模型 {cfg.ai_agent_provider_model}")
    print(f"{'组':<8}{'用例':<30}{'期望':<20}{'实际（两步）':<30}首调 路由")
    print("-" * 118)

    provider = DeepSeekProvider(
        cfg.ai_agent_provider_api_key, cfg.ai_agent_provider_base_url,
    )
    hits: list[bool] = []
    leaks: list[bool] = []
    try:
        for group, utterance, expected in cases:
            payload, marker = full_payload, "全量"
            if not args.full:
                decision = routing.route_domains(utterance)
                payload = T.to_openai_tools(
                    T.tools_for_domains(decision.domains, contract_version=2),
                )
                marker = f"{len(decision.domains)}域{'' if T.DOMAIN_HELP in decision.domains else '（无 help）'}"
            names = await _sequence(
                provider, system=system, tools=payload,
                model=cfg.ai_agent_provider_model, utterance=utterance,
            )
            first = names[0] if names else "-"
            hit = first == expected
            if group == "该命中":
                hits.append(hit)
            else:
                leaks.append(HELP in names)
            print(
                f"{group:<8}{utterance:<30}{expected:<20}{' -> '.join(names) or '-':<30}"
                f"{'命中' if hit else '未中'} {marker}"
            )
    finally:
        await provider.aclose()

    print("-" * 118)
    if hits:
        print(f"该命中组：首调 help_search {sum(hits)}/{len(hits)}"
              f"（{sum(hits) / len(hits) * 100:.0f}%）")
    if leaks:
        print(f"不该抢组：help_search 被误点的用例 {sum(leaks)}/{len(leaks)}")
    print("注：「未中」= 这一次模型没去查帮助（答案因此没有事实来源），不是功能不可用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
