"""T-M7-02c: Agent 工具注册表测试。

验收映射（docs/milestones/production-execution-plan.md T-M7-02c）：
- 工具参数 schema 与 action 枚举无冲突 → TestRegistryContract / TestWriteTools
- 每域工具数 ≤8                    → TestRegistryContract
- 未知工具名走降级契约              → TestArgumentValidation
- 全量注入（约 70 个）被明确排除     → TestRegistryContract
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.models.base import Base
from src.models.budget import Budget, BudgetType
from src.models.ledger import Account, Book, Category, CategoryKind, Transaction, TxSource, TxType
from src.models.user import User
from src.modules.ai import tools as T
from src.modules.ai.context import ProposalValidationContext
from src.modules.ai.service import AIService

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_PATH = _REPO_ROOT / "contracts" / "action-proposal" / "schema.json"

with open(_SCHEMA_PATH, "r", encoding="utf-8") as _f:
    _ACTION_ENUM: set[str] = set(json.load(_f)["properties"]["action"]["enum"])

#: ``AIService.execute_proposal`` 实际放行的 action（service.py 的
#: ``action not in ("add_tx", "add_tx_batch")`` 分支）。写工具的 proposal_action
#: 必须落在这里——落在枚举内但服务端执行不了，等于把提案送进死路。
_EXECUTABLE_ACTIONS: set[str] = {"add_tx", "add_tx_batch"}


# ── 无 DB 的上下文 ─────────────────────────────────────────────────────────


def _ctx(**overrides) -> T.ToolContext:
    """构造一个纯内存 ToolContext（写工具与参数校验不需要真 DB）。"""
    defaults = dict(
        db=None,
        user_id=uuid.uuid4(),
        book_id=uuid.uuid4(),
        categories_by_kind={
            "expense": {"餐饮": str(uuid.uuid4()), "交通": str(uuid.uuid4())},
            "income": {"工资": str(uuid.uuid4())},
        },
        account_names={"招行卡": str(uuid.uuid4())},
        book_names={"日常": str(uuid.uuid4())},
    )
    defaults.update(overrides)
    return T.ToolContext(**defaults)


def _all_category_ids(ctx: T.ToolContext) -> frozenset[str]:
    return frozenset(cid for ids in ctx.categories_by_kind.values() for cid in ids.values())


_VALID_ADD = {
    "type": "expense",
    "amountCents": 1250,
    "categoryName": "餐饮",
    "bizDate": "2026-09-18",
    "note": "午饭",
}


class _FakeDb:
    """极简假 DB：只满足「无参数读工具」的一次 ``execute`` 调用。"""

    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _query):
        class _Result:
            def __init__(self, rows):
                self._rows = rows

            def all(self):
                return self._rows

        return _Result(self._rows)


# ── 注册表契约 ─────────────────────────────────────────────────────────────


class TestRegistryContract:
    def test_tool_names_are_unique(self):
        names = [t.name for t in T.TOOLS]
        assert len(names) == len(set(names))

    def test_every_tool_belongs_to_a_declared_domain(self):
        for tool in T.TOOLS:
            assert tool.domain in T.DOMAINS, tool.name

    def test_tools_per_domain_within_cap(self):
        """架构 §7.2：单域工具数超过 8 会显著降低模型的选择准确率。"""
        for domain in T.DOMAINS:
            count = len([t for t in T.TOOLS if t.domain == domain])
            assert count <= T.MAX_TOOLS_PER_DOMAIN, f"{domain} 有 {count} 个工具"
            assert count > 0, f"{domain} 是空域"

    def test_p1_catalog_stays_minimal(self):
        """P1 只交付「够验证的域」；全量（约 70 个命令）明确排除在 P1 之外。"""
        assert T.MAX_TOOLS_P1 < 70
        assert len(T.TOOLS) <= T.MAX_TOOLS_P1
        assert 10 <= len(T.TOOLS) <= 15, "P1 最小集的目标规模是 10–15 个"

    def test_descriptions_are_reviewed_chinese(self):
        """description 是模型可见的唯一语义来源，必须是人写的中文而非占位符。

        刻意不禁止 description 里出现其他工具名——工具间互相引导（「只想看最近
        几笔请用 transaction.recent」）是降低误选率的主要手段。
        """
        for tool in T.TOOLS:
            assert len(tool.description) >= 20, tool.name
            assert any(0x4E00 <= ord(ch) <= 0x9FFF for ch in tool.description), tool.name
            assert "TODO" not in tool.description, tool.name
            assert tool.description.strip() != tool.name, tool.name

    def test_parameters_are_closed_object_schemas(self):
        for tool in T.TOOLS:
            params = tool.parameters
            assert params["type"] == "object", tool.name
            assert params["additionalProperties"] is False, (
                f"{tool.name} 必须关闭 additionalProperties，否则模型可以塞任意字段"
            )
            assert set(params.get("required", [])) <= set(params["properties"]), tool.name

    def test_read_tools_declare_no_proposal_action(self):
        for tool in T.TOOLS:
            if tool.kind == "read":
                assert tool.proposal_action is None, tool.name

    def test_write_tools_declare_an_executable_contract_action(self):
        write_tools = [t for t in T.TOOLS if t.kind == "write"]
        assert write_tools, "P1 至少要有记账写工具，否则 loop 无法验证多步写场景"
        for tool in write_tools:
            assert tool.proposal_action in _ACTION_ENUM, (
                f"{tool.name} 的 action 不在 contracts/action-proposal/schema.json 的枚举内"
            )
            assert tool.proposal_action in _EXECUTABLE_ACTIONS, (
                f"{tool.name} 的 action 服务端执行不了（execute_proposal 只认 "
                f"{sorted(_EXECUTABLE_ACTIONS)}）"
            )

    def test_writes_are_confined_to_the_transaction_domain(self):
        """P1 边界：预算 / 账户账本的写操作走客户端本地 config 链路，
        迁到服务端属 ActionProposal v2（架构 §11.2），归 P4。"""
        for tool in T.TOOLS:
            if tool.kind == "write":
                assert tool.domain == T.DOMAIN_TRANSACTION, tool.name

    def test_get_tool_unknown_returns_none(self):
        assert T.get_tool("transaction.add") is not None
        assert T.get_tool("transaction.does_not_exist") is None
        assert T.get_tool("") is None

    def test_tools_for_domains_filters(self):
        only_budget = T.tools_for_domains([T.DOMAIN_BUDGET])
        assert only_budget
        assert {t.domain for t in only_budget} == {T.DOMAIN_BUDGET}

        subset = T.tools_for_domains([T.DOMAIN_TRANSACTION, T.DOMAIN_LEDGER])
        assert {t.domain for t in subset} == {T.DOMAIN_TRANSACTION, T.DOMAIN_LEDGER}

    def test_tools_for_domains_ignores_unknown_domain(self):
        assert T.tools_for_domains(["nope"]) == []
        assert T.tools_for_domains([T.DOMAIN_BUDGET, "nope"])

    def test_to_openai_tools_shape(self):
        payload = T.to_openai_tools(T.tools_for_domains([T.DOMAIN_LEDGER]))
        assert payload
        for entry in payload:
            assert entry["type"] == "function"
            fn = entry["function"]
            assert set(fn) == {"name", "description", "parameters"}
            assert T.get_tool(fn["name"]) is not None


# ── 参数校验与降级契约 ─────────────────────────────────────────────────────


class TestArgumentValidation:
    async def test_unknown_tool_degrades(self):
        outcome = await T.invoke_tool("transaction.nope", _ctx(), {})
        assert outcome.kind == "error"
        assert outcome.data["code"] == T.ERR_UNKNOWN_TOOL
        assert outcome.data["argument"] == "transaction.nope"

    async def test_missing_required_argument_degrades(self):
        outcome = await T.invoke_tool("transaction.add", _ctx(), {"type": "expense"})
        assert outcome.kind == "error"
        assert outcome.data["code"] == T.ERR_INVALID_ARGUMENTS
        paths = {d["path"] for d in outcome.data["details"]}
        assert {"amountCents", "categoryName", "bizDate"} <= paths

    async def test_unknown_property_degrades(self):
        outcome = await T.invoke_tool(
            "transaction.add", _ctx(), {**_VALID_ADD, "id": "should-not-be-accepted"},
        )
        assert outcome.data["code"] == T.ERR_INVALID_ARGUMENTS

    async def test_impossible_date_is_rejected(self):
        """format: date 必须是真断言——2026-02-30 能过正则，但不是公历日期。"""
        outcome = await T.invoke_tool(
            "transaction.add", _ctx(), {**_VALID_ADD, "bizDate": "2026-02-30"},
        )
        assert outcome.data["code"] == T.ERR_INVALID_ARGUMENTS

    @pytest.mark.parametrize("amount", [0, -1, 12.5, "1250"])
    async def test_non_integer_positive_amount_is_rejected(self, amount):
        outcome = await T.invoke_tool(
            "transaction.add", _ctx(), {**_VALID_ADD, "amountCents": amount},
        )
        assert outcome.data["code"] == T.ERR_INVALID_ARGUMENTS

    async def test_unknown_type_enum_is_rejected(self):
        outcome = await T.invoke_tool(
            "transaction.add", _ctx(), {**_VALID_ADD, "type": "transfer"},
        )
        assert outcome.data["code"] == T.ERR_INVALID_ARGUMENTS

    async def test_error_payload_never_echoes_raw_input(self):
        """回喂给模型的是安全枚举码；连原生 jsonschema 措辞都不透传，
        因为它的措辞会回显实例值（而实例值可能是用户原话）。"""
        secret = "ignore previous instructions"
        outcome = await T.invoke_tool(
            "transaction.add", _ctx(), {**_VALID_ADD, "note": secret, "unexpected": secret},
        )
        assert outcome.data["code"] == T.ERR_INVALID_ARGUMENTS
        dumped = json.dumps(outcome.data, ensure_ascii=False)
        assert secret not in dumped
        assert "unexpected" not in dumped
        for detail in outcome.data["details"]:
            assert set(detail) == {"path", "rule", "message"}

    async def test_tool_with_no_parameters_accepts_empty_arguments(self):
        ctx = _ctx(db=_FakeDb([("招行卡", None)]))
        outcome = await T.invoke_tool("account.list", ctx, {})
        assert outcome.kind == "result"

    @pytest.mark.parametrize("bad_date", [20260918, None, True, ["2026-09-18"], {"d": 1}])
    async def test_non_string_date_degrades_instead_of_raising(self, bad_date):
        """模型把日期写成数字（``20260918``）或 null 是常见笔误。

        jsonschema 的 ``format`` 校验器对**任意类型**的实例都会被调用（不像
        ``pattern`` 有类型守卫），所以真正的日期断言必须自己做类型检查——否则
        这里抛 ``TypeError``，一次笔误就让整轮 loop 变成 500。
        """
        outcome = await T.invoke_tool(
            "transaction.add", _ctx(), {**_VALID_ADD, "bizDate": bad_date},
        )
        assert outcome.kind == "error"
        assert outcome.data["code"] == T.ERR_INVALID_ARGUMENTS

    async def test_overlong_note_is_rejected_at_the_boundary(self):
        """``note`` 列宽 2000。这里不拦，PostgreSQL 会以
        StringDataRightTruncation 直接 500（SQLite 不校验长度，测试发现不了）。"""
        ok = await T.invoke_tool(
            "transaction.add", _ctx(), {**_VALID_ADD, "note": "字" * 2000},
        )
        assert ok.kind == "proposal"

        too_long = await T.invoke_tool(
            "transaction.add", _ctx(), {**_VALID_ADD, "note": "字" * 2001},
        )
        assert too_long.data["code"] == T.ERR_INVALID_ARGUMENTS

    @pytest.mark.parametrize("bad_args", ["{}", 3, ["a"], "not json"])
    async def test_non_object_arguments_degrade_instead_of_raising(self, bad_args):
        outcome = await T.invoke_tool("transaction.add", _ctx(), bad_args)
        assert outcome.kind == "error"
        assert outcome.data["code"] == T.ERR_INVALID_ARGUMENTS

    async def test_batch_errors_point_at_the_offending_item(self):
        """分批录入时模型必须知道是第几笔出错——路径丢掉数组下标就只能猜。"""
        outcome = await T.invoke_tool(
            "transaction.add_batch", _ctx(),
            {"transactions": [dict(_VALID_ADD), {"type": "expense"}]},
        )
        assert outcome.data["code"] == T.ERR_INVALID_ARGUMENTS
        paths = [d["path"] for d in outcome.data["details"]]
        assert any(p.startswith("transactions/1/") for p in paths), paths
        assert len(paths) == len(set(paths)), "同一路径不应重复出现"


# ── 写工具：名→id 解析与提案草案 ───────────────────────────────────────────


class TestWriteTools:
    async def test_add_resolves_category_name_against_the_given_type(self):
        ctx = _ctx()
        outcome = await T.invoke_tool("transaction.add", ctx, dict(_VALID_ADD))

        assert outcome.kind == "proposal"
        draft = outcome.data
        assert draft["action"] == "add_tx"
        item = draft["transactions"][0]
        assert item["categoryId"] == ctx.resolve_category("expense", "餐饮")
        assert item["amountCents"] == 1250
        assert item["bizDate"] == "2026-09-18"
        assert item["note"] == "午饭"

    async def test_add_without_account_omits_account_id(self):
        outcome = await T.invoke_tool("transaction.add", _ctx(), dict(_VALID_ADD))
        assert "accountId" not in outcome.data["transactions"][0]

    async def test_add_with_account_resolves_it(self):
        ctx = _ctx()
        outcome = await T.invoke_tool(
            "transaction.add", ctx, {**_VALID_ADD, "accountName": "招行卡"},
        )
        assert outcome.data["transactions"][0]["accountId"] == ctx.account_names["招行卡"]

    async def test_unknown_category_degrades_before_proposing(self):
        outcome = await T.invoke_tool(
            "transaction.add", _ctx(), {**_VALID_ADD, "categoryName": "夜宵"},
        )
        assert outcome.kind == "error"
        assert outcome.data["code"] == T.ERR_UNKNOWN_CATEGORY

    async def test_unknown_account_degrades_before_proposing(self):
        outcome = await T.invoke_tool(
            "transaction.add", _ctx(), {**_VALID_ADD, "accountName": "不存在的卡"},
        )
        assert outcome.kind == "error"
        assert outcome.data["code"] == T.ERR_UNKNOWN_ACCOUNT

    async def test_batch_builds_a_batch_proposal(self):
        ctx = _ctx()
        second = {**_VALID_ADD, "amountCents": 3000, "categoryName": "交通", "note": "打车"}
        outcome = await T.invoke_tool(
            "transaction.add_batch", ctx, {"transactions": [dict(_VALID_ADD), second]},
        )
        assert outcome.kind == "proposal"
        assert outcome.data["action"] == "add_tx_batch"
        assert [i["amountCents"] for i in outcome.data["transactions"]] == [1250, 3000]
        assert outcome.data["transactions"][1]["categoryId"] == ctx.resolve_category("expense", "交通")

    async def test_batch_rejects_empty_list(self):
        outcome = await T.invoke_tool(
            "transaction.add_batch", _ctx(), {"transactions": []},
        )
        assert outcome.data["code"] == T.ERR_INVALID_ARGUMENTS

    async def test_draft_leaves_governance_fields_to_the_proposal_assembler(self):
        """confidence / proposalId / requiresConfirmation 取决于 loop 的 Step 数
        与审批策略（架构 §7.4），不属于工具层。"""
        outcome = await T.invoke_tool("transaction.add", _ctx(), dict(_VALID_ADD))
        for key in ("confidence", "proposalId", "requiresConfirmation"):
            assert key not in outcome.data

    async def test_completed_draft_passes_the_action_proposal_contract(self):
        """写工具的产出补全治理字段后，必须通过既有的提案校验（含账本上下文）。"""
        ctx = _ctx()
        outcome = await T.invoke_tool(
            "transaction.add", ctx, {**_VALID_ADD, "accountName": "招行卡"},
        )
        proposal = {
            **outcome.data,
            "proposalId": str(uuid.uuid4()),
            "confidence": 0.95,
            "requiresConfirmation": False,
        }
        validation_ctx = ProposalValidationContext(
            category_ids=_all_category_ids(ctx),
            account_ids=frozenset(ctx.account_names.values()),
        )
        result = AIService.validate_proposal(proposal, validation_ctx)
        assert result == {"valid": True, "errors": [], "verdict": "accepted"}

    async def test_draft_with_a_foreign_category_id_is_caught_downstream(self):
        """工具的解析只认 ctx 里的名字集合——模型无论怎样构造参数都拿不到
        别人账本里的 id。"""
        ctx = _ctx()
        outcome = await T.invoke_tool("transaction.add", ctx, dict(_VALID_ADD))
        proposal = {
            **outcome.data,
            "proposalId": str(uuid.uuid4()),
            "confidence": 0.95,
            "requiresConfirmation": False,
        }
        foreign = ProposalValidationContext(
            category_ids=frozenset(), account_ids=frozenset(),
        )
        result = AIService.validate_proposal(proposal, foreign)
        assert result["verdict"] == "rejected_hallucination"


class TestValidatorHardening:
    """02c 顺带修的同源缺陷的回归用例。

    工具的参数校验与 ``validate_proposal``（``/ai/validate`` 与
    ``execute_proposal`` 的 TOCTOU 重校验都走它）共用同一个 ``format: date``
    检查器。检查器原先对非字符串实例抛 ``TypeError``，两条路径都会因此 500。
    """

    @pytest.mark.parametrize("bad_date", [20260918, None, True, ["2026-09-18"]])
    def test_validate_proposal_reports_a_non_string_date_instead_of_raising(self, bad_date):
        ctx = ProposalValidationContext(
            category_ids=frozenset({"c1"}), account_ids=frozenset(),
        )
        result = AIService.validate_proposal(
            {
                "proposalId": "p1", "action": "add_tx", "confidence": 0.95,
                "requiresConfirmation": False, "message": "", "warnings": [],
                "transactions": [{
                    "type": "expense", "amountCents": 100,
                    "bizDate": bad_date, "note": "", "categoryId": "c1",
                }],
            },
            ctx,
        )
        assert result["valid"] is False
        assert result["verdict"] == "rejected_schema"

    def test_validate_proposal_still_accepts_a_real_date(self):
        ctx = ProposalValidationContext(
            category_ids=frozenset({"c1"}), account_ids=frozenset(),
        )
        result = AIService.validate_proposal(
            {
                "proposalId": "p1", "action": "add_tx", "confidence": 0.95,
                "requiresConfirmation": False, "message": "", "warnings": [],
                "transactions": [{
                    "type": "expense", "amountCents": 100,
                    "bizDate": "2026-09-18", "note": "", "categoryId": "c1",
                }],
            },
            ctx,
        )
        assert result == {"valid": True, "errors": [], "verdict": "accepted"}


# ── 读工具（真实 SQLite）────────────────────────────────────────────────────


@pytest_asyncio.fixture()
async def seeded():
    """一个有数据的库：2 账本 / 3 分类 / 2 账户 / 4 笔交易 / 2 条预算。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    user = User(id=uuid.uuid4(), nickname="tool-tester")
    book = Book(id=uuid.uuid4(), owner_id=user.id, name="日常")
    other_book = Book(id=uuid.uuid4(), owner_id=user.id, name="旅行")
    food = Category(id=uuid.uuid4(), owner_id=user.id, book_id=book.id,
                    kind=CategoryKind.EXPENSE, name="餐饮")
    traffic = Category(id=uuid.uuid4(), owner_id=user.id, book_id=book.id,
                       kind=CategoryKind.EXPENSE, name="交通")
    salary = Category(id=uuid.uuid4(), owner_id=user.id, book_id=book.id,
                      kind=CategoryKind.INCOME, name="工资")
    card = Account(id=uuid.uuid4(), owner_id=user.id, name="招行卡")
    cash = Account(id=uuid.uuid4(), owner_id=user.id, name="现金")
    archived = Category(id=uuid.uuid4(), owner_id=user.id, book_id=book.id,
                        kind=CategoryKind.EXPENSE, name="已归档", archived_at=datetime.now(UTC))
    # 同名但收支类型不同：数据库的唯一约束是 (book_id, kind, name)，两者可以共存。
    misc_expense = Category(id=uuid.uuid4(), owner_id=user.id, book_id=book.id,
                            kind=CategoryKind.EXPENSE, name="其他")
    misc_income = Category(id=uuid.uuid4(), owner_id=user.id, book_id=book.id,
                           kind=CategoryKind.INCOME, name="其他")

    now = datetime.now(UTC)

    def tx(tx_type, cents, category, account, biz_date, note, *, in_book=None, deleted=None):
        return Transaction(
            id=uuid.uuid4(), book_id=(in_book or book).id, type=tx_type, amount_cents=cents,
            category_id=category.id, account_id=account.id if account else None,
            note=note, biz_date=biz_date, occurred_at=now, source=TxSource.MANUAL,
            deleted_at=deleted,
        )

    txs = [
        tx(TxType.EXPENSE, 1250, food, card, date(2026, 9, 1), "午饭"),
        tx(TxType.EXPENSE, 3000, traffic, cash, date(2026, 9, 2), "打车"),
        tx(TxType.INCOME, 500000, salary, None, date(2026, 9, 5), "九月工资"),
        tx(TxType.EXPENSE, 2000, food, card, date(2026, 8, 20), "八月午饭"),
        # 软删除：所有读工具都必须看不见它
        tx(TxType.EXPENSE, 7777, food, card, date(2026, 9, 4), "已删除", deleted=now),
        # 另一个账本：所有读工具都必须看不见它（工具层的每条查询都用 ctx.book_id 过滤）
        tx(TxType.EXPENSE, 9999, food, card, date(2026, 9, 3), "旅行开销", in_book=other_book),
    ]
    budgets = [
        Budget(id=uuid.uuid4(), book_id=book.id, budget_type=BudgetType.MONTHLY,
               category_id=food.id, period="2026-09", amount_cents=1000),
        Budget(id=uuid.uuid4(), book_id=book.id, budget_type=BudgetType.MONTHLY,
               category_id=traffic.id, period="2026-09", amount_cents=5000),
        # 归档分类的预算：不应出现在任何结果里
        Budget(id=uuid.uuid4(), book_id=book.id, budget_type=BudgetType.MONTHLY,
               category_id=archived.id, period="2026-09", amount_cents=8000),
    ]

    async with factory() as session:
        session.add_all([user, book, other_book, food, traffic, salary,
                         card, cash, archived, misc_expense, misc_income, *txs, *budgets])
        await session.commit()

        ctx = await T.load_tool_context(session, user.id, book.id)
        yield {"ctx": ctx, "user": user, "book": book, "other_book": other_book,
               "food": food, "traffic": traffic, "salary": salary,
               "card": card, "cash": cash, "archived": archived,
               "misc_expense": misc_expense, "misc_income": misc_income}

    await engine.dispose()


class TestToolContextLoading:
    async def test_names_map_to_ids(self, seeded):
        ctx = seeded["ctx"]
        assert ctx.resolve_category("expense", "餐饮") == str(seeded["food"].id)
        assert ctx.resolve_category("income", "工资") == str(seeded["salary"].id)
        assert ctx.account_names["招行卡"] == str(seeded["card"].id)
        assert ctx.book_names["旅行"] == str(seeded["other_book"].id)

    async def test_archived_categories_are_not_offered(self, seeded):
        assert seeded["ctx"].resolve_category("expense", "已归档") is None

    async def test_category_of_one_kind_is_not_reachable_through_another(self, seeded):
        """同名的支出分类与收入分类靠 kind 区分；用错类型解析不到，而不是落到
        另一个类型的 id 上（否则会产出「支出交易 + 收入分类」的脏数据）。"""
        assert seeded["ctx"].resolve_category("income", "餐饮") is None

    async def test_a_foreign_book_is_rejected(self, seeded):
        """工具层不信任调用方传来的账本 id —— 每条读查询都用它过滤。"""
        from src.common.errors import AppException

        foreign = uuid.uuid4()
        with pytest.raises(AppException) as excinfo:
            await T.load_tool_context(seeded["ctx"].db, seeded["user"].id, foreign)
        assert excinfo.value.status_code == 404

    async def test_unknown_name_resolution_degrades(self, seeded):
        outcome = await T.invoke_tool(
            "transaction.add", seeded["ctx"],
            {**_VALID_ADD, "categoryName": "已归档"},
        )
        assert outcome.data["code"] == T.ERR_UNKNOWN_CATEGORY

    async def test_same_name_categories_of_different_kinds_are_told_apart(self, seeded):
        """库里有一个「其他(支出)」和一个「其他(收入)」。只按名字解析会落到任意
        一个上——写路径没有任何环节比对分类类型与交易类型，那就是脏数据。"""
        ctx = seeded["ctx"]
        assert ctx.resolve_category("expense", "其他") == str(seeded["misc_expense"].id)
        assert ctx.resolve_category("income", "其他") == str(seeded["misc_income"].id)

        expense = await T.invoke_tool("transaction.add", ctx, {
            "type": "expense", "amountCents": 100,
            "categoryName": "其他", "bizDate": "2026-09-18",
        })
        assert expense.data["transactions"][0]["categoryId"] == str(seeded["misc_expense"].id)

        income = await T.invoke_tool("transaction.add", ctx, {
            "type": "income", "amountCents": 100,
            "categoryName": "其他", "bizDate": "2026-09-18",
        })
        assert income.data["transactions"][0]["categoryId"] == str(seeded["misc_income"].id)

    async def test_a_duplicate_account_name_is_reported_as_ambiguous(self, seeded):
        """重名账户的**写入**必须唯一解——报 ``ambiguous_account`` 让模型请用户
        指明，而不是挑一个默默记账。读路径相反：按名字取全集（见下一条用例）。"""
        from src.models.ledger import Account

        seeded["ctx"].db.add(Account(
            id=uuid.uuid4(), owner_id=seeded["user"].id, name="招行卡",
        ))
        await seeded["ctx"].db.commit()
        reloaded = await T.load_tool_context(
            seeded["ctx"].db, seeded["user"].id, seeded["book"].id,
        )
        assert "招行卡" not in reloaded.account_names

        outcome = await T.invoke_tool(
            "transaction.add", reloaded, {**_VALID_ADD, "accountName": "招行卡"},
        )
        assert outcome.data["code"] == T.ERR_AMBIGUOUS_ACCOUNT
        assert "同名" in outcome.data["message"], "错误必须可行动"

    async def test_reads_by_a_duplicated_account_name_cover_all_of_them(self, seeded):
        """读过滤按名字取**全集**。若改成走唯一解映射，重名会让查询静默返回
        空集——模型据此对用户断言「这个账户没有支出」，那是事实错误，比拒绝
        回答糟得多。"""
        from src.models.ledger import Account, Transaction, TxSource

        dup = Account(id=uuid.uuid4(), owner_id=seeded["user"].id, name="现金")
        seeded["ctx"].db.add(dup)
        seeded["ctx"].db.add(Transaction(
            id=uuid.uuid4(), book_id=seeded["book"].id, type=TxType.EXPENSE,
            amount_cents=500, category_id=seeded["food"].id, account_id=dup.id,
            note="另一个现金", biz_date=date(2026, 9, 6),
            occurred_at=datetime.now(UTC), source=TxSource.MANUAL,
        ))
        await seeded["ctx"].db.commit()
        reloaded = await T.load_tool_context(
            seeded["ctx"].db, seeded["user"].id, seeded["book"].id,
        )

        listed = await T.invoke_tool("transaction.list", reloaded, {"accountName": "现金"})
        assert listed.data["total"] == 2, "两个同名账户的流水都要出现"

        summary = await T.invoke_tool("transaction.summary", reloaded, {
            "dateFrom": "2026-09-01", "dateTo": "2026-09-30", "accountName": "现金",
        })
        assert summary.data["totalExpenseCents"] == 3500, "3000（原现金）+ 500（重名现金）"

        balances = await T.invoke_tool("account.balances", reloaded, {})
        cash_rows = [a for a in balances.data["accounts"] if a["name"] == "现金"]
        assert len(cash_rows) == 2, "同名账户必须分列，金额不能合并成一个数"
        assert {r["expenseCents"] for r in cash_rows} == {3000, 500}


class TestTransactionReadTools:
    async def test_list_returns_all_within_range(self, seeded):
        outcome = await T.invoke_tool(
            "transaction.list", seeded["ctx"],
            {"dateFrom": "2026-09-01", "dateTo": "2026-09-30"},
        )
        assert outcome.kind == "result"
        assert outcome.data["total"] == 3
        assert outcome.data["truncated"] is False
        assert outcome.data["transactions"][0]["bizDate"] == "2026-09-05"
        assert outcome.data["transactions"][0]["categoryName"] == "工资"

    async def test_soft_deleted_transactions_are_invisible(self, seeded):
        """每条读查询都带 ``deleted_at IS NULL``——库里有一笔软删除的记录，
        去掉过滤这条就会失败。"""
        outcome = await T.invoke_tool("transaction.list", seeded["ctx"], {})
        assert "已删除" not in {r["note"] for r in outcome.data["transactions"]}
        assert outcome.data["total"] == 4

    async def test_transactions_of_another_book_are_invisible(self, seeded):
        """每条读查询都用 ``ctx.book_id`` 过滤——库里另一个账本有一笔记录，
        去掉过滤这条就会失败。"""
        outcome = await T.invoke_tool("transaction.list", seeded["ctx"], {})
        assert "旅行开销" not in {r["note"] for r in outcome.data["transactions"]}
        assert outcome.data["total"] == 4

    async def test_list_never_exposes_ids(self, seeded):
        """模型拿到 UUID 就会有把它写回参数的机会——铁律是模型永不输出 UUID。"""
        outcome = await T.invoke_tool("transaction.list", seeded["ctx"], {})
        dumped = json.dumps(outcome.data, ensure_ascii=False)
        assert "id" not in outcome.data["transactions"][0]
        assert str(seeded["book"].id) not in dumped
        assert str(seeded["food"].id) not in dumped
        assert str(seeded["card"].id) not in dumped

    async def test_list_filters_by_category(self, seeded):
        outcome = await T.invoke_tool(
            "transaction.list", seeded["ctx"], {"categoryName": "餐饮"},
        )
        assert outcome.data["total"] == 2
        assert {r["note"] for r in outcome.data["transactions"]} == {"午饭", "八月午饭"}

    async def test_list_filters_by_keyword_across_note_and_category(self, seeded):
        by_note = await T.invoke_tool(
            "transaction.list", seeded["ctx"], {"keyword": "打车"},
        )
        assert by_note.data["total"] == 1
        by_category = await T.invoke_tool(
            "transaction.list", seeded["ctx"], {"keyword": "交通"},
        )
        assert by_category.data["total"] == 1

    async def test_list_keyword_wildcards_are_literals(self, seeded):
        outcome = await T.invoke_tool(
            "transaction.list", seeded["ctx"], {"keyword": "%"},
        )
        assert outcome.data["total"] == 0

    async def test_list_filters_by_type_and_account(self, seeded):
        income = await T.invoke_tool(
            "transaction.list", seeded["ctx"], {"type": "income"},
        )
        assert income.data["total"] == 1
        by_account = await T.invoke_tool(
            "transaction.list", seeded["ctx"], {"accountName": "现金"},
        )
        assert by_account.data["total"] == 1
        assert by_account.data["transactions"][0]["accountName"] == "现金"

    async def test_list_unknown_account_name_matches_nothing(self, seeded):
        outcome = await T.invoke_tool(
            "transaction.list", seeded["ctx"], {"accountName": "不存在的卡"},
        )
        assert outcome.data["total"] == 0

    async def test_list_truncates_and_reports_it(self, seeded):
        outcome = await T.invoke_tool("transaction.list", seeded["ctx"], {"limit": 2})
        assert outcome.data["total"] == 4
        assert len(outcome.data["transactions"]) == 2
        assert outcome.data["truncated"] is True

    async def test_recent_returns_newest_first(self, seeded):
        outcome = await T.invoke_tool("transaction.recent", seeded["ctx"], {"limit": 2})
        assert [r["bizDate"] for r in outcome.data["transactions"]] == [
            "2026-09-05", "2026-09-02",
        ]

    async def test_summary_totals(self, seeded):
        outcome = await T.invoke_tool(
            "transaction.summary", seeded["ctx"],
            {"dateFrom": "2026-09-01", "dateTo": "2026-09-30"},
        )
        assert outcome.data["totalIncomeCents"] == 500000
        assert outcome.data["totalExpenseCents"] == 4250
        assert outcome.data["balanceCents"] == 495750
        assert outcome.data["transactionCount"] == 3
        assert "groups" not in outcome.data

    async def test_summary_groups_by_category_keep_income_and_expense_apart(self, seeded):
        outcome = await T.invoke_tool(
            "transaction.summary", seeded["ctx"],
            {"dateFrom": "2026-09-01", "dateTo": "2026-09-30", "groupBy": "category"},
        )
        groups = {(g["key"], g["type"]): g["totalCents"] for g in outcome.data["groups"]}
        assert groups == {("餐饮", "expense"): 1250, ("交通", "expense"): 3000,
                          ("工资", "income"): 500000}

    async def test_summary_groups_by_month(self, seeded):
        outcome = await T.invoke_tool(
            "transaction.summary", seeded["ctx"],
            {"dateFrom": "2026-08-01", "dateTo": "2026-09-30", "groupBy": "month"},
        )
        # 断言金额而非仅断言 key —— 月度口径靠 cast+substr 推导，只看 key 存在
        # 无法发现它算错月份或算错金额。
        groups = {(g["key"], g["type"]): g["totalCents"] for g in outcome.data["groups"]}
        assert groups == {
            ("2026-08", "expense"): 2000,
            ("2026-09", "expense"): 4250,
            ("2026-09", "income"): 500000,
        }

    async def test_summary_missing_dates_degrades(self, seeded):
        outcome = await T.invoke_tool(
            "transaction.summary", seeded["ctx"], {"dateFrom": "2026-09-01"},
        )
        assert outcome.kind == "error"
        assert outcome.data["code"] == T.ERR_INVALID_ARGUMENTS


class TestBudgetReadTools:
    async def test_list_returns_both_budgets(self, seeded):
        outcome = await T.invoke_tool("budget.list", seeded["ctx"], {"period": "2026-09"})
        items = {i["categoryName"]: i for i in outcome.data["items"]}
        assert set(items) == {"餐饮", "交通"}
        assert items["餐饮"]["budgetCents"] == 1000
        assert items["餐饮"]["spentCents"] == 1250
        assert items["餐饮"]["exceeded"] is True
        assert items["餐饮"]["isOverall"] is False

    async def test_list_rejects_bad_period(self, seeded):
        outcome = await T.invoke_tool("budget.list", seeded["ctx"], {"period": "2026-13"})
        assert outcome.data["code"] == T.ERR_INVALID_PERIOD

    async def test_archived_category_budget_is_not_listed(self, seeded):
        """归档分类的预算不列：``budget.status`` 对归档分类返回 unknown_category，
        列表若还显示它，模型就会「看得见却查不到」。库里正有这样一条。"""
        outcome = await T.invoke_tool("budget.list", seeded["ctx"], {"period": "2026-09"})
        assert len(outcome.data["items"]) == 2
        assert all(i["categoryName"] != "已归档" for i in outcome.data["items"])

    async def test_a_duplicate_category_name_resolves_to_the_book_scoped_one(self, seeded):
        """分类唯一约束含 ``book_id``，所以本账本分类与全局分类可以同 kind 同名
        ——客户端默认分类就是全局的（``Repository.kt seedCategory()`` 建成
        ``bookId=null``），用户再给某个账本建一套同名分类很自然。

        解析取**本账本**那个。若改成拒绝解析，模型会看到合法分类名却永远写不
        进去：``category.list`` 显示两行同名、写操作恒失败，无路可走。
        """
        from src.models.ledger import Category

        global_id = uuid.uuid4()
        seeded["ctx"].db.add(Category(
            id=global_id, owner_id=seeded["user"].id, book_id=None,
            kind=CategoryKind.EXPENSE, name="交通",
        ))
        await seeded["ctx"].db.commit()
        reloaded = await T.load_tool_context(
            seeded["ctx"].db, seeded["user"].id, seeded["book"].id,
        )

        assert reloaded.resolve_category("expense", "交通") == str(seeded["traffic"].id), \
            "本账本分类优先于同名的全局分类"
        assert reloaded.category_name_of(seeded["traffic"].id) == "交通"
        assert reloaded.category_name_of(global_id) == "交通", "反查不受重名影响"

        outcome = await T.invoke_tool("transaction.add", reloaded, {
            "type": "expense", "amountCents": 100,
            "categoryName": "交通", "bizDate": "2026-09-18",
        })
        assert outcome.data["transactions"][0]["categoryId"] == str(seeded["traffic"].id)

        # 预算与读过滤都不受影响
        listed = await T.invoke_tool("budget.list", reloaded, {"period": "2026-09"})
        assert "交通" in [i["categoryName"] for i in listed.data["items"]]
        txs = await T.invoke_tool("transaction.list", reloaded, {"categoryName": "交通"})
        assert txs.data["total"] == 1, "本账本的交通交易应仍然查得到"

    async def test_status_for_a_category_with_budget(self, seeded):
        outcome = await T.invoke_tool(
            "budget.status", seeded["ctx"],
            {"categoryName": "交通", "period": "2026-09"},
        )
        assert outcome.data["hasBudget"] is True
        assert outcome.data["budgetCents"] == 5000
        assert outcome.data["spentCents"] == 3000
        assert outcome.data["remainingCents"] == 2000
        assert outcome.data["exceeded"] is False

    async def test_status_reports_overrun_as_a_negative_remainder(self, seeded):
        """remaining 允许为负——`exceeded` 给出判断，负数给出超了多少。"""
        outcome = await T.invoke_tool(
            "budget.status", seeded["ctx"],
            {"categoryName": "餐饮", "period": "2026-09"},
        )
        assert outcome.data["remainingCents"] == -250
        assert outcome.data["exceeded"] is True

    async def test_status_without_budget_says_so(self, seeded):
        outcome = await T.invoke_tool(
            "budget.status", seeded["ctx"],
            {"categoryName": "工资", "period": "2026-09"},
        )
        assert outcome.data["hasBudget"] is False
        assert outcome.data["budgetCents"] is None

    async def test_status_unknown_category_degrades(self, seeded):
        outcome = await T.invoke_tool(
            "budget.status", seeded["ctx"],
            {"categoryName": "夜宵", "period": "2026-09"},
        )
        assert outcome.data["code"] == T.ERR_UNKNOWN_CATEGORY

    async def test_check_flags_an_exceeding_amount(self, seeded):
        outcome = await T.invoke_tool(
            "budget.check", seeded["ctx"],
            {"categoryName": "交通", "amountCents": 1000, "period": "2026-09"},
        )
        assert outcome.data["afterCents"] == 4000
        assert outcome.data["willExceed"] is False

        over = await T.invoke_tool(
            "budget.check", seeded["ctx"],
            {"categoryName": "交通", "amountCents": 2500, "period": "2026-09"},
        )
        assert over.data["afterCents"] == 5500
        assert over.data["willExceed"] is True

    async def test_summary_aggregates_category_budgets(self, seeded):
        outcome = await T.invoke_tool("budget.summary", seeded["ctx"], {"period": "2026-09"})
        assert outcome.data["overall"] is None
        categories = outcome.data["categories"]
        assert categories["count"] == 2
        assert categories["budgetCents"] == 6000
        assert categories["spentCents"] == 4250
        assert categories["remainingCents"] == 1750
        assert outcome.data["exceededCount"] == 1
        assert outcome.data["exceededCategories"] == ["餐饮"]

    async def test_summary_counts_an_overall_budget_against_all_spending(self, seeded):
        """只有总预算时，已用必须是**全账本**支出——曾漏算成 0，会让模型对用户
        说「这个月还没花预算」。总预算与分类预算并存时两者分开报，不重复计数。

        fixture 里必须先有一笔**没有预算的分类**的支出：否则两层口径的已用恰好
        相等，改回旧实现这个测试照样绿（是空测试）。
        """
        from src.models.budget import Budget as BudgetModel
        from src.models.budget import BudgetType
        from src.models.ledger import Transaction, TxSource

        no_budget_expense = 888
        seeded["ctx"].db.add(Transaction(
            id=uuid.uuid4(), book_id=seeded["book"].id, type=TxType.EXPENSE,
            amount_cents=no_budget_expense, category_id=seeded["misc_expense"].id,
            account_id=seeded["cash"].id, note="没有预算的分类",
            biz_date=date(2026, 9, 7), occurred_at=datetime.now(UTC),
            source=TxSource.MANUAL,
        ))
        seeded["ctx"].db.add(BudgetModel(
            id=uuid.uuid4(), book_id=seeded["book"].id,
            budget_type=BudgetType.MONTHLY, category_id=None,
            period="2026-09", amount_cents=100000,
        ))
        await seeded["ctx"].db.commit()
        reloaded = await T.load_tool_context(
            seeded["ctx"].db, seeded["user"].id, seeded["book"].id,
        )

        outcome = await T.invoke_tool("budget.summary", reloaded, {"period": "2026-09"})
        overall = outcome.data["overall"]
        assert overall["budgetCents"] == 100000
        assert overall["spentCents"] == 4250 + no_budget_expense, "总预算的已用是全账本支出"
        assert overall["remainingCents"] == 100000 - 4250 - no_budget_expense
        categories = outcome.data["categories"]
        assert categories["spentCents"] == 4250, "分类层只算分类预算覆盖的支出，不与总预算相加"

    async def test_overspent_lists_only_the_overrun(self, seeded):
        outcome = await T.invoke_tool("budget.overspent", seeded["ctx"], {"period": "2026-09"})
        assert outcome.data["overspentCount"] == 1
        assert outcome.data["overspent"][0]["categoryName"] == "餐饮"

    async def test_empty_period_is_not_an_error(self, seeded):
        outcome = await T.invoke_tool("budget.list", seeded["ctx"], {"period": "2026-01"})
        assert outcome.kind == "result"
        assert outcome.data["items"] == []


class TestLedgerReadTools:
    async def test_account_list(self, seeded):
        outcome = await T.invoke_tool("account.list", seeded["ctx"], {})
        assert {a["name"] for a in outcome.data["accounts"]} == {"招行卡", "现金"}
        assert outcome.data["total"] == 2

    async def test_account_balances_spans_history_by_default(self, seeded):
        outcome = await T.invoke_tool("account.balances", seeded["ctx"], {})
        balances = {a["name"]: a for a in outcome.data["accounts"]}
        assert balances["招行卡"]["expenseCents"] == 3250
        assert balances["招行卡"]["netCents"] == -3250
        assert balances["现金"]["incomeCents"] == 0

    async def test_account_balances_honours_date_range(self, seeded):
        outcome = await T.invoke_tool(
            "account.balances", seeded["ctx"],
            {"dateFrom": "2026-09-01", "dateTo": "2026-09-30"},
        )
        balances = {a["name"]: a for a in outcome.data["accounts"]}
        assert balances["招行卡"]["expenseCents"] == 1250

    async def test_book_list_marks_the_current_book(self, seeded):
        outcome = await T.invoke_tool("book.list", seeded["ctx"], {})
        books = {b["name"]: b for b in outcome.data["books"]}
        assert books["日常"]["current"] is True
        assert books["旅行"]["current"] is False

    async def test_book_detail_defaults_to_the_current_book(self, seeded):
        outcome = await T.invoke_tool("book.detail", seeded["ctx"], {})
        assert outcome.data["name"] == "日常"
        assert outcome.data["current"] is True
        assert outcome.data["categoryCount"] == 5  # 已归档的那个不算
        # 4 笔可见：软删除的那笔与另一个账本的那笔都不计入
        assert outcome.data["transactionCount"] == 4
        assert outcome.data["firstBizDate"] == "2026-08-20"
        assert outcome.data["lastBizDate"] == "2026-09-05"

    async def test_book_detail_by_name(self, seeded):
        outcome = await T.invoke_tool(
            "book.detail", seeded["ctx"], {"bookName": "旅行"},
        )
        assert outcome.data["name"] == "旅行"
        assert outcome.data["current"] is False
        assert outcome.data["transactionCount"] == 1
        assert outcome.data["firstBizDate"] == "2026-09-03"

    async def test_book_detail_unknown_name_degrades(self, seeded):
        outcome = await T.invoke_tool(
            "book.detail", seeded["ctx"], {"bookName": "不存在的账本"},
        )
        assert outcome.data["code"] == T.ERR_UNKNOWN_BOOK

    async def test_category_list(self, seeded):
        outcome = await T.invoke_tool("category.list", seeded["ctx"], {})
        assert {c["name"] for c in outcome.data["categories"]} == {
            "餐饮", "交通", "工资", "其他",
        }
        kinds = {c["name"]: c["kind"] for c in outcome.data["categories"]}
        assert kinds["工资"] == "income"

    async def test_other_book_has_no_categories_of_its_own(self, seeded):
        outcome = await T.invoke_tool(
            "category.list", seeded["ctx"], {"bookName": "旅行"},
        )
        assert outcome.data["categories"] == []
