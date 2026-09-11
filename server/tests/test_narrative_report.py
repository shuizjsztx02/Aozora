"""T-M4-03b: 叙述生成与数字校验测试。

测试范围：
1. verify_narrative() — 校验 LLM 输出的 data_references 是否与 FactPack 一致
2. build_context_dict() — 从 FactPack 构建 LLM 上下文
3. _find_undeclared_numbers() — 检测叙述中未声明的数字
4. 叙述缓存行为 — 同数据不重复调 LLM、不重复计费（R-M4-09）
"""

from __future__ import annotations

import uuid

import pytest

from src.modules.ai.fact_pack import (
    BudgetFact,
    CategoryFact,
    FactPack,
    TransactionFact,
    TrendFact,
)
from src.modules.ai.narrative import (
    _build_lookup,
    _find_undeclared_numbers,
    build_context_dict,
    verify_narrative,
)

# TestNarrativeCache (R-M4-09) uses the shared async ``db_session`` fixture;
# it must be imported to be in scope (mirrors tests/test_models.py).
from tests.fixtures_db import db_session  # noqa: F401 — re-exported as a fixture

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def sample_fact_pack():
    """创建测试用的事实包，包含完整的数据结构。"""
    return FactPack(
        book_id="12345678-1234-5678-1234-567812345678",
        period="2024-06",
        total_income=1000000,  # 10000元
        total_expense=500000,  # 5000元
        balance=500000,  # 5000元
        transaction_count=42,
        expense_by_category=[
            CategoryFact("cat-1", "餐饮", 200000, 15),
            CategoryFact("cat-2", "交通", 150000, 8),
        ],
        income_by_category=[
            CategoryFact("cat-3", "工资", 1000000, 1),
        ],
        budget=BudgetFact(
            amount_cents=600000,  # 预算6000元
            spent_cents=500000,   # 已花5000元
            remaining_cents=100000,  # 剩余1000元
            pct_used=83.3,
        ),
        largest_expense=TransactionFact(
            id="tx-1",
            amount_cents=30000,  # 300元
            category_name="餐饮",
            note="聚餐",
            biz_date="2024-06-15",
        ),
        largest_income=TransactionFact(
            id="tx-2",
            amount_cents=1000000,  # 10000元
            category_name="工资",
            note="6月工资",
            biz_date="2024-06-01",
        ),
        trend=[
            TrendFact("2024-04", 950000, 450000, 500000, 40),
            TrendFact("2024-05", 950000, 480000, 470000, 38),
            TrendFact("2024-06", 1000000, 500000, 500000, 42),
        ],
        data_hash="abc123",
    )


# ── Test verify_narrative() ─────────────────────────────────────────────────


class TestNarrativeVerification:
    """测试 verify_narrative() 函数"""

    def test_valid_references_pass(self, sample_fact_pack):
        """正确的 data_references 通过校验"""
        llm_output = {
            # Use narrative without specific numbers to avoid unit mismatch
            # (narrative says 元 while fact pack stores 分).
            # The primary check is data_references.
            "narrative": "本月收支情况良好，结余充足。",
            "data_references": [
                {"fact": "total_income", "value": 1000000},
                {"fact": "total_expense", "value": 500000},
                {"fact": "balance", "value": 500000},
            ],
        }
        errors = verify_narrative(llm_output, sample_fact_pack)
        assert len(errors) == 0

    def test_invalid_value_rejected(self, sample_fact_pack):
        """错误的值被拒绝"""
        llm_output = {
            "narrative": "",
            "data_references": [
                {"fact": "total_income", "value": 9999},  # 错误的值
            ],
        }
        errors = verify_narrative(llm_output, sample_fact_pack)
        assert len(errors) > 0
        assert any("value mismatch" in err for err in errors)

    def test_unknown_fact_key_rejected(self, sample_fact_pack):
        """不存在的 fact key 被拒绝"""
        llm_output = {
            "narrative": "",
            "data_references": [
                {"fact": "unknown_fact", "value": 10000},
            ],
        }
        errors = verify_narrative(llm_output, sample_fact_pack)
        assert len(errors) > 0
        assert any("Unknown fact key" in err for err in errors)

    def test_budget_references(self, sample_fact_pack):
        """预算相关的引用"""
        llm_output = {
            # Narrative without raw numbers — primary check is data_references
            "narrative": "本月预算控制良好，使用率达标。",
            "data_references": [
                {"fact": "budget_amount", "value": 600000},
                {"fact": "budget_spent", "value": 500000},
                {"fact": "budget_remaining", "value": 100000},
                {"fact": "budget_pct", "value": 83.3},
            ],
        }
        errors = verify_narrative(llm_output, sample_fact_pack)
        assert len(errors) == 0

    def test_multiple_invalid_references(self, sample_fact_pack):
        """多个错误的引用"""
        llm_output = {
            "narrative": "",
            "data_references": [
                {"fact": "total_income", "value": 8888},
                {"fact": "total_expense", "value": 7777},
            ],
        }
        errors = verify_narrative(llm_output, sample_fact_pack)
        assert len(errors) == 2

    def test_empty_references_pass(self, sample_fact_pack):
        """空的 data_references 通过校验（无数字引用）"""
        llm_output = {
            "narrative": "本月消费情况良好",
            "data_references": [],
        }
        errors = verify_narrative(llm_output, sample_fact_pack)
        assert len(errors) == 0


# ── Test _find_undeclared_numbers() ──────────────────────────────────────────


class TestUndeclaredNumbers:
    """测试 _find_undeclared_numbers() 函数"""

    def test_no_undeclared_numbers(self, sample_fact_pack):
        """所有数字都已声明（叙述中不含任何三位以上数字）"""
        lookup = _build_lookup(sample_fact_pack)
        # Use narrative without numbers to test the clean case
        narrative = "本月收支平衡，情况良好"
        declared_refs = [
            {"fact": "total_income", "value": 1000000},
            {"fact": "total_expense", "value": 500000},
        ]
        undeclared = _find_undeclared_numbers(narrative, lookup, declared_refs)
        assert len(undeclared) == 0

    def test_undeclared_numbers_detected(self, sample_fact_pack):
        """检测到未声明的数字"""
        lookup = _build_lookup(sample_fact_pack)
        narrative = "本月额外数字9999"
        declared_refs = [
            {"fact": "total_income", "value": 1000000},
        ]
        undeclared = _find_undeclared_numbers(narrative, lookup, declared_refs)
        assert 9999 in undeclared

    def test_small_numbers_ignored(self, sample_fact_pack):
        """小于100的数字被忽略"""
        lookup = _build_lookup(sample_fact_pack)
        narrative = "本月交易42笔，温度35度"
        declared_refs = []
        undeclared = _find_undeclared_numbers(narrative, lookup, declared_refs)
        # 42和35都小于100，应该被忽略
        assert len(undeclared) == 0


# ── Test build_context_dict() ────────────────────────────────────────────────


class TestContextBuilder:
    """测试 build_context_dict() 函数"""

    def test_basic_context_fields(self, sample_fact_pack):
        """基础上下文字段"""
        context = build_context_dict(sample_fact_pack)

        assert "total_income" in context
        assert "total_expense" in context
        assert "balance" in context
        assert "transaction_count" in context

        assert context["total_income"] == 1000000
        assert context["total_expense"] == 500000
        assert context["balance"] == 500000
        assert context["transaction_count"] == 42

    def test_top_category_context(self, sample_fact_pack):
        """Top分类上下文"""
        context = build_context_dict(sample_fact_pack)

        assert "top_expense_category" in context
        assert "top_expense_amount" in context
        assert context["top_expense_category"] == "餐饮"
        assert context["top_expense_amount"] == 200000

    def test_budget_context(self, sample_fact_pack):
        """预算上下文"""
        context = build_context_dict(sample_fact_pack)

        assert "budget_amount" in context
        assert "budget_spent" in context
        assert "budget_remaining" in context
        assert "budget_pct" in context

        assert context["budget_amount"] == 600000
        assert context["budget_spent"] == 500000
        assert context["budget_remaining"] == 100000
        assert context["budget_pct"] == 83.3

    def test_largest_transaction_context(self, sample_fact_pack):
        """最大交易上下文"""
        context = build_context_dict(sample_fact_pack)

        assert "largest_expense_category" in context
        assert "largest_expense_amount" in context
        assert "largest_expense_note" in context
        assert "largest_income_category" in context
        assert "largest_income_amount" in context

        assert context["largest_expense_category"] == "餐饮"
        assert context["largest_expense_amount"] == 30000
        assert context["largest_expense_note"] == "聚餐"

    def test_trend_context(self, sample_fact_pack):
        """趋势上下文"""
        context = build_context_dict(sample_fact_pack)

        assert "last_month_expense" in context
        assert "last_month_income" in context
        assert "trend_avg_expense" in context
        assert "trend_direction" in context

        assert context["last_month_expense"] == 480000  # 2024-05
        assert context["last_month_income"] == 950000
        assert context["trend_direction"] == "stable"  # 500000 vs 480000

    def test_no_budget_context(self):
        """无预算时不生成预算上下文"""
        fp = FactPack(
            book_id="test",
            period="2024-06",
            total_income=1000000,
            total_expense=500000,
            balance=500000,
            transaction_count=10,
            expense_by_category=[],
            income_by_category=[],
            budget=None,  # 无预算
            largest_expense=None,
            largest_income=None,
            trend=[],
            data_hash="xyz",
        )
        context = build_context_dict(fp)

        assert "budget_amount" not in context
        assert "budget_spent" not in context
        assert "largest_expense_category" not in context


# ── Test narrative cache (R-M4-09 fix) ──────────────────────────────────────


class MockProvider:
    """Mock LLM provider for testing."""

    def __init__(self, response_content: str = ""):
        self.response_content = response_content
        self.call_count = 0

    @property
    def name(self) -> str:
        return "mock"

    async def complete(self, messages, model=None, max_tokens=None, timeout=None):
        from src.modules.ai.provider import ProviderResult
        self.call_count += 1
        return ProviderResult(
            content=self.response_content,
            prompt_tokens=100,
            completion_tokens=50,
        )


@pytest.fixture()
def mock_provider():
    """Create a mock provider with valid narrative output."""
    # Create a response that passes number verification
    # Use minimal narrative without specific numbers to avoid verification issues
    response = {
        "narrative": "本月收支情况良好，结余充足。",
        "data_references": [
            {"fact": "total_income", "value": 1000000},
            {"fact": "total_expense", "value": 500000},
        ],
    }
    import json
    return MockProvider(json.dumps(response, ensure_ascii=False))


class TestNarrativeCache:
    """Test narrative caching behavior (R-M4-09)."""

    @pytest.mark.asyncio
    async def test_same_data_uses_cache(
        self, sample_fact_pack, mock_provider, db_session,
    ):
        """Same data_hash should use cached narrative on second request."""
        from src.models.user import User, UserStatus
        from src.modules.ai.fact_pack import FactPackService
        from src.modules.ai.schemas import AIReportRequest
        from src.modules.ai.service import AIService, _narrative_cache_clear

        # Clear cache before test
        _narrative_cache_clear()

        # Create a test user
        user = User(
            id=uuid.uuid4(),
            phone="13800138001",
            nickname="test-user",
            status=UserStatus.ACTIVE,
            timezone="UTC",
        )
        db_session.add(user)
        await db_session.flush()

        # Mock FactPackService.build to return our sample_fact_pack
        original_build = FactPackService.build

        async def mock_build(self_service, book_id, user_id, period):
            # Override to return our test fact pack without DB queries
            return sample_fact_pack

        FactPackService.build = mock_build

        try:
            service = AIService(db_session, mock_provider)
            request = AIReportRequest(
                book_id=sample_fact_pack.book_id,
                period=sample_fact_pack.period,
            )

            # First request: should call provider
            response1 = await service.generate_report(user, "req-1", request)
            assert response1.narrative is not None
            assert response1.narrative_cached is False
            assert response1.usage is not None
            assert mock_provider.call_count == 1

            # Second request with same data: should use cache
            response2 = await service.generate_report(user, "req-2", request)
            assert response2.narrative is not None
            assert response2.narrative_cached is True
            assert response2.usage is None  # No new LLM call
            assert mock_provider.call_count == 1  # Provider not called again

        finally:
            # Restore original method
            FactPackService.build = original_build
            _narrative_cache_clear()

    @pytest.mark.asyncio
    async def test_different_data_calls_provider(
        self, sample_fact_pack, mock_provider, db_session,
    ):
        """Different data_hash should call provider again."""
        from src.models.user import User, UserStatus
        from src.modules.ai.fact_pack import FactPackService
        from src.modules.ai.schemas import AIReportRequest
        from src.modules.ai.service import AIService, _narrative_cache_clear

        _narrative_cache_clear()

        user = User(
            id=uuid.uuid4(),
            phone="13800138002",
            nickname="test-user-2",
            status=UserStatus.ACTIVE,
            timezone="UTC",
        )
        db_session.add(user)
        await db_session.flush()

        # Track which fact pack to return
        call_counter = [0]
        original_build = FactPackService.build

        async def mock_build(self_service, book_id, user_id, period):
            call_counter[0] += 1
            if call_counter[0] == 1:
                return sample_fact_pack
            else:
                # Return a different fact pack (different hash)
                return FactPack(
                    book_id=sample_fact_pack.book_id,
                    period=sample_fact_pack.period,
                    total_income=2000000,  # Different amount
                    total_expense=sample_fact_pack.total_expense,
                    balance=1500000,
                    transaction_count=sample_fact_pack.transaction_count,
                    expense_by_category=sample_fact_pack.expense_by_category,
                    income_by_category=sample_fact_pack.income_by_category,
                    budget=sample_fact_pack.budget,
                    largest_expense=sample_fact_pack.largest_expense,
                    largest_income=sample_fact_pack.largest_income,
                    trend=sample_fact_pack.trend,
                    data_hash="different-hash",
                )

        FactPackService.build = mock_build

        try:
            service = AIService(db_session, mock_provider)
            request = AIReportRequest(
                book_id=sample_fact_pack.book_id,
                period=sample_fact_pack.period,
            )

            # First request
            response1 = await service.generate_report(user, "req-1", request)
            assert response1.narrative_cached is False
            assert mock_provider.call_count == 1

            # Second request with different data: should call provider again
            response2 = await service.generate_report(user, "req-2", request)
            assert response2.narrative_cached is False
            assert mock_provider.call_count == 2

        finally:
            FactPackService.build = original_build
            _narrative_cache_clear()


# ── Test R-M4-10: usage recorded on verification failure ───────────────────


class TestUsageOnVerificationFailure:
    """R-M4-10: tokens consumed must be recorded even when narrative fails verification."""

    @pytest.mark.asyncio
    async def test_usage_recorded_when_verification_fails(
        self, sample_fact_pack, db_session,
    ):
        """Provider returns bad numbers → narrative discarded but AIUsage still recorded."""
        from src.models.ai import AIUsage
        from src.models.user import User, UserStatus
        from src.modules.ai.fact_pack import FactPackService
        from src.modules.ai.schemas import AIReportRequest
        from src.modules.ai.service import AIService, _narrative_cache_clear

        _narrative_cache_clear()

        # Provider returns wrong numbers → verification will fail
        bad_response = {
            "narrative": "本月收入高达9999元",
            "data_references": [
                {"fact": "total_income", "value": 9999},  # Wrong!
            ],
        }
        import json
        bad_provider = MockProvider(json.dumps(bad_response, ensure_ascii=False))

        user = User(
            id=uuid.uuid4(),
            phone="13800138010",
            nickname="rm410-user",
            status=UserStatus.ACTIVE,
            timezone="UTC",
        )
        db_session.add(user)
        await db_session.flush()

        original_build = FactPackService.build

        async def mock_build(self_service, book_id, user_id, period):
            return sample_fact_pack

        FactPackService.build = mock_build

        try:
            service = AIService(db_session, bad_provider)
            request = AIReportRequest(
                book_id=sample_fact_pack.book_id,
                period=sample_fact_pack.period,
            )

            response = await service.generate_report(user, "req-rm410", request)

            # Narrative should be discarded (verification failed)
            assert response.narrative is None
            # But provider WAS called
            assert bad_provider.call_count == 1
            # And usage WAS recorded (R-M4-10 fix)
            usage_rows = await db_session.execute(
                __import__("sqlalchemy").select(AIUsage).where(
                    AIUsage.user_id == user.id,
                    AIUsage.request_id == "req-rm410",
                )
            )
            assert usage_rows.scalars().first() is not None, \
                "AIUsage must be recorded even when verification fails"

        finally:
            FactPackService.build = original_build
            _narrative_cache_clear()


    @pytest.mark.asyncio
    async def test_usage_recorded_when_invalid_json_exhausts_retries(
        self, sample_fact_pack, db_session,
    ):
        """Provider returns invalid JSON on every attempt → usage still recorded."""
        from src.models.ai import AIUsage
        from src.models.user import User, UserStatus
        from src.modules.ai.fact_pack import FactPackService
        from src.modules.ai.provider import ProviderResult
        from src.modules.ai.schemas import AIReportRequest
        from src.modules.ai.service import MAX_ATTEMPTS, AIService, _narrative_cache_clear

        _narrative_cache_clear()

        class InvalidJsonProvider:
            name = "mock-invalid"
            call_count = 0

            async def complete(self, messages, *, model, max_tokens, timeout):
                self.call_count += 1
                return ProviderResult("not valid json", prompt_tokens=100, completion_tokens=50)

        provider = InvalidJsonProvider()

        user = User(
            id=uuid.uuid4(),
            phone="13800138011",
            nickname="rm410-inj-user",
            status=UserStatus.ACTIVE,
            timezone="UTC",
        )
        db_session.add(user)
        await db_session.flush()

        original_build = FactPackService.build

        async def mock_build(self_service, book_id, user_id, period):
            return sample_fact_pack

        FactPackService.build = mock_build

        try:
            service = AIService(db_session, provider)
            request = AIReportRequest(
                book_id=sample_fact_pack.book_id,
                period=sample_fact_pack.period,
            )

            response = await service.generate_report(user, "req-rm410-inj", request)

            # Narrative should be discarded
            assert response.narrative is None
            # Provider was called MAX_ATTEMPTS times
            assert provider.call_count == MAX_ATTEMPTS
            # Usage WAS recorded — all attempts consumed tokens (R-M4-10 fix)
            usage_row = (await db_session.execute(
                __import__("sqlalchemy").select(AIUsage).where(
                    AIUsage.user_id == user.id,
                    AIUsage.request_id == "req-rm410-inj",
                )
            )).scalars().first()
            assert usage_row is not None, \
                "AIUsage must be recorded when invalid_json retries are exhausted"
            # Total tokens = MAX_ATTEMPTS × (100 + 50)
            expected_tokens = MAX_ATTEMPTS * (100 + 50)
            assert usage_row.tokens == expected_tokens, \
                f"Expected {expected_tokens} tokens, got {usage_row.tokens}"

        finally:
            FactPackService.build = original_build
            _narrative_cache_clear()


# ── Test T13-2: 0 笔交易跳过 narrative 调用（决策 #5）──────────────────────


class TestZeroTransactionsSkipNarrative:
    """T13-2 (REQ-46 / 决策 #5): transaction_count==0 → 不调 provider，
    直接返回事实包（空数据前端已有 showSyncHint 分支承接）。"""

    @pytest.mark.asyncio
    async def test_zero_transactions_skips_provider(
        self, sample_fact_pack, mock_provider, db_session,
    ):
        import dataclasses

        from src.models.user import User, UserStatus
        from src.modules.ai.fact_pack import FactPackService
        from src.modules.ai.schemas import AIReportRequest
        from src.modules.ai.service import AIService, _narrative_cache_clear

        _narrative_cache_clear()

        empty_fp = dataclasses.replace(sample_fact_pack, transaction_count=0)

        user = User(
            id=uuid.uuid4(),
            phone="13800138020",
            nickname="t132-user",
            status=UserStatus.ACTIVE,
            timezone="UTC",
        )
        db_session.add(user)
        await db_session.flush()

        original_build = FactPackService.build

        async def mock_build(self_service, book_id, user_id, period):
            return empty_fp

        FactPackService.build = mock_build

        try:
            service = AIService(db_session, mock_provider)
            request = AIReportRequest(
                book_id=sample_fact_pack.book_id,
                period=sample_fact_pack.period,
            )

            response = await service.generate_report(user, "req-t132", request)

            # Provider 完全未被调用（省 token）
            assert mock_provider.call_count == 0
            # 无叙述、无计费
            assert response.narrative is None
            assert response.usage is None
            # 事实包照常返回
            assert response.transaction_count == 0
            assert response.total_income == sample_fact_pack.total_income
            assert response.data_hash == sample_fact_pack.data_hash
        finally:
            FactPackService.build = original_build
            _narrative_cache_clear()

    @pytest.mark.asyncio
    async def test_nonzero_transactions_still_calls_provider(
        self, sample_fact_pack, mock_provider, db_session,
    ):
        """对照组：有交易数据时 provider 正常被调用（42 笔样本包）。"""
        from src.models.user import User, UserStatus
        from src.modules.ai.fact_pack import FactPackService
        from src.modules.ai.schemas import AIReportRequest
        from src.modules.ai.service import AIService, _narrative_cache_clear

        _narrative_cache_clear()

        user = User(
            id=uuid.uuid4(),
            phone="13800138021",
            nickname="t132-ctl-user",
            status=UserStatus.ACTIVE,
            timezone="UTC",
        )
        db_session.add(user)
        await db_session.flush()

        original_build = FactPackService.build

        async def mock_build(self_service, book_id, user_id, period):
            return sample_fact_pack

        FactPackService.build = mock_build

        try:
            service = AIService(db_session, mock_provider)
            request = AIReportRequest(
                book_id=sample_fact_pack.book_id,
                period=sample_fact_pack.period,
            )

            response = await service.generate_report(user, "req-t132-ctl", request)

            assert mock_provider.call_count == 1
            assert response.narrative is not None
        finally:
            FactPackService.build = original_build
            _narrative_cache_clear()


# ── Test R-M4-15: rate limit → 200 with null narrative ────────────────────


class TestRateLimitDegradation:
    """R-M4-15: rate limit hit returns 200 with null narrative, not 429."""

    @pytest.mark.asyncio
    async def test_rate_limit_returns_200_with_null_narrative(
        self, sample_fact_pack, db_session,
    ):
        """When rate limit triggers, response is 200 fact-pack-only (narrative=null)."""
        from src.models.user import User, UserStatus
        from src.modules.ai.fact_pack import FactPackService
        from src.modules.ai.schemas import AIReportRequest
        from src.modules.ai.service import (
            AIService,
            _ai_rate_limits,
            _narrative_cache_clear,
        )

        _narrative_cache_clear()
        _ai_rate_limits.clear()

        user = User(
            id=uuid.uuid4(),
            phone="13800138015",
            nickname="rm415-user",
            status=UserStatus.ACTIVE,
            timezone="UTC",
        )
        db_session.add(user)
        await db_session.flush()

        original_build = FactPackService.build

        async def mock_build(self_service, book_id, user_id, period):
            return sample_fact_pack

        FactPackService.build = mock_build

        # Use a provider that should NOT be called (rate limit blocks it)
        should_not_call_provider = MockProvider("{}")

        try:
            from src.common.config import get_config
            cfg = get_config()

            service = AIService(db_session, should_not_call_provider, cfg)
            request = AIReportRequest(
                book_id=sample_fact_pack.book_id,
                period=sample_fact_pack.period,
            )

            # Pre-fill the rate limit bucket to trigger limit
            import time as _time
            _ai_rate_limits[user.id] = [
                _time.time() for _ in range(cfg.ai_rate_limit_per_minute + 1)
            ]

            response = await service.generate_report(user, "req-rm415", request)

            # Must return fact pack data (not 429)
            assert response.total_income == sample_fact_pack.total_income
            assert response.total_expense == sample_fact_pack.total_expense
            # Narrative must be null (rate limit skipped LLM call)
            assert response.narrative is None
            assert response.narrative_cached is False
            assert response.usage is None
            # Provider was NOT called
            assert should_not_call_provider.call_count == 0

        finally:
            FactPackService.build = original_build
            _ai_rate_limits.clear()
            _narrative_cache_clear()
