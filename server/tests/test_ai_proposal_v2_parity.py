"""T-M7-05a: ActionProposal v2 契约 — 三方一致性与注册表一致性。

三处同结论：``contracts/action-proposal/schema-v2.json``（ajv，TS 侧跑）↔
TS ``validateProposalV2``（``app/src/__tests__/domain/action-proposal-v2.test.ts``）
↔ 本文件跑的 Python ``validate_proposal_v2``。共享 fixture 是唯一事实源：
``contracts/action-proposal/fixtures/v2/``。

本文件另外守住三条 v1 ↔ v2 的边界（05a 的验收条目）：
1. **v1 车道一字未动** —— v1 的 16 个 fixture 由 ``test_validator_parity.py`` /
   ``test_ai_proposal_parity.py`` 继续覆盖，这里只钉住「v1 提案不会被误判成 v2」。
2. **两套动作名不重叠** —— v1 action 枚举与 v2 op 枚举交集为空，别名表覆盖 v1 全集。
3. **v2 枚举与 tools.py 的 proposal_action 无冲突** —— 每个写工具的
   ``proposal_action`` 都能经 ``canonical_op`` 落到一个 v2 op 上。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.modules.ai import tools as T
from src.modules.ai.validator import (
    V2_OPS,
    canonical_op,
    validate_proposal,
    validate_proposal_any,
    validate_proposal_v2,
)

_CONTRACT_DIR = Path(__file__).resolve().parents[2] / "contracts" / "action-proposal"
FIXTURES_DIR = _CONTRACT_DIR / "fixtures" / "v2"

with open(_CONTRACT_DIR / "schema.json", encoding="utf-8") as _f:
    V1_ACTIONS: set[str] = set(json.load(_f)["properties"]["action"]["enum"])

with open(_CONTRACT_DIR / "v1-action-aliases.json", encoding="utf-8") as _f:
    V1_ALIASES: dict[str, str | None] = json.load(_f)["aliases"]

# (filename, expected_valid, expected_error_path_or_None)。
# ``error_path`` 与 TS 测试的 errorPath 同形；required / additionalProperties
# 这类由 Python jsonschema 报在对象本身的错误，路径是 ``$``（同 v1 的口径）。
CASES = [
    # ── valid fixtures ──
    ("valid-transaction-add.json", True, None),
    ("valid-transaction-add-batch.json", True, None),
    ("valid-budget-set-category.json", True, None),
    ("valid-meta-set.json", True, None),
    ("valid-savings-create.json", True, None),
    ("valid-sync-trigger.json", True, None),
    ("valid-account-delete.json", True, None),
    # ── invalid fixtures ──
    ("invalid-missing-contract-version.json", False, "contractVersion"),
    ("invalid-contract-version-1.json", False, "contractVersion"),
    ("invalid-unknown-op.json", False, "action"),
    ("invalid-v1-action-in-v2.json", False, "action"),
    ("invalid-missing-payload.json", False, "payload"),
    ("invalid-payload-not-object.json", False, "payload"),
    ("invalid-unknown-field.json", False, "transactions"),
    ("invalid-confidence-range.json", False, "confidence"),
    ("invalid-empty-proposal-id.json", False, "proposalId"),
    ("invalid-missing-warnings.json", False, "warnings"),
]


@pytest.mark.parametrize("filename,expected_valid,error_path", CASES, ids=[c[0] for c in CASES])
def test_parity_with_ts(filename: str, expected_valid: bool, error_path: str | None) -> None:
    with open(FIXTURES_DIR / filename, encoding="utf-8") as f:
        fixture = json.load(f)

    result = validate_proposal_v2(fixture)

    assert result["valid"] == expected_valid, (
        f"{filename}: expected valid={expected_valid}, "
        f"got valid={result['valid']}, errors={result['errors']}"
    )
    if not expected_valid and error_path is not None:
        paths = [e["path"] for e in result["errors"]]
        acceptable = {error_path, "$"}
        assert any(p in acceptable for p in paths), (
            f"{filename}: expected error at {acceptable}, got {paths}"
        )


def test_all_fixtures_discovered() -> None:
    """fixture 漂移守卫——新增 fixture 必须先登记进 CASES。"""
    actual = sorted(p.name for p in FIXTURES_DIR.glob("*.json"))
    expected = sorted(c[0] for c in CASES)
    assert actual == expected, f"fixture drift: expected {expected}, found {actual}"


def test_empty_payload_is_valid() -> None:
    """无参数 op 用 ``{}`` 表达，payload 一律必填、不设「可省略」特例。"""
    with open(FIXTURES_DIR / "valid-sync-trigger.json", encoding="utf-8") as f:
        fixture = json.load(f)
    assert fixture["payload"] == {}
    assert validate_proposal_v2(fixture)["valid"] is True


# ---------------------------------------------------------------------------
# 双读：车道判定
# ---------------------------------------------------------------------------


class TestDualRead:
    """``validate_proposal_any`` 按 contractVersion 在场与否选车道。"""

    def test_v1_fixture_takes_the_v1_lane(self) -> None:
        with open(_CONTRACT_DIR / "fixtures" / "valid-add-tx.json", encoding="utf-8") as f:
            v1_fixture = json.load(f)
        result = validate_proposal_any(v1_fixture)
        assert result["contractVersion"] == 1
        assert result["valid"] is True
        # 与直接走 v1 校验器逐字一致
        assert result == {**validate_proposal(v1_fixture), "contractVersion": 1}

    def test_v2_fixture_takes_the_v2_lane(self) -> None:
        with open(FIXTURES_DIR / "valid-meta-set.json", encoding="utf-8") as f:
            v2_fixture = json.load(f)
        result = validate_proposal_any(v2_fixture)
        assert result["contractVersion"] == 2
        assert result["valid"] is True

    def test_v1_proposal_carrying_a_v1_action_is_not_read_as_v2(self) -> None:
        """v1 车道不接受 contractVersion 字段——v1 提案不可能被误判成 v2。"""
        with open(_CONTRACT_DIR / "fixtures" / "valid-add-tx.json", encoding="utf-8") as f:
            v1_with_field = {**json.load(f), "contractVersion": 2}
        assert validate_proposal(v1_with_field)["valid"] is False
        assert validate_proposal_any(v1_with_field)["contractVersion"] == 2
        assert validate_proposal_any(v1_with_field)["valid"] is False

    def test_non_object_has_no_lane(self) -> None:
        for bad in (None, 42, "hello", [1, 2, 3]):
            result = validate_proposal_any(bad)
            assert result["valid"] is False
            assert result["contractVersion"] is None

    def test_explicit_version_1_is_rejected_in_the_v2_lane(self) -> None:
        """写了 contractVersion 就是 v2 车道；值不等于 2 即无效。"""
        with open(FIXTURES_DIR / "invalid-contract-version-1.json", encoding="utf-8") as f:
            fixture = json.load(f)
        assert validate_proposal_any(fixture)["contractVersion"] == 2
        assert validate_proposal_any(fixture)["valid"] is False


# ---------------------------------------------------------------------------
# 注册表一致性（05a 验收：v2 枚举与 proposal_action 无冲突）
# ---------------------------------------------------------------------------


class TestOpRegistry:
    def test_v1_and_v2_action_names_do_not_overlap(self) -> None:
        """两套名字不重叠——重叠就意味着同一串字符在两条车道上含义不同。"""
        assert V2_OPS & V1_ACTIONS == set(), sorted(V2_OPS & V1_ACTIONS)

    def test_alias_table_covers_the_whole_v1_action_enum(self) -> None:
        """v1 全集逐个表态：要么有规范 op，要么显式写 null。"""
        assert set(V1_ALIASES) == V1_ACTIONS, sorted(V1_ACTIONS ^ set(V1_ALIASES))

    def test_alias_targets_are_registered_ops(self) -> None:
        for alias, target in V1_ALIASES.items():
            if target is not None:
                assert target in V2_OPS, f"{alias} → {target} 不在 v2 op 注册表内"

    def test_alias_table_matches_the_schema_enum(self) -> None:
        """validator 里的表就是磁盘上的表（防「改了 JSON 忘了重新加载」）。"""
        assert canonical_op("add_tx") == "transaction_add"
        assert canonical_op("add_tx_batch") == "transaction_add"
        assert canonical_op("create_category") == "category_create"
        for no_op in ("query", "report", "config", "chat"):
            assert canonical_op(no_op) is None
        assert canonical_op("budget_frobnicate") is None

    def test_canonical_op_is_idempotent_on_v2_ops(self) -> None:
        for op in V2_OPS:
            assert canonical_op(op) == op

    def test_write_tools_resolve_to_a_registered_op(self) -> None:
        """**05a 的核心约束**：每个写工具的 proposal_action 都能落到一个 v2 op。

        P1 的工具写的是 v1 动作名（``add_tx`` / ``add_tx_batch``）——它们是别名，
        不是 v2 成员。05b–05d 新增的写工具直接写规范 op 名，两种都通过。
        """
        write_tools = [t for t in T.TOOLS if t.kind == "write"]
        assert write_tools, "写工具集为空，这条约束就成了空转"
        for tool in write_tools:
            assert tool.proposal_action is not None, tool.name
            canonical = canonical_op(tool.proposal_action)
            assert canonical in V2_OPS, (
                f"{tool.name} 的 proposal_action='{tool.proposal_action}' "
                f"既不是 v2 op 也不是有 v2 对应的 v1 别名"
            )
