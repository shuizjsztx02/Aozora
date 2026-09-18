"""T-M4-02: server-side ActionProposal validator.

Draft-07 parity with ``ajv`` / TS ``validateProposal`` — same shared
fixtures in ``contracts/action-proposal/fixtures/`` must produce the
same valid/invalid verdict on all three validators (TS / ajv / Python).

Design notes
- Format ``date`` is enforced with a full round-trip via ``datetime.date``
  (matches ``parseBizDate`` in ``app/src/domain/date.ts``). A plain regex
  would accept ``2025-02-30``; the round-trip rejects it.
- ``additionalProperties: false`` at both root and TransactionItem is
  enforced by the schema; ``jsonschema`` honours it out of the box.
- The validator is intentionally separate from proposal assembly —
  validation is pure schema + format checking. Assembly happens on the
  client (see ADR-003); DB context (category/account IDs) is applied
  server-side via ``context.py`` when ``/ai/validate`` runs.
"""

from __future__ import annotations

import datetime
import json
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator, FormatChecker
from jsonschema.exceptions import ValidationError as _JsonSchemaError

# Schema is loaded once at import time and cached. The path is relative
# to this file: server/src/modules/ai → repo root = 4 parents up.
_SCHEMA_PATH = Path(__file__).resolve().parents[4] / "contracts" / "action-proposal" / "schema.json"

_BIZ_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_valid_biz_date(value: str) -> bool:
    """Round-trip YYYY-MM-DD through ``datetime.date``.

    Mirrors the TS ``parseBizDate``: regex first, then construct a date
    object and compare year/month/day to catch impossible dates like
    Feb 30 or leap-year violations.
    """
    if not _BIZ_DATE_RE.match(value):
        return False
    try:
        y, m, d = int(value[0:4]), int(value[5:7]), int(value[8:10])
        dt = datetime.date(y, m, d)
        return dt.year == y and dt.month == m and dt.day == d
    except (ValueError, OverflowError):
        return False


def _load_schema() -> dict[str, Any]:
    with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


_SCHEMA = _load_schema()

# Build the validator once; format checking is enabled explicitly via
# ``format_checker``. Draft7Validator does NOT check formats by default
# (APP-CONTEXT §6.3 / ai-architecture.md: "消费者必须启用 format: date
# 的完整公历日期断言").
_FORMAT_CHECKER = FormatChecker()


@_FORMAT_CHECKER.checks("date", raises=())
def _check_biz_date_format(value: object) -> bool:
    """``format: date`` 断言。

    ``isinstance`` 守卫是必需的：jsonschema 对 ``format`` 关键字**不做类型
    前置过滤**（与 ``pattern`` 不同，后者有 ``is_type(instance,"string")``
    守卫），任何类型的实例都会被送进校验器。缺了守卫时，模型把日期写成
    数字（``20260918``）或 ``null`` 会让 ``re.match`` 抛 ``TypeError``，
    异常穿过 ``iter_errors`` —— 调用方看到的是 500 而不是「校验失败」。
    """
    if not isinstance(value, str):
        return False
    return _is_valid_biz_date(value)


_VALIDATOR = Draft7Validator(_SCHEMA, format_checker=_FORMAT_CHECKER)


def validate_proposal(input: Any) -> dict[str, Any]:
    """Validate an ActionProposal-shaped object.

    Returns a dict ``{"valid": bool, "errors": list}`` mirroring the
    TS ``ValidationResult`` shape. Each error has ``path`` and ``message``.
    ``path`` uses dotted / bracketed JSON path notation (e.g.
    ``transactions[0].amountCents``).
    """
    errors: list[dict[str, str]] = []

    for err in _VALIDATOR.iter_errors(input):
        path = _error_path(err)
        errors.append({"path": path, "message": err.message})

    return {"valid": len(errors) == 0, "errors": errors}


def _error_path(err: _JsonSchemaError) -> str:
    """Convert jsonschema's ``path`` deque to a dotted/bracketed string."""
    parts: list[str] = []
    for segment in err.absolute_path:
        if isinstance(segment, int):
            parts.append(f"[{segment}]")
        else:
            parts.append(f".{segment}" if parts else str(segment))
    path = "".join(parts)
    # Replace leading `.0]` style — fix the first bracket to follow a dot
    return path or "$"


def is_valid_proposal(input: Any) -> bool:
    """Shorthand: does the input pass validation?"""
    return _VALIDATOR.is_valid(input)
