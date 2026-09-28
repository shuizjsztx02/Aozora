"""T20-3（REQ-78）：风险分级与确认清单的**守护用例**。

验收映射（``docs/人工测试和需求变更-v2.md`` 第二十轮 T20-3）：

- 注册表逐条有 ``risk``、新增工具漏标即红        → ``TestRegistryDeclaresRisk``
- ``HIGH_RISK_OPS`` 从注册表派生（不两处各写一份）→ ``TestHighRiskSetIsDerived``
- 清单文档与注册表**逐条相等**（可自动化那半）    → ``TestPolicyDocumentMatchesRegistry``
- 判定结果与清单的「分级」逐条一致                → ``TestConfirmReasonFollowsThePolicy``

三条硬约束的理由同源（设计决策 #8）：R-PROD-47 记过「≤64 这条尺子有三层、必须
同时是 64」——同一个数写在三处，改一处忘一处**且不报错**。风险等级现在是同构的
情形（注册表 / 派生的 ``HIGH_RISK_OPS`` / 清单文档），故三层各钉一条机器断言。

**本文件不测行为**（「删一笔账现在会问」这类在 ``test_ai_agent_approval.py`` 的
``test_medium_risk_write_asks_even_in_one_step``，纯函数的优先级在
``test_ai_tools_05d.py``）；这里只测**三份事实源彼此相等**。
"""

from __future__ import annotations

import re
from pathlib import Path

from src.modules.ai import tools as T
from src.modules.ai.agent import confirm_reason
from src.modules.ai.devices import DEVICE_TOOLS, DEVICE_TOOLS_BY_NAME

_REPO_ROOT = Path(__file__).resolve().parents[2]
_POLICY_DOC = _REPO_ROOT / "docs" / "product" / "ai-confirmation-policy.md"

#: 清单文档的表格行：``| `op` | 域 | `分级` | 处置 | 理由 |``。
#: 只认**第一列是反引号包起来的 op 名**的行，因此表头与说明行不会被误收。
_ROW = re.compile(
    r"^\|\s*`(?P<op>[a-z_]+)`\s*"
    r"\|\s*(?P<domain>[a-z]+)\s*"
    r"\|\s*`(?P<risk>low|medium|high)`\s*"
    r"\|\s*(?P<disposition>[^|]+?)\s*\|"
)

#: 处置列的词表（与文档「逐条清单」一节的说明一致）。
_DIRECT = "直接执行"
_ASK = "需确认"
_FORCE = "强制确认"
_ON_DEVICE = "确认后设备执行"
_DEVICE_INSTRUCTION = "设备指令"

_DISPOSITION_BY_RISK = {"low": _DIRECT, "medium": _ASK, "high": _FORCE}


def _expected_disposition(name: str, kind: str, risk: str) -> str:
    """一个 op **应当**落在清单的哪一档处置上（从注册表重新推一遍，独立于文档）。"""
    if name in DEVICE_TOOLS_BY_NAME:
        # 设备能力走设备通道，不产提案、不进服务端审批策略。
        return _DEVICE_INSTRUCTION
    if kind == "read":
        return _DIRECT
    if name in T.DEVICE_EXECUTED_OPS:
        # 服务端只挂起不执行，确认后由设备经桥命令执行；**无论分级都出卡**。
        return _ON_DEVICE
    return _DISPOSITION_BY_RISK[risk]


def _doc_rows() -> list[dict[str, str]]:
    text = _POLICY_DOC.read_text(encoding="utf-8")
    return [m.groupdict() for m in (_ROW.match(line) for line in text.splitlines()) if m]


# ── 注册表逐条有 risk ──────────────────────────────────────────────────────


class TestRegistryDeclaresRisk:
    def test_every_tool_declares_a_valid_risk(self):
        """新增工具漏标即红。**字段无默认值**，漏标在导入期就 TypeError；此处再遍历
        一遍，把「有人给它加了个默认值」这条路也堵上。
        """
        for tool in T.TOOLS:
            assert tool.risk in ("low", "medium", "high"), tool.name

    def test_read_tools_are_low(self):
        """读工具不产提案、不过审批门——它们恒为 ``low``。

        「不为读工具分级」是**它们的处置恒为直接执行**，不是漏标：真漏了这一步，
        一份「读工具忘标就默认成了 medium」的改动会让只读指令莫名出卡。
        """
        for tool in T.TOOLS:
            if tool.kind == "read":
                assert tool.risk == "low", tool.name

    def test_every_device_capability_declares_a_valid_risk(self):
        for tool in DEVICE_TOOLS:
            assert tool.risk in ("low", "medium", "high"), tool.name

    def test_the_risk_index_covers_exactly_the_write_ops(self):
        """``TOOL_RISK_BY_OP`` 与注册表的 ``proposal_action`` 一一对应。

        判定量是 op 而不是工具名（``_handle_proposal`` 传的是
        ``canonical_op(tool.proposal_action)``），所以索引漏一个 op 就等于那个 op
        永远查不到风险、永远自动执行——这是本卡最该防的静默形态。
        """
        declared = {t.proposal_action for t in T.TOOLS if t.proposal_action is not None}
        assert set(T.TOOL_RISK_BY_OP) == declared

    def test_tools_sharing_an_op_share_its_risk(self):
        """``transaction_add`` / ``transaction_add_batch`` 共用一个 op。"""
        shared = {}
        for tool in T.TOOLS:
            if tool.proposal_action is None:
                continue
            shared.setdefault(tool.proposal_action, set()).add(tool.risk)
        assert [op for op, risks in shared.items() if len(risks) > 1] == []


# ── HIGH_RISK_OPS 从注册表派生 ─────────────────────────────────────────────


class TestHighRiskSetIsDerived:
    def test_high_risk_ops_are_the_high_risk_ops(self):
        assert T.HIGH_RISK_OPS == frozenset(
            op for op, risk in T.TOOL_RISK_BY_OP.items() if risk == "high"
        )

    def test_high_risk_ops_are_also_the_high_risk_tool_names(self):
        """按 op 派生的集合，与按**工具名**派的集合必须相等。

        两条口径今天是同一个集合（两个高风险工具名与 op 同名），但名与 op 一旦
        分叉，「按名查」的老写法会静默漏掉那个工具——这条断言就是钉子。
        """
        assert T.HIGH_RISK_OPS == frozenset(
            tool.name for tool in T.TOOLS if tool.risk == "high"
        )


# ── 清单文档与注册表逐条相等 ───────────────────────────────────────────────


class TestPolicyDocumentMatchesRegistry:
    def test_the_document_exists(self):
        assert _POLICY_DOC.is_file(), f"清单文档不见了：{_POLICY_DOC}"

    def test_the_op_set_equals_the_registry(self):
        """**清单的 op 集合 == 工具注册表 ∪ 设备能力目录**（61 条，含读工具）。"""
        expected = {t.name for t in T.TOOLS} | {t.name for t in DEVICE_TOOLS}
        rows = _doc_rows()
        assert {r["op"] for r in rows} == expected

    def test_no_op_is_listed_twice(self):
        ops = [r["op"] for r in _doc_rows()]
        assert len(ops) == len(set(ops))

    def test_every_row_matches_the_registry(self):
        """每一行的「分级」与「处置」都与注册表推出来的一致。

        处置不是把分级抄一遍：设备 op 无论分级都是「确认后设备执行」，设备能力无论
        分级都是「设备指令」——这两类恰好是「分级不等于处置」的地方，也正是**最
        容易在文档里写错**的地方（分级看着是 medium，处置其实不由它决定）。
        """
        by_name = {t.name: t for t in T.TOOLS}
        for row in _doc_rows():
            name = row["op"]
            tool = by_name.get(name)
            if tool is None:
                tool = DEVICE_TOOLS_BY_NAME[name]
                kind = "device"
            else:
                kind = tool.kind
            assert row["risk"] == tool.risk, f"{name} 的分级与注册表不一致"
            assert row["disposition"] == _expected_disposition(name, kind, tool.risk), name


# ── 判定结果与清单一致 ─────────────────────────────────────────────────────


class TestConfirmReasonFollowsThePolicy:
    def test_every_op_gets_the_code_its_risk_promises(self):
        """单 Step + 高置信下逐 op 比对：``low`` → 自动执行，其余 → 对应理由码。

        这是「清单文档与真机行为抽样三类一致」里**可自动化**的那一半（真机那一半
        在收口卡 T20-7）。
        """
        expected = {"low": None, "medium": "medium_risk", "high": "high_risk"}
        for op, risk in T.TOOL_RISK_BY_OP.items():
            assert confirm_reason(step=1, confidence=1.0, op=op) == expected[risk], op

    def test_an_unregistered_op_falls_back_to_the_old_rules(self):
        """未注册的 op 不走风险两条，**fail-open 到步数 / 置信度**（旧口径）。

        ``account_delete`` 是 v2 注册表的成员但**没有工具产出它**（它的 payload 是
        一次性删除 token，凭据不进模型上下文）——伪造的提案在 payload 授权面就被
        挡下，所以这里不判它高风险不构成漏洞；把它判成高风险反而会掩盖
        「授权面 fail-closed」这件事的独立性。
        """
        assert confirm_reason(step=1, confidence=1.0, op="account_delete") is None
        assert confirm_reason(step=2, confidence=1.0, op="account_delete") == "multi_step"
