"""T-M4-02: server-side ActionProposal validator.

Draft-07 parity with ``ajv`` / TS ``validateProposal`` — same shared
fixtures in ``contracts/action-proposal/fixtures/`` must produce the
same valid/invalid verdict on all three validators (TS / ajv / Python).

Two lanes since T-M7-05a (the v1 lane is byte-for-byte unchanged):

- **v1** — ``contracts/action-proposal/schema.json`` (fixed action set),
  entry points :func:`validate_proposal` / :func:`is_valid_proposal`.
- **v2** — ``contracts/action-proposal/schema-v2.json`` (op registry +
  generic ``payload``), entry point :func:`validate_proposal_v2`.
  :func:`validate_proposal_any` is the dual-read dispatcher: a proposal
  belongs to v2 iff it carries the ``contractVersion`` key (v1 forbids
  unknown fields and has no such field, so the two lanes cannot be
  confused for one another).

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
- **v2 validates the envelope only.** The field shape of an op's
  ``payload`` is owned by that op's tool registry entry
  (``tools.py`` ``parameters``); the contract deliberately does not
  restate it, because a restated copy drifts from the tool. Payload-level
  authorization is therefore applied server-side at assembly / execute
  time — passing the v2 schema is not an execution permit.
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
_CONTRACT_DIR = Path(__file__).resolve().parents[4] / "contracts" / "action-proposal"
_SCHEMA_PATH = _CONTRACT_DIR / "schema.json"
_SCHEMA_V2_PATH = _CONTRACT_DIR / "schema-v2.json"
_ALIASES_PATH = _CONTRACT_DIR / "v1-action-aliases.json"

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


def _load_json(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


_SCHEMA = _load_json(_SCHEMA_PATH)
_SCHEMA_V2 = _load_json(_SCHEMA_V2_PATH)

#: v1 action → v2 规范 op（``None`` = 该 v1 action 在 v2 里没有对应提案）。
_V1_ACTION_ALIASES: dict[str, str | None] = _load_json(_ALIASES_PATH)["aliases"]

#: v2 op 注册表。**唯一来源是 ``schema-v2.json`` 的 action 枚举**，此处不另抄一份。
V2_OPS: frozenset[str] = frozenset(_SCHEMA_V2["properties"]["action"]["enum"])

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

# v2 的信封里目前没有 ``format`` 关键字；同一个 checker 传进去，是为了以后给
# v2 加 format 时不会静默失效（Draft7Validator 默认不检查 format）。
_VALIDATOR_V2 = Draft7Validator(_SCHEMA_V2, format_checker=_FORMAT_CHECKER)


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


# ---------------------------------------------------------------------------
# v2 车道（T-M7-05a）
# ---------------------------------------------------------------------------


def validate_proposal_v2(input: Any) -> dict[str, Any]:
    """Validate an ActionProposal **v2** (``contractVersion: 2``).

    Envelope-level only — same return shape as :func:`validate_proposal`.
    The op's ``payload`` is checked for being an object and nothing more:
    its field shape belongs to the op's tool registry entry, and
    payload-level authorization is applied server-side at assembly /
    execute time (see the module docstring).
    """
    errors: list[dict[str, str]] = []

    for err in _VALIDATOR_V2.iter_errors(input):
        errors.append({"path": _error_path(err), "message": err.message})

    return {"valid": len(errors) == 0, "errors": errors}


def validate_proposal_any(input: Any) -> dict[str, Any]:
    """双读入口：``{"valid", "errors", "contractVersion"}``。

    车道判定＝ ``contractVersion`` 键在场与否（v1 关闭 ``additionalProperties``
    且没有这个字段，所以两条车道不可能互相误判）。``contractVersion`` 为
    ``1`` / ``2``，输入根本不是对象时为 ``None``。

    客户端可能收到 v1（服务端为不认识 v2 的旧客户端降级产出）也可能收到 v2，
    两种都必须能判定——这就是本函数存在的理由。
    """
    if not isinstance(input, dict):
        return {**validate_proposal(input), "contractVersion": None}
    if "contractVersion" not in input:
        return {**validate_proposal(input), "contractVersion": 1}
    return {**validate_proposal_v2(input), "contractVersion": 2}


def canonical_op(action: str) -> str | None:
    """v1 action 或 v2 op 名 → v2 规范 op；未注册的名字返回 ``None``。

    ``add_tx`` / ``add_tx_batch`` 都映射到 ``transaction_add``（单条与批量的
    差别只在 ``payload.transactions`` 的条数），``query`` / ``report`` /
    ``config`` / ``chat`` 映射到 ``None``（它们在 v2 里不是提案）。映射表在
    ``contracts/action-proposal/v1-action-aliases.json``，**不在这里另抄一份**。

    05b 的提案组装器用它把一个 ``proposal_action`` 翻译成两条车道各自的动作名。
    """
    if action in V2_OPS:
        return action
    return _V1_ACTION_ALIASES.get(action)


#: 别名表的**反向索引**：v2 op → 它在 v1 车道上的动作名（可能多于一个：
#: ``transaction_add`` 同时对应 ``add_tx`` 与 ``add_tx_batch``）。没有 v1 形态的
#: op 不在表里——它们只能出现在 v2 车道。本索引与别名表同源，不另立一份映射。
_V1_ACTIONS_BY_OP: dict[str, tuple[str, ...]] = {}
for _v1_name, _op in _V1_ACTION_ALIASES.items():
    if _op is not None:
        _V1_ACTIONS_BY_OP[_op] = (*_V1_ACTIONS_BY_OP.get(_op, ()), _v1_name)


def v1_actions_for_op(op: str) -> tuple[str, ...]:
    """v2 op → 它的 v1 动作名（空元组＝v1 车道承载不了这个 op）。"""
    return _V1_ACTIONS_BY_OP.get(op, ())
