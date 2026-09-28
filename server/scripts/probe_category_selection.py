"""T20-6: 分类选择准确率抽样探针（REQ-80 的验收尺子）。

**为什么要有这个脚本**：R-PROD-17 那套抽样（``probe_tool_selection.py``）量的是
「模型点不点得中**工具**」；本卡要量的是另一个问题——「给定要记一笔，**分类**选对
了没有」。两者不可互相替代：工具选对（``transaction_add``）而分类选错（落「交通」
而不是「交通 · 打车」）在旧探针里是全绿。

口径
----
- **一步一调用**：一次真实模型调用，抽出 ``transaction_add`` /
  ``transaction_add_batch`` 第一个元素的 ``categoryName``。这是「用户说一句、
  模型直接记一笔」的最短形态，也是分类规则唯一必须生效的地方。
- 系统提示与工具清单走**真实链路**（``AgentLoop._system_message`` +
  ``to_openai_tools``），上下文是一个假用户，分类清单**逐字取自 T20-5 落地的
  真实种子**（28 支出 + 10 二级 / 15 收入），因此注入面的大小与截断行为都是真的。
- **四组分开报**（对应 REQ-80 的三个子诉求）：
  - ``二级``：「能落到具体二级就落二级」——期望是二级名；
  - ``一级``：没有贴切二级、一级就是终值——期望是一级名；
  - ``兜底``：四不像的消费也必须落**一个**名单内的名字——期望是「有名字且合法」；
  - ``提议``：明显没有对应分类时**问一句**要不要新建——期望是「没擅自创建」。
- **``--tokens``**：只做一次真实调用并把 ``usage.prompt_tokens`` 打出来，用于
  REQ-80 决策 #12 的 token 成本实测（改前 / 改后各跑一次）。

用法::

    cd server && python scripts/probe_category_selection.py            # 真实抽样
    cd server && python scripts/probe_category_selection.py --tokens   # token 实测
    cd server && python scripts/probe_category_selection.py --dump     # 只看注入面

**它不写库、不落任何状态**，可以随时重跑。
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
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
from src.modules.ai.provider import DeepSeekProvider, ReasoningLevel  # noqa: E402

# ---------------------------------------------------------------------------
# 假用户：分类清单逐字取自 T20-5 的种子（Repository.kt 的
# DEFAULT_EXPENSE_CATEGORIES / DEFAULT_INCOME_CATEGORIES），只有 id 是随机 UUID。
# ---------------------------------------------------------------------------

_EXPENSE_TREE: list[tuple[str, list[str]]] = [
    ("餐饮", []), ("休闲娱乐", []), ("购物", []), ("穿搭美容", []), ("水果零食", []),
    ("交通", ["公交", "打车", "地铁", "火车", "飞机"]),
    ("生活日用", []),
    ("人情社交", ["职场", "亲戚", "借用", "借出", "回礼"]),
    ("宠物", []), ("养娃", []), ("运动", []), ("生活服务", []), ("买菜", []),
    ("住房", []), ("爱车", []), ("发红包", []), ("转账", []), ("学习教育", []),
    ("网络虚拟", []), ("烟酒", []), ("医疗保健", []), ("金融保险", []),
    ("家居家电", []), ("酒店旅行", []), ("公益", []), ("互助保障", []),
    ("其他", []), ("约会", []),
]
_INCOME_TREE: list[tuple[str, list[str]]] = [
    (name, []) for name in (
        "工资", "兼职", "投资理财", "人情社交", "奖金补贴", "报销", "生意",
        "卖二手", "生活费", "中奖", "收红包", "收转账", "保险理赔", "退款", "其他",
    )
]


def _flatten(tree: list[tuple[str, list[str]]]) -> list[str]:
    return [name for name, kids in tree for name in [name, *kids]]


_EXPENSE_ALL = _flatten(_EXPENSE_TREE)
_INCOME_ALL = _flatten(_INCOME_TREE)
#: 服务端真的能解析出来的分类名（写路径按逐字匹配）——用来把「记错分类」
#: 与「写路径必然失败」分开。
_LEGAL_NAMES = frozenset(_EXPENSE_ALL) | frozenset(_INCOME_ALL)

_ACCOUNTS = {"招行卡": str(uuid.uuid4()), "现金": str(uuid.uuid4())}
_BOOKS = {"日常": str(uuid.uuid4())}

_FAKE_READS: dict[str, dict] = {
    "category_list": {
        "categories": [
            {
                "name": name,
                "kind": "expense",
                "parentName": next(
                    (p for p, kids in _EXPENSE_TREE if name in kids), None,
                ),
            }
            for name in _EXPENSE_ALL
        ] + [
            {"name": name, "kind": "income", "parentName": None}
            for name in _INCOME_ALL
        ],
    },
}
_FAKE_DEFAULT: dict = {"status": "ok", "note": "（探针的假结果）"}


def _fake_result(name: str) -> str:
    return json.dumps(_FAKE_READS.get(name, _FAKE_DEFAULT), ensure_ascii=False)


class _NullProvider(DeepSeekProvider):  # type: ignore[misc]
    """只为把 ``AgentLoop`` 拼系统提示这一步跑起来（本探针不经过它调用）。"""


def _tool_context() -> T.ToolContext:
    """与生产同形的上下文；父子关系**只在字段存在时**传（脚本改前改后都能跑）。"""
    by_kind = {
        "expense": {name: str(uuid.uuid4()) for name in _EXPENSE_ALL},
        "income": {name: str(uuid.uuid4()) for name in _INCOME_ALL},
    }
    kwargs: dict = {
        "db": None,  # 探针不执行工具
        "user_id": uuid.uuid4(),
        "book_id": uuid.UUID(_BOOKS["日常"]),
        "categories_by_kind": by_kind,
        "category_names_by_id": {
            cid: name for bucket in by_kind.values() for name, cid in bucket.items()
        },
        "account_names": _ACCOUNTS,
        "account_names_by_id": {aid: name for name, aid in _ACCOUNTS.items()},
        "book_names": _BOOKS,
        "book_names_by_id": {bid: name for name, bid in _BOOKS.items()},
    }
    known = {f.name for f in dataclasses.fields(T.ToolContext)}
    for field, tree in (
        ("category_children_by_kind", {"expense": _EXPENSE_TREE, "income": _INCOME_TREE}),
    ):
        if field in known:
            kwargs[field] = {
                kind: {parent: tuple(kids) for parent, kids in t if kids}
                for kind, t in tree.items()
            }
    return T.ToolContext(**kwargs)


# ---------------------------------------------------------------------------
# 用例：``(分组, 用户原话, 期望)``。期望的三种形态见模块头。
# ---------------------------------------------------------------------------

CASES: list[tuple[str, str, str]] = [
    # ── 二级：输入明确指向某个二级分类，就该落到二级 ──
    ("二级", "打车 25", "打车"),
    ("二级", "今天坐地铁花了 6 块", "地铁"),
    ("二级", "买了张高铁票 553，记一笔", "火车"),
    ("二级", "去机场打车花了 88", "打车"),
    ("二级", "上次跟老王借的 200 还他了", "借用"),
    # 多解：随份子可以是「职场」（同事）也可以是「回礼」——判据是**别停在笼统的
    # 一级「人情社交」**，因此期望写成可接受集合（竖线分隔），不是单一答案。
    ("二级", "同事结婚随了 600 份子钱", "职场|回礼|亲戚"),
    # 多解：「发红包」是**与「人情社交」并列的另一个具体类目**（不是宽泛一级），
    # 用户开口就说了「红包」——它和「亲戚」（姑姑）都是贴切解。
    ("二级", "给姑姑家孩子包了个 500 的红包", "亲戚|回礼|发红包"),
    # ── 一级：没有贴切的二级，一级本身就是终值 ──
    ("一级", "记一笔午饭 12 块", "餐饮"),
    ("一级", "交了这个月房租 3500", "住房"),
    ("一级", "买了袋猫粮 180", "宠物"),
    ("一级", "超市买菜 45", "买菜"),
    ("一级", "剪了个头发 88", "穿搭美容"),
    ("一级", "这个月工资到账 12000", "工资"),
    ("一级", "退了个款 99 到账", "退款"),
    # ── 兜底：四不像的消费也必须落一个名单内的名字 ──
    ("兜底", "记一笔 300 块，说不出是啥", ""),
    ("兜底", "花了 66，杂七杂八的", ""),
    ("兜底", "扣了 128 块，好像是什么服务费", ""),
    # ── 提议：明显没有对应分类时问一句，且**不得擅自创建** ──
    ("提议", "记一笔 500 块，给我家鹦鹉买了个笼子", ""),
    ("提议", "花了 800 块买了个域名做个人网站", ""),
]


async def _one(
    provider: DeepSeekProvider, *, system: str, tools: list[dict], model: str,
    utterance: str,
) -> tuple[str, str, list[str]]:
    """一次真实调用 → ``(分类名, 回复文本, 点过的工具名)``。"""
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": utterance},
    ]
    called: list[str] = []
    for _ in range(4):
        result = await provider.complete_with_tools(
            messages, model=model, max_tokens=1024, timeout=60.0,
            tools=tools, tool_choice="auto", reasoning=ReasoningLevel.FAST,
        )
        if not result.tool_calls:
            return "", result.content or "", called
        call = result.tool_calls[0]
        called.extend(c.name for c in result.tool_calls)
        args = _parse_args(call.arguments)
        if call.name in ("transaction_add", "transaction_add_batch"):
            first = args if call.name == "transaction_add" else (
                (args.get("transactions") or [{}])[0]
            )
            return str(first.get("categoryName") or ""), result.content or "", called
        if call.name in _FAKE_READS:
            messages.append({
                "role": "assistant", "content": result.content or "",
                "reasoning_content": result.reasoning_content or "",
                "tool_calls": [{
                    "id": c.id or f"call-{i}", "type": "function",
                    "function": {"name": c.name, "arguments": c.arguments},
                } for i, c in enumerate(result.tool_calls)],
            })
            for i, c in enumerate(result.tool_calls):
                messages.append({
                    "role": "tool", "tool_call_id": c.id or f"call-{i}",
                    "content": _fake_result(c.name),
                })
            continue
        return "", result.content or "", called
    return "", "", called


def _parse_args(raw: str | None) -> dict:
    try:
        parsed = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _verdict(group: str, got: str, text: str, called: list[str]) -> bool:
    if group == "兜底":
        return got in _LEGAL_NAMES
    if group == "提议":
        # 决策 #13：「提议 ≠ 擅自创建」。硬判据只有一条——**没调 category_create**
        # （那要过审批门、会在用户不知情时改了他的分类表）。回复里有没有问一句是
        # **观察项**：用户原话是「**可以**主动询问」，是加分行为而非通过条件。
        return "category_create" not in called
    return False


async def _tokens(provider: DeepSeekProvider, system: str, tools: list[dict], model: str) -> int:
    """一次真实调用，读 ``ProviderResult.prompt_tokens``（含工具清单，与生产同形）。"""
    result = await provider.complete_with_tools(
        [{"role": "system", "content": system}, {"role": "user", "content": "打车 25"}],
        model=model, max_tokens=1, timeout=60.0,
        tools=tools, tool_choice="auto", reasoning=ReasoningLevel.FAST,
    )
    return result.prompt_tokens


async def main() -> int:
    parser = argparse.ArgumentParser(description="分类选择准确率抽样（REQ-80）")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 个用例（冒烟）")
    parser.add_argument("--dump", action="store_true", help="只打印注入面，不调模型")
    parser.add_argument("--tokens", action="store_true", help="只做一次调用量 prompt token")
    parser.add_argument("--routing", action="store_true", help="按域路由收窄注入面")
    parser.add_argument(
        "--repeat", type=int, default=1,
        help="每条用例跑几轮（模型有随机性，单轮噪声大；默认 1）",
    )
    parser.add_argument("--group", default="", help="只跑某一组（二级/一级/兜底/提议）")
    parser.add_argument("-v", "--verbose", action="store_true", help="打印工具序列与回复")
    args = parser.parse_args()

    cfg = get_config()
    lane_tools = T.tools_for_domains(T.DOMAINS, contract_version=2)
    ctx = _tool_context()
    loop = AgentLoop(
        _NullProvider(cfg.ai_agent_provider_api_key, cfg.ai_agent_provider_base_url),
        ctx,
        model=cfg.ai_agent_provider_model,
        timeout=60.0,
        today=date(2026, 9, 28),
        tools=lane_tools,
        contract_version=2,
    )
    system = loop._system_message()["content"]  # noqa: SLF001 — 要的就是真实系统提示
    section = next(
        (ln for ln in system.splitlines() if "现有分类（支出）" in ln), "（没找到分类行）",
    )

    if args.dump:
        print(f"支出行长度 {len(section)} 字符：\n{section}")
        print(f"\n系统提示总长 {len(system)} 字符")
        return 0

    if not cfg.ai_agent_provider_api_key:
        print("缺少 AI_AGENT_PROVIDER_API_KEY（server/.env）", file=sys.stderr)
        return 2

    provider = DeepSeekProvider(
        cfg.ai_agent_provider_api_key, cfg.ai_agent_provider_base_url,
    )
    try:
        if args.tokens:
            total = await _tokens(
                provider, system, T.to_openai_tools(lane_tools),
                cfg.ai_agent_provider_model,
            )
            print(f"系统提示 {len(system)} 字符 → prompt_tokens = {total}")
            return 0

        cases = CASES[: args.limit] if args.limit else CASES
        if args.group:
            cases = [c for c in cases if c[0] == args.group]
        print(f"模型 {cfg.ai_agent_provider_model}；注入面：{section[:60]}…")
        print(f"{'组':<5} {'用户原话':<26} {'期望':<14} {'实得（逐轮）':<18} 命中")
        print("-" * 88)
        stats: dict[str, list[int]] = {}
        totals: dict[str, int] = {}
        for group, utterance, expected in cases:
            tools = T.to_openai_tools(lane_tools)
            if args.routing:
                decision = routing.route_domains(utterance)
                tools = T.to_openai_tools(
                    T.tools_for_domains(decision.domains, contract_version=2),
                )
            hits = 0
            seen: list[str] = []
            note = ""
            for _ in range(args.repeat):
                got, text, called = await _one(
                    provider, system=system, tools=tools,
                    model=cfg.ai_agent_provider_model, utterance=utterance,
                )
                if group == "二级":
                    ok = got in expected.split("|")
                elif group == "一级":
                    ok = got == expected or (
                        # 「交通」这类一级名有二级同域时，落到贴切二级也算对
                        got in {c for p, kids in _EXPENSE_TREE if p == expected for c in kids}
                    )
                else:
                    ok = _verdict(group, got, text, called)
                hits += ok
                seen.append(got or "—")
                # 非法值＝不在任何真实分类名里。这种值在服务端 `resolve_category`
                # 上必然 `unknown_category`（**记账失败**，不是记错分类），因此
                # 单独标出来——它是本轮改动引入的风险面（层级渲染的括号格式）。
                if got and got not in _LEGAL_NAMES:
                    note = f"   ⚠ 非法值：{got[:28]!r}"
                elif group == "提议" and text:
                    note = f"   说：{text[:24]}"
                if args.verbose:
                    print(f"      · 工具={' > '.join(called) or '（未调）'} "
                          f"分类={got!r} 回复={text[:40]!r}")
            stats.setdefault(group, []).append(hits)
            totals[group] = totals.get(group, 0) + args.repeat
            print(
                f"{group:<5} {utterance:<26} {expected or '(任意)':<14} "
                f"{'/'.join(seen)[:18]:<18} {hits}/{args.repeat}"
                + ("" if hits == args.repeat else note),
            )
    finally:
        await provider.aclose()

    print("-" * 88)
    for group, checks in stats.items():
        hit = sum(checks)
        total = totals[group]
        print(f"{group}：{hit}/{total} = {hit / total:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
