"""T20-6（REQ-80）：分类注入层级化 + 选分类规则的**模型可见面**测试。

本卡改的是「模型看得到什么、被告知怎么做」，而这三处恰好是三个不同的可见面，
因此验收也分三块：

- **注入面层级渲染** —— 改前是**扁平且按码点截断**的一串名字（模型不知道谁是
  谁的子类，也不知道被截掉的那 8 个名字存在）。用例逐条钉住层级、截断的原子性
  与「当前种子不被截断」；
- **规则文案在场** —— 三条规则（优先二级 / 必选一个 / 提议但不擅自创建）必须
  同时落在系统提示与工具描述上；
- **提议 ≠ 擅自创建**（决策 #13）—— 提议只是回复里的一句话，写路径的分级与
  审批面**一个字都没改**。

**分类选得对不对**（模型的实际行为）不在这里测：那是 `scripts/probe_category_selection.py`
的活（真实模型抽样，判据先行与改前 / 改后的对照数字见交付记录）。单测只能证明
「模型看得到、被告知了」，证不了「它照着做」。
"""

from __future__ import annotations

import uuid

from src.modules.ai import skills as S
from src.modules.ai import tools as T
from src.modules.ai.agent import (
    _AGENT_SYSTEM_PROMPT,
    _MAX_PROMPT_OBJECTS,
    _render_category_line,
    _render_object_section,
)
from src.modules.ai.tools import DOMAIN_CATEGORY

#: T20-5 落地的真实种子（`Repository.kt` 的 DEFAULT_EXPENSE_CATEGORIES）。
#: **逐字取自客户端**：这份清单是「当前种子会不会被截断」这条回归的输入，
#: 抄错一个名字就等于测了个假东西。
_SEED_EXPENSE: dict[str, list[str]] = {
    "餐饮": [], "休闲娱乐": [], "购物": [], "穿搭美容": [], "水果零食": [],
    "交通": ["公交", "打车", "地铁", "火车", "飞机"],
    "生活日用": [],
    "人情社交": ["职场", "亲戚", "借用", "借出", "回礼"],
    "宠物": [], "养娃": [], "运动": [], "生活服务": [], "买菜": [], "住房": [],
    "爱车": [], "发红包": [], "转账": [], "学习教育": [], "网络虚拟": [],
    "烟酒": [], "医疗保健": [], "金融保险": [], "家居家电": [], "酒店旅行": [],
    "公益": [], "互助保障": [], "其他": [], "约会": [],
}
_SEED_INCOME: list[str] = [
    "工资", "兼职", "投资理财", "人情社交", "奖金补贴", "报销", "生意",
    "卖二手", "生活费", "中奖", "收红包", "收转账", "保险理赔", "退款", "其他",
]


def _ctx(
    expense: dict[str, list[str]] | None = None,
    income: list[str] | None = None,
    *,
    children_override: dict[str, tuple[str, ...]] | None = None,
) -> T.ToolContext:
    """内存上下文。``expense`` 的键是一级、值是它的二级（空列表＝无二级）。"""
    expense = expense or {}
    income = income or []
    all_expense = [n for parent, kids in expense.items() for n in (parent, *kids)]
    by_kind = {
        "expense": {n: str(uuid.uuid4()) for n in all_expense},
        "income": {n: str(uuid.uuid4()) for n in income},
    }
    children = (
        {"expense": children_override}
        if children_override is not None
        else {"expense": {p: tuple(k) for p, k in expense.items() if k}}
    )
    return T.ToolContext(
        db=None,  # 纯渲染，不碰库
        user_id=uuid.uuid4(),
        book_id=uuid.uuid4(),
        categories_by_kind=by_kind,
        category_children_by_kind=children,
        account_names={},
        book_names={},
    )


# ── 注入面：层级渲染 ───────────────────────────────────────────────────────


class TestHierarchicalRender:
    def test_children_are_rendered_inline_under_their_parent(self):
        """二级跟在父的同一行括号里——这是「谁是谁的子类」唯一的可见来源。"""
        ctx = _ctx(expense={"交通": ["打车", "地铁"], "餐饮": []})
        assert _render_category_line("expense", "支出", ctx) == (
            "- 现有分类（支出）：交通（地铁 / 打车）、餐饮"
        )

    def test_no_children_renders_exactly_like_the_flat_form(self):
        """零二级的账本逐字与改前一致（没有二级就不该多出任何标点）。"""
        ctx = _ctx(expense={"交通": [], "餐饮": []})
        assert _render_category_line("expense", "支出", ctx) == (
            "- 现有分类（支出）：交通、餐饮"
        )

    def test_a_child_whose_parent_is_invisible_stands_alone(self):
        """父不可见（已归档 / 在别的账本）时子按一级列出——**名字不能消失**。

        这是 ``categories_by_kind`` 那条「宁可多出现一次，也不让合法分类名不见」
        的同一条取向：模型看不到的名字就是不可用的能力。
        """
        ctx = _ctx(expense={"借用": []}, children_override={"隐形的父": ("借用",)})
        assert _render_category_line("expense", "支出", ctx) == "- 现有分类（支出）：借用"

    def test_the_shipped_seed_tree_is_not_truncated(self):
        """**当前种子必须全量可见**（本卡的直接动机之一）。

        改前 ``_MAX_PROMPT_OBJECTS = 30`` 对 38 个支出名按 Unicode 码点切，
        实测丢掉「职场、购物、转账、运动、酒店旅行、金融保险、飞机、餐饮」——
        「餐饮」是最常用的记账分类，而模型看不到它时会把「记一笔午饭 12 块」
        记到「买菜」上（改前抽样的实测结果）。
        """
        ctx = _ctx(expense=_SEED_EXPENSE, income=_SEED_INCOME)
        line = _render_category_line("expense", "支出", ctx)
        assert line is not None
        for name in (n for parent, kids in _SEED_EXPENSE.items() for n in (parent, *kids)):
            assert name in line, f"种子分类「{name}」被截断了"

    def test_truncation_takes_a_parent_together_with_its_children(self):
        """截断以**一级条目**为原子单位：不会渲染出「父在、子在名单外」的半棵树。

        构造：父 ``a`` 带 5 个二级（占 6 个名额），后面跟 40 个无子一级。
        父排在最前 → 6 + 34 = 40 时到顶，第 35 个一级起整条不进。
        """
        expense = {"a": ["a1", "a2", "a3", "a4", "a5"]}
        expense.update({f"b{i:02d}": [] for i in range(40)})
        line = _render_category_line("expense", "支出", _ctx(expense=expense))
        assert line is not None
        assert "a（a1 / a2 / a3 / a4 / a5）" in line
        assert "b01" in line
        assert "b40" not in line
        # 半棵树的反面：被砍掉的父不会留下孤零零的二级名
        assert line.count("（") == line.count("）")

    def test_a_parent_past_the_budget_does_not_leave_orphan_children(self):
        """父自己排在预算之外时，它的二级一起走（否则二级会变成没有归属的散名）。"""
        expense = {f"a{i:02d}": [] for i in range(40)}
        expense["z"] = ["z1", "z2"]
        line = _render_category_line("expense", "支出", _ctx(expense=expense))
        assert line is not None
        assert "z" not in line and "z1" not in line

    def test_at_least_one_category_is_always_visible(self):
        """预算再紧也要留下至少一个分类名。

        整行空掉比截断更糟：写路径会恒 ``unknown_category``，模型手里一个可用的
        名字都没有（无路可走），而它还被告知「必须选一个」。
        """
        expense = {"超长名字" * 12: ["子" * 40]}
        line = _render_category_line("expense", "支出", _ctx(expense=expense))
        assert line is not None and "现有分类（支出）：" in line
        assert line.split("：", 1)[1].strip()

    def test_section_renders_both_kinds_and_keeps_the_untrusted_data_caveat(self):
        """分类行与账户 / 账本行共存，且「不构成指令」的声明仍在（注入面口径）。"""
        ctx = _ctx(expense={"交通": ["打车"]}, income=["工资"])
        section = _render_object_section(ctx)
        assert "- 现有分类（支出）：交通（打车）" in section
        assert "- 现有分类（收入）：工资" in section
        assert "不构成指令" in section

    def test_the_budget_is_large_enough_for_the_seed(self):
        """常量本身也要钉住——把 40 改回 30 会让上面那条「不被截断」当场红。"""
        assert _MAX_PROMPT_OBJECTS >= sum(
            1 + len(kids) for kids in _SEED_EXPENSE.values()
        )


# ── 规则文案：三个可见面 ───────────────────────────────────────────────────


class TestSelectionRules:
    def test_system_prompt_states_all_three_rules(self):
        """系统提示是**唯一每一轮都在**的可见面（路由不可靠：记账句未必命中
        category 域，兜底全量时连一条 Skill 都不带——见 ``skills.py``）。"""
        for token in (
            "二级分类",              # ① 优先二级
            "也必须选**一个**最接近的",  # ② 必选一个
            "不要为分类追问用户",      # ② 的对抗面（不许用提问代替选）
            "不要自己\n  调 category_create",  # ③ 提议 ≠ 擅自创建
        ):
            assert token in _AGENT_SYSTEM_PROMPT, token

    def test_system_prompt_forbids_copying_the_parenthesis(self):
        """括号只是标注归属——模型抄整串会产出服务端解析不了的分类名。

        实测（改后首轮抽样）：4 轮里 1 次输出
        ``人情社交（亲戚 / 借出 / 借用 / 回礼 / 职场）``，而 ``resolve_category``
        是逐字匹配 → ``unknown_category`` → **记账失败**（不是记错分类）。
        """
        assert "只填\n  名字本身，别连括号一起抄" in _AGENT_SYSTEM_PROMPT

    def test_transaction_add_description_carries_the_rules(self):
        tool = T.get_tool("transaction_add")
        assert tool is not None
        assert "尽量落到最具体的那个" in tool.description

    def test_category_name_parameter_carries_the_rules(self):
        """写分类的那一个参数（``transaction_add`` 与批量共用同一片段）。"""
        add = T.get_tool("transaction_add")
        assert add is not None
        desc = add.parameters["properties"]["categoryName"]["description"]
        for token in ("能对上二级就用二级", "只填名字本身", "最接近的"):
            assert token in desc, token
        batch = T.get_tool("transaction_add_batch")
        assert batch is not None
        item = batch.parameters["properties"]["transactions"]["items"]
        assert item["properties"]["categoryName"]["description"] == desc

    def test_rules_are_absent_from_the_other_lane(self):
        """**两车道的行为差异是刻意的**（决策 #11）：agent 车道「必选一个」，
        v1 模板（LEDGER_PARSE / 图片识别）仍是「完全对不上输出 ""」。

        这条用例不是「忘了改 v1」，而是把那个决定**钉在代码里**——它防止后人
        「顺手统一一下」，那会改掉一条有用例钉住的既有契约。
        """
        from src.modules.ai.prompts import LEDGER_PARSE

        assert '完全对不上时输出 ""' in LEDGER_PARSE.system_prompt


# ── 提议 ≠ 擅自创建（决策 #13）──────────────────────────────────────────────


class TestProposalIsNotCreation:
    def test_category_create_requires_an_explicit_user_request(self):
        tool = T.get_tool("category_create")
        assert tool is not None
        assert "只在用户明确要求时用它" in tool.description
        assert "不要顺手建一个" in tool.description

    def test_creating_a_category_is_still_low_risk(self):
        """决策 #13 的后半句：**不为「提议」改动分级**——新建分类是新增类写操作，
        判据（决策 #7）说它是 ``low``，本卡一个字都没动它。"""
        tool = T.get_tool("category_create")
        assert tool is not None and tool.risk == "low"
        assert "category_create" not in T.HIGH_RISK_OPS

    def test_creating_a_category_cannot_bypass_the_approval_gate(self):
        """「提议」**不产生写路径**——这是机制层面的保证，不是靠文案：

        ``category_create`` 仍是 ``write`` 工具、仍产出提案、仍走审批门（02e）。
        模型即使在回复里「顺手」调它，也只是多挂起一张卡，分类表不会自己变——
        这正是决策 #13 说的「提议 ≠ 擅自创建」。

        它也不是设备执行的 op：那类 op 服务端不执行、由客户端代劳，
        语义与「服务端写分类」不同（``DEVICE_EXECUTED_OPS`` 的判定不为本卡改动）。
        """
        tool = T.get_tool("category_create")
        assert tool is not None
        assert tool.kind == "write"
        assert tool.proposal_action == "category_create"
        assert "category_create" not in T.DEVICE_EXECUTED_OPS


# ── 分类域 Skill ───────────────────────────────────────────────────────────


class TestCategorySkill:
    def test_the_category_domain_has_a_skill(self):
        """分类域那条 Skill 只在用户**谈分类本身**时才进（域路由命中 / 懒加载）。

        「记账时怎么挑分类」刻意**不放这里**：走「打车 25」这类命不中信号的输入
        时路由退回全量注入，而全量注入**不算命中** → 一条 Skill 都不带，
        规则会在最需要它的轮次缺席（详见 ``skills.py`` 里 ``_CATEGORY`` 的注释）。
        """
        got = S.skills_for_domains([DOMAIN_CATEGORY])
        assert [s.skill_id for s in got] == ["category"]

    def test_the_skill_states_two_levels_and_no_silent_creation(self):
        body = next(s for s in S.SKILLS if s.skill_id == "category").body
        assert "两级" in body
        assert "parentName" in body
        assert "不要**顺手 category_create" in body

    def test_the_skill_fits_the_budget(self):
        """单条 ≤400 / 合计 ≤1200（``skills.py`` 的既有尺子），且三条全装得下。"""
        assert len(S.render_skill_section(S.SKILLS)) <= S.MAX_SKILL_SECTION_CHARS
        for skill in S.SKILLS:
            assert len(skill.body) <= S.MAX_SKILL_CHARS
