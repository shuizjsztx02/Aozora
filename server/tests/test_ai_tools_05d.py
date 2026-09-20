"""T-M7-05d: 分析 / 导出 / 反馈与隐私 / 安全账号 / 同步五域的工具（11 个新工具）。

验收映射（``docs/milestones/T-M7-05-P4-implementation-plan.md`` §三 05d）：
- 架构 §3.2 中本卡负责的各行逐项可达 → 各测试类
- 五域各 ≤8 个工具、写工具的 op 都是 v2 注册表成员 → ``test_ai_tools.py`` 的注册表契约
- **删除类工具强制确认**（``HIGH_RISK_OPS``）→ ``TestApprovalPolicy``
- 读工具结果**不含 id**（铁律 1）→ 分析 / 导出 / 同意三处
- 设备类 op（同意 / 同步触发）服务端不执行 → ``TestDeviceOps``

这些用例跑真实 SQLite（处理器要查库：统计、导出记录、同意回查），**不经过 HTTP**
——执行面（``/ai/execute``、删号状态机、sync 登记）在 ``test_ai_proposal_v2_05d.py``。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.models.base import Base
from src.models.budget import Budget, BudgetType
from src.models.consent import UserConsent
from src.models.export import Export, ExportFormat, ExportStatus
from src.models.ledger import Account, Book, Category, CategoryKind, Transaction, TxSource, TxType
from src.models.user import DeviceSession, User, UserStatus
from src.modules.ai import tools as T


@pytest_asyncio.fixture()
async def seeded():
    """1 账本 / 2 分类 / 1 账户 / 2026-09 的 3 笔流水 / 1 条预算 / 2 条导出记录 /
    1 台已同意的设备。

    刻意埋进的边界：**另一本账本**（跨账本导出要被拒）、**一条已同意的设备记录**
    （同意回查走 ``device_sessions`` 反查）、**一条别人的导出**（列表只列本人的）。
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    user = User(id=uuid.uuid4(), nickname="05d-tester", status=UserStatus.ACTIVE)
    other_user = User(id=uuid.uuid4(), nickname="别人", status=UserStatus.ACTIVE)
    book = Book(id=uuid.uuid4(), owner_id=user.id, name="日常")
    other_book = Book(id=uuid.uuid4(), owner_id=user.id, name="旅行")

    food = Category(id=uuid.uuid4(), owner_id=user.id, book_id=book.id,
                    kind=CategoryKind.EXPENSE, name="餐饮")
    salary = Category(id=uuid.uuid4(), owner_id=user.id, book_id=book.id,
                      kind=CategoryKind.INCOME, name="工资")
    card = Account(id=uuid.uuid4(), owner_id=user.id, name="招行卡")

    now = datetime.now(UTC)
    txs = [
        Transaction(id=uuid.uuid4(), book_id=book.id, type=TxType.EXPENSE,
                    amount_cents=12_500, category_id=food.id, account_id=card.id,
                    note="打车", biz_date=date(2026, 9, 3), occurred_at=now,
                    source=TxSource.MANUAL),
        Transaction(id=uuid.uuid4(), book_id=book.id, type=TxType.EXPENSE,
                    amount_cents=3_000, category_id=food.id, account_id=card.id,
                    note="午饭", biz_date=date(2026, 9, 4), occurred_at=now,
                    source=TxSource.MANUAL),
        Transaction(id=uuid.uuid4(), book_id=book.id, type=TxType.INCOME,
                    amount_cents=800_000, category_id=salary.id, account_id=card.id,
                    note="九月工资", biz_date=date(2026, 9, 5), occurred_at=now,
                    source=TxSource.MANUAL),
        # 另一本账本的流水：分析工具必须看不见它
        Transaction(id=uuid.uuid4(), book_id=other_book.id, type=TxType.EXPENSE,
                    amount_cents=99_999, category_id=food.id, account_id=card.id,
                    note="旅行开销", biz_date=date(2026, 9, 6), occurred_at=now,
                    source=TxSource.MANUAL),
    ]
    budget = Budget(id=uuid.uuid4(), book_id=book.id, budget_type=BudgetType.MONTHLY,
                    period="2026-09", amount_cents=1_000_000, category_id=None)

    mine = Export(id=uuid.uuid4(), user_id=user.id, book_id=book.id,
                  format=ExportFormat.CSV, status=ExportStatus.COMPLETED,
                  file_size=2048, expires_at=now + timedelta(hours=24))
    # 别人的导出：列表必须只列本人的
    theirs = Export(id=uuid.uuid4(), user_id=other_user.id, book_id=book.id,
                    format=ExportFormat.JSON, status=ExportStatus.COMPLETED)

    session_row = DeviceSession(
        id=uuid.uuid4(), user_id=user.id, device_id="05d-device",
        refresh_token_hash="x" * 64, expires_at=now + timedelta(days=30),
    )
    consent = UserConsent(id=uuid.uuid4(), device_id="05d-device",
                          policy_version="2026-01-01", consented_at=now)

    async with factory() as session:
        session.add_all([
            user, other_user, book, other_book, food, salary, card,
            *txs, budget, mine, theirs, session_row, consent,
        ])
        await session.commit()

        ctx = await T.load_tool_context(session, user.id, book.id)
        yield {
            "ctx": ctx, "db": session, "user": user, "book": book,
            "other_book": other_book, "mine": mine,
        }

    await engine.dispose()


def _result(outcome: T.ToolOutcome) -> dict:
    assert outcome.kind == "result", (outcome.kind, outcome.data)
    return outcome.data


async def _draft(tool: str, ctx: T.ToolContext, params: dict) -> dict:
    outcome = await T.invoke_tool(tool, ctx, params)
    assert outcome.kind == "proposal", (tool, outcome.kind, outcome.data)
    return outcome.data


async def _failure(tool: str, ctx: T.ToolContext, params: dict) -> dict:
    outcome = await T.invoke_tool(tool, ctx, params)
    assert outcome.kind == "error", (tool, outcome.kind, outcome.data)
    return outcome.data


def _no_ids(payload) -> None:
    """递归断言：回喂给模型的结果里没有任何 id 形状的字段。"""
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert "id" not in key.lower(), f"结果里出现了 {key}"
            _no_ids(value)
    elif isinstance(payload, list):
        for item in payload:
            _no_ids(item)


# ── 分析域 ─────────────────────────────────────────────────────────────────


class TestAnalysisTools:
    async def test_monthly_gives_the_deterministic_facts(self, seeded):
        data = _result(await T.invoke_tool(
            "stats_monthly", seeded["ctx"], {"period": "2026-09"},
        ))
        assert data["totalExpenseCents"] == 15_500
        assert data["totalIncomeCents"] == 800_000
        assert data["balanceCents"] == 784_500
        assert data["transactionCount"] == 3
        assert data["expenseByCategory"] == [
            {"name": "餐饮", "totalCents": 15_500, "count": 2},
        ]
        assert data["budget"]["budgetCents"] == 1_000_000
        assert data["budget"]["spentCents"] == 15_500
        assert data["largestExpense"]["amountCents"] == 12_500
        assert data["largestIncome"]["amountCents"] == 800_000

    async def test_monthly_never_leaks_ids(self, seeded):
        """铁律 1：读工具的结果里不含任何 id——分类 / 交易 / 账本的都不行。"""
        data = _result(await T.invoke_tool(
            "stats_monthly", seeded["ctx"], {"period": "2026-09"},
        ))
        _no_ids(data)

    async def test_monthly_sees_only_this_book(self, seeded):
        """另一本账本的 99_999 分不该出现在本账本的统计里。"""
        data = _result(await T.invoke_tool(
            "stats_monthly", seeded["ctx"], {"period": "2026-09"},
        ))
        assert data["totalExpenseCents"] == 15_500

    async def test_monthly_rejects_an_impossible_month(self, seeded):
        data = await _failure("stats_monthly", seeded["ctx"], {"period": "2026-13"})
        assert data["code"] == T.ERR_INVALID_PERIOD

    async def test_trends_fills_the_empty_months(self, seeded):
        data = _result(await T.invoke_tool(
            "stats_trends", seeded["ctx"],
            {"periodFrom": "2026-07", "periodTo": "2026-09"},
        ))
        assert [p["period"] for p in data["points"]] == ["2026-07", "2026-08", "2026-09"]
        assert data["points"][0]["totalExpenseCents"] == 0
        assert data["points"][2]["totalExpenseCents"] == 15_500

    async def test_trends_rejects_a_reversed_range(self, seeded):
        """区间反了要与「月份格式不对」分开报——两者的修法不同。"""
        data = await _failure("stats_trends", seeded["ctx"],
                              {"periodFrom": "2026-09", "periodTo": "2026-07"})
        assert data["code"] == T.ERR_INVALID_PERIOD_RANGE

    async def test_trends_never_leaks_ids(self, seeded):
        data = _result(await T.invoke_tool(
            "stats_trends", seeded["ctx"],
            {"periodFrom": "2026-09", "periodTo": "2026-09"},
        ))
        _no_ids(data)


# ── 导出域 ─────────────────────────────────────────────────────────────────


class TestExportTools:
    async def test_create_fills_the_book_from_the_context(self, seeded):
        """账本由服务端填（模型只选格式）——它永不输出 UUID。"""
        draft = await _draft("export_create", seeded["ctx"], {"format": "csv"})
        assert draft["action"] == "export_create"
        assert draft["payload"] == {
            "bookId": str(seeded["book"].id), "format": "csv",
        }
        assert "日常" in draft["message"]

    async def test_create_rejects_an_unknown_format(self, seeded):
        data = await _failure("export_create", seeded["ctx"], {"format": "xlsx"})
        assert data["code"] == T.ERR_INVALID_ARGUMENTS

    async def test_list_returns_only_my_exports_without_ids(self, seeded):
        data = _result(await T.invoke_tool("export_list", seeded["ctx"], {}))
        assert data["count"] == 1
        row = data["exports"][0]
        assert row["format"] == "csv"
        assert row["status"] == "completed"
        assert row["fileSizeBytes"] == 2048
        _no_ids(data)


# ── 反馈与隐私域 ───────────────────────────────────────────────────────────


class TestPrivacyTools:
    async def test_feedback_carries_the_users_words(self, seeded):
        draft = await _draft("feedback_submit", seeded["ctx"], {
            "category": "bug", "content": "预算页点进去是白屏", "contact": "a@b.c",
        })
        assert draft["action"] == "feedback_submit"
        assert draft["payload"] == {
            "category": "bug", "content": "预算页点进去是白屏", "contact": "a@b.c",
        }

    async def test_feedback_omits_an_absent_contact(self, seeded):
        """用户没留联系方式时，payload 里不该出现一个空字符串字段。"""
        draft = await _draft("feedback_submit", seeded["ctx"], {
            "category": "suggestion", "content": "希望能导出图表",
        })
        assert "contact" not in draft["payload"]

    async def test_feedback_rejects_an_unknown_category(self, seeded):
        data = await _failure("feedback_submit", seeded["ctx"], {
            "category": "praise", "content": "很好用",
        })
        assert data["code"] == T.ERR_INVALID_ARGUMENTS

    async def test_consent_record_is_a_proposal(self, seeded):
        draft = await _draft("consent_record", seeded["ctx"], {})
        assert draft["action"] == "consent_record"
        assert draft["warnings"], "同意是法律表态，卡面上该有提示"

    async def test_consent_status_reads_through_the_device_session(self, seeded):
        """同意记录按设备存，回查要经 ``device_sessions`` 关联到本人。"""
        data = _result(await T.invoke_tool("consent_status", seeded["ctx"], {}))
        assert data["hasConsented"] is True
        assert data["policyVersion"] == "2026-01-01"
        assert data["consentedAt"]

    async def test_consent_status_says_no_when_nothing_is_bound(self, seeded):
        stranger = User(id=uuid.uuid4(), nickname="新用户")
        stranger_book = Book(id=uuid.uuid4(), owner_id=stranger.id, name="陌生账本")
        seeded["db"].add_all([stranger, stranger_book])
        await seeded["db"].commit()

        ctx = await T.load_tool_context(seeded["db"], stranger.id, stranger_book.id)
        data = _result(await T.invoke_tool("consent_status", ctx, {}))
        assert data == {"hasConsented": False}


# ── 安全账号域 ─────────────────────────────────────────────────────────────


class TestSecurityTools:
    async def test_profile_update_proposes_the_new_nickname(self, seeded):
        draft = await _draft("auth_update_profile", seeded["ctx"], {"nickname": "小王"})
        assert draft["action"] == "auth_update_profile"
        assert draft["payload"] == {"nickname": "小王"}

    async def test_profile_update_rejects_a_blank_nickname(self, seeded):
        """``minLength: 1`` 拦不住 ``"   "``——空白昵称要在工具层就现形。"""
        data = await _failure("auth_update_profile", seeded["ctx"], {"nickname": "   "})
        assert data["code"] == T.ERR_INVALID_ARGUMENTS

    async def test_deletion_request_warns_about_the_consequences(self, seeded):
        draft = await _draft("account_deletion_request", seeded["ctx"], {})
        assert draft["action"] == "account_deletion_request"
        assert draft["payload"] == {}
        assert any("7 天" in w for w in draft["warnings"])


# ── 同步域 ─────────────────────────────────────────────────────────────────


class TestSyncTools:
    async def test_status_reports_the_preconditions(self, seeded):
        data = _result(await T.invoke_tool("sync_status", seeded["ctx"], {}))
        assert data["accountStatus"] == "active"
        assert data["consented"] is True
        assert data["canSync"] is True

    async def test_status_reports_an_unconsented_account(self, seeded):
        """没同意隐私政策 → 同步跑不起来（``/sync/push`` 会 403）。"""
        stranger = User(id=uuid.uuid4(), nickname="未同意者")
        stranger_book = Book(id=uuid.uuid4(), owner_id=stranger.id, name="陌生账本")
        seeded["db"].add_all([stranger, stranger_book])
        await seeded["db"].commit()

        ctx = await T.load_tool_context(seeded["db"], stranger.id, stranger_book.id)
        data = _result(await T.invoke_tool("sync_status", ctx, {}))
        assert data["consented"] is False
        assert data["canSync"] is False

    async def test_status_exposes_no_ids(self, seeded):
        data = _result(await T.invoke_tool("sync_status", seeded["ctx"], {}))
        _no_ids(data)

    async def test_trigger_is_a_proposal(self, seeded):
        draft = await _draft("sync_trigger", seeded["ctx"], {})
        assert draft["action"] == "sync_trigger"
        assert draft["payload"] == {}


# ── 审批策略（本卡的关键守护）──────────────────────────────────────────────


class TestApprovalPolicy:
    """删号是**不可自动执行**的：单 Step + 高置信也必须过确认卡。

    ``confirm_reason`` 是纯函数，这里直接对三条理由的**优先级**下断言；loop 里的
    接线（真的出确认卡）在 ``test_ai_proposal_v2_05d.py`` 的端到端用例。
    """

    def test_high_risk_outranks_the_fast_path(self):
        from src.modules.ai.agent import confirm_reason

        assert confirm_reason(
            step=1, confidence=1.0, op="account_deletion_request",
        ) == "high_risk"

    def test_high_risk_outranks_multi_step(self):
        from src.modules.ai.agent import confirm_reason

        reason = confirm_reason(step=3, confidence=1.0, op="account_deletion_request")
        assert reason == "high_risk", "两条理由都在场时，报最严的那条"

    def test_ordinary_ops_keep_the_fast_path(self):
        """05d 的其余 op 不受影响——PR-2 的高置信快速通道不能被本卡击穿。"""
        from src.modules.ai.agent import confirm_reason

        for op in (
            "export_create", "feedback_submit", "auth_update_profile", "transaction_add",
        ):
            assert confirm_reason(step=1, confidence=1.0, op=op) is None, op

    def test_the_op_argument_is_optional(self):
        """不传 op 的老调用方行为逐字不变（fail-open 到旧口径，不是 fail-closed）。"""
        from src.modules.ai.agent import confirm_reason

        assert confirm_reason(step=1, confidence=1.0) is None
        assert confirm_reason(step=1, confidence=0.5) == "low_confidence"

    def test_only_the_deletion_op_is_high_risk(self):
        assert T.HIGH_RISK_OPS == frozenset({"account_deletion_request"})


# ── 注册表增量 ─────────────────────────────────────────────────────────────


class TestRegistryDelta:
    def test_the_five_new_domains_are_declared_and_within_cap(self):
        for domain in ("analysis", "export", "privacy", "security", "sync"):
            assert domain in T.DOMAINS
            count = len([t for t in T.TOOLS if t.domain == domain])
            assert 0 < count <= T.MAX_TOOLS_PER_DOMAIN, domain

    def test_the_device_ops_cover_both_new_ones(self):
        """同意与同步触发的执行位置在设备——服务端只挂起（见 ``tools.py`` 的注释）。"""
        assert "consent_record" in T.DEVICE_EXECUTED_OPS
        assert "sync_trigger" in T.DEVICE_EXECUTED_OPS

    def test_account_delete_is_not_wired_anywhere(self):
        """``account_delete`` 是**刻意**不接线的：凭据不进模型上下文。"""
        from src.modules.ai.authorization import has_authorization_rule
        from src.modules.ai.proposal_ops import EXECUTORS

        assert "account_delete" not in EXECUTORS
        assert not has_authorization_rule("account_delete")
        assert "account_delete" not in {t.proposal_action for t in T.TOOLS}

    def test_reminder_set_is_still_unclaimed(self):
        """提醒行的工具体归 05f 的设备执行器，05d 不越界（计划 §五 挂账项）。"""
        assert "reminder_set" not in {t.proposal_action for t in T.TOOLS}
