"""T-M4-03b: Narrative generation & number verification.

The LLM receives a deterministic FactPack (T-M4-03a) and generates a
natural-language narrative.  Every number in the output must be traceable
to the FactPack — if a number cannot be mapped, the output is rejected and
a security-level event is logged.

Verification algorithm:
1. Parse the LLM's JSON output to extract ``data_references``.
2. For each reference, look up the corresponding field in the FactPack.
3. If the value does not match, or references a fact key that does not
   exist in the FactPack, the entire output is rejected.
"""

from __future__ import annotations

import logging
from typing import Any

from .fact_pack import FactPack

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def verify_narrative(
    llm_output: dict[str, Any],
    fact_pack: FactPack,
) -> list[str]:
    """Verify every referenced number in *llm_output* against *fact_pack*.

    Returns a list of verification errors (empty = all numbers verified).
    Each error is a human-readable string describing the mismatch.
    """
    errors: list[str] = []
    refs: list[dict] = llm_output.get("data_references", [])

    # Build a lookup map from the fact pack
    lookup = _build_lookup(fact_pack)

    for ref in refs:
        key = ref.get("fact")
        value = ref.get("value")
        if key is None or value is None:
            errors.append(f"Invalid reference entry: {ref}")
            continue
        if key not in lookup:
            errors.append(f"Unknown fact key: {key}")
            continue
        expected = lookup[key]
        if value != expected:
            errors.append(
                f"Fact key '{key}' value mismatch: "
                f"narrative says {value}, fact pack has {expected}"
            )

    # Also check that the narrative text doesn't contain random numbers
    # that weren't declared in data_references.  This is a best-effort
    # heuristic: extract all integers >= 100 from the narrative and check
    # they appear in the lookup.
    narrative = llm_output.get("narrative", "")
    if narrative:
        undeclared = _find_undeclared_numbers(narrative, lookup, refs)
        if undeclared:
            errors.append(f"Narrative contains undeclared numbers: {undeclared}")

    return errors


def build_context_dict(fact_pack: FactPack) -> dict[str, Any]:
    """Build a flat dict of fact keys → values for LLM context injection.

    This is the *only* data the LLM sees for report generation.
    """
    ctx: dict[str, Any] = {
        "total_income": fact_pack.total_income,
        "total_expense": fact_pack.total_expense,
        "balance": fact_pack.balance,
        "transaction_count": fact_pack.transaction_count,
    }

    # Top categories
    if fact_pack.expense_by_category:
        top = fact_pack.expense_by_category[0]
        ctx["top_expense_category"] = top.category_name
        ctx["top_expense_amount"] = top.total_cents
    if fact_pack.income_by_category:
        top = fact_pack.income_by_category[0]
        ctx["top_income_category"] = top.category_name
        ctx["top_income_amount"] = top.total_cents

    # Budget
    if fact_pack.budget:
        ctx["budget_amount"] = fact_pack.budget.amount_cents
        ctx["budget_spent"] = fact_pack.budget.spent_cents
        ctx["budget_remaining"] = fact_pack.budget.remaining_cents
        ctx["budget_pct"] = fact_pack.budget.pct_used

    # Largest transactions
    if fact_pack.largest_expense:
        ctx["largest_expense_category"] = fact_pack.largest_expense.category_name
        ctx["largest_expense_amount"] = fact_pack.largest_expense.amount_cents
        ctx["largest_expense_note"] = fact_pack.largest_expense.note
    if fact_pack.largest_income:
        ctx["largest_income_category"] = fact_pack.largest_income.category_name
        ctx["largest_income_amount"] = fact_pack.largest_income.amount_cents

    # Trend
    if len(fact_pack.trend) > 1:
        current = fact_pack.trend[-1]
        previous = fact_pack.trend[-2]
        ctx["last_month_expense"] = previous.total_expense
        ctx["last_month_income"] = previous.total_income
        # Average of last 6 months
        avg_expense = sum(t.total_expense for t in fact_pack.trend) / len(fact_pack.trend)
        ctx["trend_avg_expense"] = round(avg_expense)
        # Direction
        if current.total_expense > previous.total_expense * 1.1:
            ctx["trend_direction"] = "up"
        elif current.total_expense < previous.total_expense * 0.9:
            ctx["trend_direction"] = "down"
        else:
            ctx["trend_direction"] = "stable"

    # Income/expense ratio
    if fact_pack.total_income > 0:
        ctx["income_expense_ratio"] = round(
            fact_pack.total_expense / fact_pack.total_income, 2
        )

    # Category breakdown text
    if fact_pack.expense_by_category:
        ctx["top_expense_categories"] = [
            {"name": c.category_name, "amount": c.total_cents, "count": c.count}
            for c in fact_pack.expense_by_category[:3]
        ]

    return ctx


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_lookup(fact_pack: FactPack) -> dict[str, Any]:
    """Build a flat key→value lookup from the fact pack for verification.

    Reuses build_context_dict to ensure consistency between what the LLM sees
    and what we verify against. Filters to only include numeric values that
    can be referenced in data_references.
    """
    # Start with the full context dict
    context = build_context_dict(fact_pack)

    # Filter to only include numeric values (strings/lists can't be verified
    # via data_references value matching)
    lookup: dict[str, Any] = {}
    for key, value in context.items():
        if isinstance(value, (int, float)):
            lookup[key] = value

    return lookup


def _find_undeclared_numbers(
    text: str,
    lookup: dict[str, Any],
    declared_refs: list[dict],
) -> list[int]:
    """Find integers >= 100 in *text* that are not in *lookup* or *declared_refs*.

    Returns a list of undeclared numbers found.

    **Behavior note**: This function considers a number "declared" if it appears
    in either the explicit data_references OR anywhere in the lookup values.
    This means if the LLM mentions a number that happens to match a fact pack
    value (e.g., total_expense=500000) without explicitly declaring it in
    data_references, it won't be flagged as undeclared. This is intentional:
    the primary verification is the data_references check, and this heuristic
    is a secondary safety net to catch truly fabricated numbers.

    Known limitation: Years like "2024" in dates will be flagged unless the
    lookup happens to contain that exact value. This is acceptable because
    the LLM is instructed to use data_references for numeric claims.
    """
    import re

    # Collect values that are already declared (from explicit refs + lookup)
    declared_values: set[int] = set()
    for ref in declared_refs:
        val = ref.get("value")
        if isinstance(val, (int, float)):
            declared_values.add(int(val))
    for val in lookup.values():
        if isinstance(val, (int, float)):
            declared_values.add(int(val))

    # Find all integers >= 100 in the narrative text.
    # Use digit-boundary lookahead/lookbehind instead of \b because \b
    # does not match between Chinese characters and digits.
    undeclared: list[int] = []
    for match in re.finditer(r"(?<!\d)(\d{3,})(?!\d)", text):
        num = int(match.group(1))
        if num not in declared_values:
            undeclared.append(num)

    return undeclared
