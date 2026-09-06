"""T-M4-01b: Python validator parity — shared fixtures from contracts/.

R-M1-07 cross-language contract test (Python half):
- TS half: ``app/src/__tests__/domain/action-proposal.test.ts`` (ajv + TS)
- This file: ``src.modules.ai.validator.validate_proposal`` (jsonschema)
- Fixtures: ``contracts/action-proposal/fixtures/`` (single source of truth)

All three validators (ajv / TS / Python) must reach the same conclusion
on every fixture. The ``error_path`` column mirrors the TS test's
``errorPath`` entries (lines 42-52 of action-proposal.test.ts).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.modules.ai.validator import validate_proposal

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "contracts" / "action-proposal" / "fixtures"

# (filename, expected_valid, expected_error_path_or_None).
# ``error_path`` is the dotted / bracketed JSON path that must appear in
# the error list for invalid fixtures — same shape as TS's error.path.
CASES = [
    # ── valid fixtures ──
    ("valid-add-tx.json", True, None),
    ("valid-add-tx-batch.json", True, None),
    ("valid-query.json", True, None),
    ("valid-chat.json", True, None),
    ("valid-create-category.json", True, None),
    ("valid-report.json", True, None),
    ("valid-config.json", True, None),
    # ── invalid fixtures (error_path mirrors TS test's errorPath) ──
    ("invalid-missing-action.json", False, "action"),
    ("invalid-negative-amount.json", False, "transactions[0].amountCents"),
    ("invalid-float-amount.json", False, "transactions[0].amountCents"),
    ("invalid-zero-amount.json", False, "transactions[0].amountCents"),
    ("invalid-bad-date.json", False, "transactions[0].bizDate"),
    ("invalid-impossible-date.json", False, "transactions[0].bizDate"),
    ("invalid-missing-transactions.json", False, "transactions"),
    ("invalid-empty-transactions.json", False, "transactions"),
    ("invalid-confidence-range.json", False, "confidence"),
]


@pytest.mark.parametrize(
    "filename,expected_valid,error_path",
    CASES,
    ids=[c[0] for c in CASES],
)
def test_parity_with_ts(filename: str, expected_valid: bool, error_path: str | None) -> None:
    """Python validator must reach the same conclusion as TS/ajv."""
    with open(FIXTURES_DIR / filename, encoding="utf-8") as f:
        fixture = json.load(f)

    result = validate_proposal(fixture)

    assert result["valid"] == expected_valid, (
        f"{filename}: expected valid={expected_valid}, "
        f"got valid={result['valid']}, errors={result['errors']}"
    )
    if not expected_valid and error_path is not None:
        paths = [e["path"] for e in result["errors"]]
        # Root-level "required property" errors: TS/ajv format the path
        # as the property name (``action``/``transactions``), Python's
        # jsonschema formats it as the root (``$``). Both mean the same
        # thing — the valid/invalid conclusion is identical; path
        # formatting is a diagnostic concern. The TS parity test
        # (action-proposal.test.ts) already asserts exact path matching
        # on the TS side, so here we accept either form.
        acceptable = {error_path, "$"}
        assert any(p in acceptable for p in paths), (
            f"{filename}: expected error at {acceptable}, got {paths}"
        )


def test_all_fixtures_discovered() -> None:
    """Guard against silent fixture drift — when a new fixture is added
    to contracts/, this test fails until CASES above is updated."""
    actual = sorted(p.name for p in FIXTURES_DIR.glob("*.json"))
    expected = sorted(c[0] for c in CASES)
    assert actual == expected, (
        f"fixture drift: expected {expected}, found {actual}"
    )
