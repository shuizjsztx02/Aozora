"""T-M4-02d: Python validator parity with shared contract fixtures.

Loads all 16 fixtures from ``contracts/action-proposal/fixtures/`` and
asserts the Python ``validate_proposal`` produces the same valid/invalid
verdict as the TS and ajv validators (TS↔ajv parity already covered in
``app/src/__tests__/domain/action-proposal.test.ts``).

Fixture naming convention:
- ``valid-*.json``   → must return ``{"valid": True, "errors": []}``
- ``invalid-*.json`` → must return ``{"valid": False, "errors": [...]}``
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.modules.ai.validator import validate_proposal

# server/tests → repo root = 2 parents up
_FIXTURES_DIR = Path(__file__).resolve().parents[2] / "contracts" / "action-proposal" / "fixtures"


def _load_fixtures(prefix: str) -> list[tuple[str, dict]]:
    """Load all fixtures matching the given filename prefix."""
    cases: list[tuple[str, dict]] = []
    for path in sorted(_FIXTURES_DIR.glob(f"{prefix}*.json")):
        with open(path, "r", encoding="utf-8") as f:
            cases.append((path.stem, json.load(f)))
    return cases


_VALID_CASES = _load_fixtures("valid-")
_INVALID_CASES = _load_fixtures("invalid-")


class TestValidatorParity:
    """Python validator must agree with the shared contract fixtures."""

    @pytest.mark.parametrize(
        "name,payload", _VALID_CASES, ids=[c[0] for c in _VALID_CASES],
    )
    def test_valid_fixtures_pass(self, name, payload):
        result = validate_proposal(payload)
        assert result["valid"] is True, (
            f"{name} should be valid, got errors: {result['errors']}"
        )
        assert result["errors"] == []

    @pytest.mark.parametrize(
        "name,payload", _INVALID_CASES, ids=[c[0] for c in _INVALID_CASES],
    )
    def test_invalid_fixtures_rejected(self, name, payload):
        result = validate_proposal(payload)
        assert result["valid"] is False, f"{name} should be invalid"
        assert len(result["errors"]) > 0, (
            f"{name} should produce at least one error"
        )
        # Each error must carry path + message (mirrors TS ValidationResult)
        for err in result["errors"]:
            assert "path" in err
            assert "message" in err

    def test_fixture_count_matches_contract(self):
        """Guard: fixture set must stay at 16 (7 valid + 9 invalid)."""
        assert len(_VALID_CASES) == 7, (
            f"Expected 7 valid fixtures, got {len(_VALID_CASES)}"
        )
        assert len(_INVALID_CASES) == 9, (
            f"Expected 9 invalid fixtures, got {len(_INVALID_CASES)}"
        )
