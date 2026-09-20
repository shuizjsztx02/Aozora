"""T-M7-05c: 预算存钱 / 定时 / 角色三域的工具（16 个新工具）。

验收映射（``docs/milestones/T-M7-05-P4-implementation-plan.md`` §三 05c）：
- 三域各 ≤8 个工具、写工具的 op 都是 v2 注册表成员 → ``test_ai_tools.py`` 的注册表契约
- 写操作一律产出**规范草案**（v2 op + payload），经组装器进审批门 → 全文件
- **模型永不输出 UUID**：改 / 删 / 归档只按名字定位，名→id 走 ``ToolContext``
- 预算的分类解析、定时规则的账本收窄、存钱的存入累加（铁律 2）→ 各自的测试类

这些用例跑真实 SQLite（处理器要查库：解析名字、读版本、读规则类型），
**不经过 HTTP**——执行面（``/ai/execute``、sync 登记）在
``test_ai_proposal_v2_05c.py``。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.models.base import Base
from src.models.budget import Budget, BudgetType
from src.models.ledger import Account, Book, Category, CategoryKind
from src.models.persona import ChatPersona
from src.models.savings import SavingsGoal
from src.models.scheduled import ScheduledRule
from src.models.user import User
from src.modules.ai import tools as T


@pytest_asyncio.fixture()
async def seeded():
    """2 账本 / 5 分类 / 1 账户 / 3 存钱目标 / 3 定时规则 / 3 角色。

    刻意埋进的边界：每个域都有一条**已归档**的、一条**重名**的、以及（定时域）
    一条**别的账本的**——它们分别对应「归档的不该被解析到」「重名要报 ambiguous」
    「跨账本不该够得到」。
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    user = User(id=uuid.uuid4(), nickname="05c-tester")
    book = Book(id=uuid.uuid4(), owner_id=user.id, name="日常")
    other_book = Book(id=uuid.uuid4(), owner_id=user.id, name="旅行")

    food = Category(id=uuid.uuid4(), owner_id=user.id, book_id=book.id,
                    kind=CategoryKind.EXPENSE, name="餐饮")
    salary = Category(id=uuid.uuid4(), owner_id=user.id, book_id=book.id,
                      kind=CategoryKind.INCOME, name="工资")
    # 同名跨收支类型：预算只认支出侧那条
    other_income = Category(id=uuid.uuid4(), owner_id=user.id, book_id=book.id,
                            kind=CategoryKind.INCOME, name="餐饮")
    card = Account(id=uuid.uuid4(), owner_id=user.id, name="招行卡")

    now = datetime.now(UTC)

    trip = SavingsGoal(id=uuid.uuid4(), owner_id=user.id, name="旅行基金",
                       target_cents=800000, current_cents=200000, icon="✈️",
                       period_value=3, period_unit="month")
    camera = SavingsGoal(id=uuid.uuid4(), owner_id=user.id, name="相机",
                         target_cents=500000, current_cents=500000)
    # 重名：写路径必须报 ambiguous_savings
    dup_a = SavingsGoal(id=uuid.uuid4(), owner_id=user.id, name="同名目标",
                        target_cents=100000, current_cents=0)
    dup_b = SavingsGoal(id=uuid.uuid4(), owner_id=user.id, name="同名目标",
                        target_cents=200000, current_cents=0)
    gone = SavingsGoal(id=uuid.uuid4(), owner_id=user.id, name="已归档目标",
                       target_cents=100000, current_cents=0, archived_at=now)

    rent = ScheduledRule(
        id=uuid.uuid4(), owner_id=user.id, book_id=book.id, type="expense",
        name="房租", amount_cents=300000, category_id=food.id, account_id=card.id,
        repeat="monthly", start_date=date(2026, 1, 1), run_hour=8, run_minute=0,
        note="每月一号", last_run_date=date(2026, 9, 1),
    )
    # 收入侧规则：改分类名而不给 type 时，解析要用**规则自己的**类型
    bonus = ScheduledRule(
        id=uuid.uuid4(), owner_id=user.id, book_id=book.id, type="income",
        name="奖金", amount_cents=100000, category_id=salary.id,
        repeat="yearly", start_date=date(2026, 1, 1),
    )
    # 别的账本的规则：本轮的上下文不该够得到它
    foreign = ScheduledRule(
        id=uuid.uuid4(), owner_id=user.id, book_id=other_book.id, type="expense",
        name="旅行开销", amount_cents=50000, category_id=food.id,
        repeat="weekly", start_date=date(2026, 1, 1),
    )
    archived_rule = ScheduledRule(
        id=uuid.uuid4(), owner_id=user.id, book_id=book.id, type="expense",
        name="已归档规则", amount_cents=1000, category_id=food.id,
        repeat="daily", start_date=date(2026, 1, 1), archived_at=now,
    )

    xiaobei = ChatPersona(id=uuid.uuid4(), owner_id=user.id, name="小贝",
                          avatar="🐻", persona="温和的记账助手")
    catgirl = ChatPersona(id=uuid.uuid4(), owner_id=user.id, name="猫娘",
                          avatar="🐱", persona="说话带喵")
    gone_persona = ChatPersona(id=uuid.uuid4(), owner_id=user.id, name="已归档角色",
                               persona="", archived_at=now)

    overall = Budget(id=uuid.uuid4(), book_id=book.id, budget_type=BudgetType.MONTHLY,
                     period="2026-09", amount_cents=1000000, category_id=None)
    food_budget = Budget(id=uuid.uuid4(), book_id=book.id, budget_type=BudgetType.MONTHLY,
                         period="2026-09", amount_cents=150000, category_id=food.id)

    async with factory() as session:
        session.add_all([
            user, book, other_book, food, salary, other_income, card,
            trip, camera, dup_a, dup_b, gone,
            rent, bonus, foreign, archived_rule,
            xiaobei, catgirl, gone_persona,
            overall, food_budget,
        ])
        await session.commit()

        ctx = await T.load_tool_context(session, user.id, book.id)
        yield {
            "ctx": ctx, "db": session, "user": user, "book": book,
            "other_book": other_book, "food": food, "salary": salary,
            "food_income": other_income, "card": card,
            "trip": trip, "camera": camera, "gone": gone,
            "rent": rent, "bonus": bonus, "foreign": foreign,
            "xiaobei": xiaobei, "catgirl": catgirl, "gone_persona": gone_persona,
            "overall": overall, "food_budget": food_budget,
        }

    await engine.dispose()


async def _draft(tool: str, ctx: T.ToolContext, params: dict) -> dict:
    """调一个写工具并断言「产出了提案」，返回草案。"""
    outcome = await T.invoke_tool(tool, ctx, params)
    assert outcome.kind == "proposal", (tool, outcome.kind, outcome.data)
    return outcome.data


async def _failure(tool: str, ctx: T.ToolContext, params: dict) -> dict:
    """调一个工具并断言「降级成安全错误码」，返回 error data。"""
    outcome = await T.invoke_tool(tool, ctx, params)
    assert outcome.kind == "error", (tool, outcome.kind, outcome.data)
    return outcome.data


def _result(outcome: T.ToolOutcome) -> dict:
    assert outcome.kind == "result", (outcome.kind, outcome.data)
    return outcome.data


# ── 上下文装载 ─────────────────────────────────────────────────────────────


class TestToolContextLoading05c:
    async def test_the_three_name_indexes_are_loaded(self, seeded):
        ctx = seeded["ctx"]
        assert ctx.savings_names["旅行基金"] == str(seeded["trip"].id)
        assert ctx.persona_names["小贝"] == str(seeded["xiaobei"].id)
        assert ctx.scheduled_names["房租"] == str(seeded["rent"].id)

    async def test_archived_objects_are_not_offered(self, seeded):
        ctx = seeded["ctx"]
        assert "已归档目标" not in ctx.savings_names
        assert "已归档规则" not in ctx.scheduled_names
        assert "已归档角色" not in ctx.persona_names

    async def test_a_duplicate_name_is_not_resolvable_for_writes(self, seeded):
        """重名不进名字索引（写路径要唯一解），但 id→名字那套里两条都在。"""
        ctx = seeded["ctx"]
        assert "同名目标" not in ctx.savings_names
        assert len(ctx.savings_ids_named("同名目标")) == 2

    async def test_scheduled_rules_are_scoped_to_the_current_book(self, seeded):
        """定时规则带账本归属：本轮是「日常」，别的账本的规则不该在索引里。"""
        ctx = seeded["ctx"]
        assert "旅行开销" not in ctx.scheduled_names


# ── 预算域：写 ─────────────────────────────────────────────────────────────


class TestBudgetWriteTools:
    async def test_set_without_an_existing_budget_has_no_version(self, seeded):
        """这个月没设过 → 草案不带 version（执行体按「新建」走）。"""
        draft = await _draft("budget_set", seeded["ctx"], {
            "period": "2026-10", "amountCents": 500000,
        })
        assert draft["action"] == "budget_set"
        assert draft["payload"] == {"period": "2026-10", "amountCents": 500000}

    async def test_set_on_an_existing_budget_carries_the_version(self, seeded):
        draft = await _draft("budget_set", seeded["ctx"], {
            "period": "2026-09", "amountCents": 600000,
        })
        assert draft["payload"]["version"] == seeded["overall"].version
        assert "10000.00" in draft["message"], "message 里要带上原额度（用户能对照）"

    async def test_an_impossible_period_degrades(self, seeded):
        data = await _failure("budget_set", seeded["ctx"], {
            "period": "2026-13", "amountCents": 100,
        })
        assert data["code"] == T.ERR_INVALID_PERIOD

    async def test_set_category_resolves_the_expense_side(self, seeded):
        """同名跨收支类型时取**支出**那条（预算的「已用」是支出）。"""
        draft = await _draft("budget_set_category", seeded["ctx"], {
            "period": "2026-10", "categoryName": "餐饮", "amountCents": 200000,
        })
        assert draft["payload"]["categoryId"] == str(seeded["food"].id)
        assert "覆盖" not in " ".join(draft["warnings"]), "新建不该报「覆盖」警告"

    async def test_set_category_on_an_existing_one_warns_about_overwriting(self, seeded):
        draft = await _draft("budget_set_category", seeded["ctx"], {
            "period": "2026-09", "categoryName": "餐饮", "amountCents": 200000,
        })
        assert draft["payload"]["version"] == seeded["food_budget"].version
        assert draft["warnings"], "覆盖已有额度要有 warnings（置信度降档、单步也出确认卡）"

    async def test_an_income_category_is_refused_with_its_own_code(self, seeded):
        """预算设在收入分类上等于给它一个永远为 0 的进度——明确拒绝，不静默照做。"""
        data = await _failure("budget_set_category", seeded["ctx"], {
            "period": "2026-09", "categoryName": "工资", "amountCents": 1000,
        })
        assert data["code"] == T.ERR_BUDGET_INCOME_CATEGORY

    async def test_an_unknown_category_degrades(self, seeded):
        data = await _failure("budget_set_category", seeded["ctx"], {
            "period": "2026-09", "categoryName": "不存在", "amountCents": 1000,
        })
        assert data["code"] == T.ERR_UNKNOWN_CATEGORY

    async def test_delete_overall_budget_has_no_category_id(self, seeded):
        draft = await _draft("budget_delete", seeded["ctx"], {"period": "2026-09"})
        assert draft["payload"] == {"period": "2026-09"}

    async def test_delete_category_budget_carries_the_category_id(self, seeded):
        draft = await _draft("budget_delete", seeded["ctx"], {
            "period": "2026-09", "categoryName": "餐饮",
        })
        assert draft["payload"] == {"period": "2026-09", "categoryId": str(seeded["food"].id)}

    async def test_deleting_something_that_is_not_there_degrades(self, seeded):
        """删一个不存在的东西不该产出一张必然失败的确认卡。"""
        for params in ({"period": "2026-10"}, {"period": "2026-10", "categoryName": "餐饮"}):
            data = await _failure("budget_delete", seeded["ctx"], params)
            assert data["code"] == T.ERR_UNKNOWN_BUDGET, params


# ── 存钱域 ─────────────────────────────────────────────────────────────────


class TestSavingsReadTools:
    async def test_list_computes_progress_without_ids(self, seeded):
        data = _result(await T.invoke_tool("savings_list", seeded["ctx"], {}))
        by_name = {item["name"]: item for item in data["savings"]}
        assert set(by_name) == {"旅行基金", "相机", "同名目标"}, "已归档的不在清单里"
        trip = by_name["旅行基金"]
        assert trip["remainingCents"] == 600000 and trip["progressPct"] == 25.0
        assert trip["achieved"] is False and by_name["相机"]["achieved"] is True
        assert "id" not in trip, "读工具的结果里不含任何 id（铁律 1）"


class TestSavingsWriteTools:
    async def test_create_carries_target_and_omits_an_absent_icon(self, seeded):
        draft = await _draft("savings_create", seeded["ctx"], {
            "name": "新目标", "targetCents": 123400,
        })
        assert draft["action"] == "savings_create"
        assert draft["payload"] == {"name": "新目标", "targetCents": 123400}

    async def test_create_with_a_period_carries_both_fields(self, seeded):
        draft = await _draft("savings_create", seeded["ctx"], {
            "name": "新目标", "targetCents": 1000, "periodValue": 3, "periodUnit": "month",
        })
        assert draft["payload"]["periodValue"] == 3
        assert draft["payload"]["periodUnit"] == "month"
        assert "3 个月" in draft["message"]

    async def test_half_a_period_is_refused_before_proposing(self, seeded):
        data = await _failure("savings_create", seeded["ctx"], {
            "name": "新目标", "targetCents": 1000, "periodValue": 3,
        })
        assert data["code"] == T.ERR_INVALID_ARGUMENTS

    async def test_update_carries_the_server_read_version(self, seeded):
        draft = await _draft("savings_update", seeded["ctx"], {
            "savingsName": "旅行基金", "targetCents": 900000,
        })
        assert draft["payload"]["savingsId"] == str(seeded["trip"].id)
        assert draft["payload"]["version"] == seeded["trip"].version

    async def test_a_deposit_goes_out_as_an_increment_not_a_total(self, seeded):
        """**铁律 2**：模型给「存多少」，不给「存完是多少」——累加在服务端做。"""
        draft = await _draft("savings_update", seeded["ctx"], {
            "savingsName": "旅行基金", "addAmountCents": 50000,
        })
        assert draft["payload"]["addAmountCents"] == 50000
        assert "currentCents" not in draft["payload"]

    async def test_setting_and_adding_at_once_is_refused(self, seeded):
        data = await _failure("savings_update", seeded["ctx"], {
            "savingsName": "旅行基金", "addAmountCents": 100, "currentCents": 200,
        })
        assert data["code"] == T.ERR_INVALID_ARGUMENTS

    async def test_update_without_any_change_degrades(self, seeded):
        data = await _failure("savings_update", seeded["ctx"], {"savingsName": "旅行基金"})
        assert data["code"] == T.ERR_INVALID_ARGUMENTS

    async def test_unknown_and_ambiguous_names_are_distinguished(self, seeded):
        unknown = await _failure("savings_update", seeded["ctx"], {"savingsName": "没有这个"})
        assert unknown["code"] == T.ERR_UNKNOWN_SAVINGS
        ambiguous = await _failure("savings_update", seeded["ctx"], {"savingsName": "同名目标"})
        assert ambiguous["code"] == T.ERR_AMBIGUOUS_SAVINGS

    async def test_an_archived_goal_is_unreachable(self, seeded):
        data = await _failure("savings_archive", seeded["ctx"], {"savingsName": "已归档目标"})
        assert data["code"] == T.ERR_UNKNOWN_SAVINGS

    async def test_archive_carries_only_the_id(self, seeded):
        draft = await _draft("savings_archive", seeded["ctx"], {"savingsName": "相机"})
        assert draft["payload"] == {"savingsId": str(seeded["camera"].id)}
        assert draft["warnings"], "归档要让用户看见代价"


# ── 定时域 ─────────────────────────────────────────────────────────────────


class TestScheduledTools:
    async def test_list_is_scoped_to_the_current_book(self, seeded):
        data = _result(await T.invoke_tool("scheduled_list", seeded["ctx"], {}))
        names = {item["name"] for item in data["rules"]}
        assert names == {"房租", "奖金"}, "别的账本 / 已归档的规则不在清单里"
        rent = next(i for i in data["rules"] if i["name"] == "房租")
        assert rent["categoryName"] == "餐饮" and rent["accountName"] == "招行卡"
        assert rent["lastRunDate"] == "2026-09-01" and "id" not in rent

    async def test_create_resolves_the_category_on_the_rules_own_side(self, seeded):
        draft = await _draft("scheduled_create", seeded["ctx"], {
            "name": "年奖", "type": "income", "amountCents": 100000,
            "categoryName": "工资", "repeat": "yearly", "startDate": "2027-01-01",
        })
        assert draft["action"] == "scheduled_create"
        assert draft["payload"]["categoryId"] == str(seeded["salary"].id)
        assert draft["payload"]["repeat"] == "yearly"

    async def test_create_resolves_the_account_when_named(self, seeded):
        draft = await _draft("scheduled_create", seeded["ctx"], {
            "name": "早餐", "type": "expense", "amountCents": 1500,
            "categoryName": "餐饮", "repeat": "daily", "startDate": "2026-10-01",
            "accountName": "招行卡", "runHour": 7,
        })
        assert draft["payload"]["accountId"] == str(seeded["card"].id)
        assert draft["payload"]["runHour"] == 7

    async def test_create_rejects_an_unknown_category(self, seeded):
        data = await _failure("scheduled_create", seeded["ctx"], {
            "name": "早餐", "type": "expense", "amountCents": 1500,
            "categoryName": "没有这个", "repeat": "daily", "startDate": "2026-10-01",
        })
        assert data["code"] == T.ERR_UNKNOWN_CATEGORY

    async def test_update_resolves_the_new_category_on_the_rules_existing_side(self, seeded):
        """改分类名而不给 type 时，按**规则自己的**类型解析（收入规则→收入分类）。

        「餐饮」在收支两侧各有一条：缺了这个 fallback，奖金规则会被改挂到支出
        分类上——而写入路径不会比对类型。
        """
        draft = await _draft("scheduled_update", seeded["ctx"], {
            "ruleName": "奖金", "categoryName": "餐饮",
        })
        assert draft["payload"]["categoryId"] == str(seeded["food_income"].id)

    async def test_update_carries_the_server_read_version(self, seeded):
        draft = await _draft("scheduled_update", seeded["ctx"], {
            "ruleName": "房租", "amountCents": 350000,
        })
        assert draft["payload"]["ruleId"] == str(seeded["rent"].id)
        assert draft["payload"]["version"] == seeded["rent"].version

    async def test_update_can_pause_a_rule(self, seeded):
        draft = await _draft("scheduled_update", seeded["ctx"], {
            "ruleName": "房租", "enabled": False,
        })
        assert draft["payload"]["enabled"] is False
        assert "暂停" in draft["message"]

    async def test_update_without_any_change_degrades(self, seeded):
        data = await _failure("scheduled_update", seeded["ctx"], {"ruleName": "房租"})
        assert data["code"] == T.ERR_INVALID_ARGUMENTS

    async def test_a_rule_in_another_book_is_unreachable(self, seeded):
        data = await _failure("scheduled_archive", seeded["ctx"], {"ruleName": "旅行开销"})
        assert data["code"] == T.ERR_UNKNOWN_SCHEDULED

    async def test_archive_carries_only_the_id(self, seeded):
        draft = await _draft("scheduled_archive", seeded["ctx"], {"ruleName": "奖金"})
        assert draft["payload"] == {"ruleId": str(seeded["bonus"].id)}


# ── 角色域 ─────────────────────────────────────────────────────────────────


class TestPersonaTools:
    async def test_list_returns_the_persona_text_without_ids(self, seeded):
        data = _result(await T.invoke_tool("persona_list", seeded["ctx"], {}))
        by_name = {item["name"]: item for item in data["personas"]}
        assert set(by_name) == {"小贝", "猫娘"}
        assert by_name["猫娘"]["persona"] == "说话带喵"
        assert "id" not in by_name["猫娘"]

    async def test_create_defaults_the_persona_text_to_empty(self, seeded):
        draft = await _draft("persona_create", seeded["ctx"], {"name": "新角色"})
        assert draft["payload"] == {"name": "新角色", "persona": ""}

    async def test_create_keeps_the_user_wording_verbatim(self, seeded):
        draft = await _draft("persona_create", seeded["ctx"], {
            "name": "毒舌", "avatar": "😼", "persona": "说话很直接，但不刻薄",
        })
        assert draft["payload"]["persona"] == "说话很直接，但不刻薄"
        assert draft["payload"]["avatar"] == "😼"

    async def test_update_carries_the_server_read_version(self, seeded):
        draft = await _draft("persona_update", seeded["ctx"], {
            "personaName": "猫娘", "persona": "说话带喵，偶尔傲娇",
        })
        assert draft["payload"]["personaId"] == str(seeded["catgirl"].id)
        assert draft["payload"]["version"] == seeded["catgirl"].version

    async def test_update_without_any_change_degrades(self, seeded):
        data = await _failure("persona_update", seeded["ctx"], {"personaName": "猫娘"})
        assert data["code"] == T.ERR_INVALID_ARGUMENTS

    async def test_archive_carries_only_the_id(self, seeded):
        draft = await _draft("persona_archive", seeded["ctx"], {"personaName": "猫娘"})
        assert draft["payload"] == {"personaId": str(seeded["catgirl"].id)}

    async def test_switch_is_a_device_executed_op(self, seeded):
        """当前角色指针是设备本地的 ``sync_meta`` 键——服务端不执行它。"""
        draft = await _draft("persona_switch", seeded["ctx"], {"personaName": "猫娘"})
        assert draft["action"] == "persona_switch"
        assert draft["payload"] == {"personaId": str(seeded["catgirl"].id)}
        assert "persona_switch" in T.DEVICE_EXECUTED_OPS

    async def test_unknown_and_archived_personas_are_unreachable(self, seeded):
        unknown = await _failure("persona_switch", seeded["ctx"], {"personaName": "没有这个"})
        assert unknown["code"] == T.ERR_UNKNOWN_PERSONA
        archived = await _failure("persona_switch", seeded["ctx"], {"personaName": "已归档角色"})
        assert archived["code"] == T.ERR_UNKNOWN_PERSONA


# ── 注册表：新错误码都要有模型可读的提示 ───────────────────────────────────


def test_every_error_code_has_a_message():
    """安全枚举错误码是回喂模型的东西——漏一个提示，模型就收到一句「工具调用失败」。"""
    codes = {value for name, value in vars(T).items() if name.startswith("ERR_")}
    assert codes <= set(T._TOOL_ERROR_MESSAGES), codes - set(T._TOOL_ERROR_MESSAGES)
