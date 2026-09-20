"""T-M7-05b: 工具选择准确率抽样探针（R-PROD-17 的数据点）。

**为什么要有这个脚本**：架构 §7.2 的经验上限（单域 5–8 个工具）与 R-PROD-17
（工具集过大降低选择准确率）都只能靠**真实调用**取证——MockTransport 不经过
真实接口，测不出模型会不会在两个近义工具之间选错（02c 的点号命名事故就是这么
漏掉的）。05b 把工具集从 15 扩到 27、05c 再扩到 43（9 个域）、05d 收尾到 54
（14 个域），每一步都是这条风险最该被量一次的时刻。

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
- 车道固定 v2（全量注入），因为 05b / 05c 的新工具只出现在这条车道上。
- **05b / 05c / 05d 的用例分开记账**（``CASES`` / ``CASES_05C`` / ``CASES_05D``）：
  扩域之后要看的不只是总准确率，还有「新工具会不会被同域的读工具或近义工具抢走」。

用法::

    cd server && python scripts/probe_tool_selection.py            # 全部用例
    cd server && python scripts/probe_tool_selection.py --lane 1   # 量 v1 车道

结果打印成表格（用例 / 期望 / 实际 / 是否命中）并给出分组准确率。**它不写库、
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
#: T-M7-05c：三实体的名字索引（与假读结果里的名字逐字一致——对不上就会把
#: 「模型在找那个目标」误判成「模型选错了工具」）。
_SAVINGS = {"旅行基金": str(uuid.uuid4()), "相机基金": str(uuid.uuid4())}
_SCHEDULED = {"房租": str(uuid.uuid4()), "早餐": str(uuid.uuid4())}
_PERSONAS = {"小贝": str(uuid.uuid4()), "猫娘": str(uuid.uuid4())}

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

#: **T-M7-05c 新增用例**（16 个新工具：预算写 / 存钱 / 定时 / 角色）。与 05b 的
#: 用例一起跑但**分开记账**——要看的正是「新工具会不会被同域的读工具或近义工具
#: 抢走」（域内工具数涨到 8 的预算域尤其值得量）。
CASES_05C: list[tuple[str, str]] = [
    # ── 预算域：写（与 budget_list / budget_status / budget_check 近义）──
    ("这个月的整体预算改成 6000", "budget_set"),
    ("给餐饮设个月度预算 1500", "budget_set_category"),
    ("把九月的餐饮预算删掉", "budget_delete"),
    ("我这个月有哪些预算", "budget_list"),
    ("餐饮预算还剩多少", "budget_status"),
    # ── 存钱域 ──
    ("建一个存钱目标叫相机基金，目标 8000", "savings_create"),
    ("旅行基金我再存 500", "savings_update"),
    ("相机基金的目标金额改成 6000", "savings_update"),
    ("旅行基金不攒了，归档掉", "savings_archive"),
    ("我的存钱目标有哪些", "savings_list"),
    # ── 定时域 ──
    ("设置每个月一号自动记一笔房租 3000", "scheduled_create"),
    ("房租那条定时记账改成 3500", "scheduled_update"),
    ("早餐那条定时记账先暂停", "scheduled_update"),
    ("房租规则不要了，删掉", "scheduled_archive"),
    ("我有哪些定时记账", "scheduled_list"),
    # ── 角色域 ──
    ("再建一个角色叫猫娘，说话带喵", "persona_create"),
    ("把猫娘的人设改得温柔一点", "persona_update"),
    ("换成猫娘跟我聊", "persona_switch"),
    ("猫娘这个角色不要了", "persona_archive"),
    ("我有哪些聊天角色", "persona_list"),
]

#: T-M7-05d：五域 11 个工具各一条。**每个工具一条**是有意的——本轮要看的正是
#: 「新域的工具会不会被别的域抢走」（`stats_monthly` 与 `transaction_summary` /
#: `stats_trends` 近义、`sync_status` 与 `consent_status` 都在回答「为什么不行」）。
CASES_05D: list[tuple[str, str]] = [
    # ── 分析域（与 transaction_summary / budget_status 近义）──
    ("这个月我一共花了多少", "stats_monthly"),
    ("帮我看看最近半年的收支趋势", "stats_trends"),
    # ── 导出域 ──
    ("把日常账本导出成 CSV", "export_create"),
    ("我之前导出的文件还在吗", "export_list"),
    # ── 反馈与隐私域 ──
    ("我要反馈一个问题，预算页点进去是白屏", "feedback_submit"),
    ("我同意隐私政策", "consent_record"),
    ("我同意过隐私政策吗", "consent_status"),
    # ── 安全账号域 ──
    ("以后叫我小王", "auth_update_profile"),
    ("我不想用了，把账号注销掉", "account_deletion_request"),
    # ── 同步域（与 consent_status 都在回答「为什么不行」）──
    ("怎么同步不了了", "sync_status"),
    ("同步一下", "sync_trigger"),
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
    # ── T-M7-05c 三个读工具：假结果里的名字与 05c 用例逐字对得上 ──
    "budget_list": {"period": "2026-09", "items": [
        {"isOverall": True, "categoryName": None, "period": "2026-09",
         "budgetCents": 500000, "spentCents": 320000, "remainingCents": 180000,
         "pctUsed": 64.0, "exceeded": False},
        {"isOverall": False, "categoryName": "餐饮", "period": "2026-09",
         "budgetCents": 150000, "spentCents": 168000, "remainingCents": -18000,
         "pctUsed": 112.0, "exceeded": True},
    ]},
    "budget_status": {"categoryName": "餐饮", "period": "2026-09", "hasBudget": True,
                      "budgetCents": 150000, "spentCents": 168000,
                      "remainingCents": -18000, "pctUsed": 112.0, "exceeded": True},
    "savings_list": {"savings": [
        {"name": "旅行基金", "targetCents": 800000, "currentCents": 200000,
         "remainingCents": 600000, "progressPct": 25.0, "achieved": False,
         "icon": "✈️", "periodValue": 3, "periodUnit": "month"},
        {"name": "相机基金", "targetCents": 500000, "currentCents": 0,
         "remainingCents": 500000, "progressPct": 0.0, "achieved": False,
         "icon": None, "periodValue": None, "periodUnit": None},
    ], "total": 2},
    "scheduled_list": {"rules": [
        {"name": "房租", "type": "expense", "amountCents": 300000, "categoryName": "餐饮",
         "accountName": "招行卡", "repeat": "monthly", "startDate": "2026-01-01",
         "endDate": None, "runHour": 8, "runMinute": 0, "enabled": True,
         "includeInBudget": True, "note": "", "lastRunDate": "2026-09-01"},
        {"name": "早餐", "type": "expense", "amountCents": 1500, "categoryName": "餐饮",
         "accountName": None, "repeat": "daily", "startDate": "2026-06-01",
         "endDate": None, "runHour": 7, "runMinute": 30, "enabled": True,
         "includeInBudget": True, "note": "", "lastRunDate": "2026-09-18"},
    ], "total": 2},
    "persona_list": {"personas": [
        {"name": "小贝", "avatar": "🐻", "persona": "你是小贝，一个温和的记账助手。"},
        {"name": "猫娘", "avatar": "🐱", "persona": "你说话带喵。"},
    ], "total": 2},
    # ── T-M7-05d 的五个读工具（形状取自各自的处理器，内容是编的）──
    "stats_monthly": {
        "period": "2026-09", "totalIncomeCents": 500000, "totalExpenseCents": 320000,
        "balanceCents": 180000, "transactionCount": 42,
        "expenseByCategory": [
            {"name": "餐饮", "totalCents": 168000, "count": 21},
            {"name": "交通", "totalCents": 152000, "count": 21},
        ],
        "incomeByCategory": [{"name": "工资", "totalCents": 500000, "count": 1}],
        "budget": {"budgetCents": 500000, "spentCents": 320000,
                   "remainingCents": 180000, "pctUsed": 64.0},
        "largestExpense": {"amountCents": 4500, "categoryName": "交通",
                           "note": "打车费", "bizDate": "2026-09-11"},
        "largestIncome": {"amountCents": 500000, "categoryName": "工资",
                          "note": "九月工资", "bizDate": "2026-09-05"},
        "trend": [
            {"period": "2026-04", "totalIncomeCents": 480000, "totalExpenseCents": 300000,
             "balanceCents": 180000, "transactionCount": 38},
            {"period": "2026-09", "totalIncomeCents": 500000, "totalExpenseCents": 320000,
             "balanceCents": 180000, "transactionCount": 42},
        ],
    },
    "stats_trends": {
        "periodFrom": "2026-04", "periodTo": "2026-09",
        "points": [
            {"period": "2026-04", "totalIncomeCents": 480000,
             "totalExpenseCents": 300000, "balanceCents": 180000, "transactionCount": 38},
            {"period": "2026-05", "totalIncomeCents": 490000,
             "totalExpenseCents": 310000, "balanceCents": 180000, "transactionCount": 40},
            {"period": "2026-06", "totalIncomeCents": 0,
             "totalExpenseCents": 0, "balanceCents": 0, "transactionCount": 0},
            {"period": "2026-07", "totalIncomeCents": 500000,
             "totalExpenseCents": 330000, "balanceCents": 170000, "transactionCount": 41},
            {"period": "2026-08", "totalIncomeCents": 500000,
             "totalExpenseCents": 340000, "balanceCents": 160000, "transactionCount": 43},
            {"period": "2026-09", "totalIncomeCents": 500000,
             "totalExpenseCents": 320000, "balanceCents": 180000, "transactionCount": 42},
        ],
    },
    "export_list": {"count": 2, "exports": [
        {"format": "csv", "status": "completed", "createdAt": "2026-09-18T09:00:00+00:00",
         "expiresAt": "2026-09-19T09:00:00+00:00", "fileSizeBytes": 20480},
        {"format": "json", "status": "expired", "createdAt": "2026-09-10T09:00:00+00:00",
         "expiresAt": "2026-09-11T09:00:00+00:00", "fileSizeBytes": 40960},
    ]},
    "consent_status": {"hasConsented": True, "policyVersion": "2026-01-01",
                       "consentedAt": "2026-01-01T00:00:00+00:00"},
    "sync_status": {"accountStatus": "active", "consented": True, "canSync": True,
                    "lastServerChangeAt": "2026-09-18T08:30:00+00:00"},
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
        savings_names=_SAVINGS,
        savings_names_by_id={gid: name for name, gid in _SAVINGS.items()},
        scheduled_names=_SCHEDULED,
        scheduled_names_by_id={rid: name for name, rid in _SCHEDULED.items()},
        persona_names=_PERSONAS,
        persona_names_by_id={pid: name for name, pid in _PERSONAS.items()},
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
    results: list[tuple[str, bool, bool]] = []
    cases = CASES[: args.limit] if args.limit else CASES
    cases_05c = CASES_05C[: args.limit] if args.limit else CASES_05C
    cases_05d = CASES_05D[: args.limit] if args.limit else CASES_05D
    n_05b = len(cases)
    n_05c = len(cases_05c)
    try:
        for offset, (utterance, expected) in enumerate(cases + cases_05c + cases_05d):
            if offset < n_05b:
                group = "05b"
            elif offset < n_05b + n_05c:
                group = "05c"
            else:
                group = "05d"
            names = await _sequence(
                provider, system=system, tools=payload,
                model=cfg.ai_agent_provider_model, utterance=utterance,
            )
            first = names[0] if names else "-"
            first_hit = first == expected
            seq_hit = expected in names
            results.append((group, first_hit, seq_hit))
            print(
                f"{utterance:<34} {expected:<22} {' -> '.join(names) or '-':<34} "
                f"{'命中' if first_hit else '未中'} {'命中' if seq_hit else '未中'}"
            )
    finally:
        await provider.aclose()

    print("-" * 104)
    for group, subset in (
        ("全部", results),
        ("05b（既有对照组）", [r for r in results if r[0] == "05b"]),
        ("05c（工具 27→43 时新增 16 个）", [r for r in results if r[0] == "05c"]),
        ("05d（工具 43→54 时新增 11 个）", [r for r in results if r[0] == "05d"]),
    ):
        if not subset:
            continue
        first_hits = sum(r[1] for r in subset)
        sequence_hits = sum(r[2] for r in subset)
        total = len(subset)
        print(
            f"{group}：首调命中 {first_hits}/{total} = {first_hits / total:.0%}，"
            f"两步内命中 {sequence_hits}/{total} = {sequence_hits / total:.0%}",
        )
    missed = [r for r in results if not r[2]]
    return 0 if not missed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
