"""T-M7-05b: 工具选择准确率抽样探针（R-PROD-17 的数据点）。

**为什么要有这个脚本**：架构 §7.2 的经验上限（单域 5–8 个工具）与 R-PROD-17
（工具集过大降低选择准确率）都只能靠**真实调用**取证——MockTransport 不经过
真实接口，测不出模型会不会在两个近义工具之间选错（02c 的点号命名事故就是这么
漏掉的）。05b 把工具集从 15 扩到 27，正是这条风险最该被量一次的时刻。

口径
----
- **每个用例最多两次模型调用**：第一次取模型点名的工具；若它先点了读工具，就
  **回一份假的读结果**（形状真实、内容是编的）再问一次，把「先查后改」这两步
  照原样走完。系统提示与工具清单都走**真实链路**（``AgentLoop`` 自己拼的系统
  提示 + ``to_openai_tools``），上下文是内存里的一个假用户（名字真实、id 随机）。
- 两个口径分开报，因为它们量的是两件事：
  - **首调命中**：第一次就点了期望的工具（单发形态的最优解）；
  - **序列命中**：期望的工具出现在这两步里（**多步 loop 的真实口径**——
    「先 transaction_list 看清那一笔，再 transaction_update」是**正确**策略，
    计成选错会把策略误判成失误）。
  两个都没中的才是真正的选择错误。
- 车道固定 v2（27 个工具全量注入），因为 05b 的新工具只出现在这条车道上。

用法::

    cd server && python scripts/probe_tool_selection.py            # 全部用例
    cd server && python scripts/probe_tool_selection.py --lane 1   # 量 v1 的 15 个

结果打印成表格（用例 / 期望 / 实际 / 是否命中）并给出总准确率。**它不写库、
不落任何状态**，可以随时重跑。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.config import get_config  # noqa: E402
from src.modules.ai import tools as T  # noqa: E402
from src.modules.ai.agent import AgentLoop  # noqa: E402
from src.modules.ai.provider import DeepSeekProvider, ToolCallingProvider  # noqa: E402

#: 一个「有数据」的假用户：名字取自真实语料，id 是随机 UUID（工具层只认名字）。
_CATEGORIES = {
    "expense": {
        "餐饮": str(uuid.uuid4()), "交通": str(uuid.uuid4()), "购物": str(uuid.uuid4()),
        "夜宵": str(uuid.uuid4()), "宠物": str(uuid.uuid4()),
    },
    "income": {"工资": str(uuid.uuid4()), "奖金": str(uuid.uuid4())},
}
_ACCOUNTS = {"招行卡": str(uuid.uuid4()), "现金": str(uuid.uuid4()), "饭卡": str(uuid.uuid4())}
_BOOKS = {"日常": str(uuid.uuid4()), "旅行账本": str(uuid.uuid4())}

#: (用户原话, 期望的工具)。期望值取「服务端真正能完成这件事」的那个工具。
#: **today 固定为 2026-09-18**（周五）：用例里的「昨天 / 上周五 / 九月五号」都以此为准，
#: 与上面的假读结果对得上。
CASES: list[tuple[str, str]] = [
    # ── 记账域：改 / 删（05b 的新工具）──
    ("帮我把昨天那笔 12 块的午饭改成 15 块", "transaction_update"),
    ("刚才记错了，上周五那笔打车费不是 45 是 30，改一下", "transaction_update"),
    ("把九月五号那笔工资的备注改成「补发」", "transaction_update"),
    ("昨天那笔 30 块的打车记重了，删掉一笔", "transaction_delete"),
    ("九月五号那笔工资记录不要了，帮我删了", "transaction_delete"),
    # ── 记账域：P1 的既有工具（对照组）──
    ("记一笔午饭 12 块", "transaction_add"),
    ("早饭 12、午饭 25、打车 30，都记上", "transaction_add_batch"),
    ("我这个月支出多少", "transaction_summary"),
    ("最近记了些什么", "transaction_recent"),
    ("上周餐饮都花了哪些", "transaction_list"),
    # ── 分类域 ──
    ("加一个支出分类叫宠物", "category_create"),
    ("收入里再建一个分类叫奖金，放在工资下面", "category_create"),
    ("把「宠物」这个分类改名叫猫主子", "category_update"),
    ("夜宵这个分类我不用了，归档掉", "category_archive"),
    ("帮我把账本里的分类列出来看看", "category_list"),
    # ── 账户域 ──
    ("我办了一张新卡，叫建设银行", "account_create"),
    ("把招行卡改名叫工资卡", "account_update"),
    ("饭卡不用了，归档掉", "account_archive"),
    ("我有哪几个账户", "account_list"),
    ("招行卡这个月花了多少", "account_balances"),
    # ── 账本域 ──
    ("再建一个账本叫蜜月账本", "book_create"),
    ("把旅行账本改名叫蜜月账本", "book_update"),
    ("旅行账本我不要了，删了吧", "book_delete"),
    ("我有几个账本", "book_list"),
    ("日常这个账本记了多少笔", "book_detail"),
    # ── meta 域（默认项）──
    ("以后默认用旅行账本记账", "meta_set"),
    ("默认账户改成招行卡", "meta_set"),
]


#: 假读结果：形状与真工具一致（键名取自工具处理器），内容是编的。
#: 只喂给**读**工具——写工具不执行（本探针不碰库）。
_FAKE_READS: dict[str, dict] = {
    # 与用例提到的记录**逐条对得上**（today 固定 2026-09-18：昨天=09-17，上周五=09-11，
    # 九月五号=09-05）。对不上就会把「模型在找那笔记录」误判成「模型选错了工具」。
    "transaction_list": {"transactions": [
        {"type": "expense", "amountCents": 1250, "categoryName": "餐饮",
         "accountName": "招行卡", "note": "午饭", "bizDate": "2026-09-17"},
        {"type": "expense", "amountCents": 3000, "categoryName": "交通",
         "accountName": "现金", "note": "打车", "bizDate": "2026-09-17"},
        {"type": "expense", "amountCents": 3000, "categoryName": "交通",
         "accountName": "现金", "note": "打车", "bizDate": "2026-09-17"},
        {"type": "expense", "amountCents": 4500, "categoryName": "交通",
         "accountName": "现金", "note": "打车费", "bizDate": "2026-09-11"},
        {"type": "income", "amountCents": 500000, "categoryName": "工资",
         "accountName": "招行卡", "note": "九月工资", "bizDate": "2026-09-05"},
    ], "total": 5, "truncated": False},
    "transaction_recent": {"transactions": [
        {"type": "expense", "amountCents": 1250, "categoryName": "餐饮",
         "accountName": "招行卡", "note": "午饭", "bizDate": "2026-09-17"},
        {"type": "expense", "amountCents": 3000, "categoryName": "交通",
         "accountName": "现金", "note": "打车", "bizDate": "2026-09-17"},
    ]},
    "category_list": {"categories": [
        {"name": "餐饮", "kind": "expense", "parentName": None},
        {"name": "交通", "kind": "expense", "parentName": None},
        {"name": "夜宵", "kind": "expense", "parentName": None},
        {"name": "宠物", "kind": "expense", "parentName": None},
        {"name": "工资", "kind": "income", "parentName": None},
        {"name": "奖金", "kind": "income", "parentName": None},
    ], "total": 6},
    "account_list": {"accounts": [{"name": "招行卡", "icon": None},
                                  {"name": "现金", "icon": None},
                                  {"name": "饭卡", "icon": None}], "total": 3},
    "book_list": {"books": [{"name": "日常", "icon": None, "current": True},
                            {"name": "旅行账本", "icon": None, "current": False}], "total": 2},
    "book_detail": {"name": "旅行账本", "icon": None, "current": False,
                    "categoryCount": 3, "transactionCount": 12,
                    "firstBizDate": "2026-01-01", "lastBizDate": "2026-09-18"},
}
_FAKE_DEFAULT: dict = {"status": "ok", "note": "（探针的假结果）"}


def _fake_result(name: str) -> str:
    import json

    return json.dumps(_FAKE_READS.get(name, _FAKE_DEFAULT), ensure_ascii=False)


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
        book_id=uuid.UUID(_BOOKS["日常"]),
        categories_by_kind=_CATEGORIES,
        category_names_by_id={
            cid: name for bucket in _CATEGORIES.values() for name, cid in bucket.items()
        },
        account_names=_ACCOUNTS,
        account_names_by_id={aid: name for name, aid in _ACCOUNTS.items()},
        book_names=_BOOKS,
        book_names_by_id={bid: name for name, bid in _BOOKS.items()},
    )


async def _sequence(
    provider: DeepSeekProvider,
    *,
    system: str,
    tools: list[dict],
    model: str,
    utterance: str,
) -> list[str]:
    """真实调用**最多两步**，返回模型点名的工具序列。

    第一步之后若点的是读工具，就回一份假读结果再问一次——把「先查后改」走完。
    第二步之后一律停下（本探针不执行写工具，再往下就不是选择问题了）。
    """
    from src.modules.ai.provider import ReasoningLevel

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
        if call.name not in _FAKE_READS:
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
            "role": "tool", "tool_call_id": call.id or "call-1",
            "content": _fake_result(call.name),
        })
    return names


async def main() -> int:
    parser = argparse.ArgumentParser(description="工具选择准确率抽样（R-PROD-17）")
    parser.add_argument("--lane", type=int, default=2, choices=(1, 2))
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 个用例（冒烟用）")
    args = parser.parse_args()

    cfg = get_config()
    if not cfg.ai_agent_provider_api_key:
        print("缺少 AI_AGENT_PROVIDER_API_KEY（server/.env）", file=sys.stderr)
        return 2

    tools = T.tools_for_domains(T.DOMAINS, contract_version=args.lane)
    loop = AgentLoop(
        _NullProvider(),
        _tool_context(),
        model=cfg.ai_agent_provider_model,
        timeout=60.0,
        today=date(2026, 9, 18),
        tools=tools,
        contract_version=args.lane,
    )
    system = loop._system_message()["content"]  # noqa: SLF001 — 探针要的就是真实系统提示
    payload = T.to_openai_tools(tools)

    print(f"车道 v{args.lane}：注入 {len(tools)} 个工具，模型 {cfg.ai_agent_provider_model}")
    print(f"{'用例':<34} {'期望':<22} {'实际（两步）':<34} 首调 序列")
    print("-" * 104)

    provider = DeepSeekProvider(
        cfg.ai_agent_provider_api_key, cfg.ai_agent_provider_base_url,
    )
    first_hits = 0
    sequence_hits = 0
    cases = CASES[: args.limit] if args.limit else CASES
    try:
        for utterance, expected in cases:
            names = await _sequence(
                provider, system=system, tools=payload,
                model=cfg.ai_agent_provider_model, utterance=utterance,
            )
            first = names[0] if names else "-"
            first_hit = first == expected
            seq_hit = expected in names
            first_hits += first_hit
            sequence_hits += seq_hit
            print(
                f"{utterance:<34} {expected:<22} {' -> '.join(names) or '-':<34} "
                f"{'命中' if first_hit else '未中'} {'命中' if seq_hit else '未中'}"
            )
    finally:
        await provider.aclose()

    total = len(cases)
    print("-" * 104)
    print(f"首调命中 {first_hits}/{total} = {first_hits / total:.0%}")
    print(f"两步内命中 {sequence_hits}/{total} = {sequence_hits / total:.0%}")
    return 0 if sequence_hits == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
