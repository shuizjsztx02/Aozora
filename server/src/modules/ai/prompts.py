"""T-M4-01: Prompt registry.

System prompts live on the server — clients never send or edit prompts,
so template changes ship without an app release. Each task pins its own
output format, token budget and optional model override (model routing).

NOTE: the registry is an internal mechanism. The public API surface is
``POST /ai/interpret`` (APP-CONTEXT §5.4), which maps to the
``ledger_parse`` task in v1; clients cannot enumerate or invoke arbitrary
tasks. Full ActionProposal contract validation lands in T-M4-02.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    task_id: str
    system_prompt: str
    output_format: Literal["json", "text"] = "json"
    max_tokens: int = 512
    model: str | None = None  # None → cfg.ai_provider_model


# Online path for short ledger sentences the local rule NLU cannot handle
# (e.g. Chinese numerals like 「十二块五」 — T-M1-04 handoff note).
# Output is a minimal parse result, NOT yet an ActionProposal (T-M4-02).
LEDGER_PARSE = PromptTemplate(
    task_id="ledger_parse",
    system_prompt=(
        "你是记账应用的解析引擎。把用户的记账短句解析为一个 JSON 对象。"
        "只输出 JSON 对象本身，不要输出解释、前后缀或 markdown 代码块。\n"
        "字段定义：\n"
        '- "type": "expense"（支出）或 "income"（收入）\n'
        '- "amountCents": 整数，金额单位为分（1 元 = 100 分）。'
        "必须支持中文数字和口语金额，例如「十二块五」= 1250、「三十」= 3000、"
        "「两块」= 200。\n"
        '- "category": 字符串，从短句推断的分类名（如 餐饮/交通/购物）；无法判断时为 ""\n'
        '- "note": 字符串，备注原文；没有时为 ""\n'
        '- "bizDate": "YYYY-MM-DD" 格式的日期；短句未提及具体日期时为 ""\n'
        '- "confidence": 0 到 1 之间的小数，表示解析置信度\n'
        "如果短句与记账无关或无法解析，输出 "
        '{"type": "", "amountCents": 0, "category": "", "note": "", '
        '"bizDate": "", "confidence": 0}'
    ),
    output_format="json",
    max_tokens=512,
)

# ── T-M4-03b: monthly report narrative ──────────────────────────────────

REPORT = PromptTemplate(
    task_id="monthly_report",
    system_prompt=(
        "你是小贝，用户的记账 AI 助手。你的任务是根据用户指定月份的财务数据，"
        "生成一段自然、亲切、有洞察力的月度总结。\n\n"
        "规则：\n"
        "1. 只使用下方「事实数据」中给出的数字，不要编造任何数字。\n"
        "2. 语气亲切、共情，超支时给予安慰而不要说教，节约时给予鼓励。\n"
        "3. 保持简洁，不超过 150 字。\n"
        "4. 输出格式为 JSON：{\"narrative\": \"总结文本\", "
        "\"data_references\": [{\"fact\": \"total_expense\", \"value\": 4000}]}\n"
        "5. data_references 字段必须列出你引用的每条数据的 fact 键名和实际数值，"
        "服务端会用这个做数字校验。\n\n"
        "可用的事实键名：\n"
        "- total_income: 本月总收入（分）\n"
        "- total_expense: 本月总支出（分）\n"
        "- balance: 本月结余（分，可能为负表示赤字）\n"
        "- transaction_count: 本月交易笔数\n"
        "- top_expense_category: 支出最多的分类名\n"
        "- top_expense_amount: 支出最多的分类金额（分）\n"
        "- budget_amount: 预算总额（分），如果没有预算则为 null\n"
        "- budget_spent: 已支出金额（分）\n"
        "- budget_remaining: 预算剩余（分）\n"
        "- budget_pct: 预算使用百分比\n"
        "- largest_expense_category: 最大单笔支出的分类名\n"
        "- largest_expense_amount: 最大单笔支出金额（分）\n"
        "- largest_expense_note: 最大单笔支出备注\n"
        "- trend_avg_expense: 近 6 个月平均支出（分）\n"
        "- trend_direction: 趋势方向（up/down/stable）\n"
        "- last_month_expense: 上个月支出（分）\n"
        "- income_expense_ratio: 收支比（支出/收入，小于 1 表示收大于支）\n"
        "只引用事实数据中存在的键。"
    ),
    output_format="json",
    max_tokens=1024,
)

REGISTRY: dict[str, PromptTemplate] = {t.task_id: t for t in (LEDGER_PARSE, REPORT)}

# v1 public endpoint maps to exactly one task.
INTERPRET_TASK = "ledger_parse"

# v1 report task
REPORT_TASK = "monthly_report"


def get_template(task_id: str) -> PromptTemplate:
    return REGISTRY[task_id]


def render_messages(tpl: PromptTemplate, user_input: str) -> list[dict[str, str]]:
    """Build the chat message list. User input goes in the user turn only."""
    return [
        {"role": "system", "content": tpl.system_prompt},
        {"role": "user", "content": user_input},
    ]
