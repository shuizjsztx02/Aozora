"""T21-2（REQ-83）：自动记账解析端点 `/ai/parse-notification` 与形状消毒的用例。

验收映射（`docs/人工测试和需求变更-v3.md` T21-2）：
- 四类「不是消费」（退款 / 转账含信用卡还款 / 理财基金买入 / 红包收发）不得记成
  消费 → ``TestNormalizeCaptureOutput`` + ``TestCaptureParseEndpoint.test_not_transaction_*``
  + ``TestCapturePromptContract``（提示词里的四类规则守护）；
- 各来源语料的正例 → ``CAPTURE_CORPUS`` 驱动的形状契约用例；
- 字段缺失时的兜底（宁低置信、不猜字段）→ ``test_missing_kind_falls_back_to_low_confidence``；
- 成本与配额（计入 ``ai_usage``、guest 日配额照常生效）→ ``TestCaptureParseLimits``。

**这里测的是形状与服务端不变量**，不是模型的识别能力——模型能不能把「信用卡还款」
判成非消费由 `scripts/probe_autobook_text_parse.py` 的真实调用实测（判据先行的
现场数字），单测用 FakeProvider 喂**模型可能的输出**（含脏形态），钉死「客户端拿到
的永远是合法形状」。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.testclient import TestClient

from src.common.config import Settings, reset_config, set_config
from src.common.database import get_db_session
from src.models.ai import AIUsage

# Ensure models are registered with Base.metadata before tables are created
from src.models.attachment import Attachment  # noqa: F401
from src.models.base import Base
from src.models.ledger import Account, Book, Category  # noqa: F401
from src.modules.ai.prompts import CAPTURE_PARSE, CAPTURE_PARSE_TASK, REGISTRY
from src.modules.ai.provider import (
    ProviderError,
    ProviderResult,
    get_llm_provider,
    reset_llm_provider,
)
from src.modules.ai.service import _ai_rate_limits, normalize_capture_output

PROMPT_TOKENS = 200
COMPLETION_TOKENS = 60

#: 两条通道 × 五个来源的代表性语料（与探针脚本同源同形）。这里只用它的**文本**
#: 断言「文本原样进 user turn」与形状契约，不依赖模型识别。
CAPTURE_CORPUS = [
    ("accessibility", "支付成功\n¥32.00\n星巴克(北京国贸店)\n零钱\n账单详情\n完成"),
    ("notification", "[微信支付] 已付款 ¥32.00\n收款方：星巴克(北京国贸店)\n时间：2026-10-13 12:30:15"),
    ("notification", "支付宝\n支付成功 32.00元\n付款给 星巴克咖啡\n付款方式：余额宝"),
    ("accessibility", "账单详情\n-32.00\n交易成功\n星巴克咖啡\n订单号 2026101322001234567890"),
    ("notification", "云闪付\n您尾号1234的银联卡于10月13日12:30在星巴克(北京国贸店)消费人民币32.00元。"),
    ("notification", "招商银行\n您尾号1234的储蓄卡10月13日12:30快捷支付支出人民币32.00元，余额12,345.67元。"),
    ("notification", "【中国工商银行】您尾号5678的储蓄卡10月13日12:30消费人民币32.00元，余额8,765.43元。"),
]


# ── Fakes & fixtures ────────────────────────────────────────────────────

class FakeProvider:
    """In-process stand-in for the text provider (FIFO results, then default)."""

    name = "fake"

    def __init__(self, results=None, default=None):
        self.results = list(results or [])
        self.default = default
        self.calls: list[dict] = []

    async def complete(self, messages, *, model, max_tokens, timeout):
        self.calls.append({"messages": messages, "model": model,
                           "max_tokens": max_tokens, "timeout": timeout})
        if self.results:
            item = self.results.pop(0)
        elif self.default is not None:
            item = self.default
        else:
            raise AssertionError("FakeProvider exhausted")
        if isinstance(item, ProviderError):
            raise item
        return item


class CaptureEnv:
    def __init__(self, client, factory, provider):
        self.client = client
        self.factory = factory
        self.provider = provider


def _result(output: dict) -> ProviderResult:
    return ProviderResult(json.dumps(output, ensure_ascii=False),
                          PROMPT_TOKENS, COMPLETION_TOKENS)


@pytest.fixture()
def capture_env():
    """TestClient with isolated file DB, FakeProvider, cleared limiters."""
    from src.main import app
    from src.modules.auth.dependencies import _rate_limits

    _rate_limits.clear()
    _ai_rate_limits.clear()
    reset_llm_provider()
    reset_config()

    db_dir = Path(tempfile.mkdtemp(prefix="aozora_test_db_"))
    # 文件库而不是 `:memory:`：内存库让 database.py 选 StaticPool，唯一一条连接
    # 跨多个短命 asyncio 循环复用且从不释放，aiosqlite 工作线程会在旧 loop 关闭后
    # 回投结果并抛 `RuntimeError: Event loop is closed`，被 filterwarnings=["error"]
    # 记到无关用例上（2026-09-18 定位；同 test_ai_gateway / test_image_parse）。
    cfg = Settings(
        database_url=f"sqlite+aiosqlite:///{(db_dir / 'test.db').as_posix()}",
        secret_key="test-secret",
        env="test",
        api_prefix="",
        # 密封 ambient .env（本机联调把 guest 配额临时提到 100000，测试面必须钉回 20）
        ai_provider_api_key=None,
        ai_agent_provider_api_key=None,
        ai_daily_quota_guest=20,
        ai_rate_limit_per_minute=10,
    )
    set_config(cfg)

    engine = create_async_engine(cfg.database_url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _create_tables():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_tables())

    async def _override_get_db_session():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    provider = FakeProvider(default=_result({
        "kind": "transaction", "type": "expense", "amountCents": 3200,
        "category": "餐饮", "note": "星巴克", "bizDate": "2026-10-13",
        "confidence": 0.95,
    }))
    app.dependency_overrides[get_db_session] = _override_get_db_session
    app.dependency_overrides[get_llm_provider] = lambda: provider

    with TestClient(app) as c:
        yield CaptureEnv(c, factory, provider)

    asyncio.run(engine.dispose())
    shutil.rmtree(db_dir, ignore_errors=True)
    app.dependency_overrides.clear()
    _rate_limits.clear()
    _ai_rate_limits.clear()
    reset_llm_provider()


def _guest(env: CaptureEnv, device_id: str = "capture-device-1") -> dict:
    resp = env.client.post("/auth/guest", json={"device_id": device_id})
    assert resp.status_code == 200
    data = resp.json()
    return {"id": data["user_id"], "token": data["tokens"]["access_token"]}


def _auth(user: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {user['token']}"}


def _parse(
    env: CaptureEnv,
    user: dict,
    text: str = "支付成功\n¥32.00\n星巴克(北京国贸店)",
    channel: str = "notification",
    context: dict | None = None,
):
    body: dict = {"text": text, "source_channel": channel}
    if context is not None:
        body["context"] = context
    return env.client.post("/ai/parse-notification", json=body, headers=_auth(user))


def _seed_usage(env: CaptureEnv, user_id: str, count: int) -> None:
    async def _run():
        async with env.factory() as session:
            for _ in range(count):
                session.add(AIUsage(
                    user_id=uuid.UUID(user_id), provider="fake",
                    model="qwen3.7-flash", tokens=100, cost=0,
                    request_id="seed",
                    created_at=datetime.now(UTC) - timedelta(minutes=1),
                ))
            await session.commit()

    asyncio.run(_run())


def _fetch_usage(env: CaptureEnv, user_id: str) -> list[AIUsage]:
    async def _run():
        async with env.factory() as session:
            rows = await session.scalars(
                select(AIUsage).where(
                    AIUsage.user_id == uuid.UUID(user_id),
                    AIUsage.request_id != "seed",
                )
            )
            return list(rows)

    return asyncio.run(_run())


# ── 形状消毒（纯函数）────────────────────────────────────────────────────

class TestNormalizeCaptureOutput:
    def test_transaction_passes_through(self):
        out = normalize_capture_output({
            "kind": "transaction", "type": "expense", "amountCents": 3200,
            "category": "餐饮", "note": "星巴克(北京国贸店)",
            "bizDate": "2026-10-13", "confidence": 0.95,
        })
        assert out == {
            "kind": "transaction", "reason": "", "type": "expense",
            "amountCents": 3200, "category": "餐饮",
            "note": "星巴克(北京国贸店)", "bizDate": "2026-10-13",
            "confidence": 0.95,
        }

    @pytest.mark.parametrize(
        ("reason", "text"),
        [
            ("refund", "退款到账 ¥32.00"),
            ("transfer", "已转账 ¥500.00 收款方：张三"),
            ("credit_card_repayment", "信用卡还款成功 2,000.00元"),
            ("investment", "余额宝 转入成功 1,000.00元"),
            ("red_packet", "已发出红包 ¥8.80"),
        ],
    )
    def test_not_transaction_clears_money_fields(self, reason, text):
        """四类「不是消费」：即使模型同时给了金额，也一律清空（不得记成消费）。"""
        out = normalize_capture_output({
            "kind": "not_transaction", "reason": reason, "type": "expense",
            "amountCents": 3200, "category": "餐饮", "note": text,
            "bizDate": "2026-10-13", "confidence": 0.9,
        })
        assert out["kind"] == "not_transaction"
        assert out["reason"] == reason
        assert out["amountCents"] == 0
        assert out["type"] == "" and out["category"] == "" and out["note"] == ""
        assert out["bizDate"] == ""
        # 置信度保留：它描述的是「这次判定有多可信」，不是金额的可信度
        assert out["confidence"] == 0.9

    def test_unknown_reason_falls_back_to_other(self):
        out = normalize_capture_output({"kind": "not_transaction", "reason": "whatever"})
        assert out["reason"] == "other"

    def test_missing_kind_falls_back_to_low_confidence_transaction(self):
        """坏形状→低置信的待确认，而不是 not_transaction（丢弃）。

        「丢弃」是 R-PROD 系列反复出现的丢账形态（REQ-72）；坏输出必须能被用户
        看到（低置信进待确认箱），所以这个回退方向是刻意的。
        """
        out = normalize_capture_output({"type": "expense", "amountCents": 3200,
                                        "confidence": 0.99})
        assert out["kind"] == "transaction"
        assert out["confidence"] == 0.0
        assert out["amountCents"] == 3200

    def test_negative_amount_is_absolutized(self):
        """账单详情页的「-32.00」：方向由 type 表达，金额恒为非负整数分（NFR-7）。"""
        out = normalize_capture_output({"kind": "transaction", "type": "expense",
                                        "amountCents": -3200, "confidence": 0.9})
        assert out["amountCents"] == 3200

    def test_bad_types_are_emptied(self):
        out = normalize_capture_output({
            "kind": "transaction", "type": "支出", "amountCents": "32.00",
            "category": None, "note": 123, "bizDate": "10月13日",
            "confidence": "0.9",
        })
        assert out["type"] == "" and out["category"] == "" and out["note"] == ""
        assert out["amountCents"] == 0 and out["bizDate"] == ""
        assert out["confidence"] == 0.0

    def test_confidence_is_clamped(self):
        assert normalize_capture_output(
            {"kind": "transaction", "confidence": 1.8})["confidence"] == 1.0
        assert normalize_capture_output(
            {"kind": "transaction", "confidence": -0.5})["confidence"] == 0.0
        assert normalize_capture_output(
            {"kind": "transaction", "confidence": float("nan")})["confidence"] == 0.0

    def test_long_note_is_truncated(self):
        """模型照抄整段文本时的护栏（T21-2 第一轮实测出现过）。"""
        out = normalize_capture_output(
            {"kind": "transaction", "note": "长" * 300, "confidence": 0.9})
        assert len(out["note"]) == 100

    def test_non_dict_output_is_safe(self):
        out = normalize_capture_output(None)
        assert out["kind"] == "transaction"
        assert out["amountCents"] == 0 and out["confidence"] == 0.0


# ── 提示词契约（守护四类规则不被后人删掉）───────────────────────────────

class TestCapturePromptContract:
    def test_registered_under_its_own_task_id(self):
        assert CAPTURE_PARSE_TASK == "capture_parse"
        assert REGISTRY[CAPTURE_PARSE_TASK] is CAPTURE_PARSE
        # 独立模板：与口述模板不是同一个对象（判据先行的结论）
        assert REGISTRY["ledger_parse"] is not CAPTURE_PARSE

    @pytest.mark.parametrize("token", [
        "refund", "transfer", "credit_card_repayment", "investment", "red_packet",
    ])
    def test_four_non_consumption_classes_are_in_the_prompt(self, token):
        assert token in CAPTURE_PARSE.system_prompt

    def test_prompt_pins_direction_and_sign(self):
        assert "绝不输出负数" in CAPTURE_PARSE.system_prompt
        assert "绝不臆造日期" in CAPTURE_PARSE.system_prompt

    def test_capture_path_pins_a_capable_model(self):
        """★ 模型能力下限（T21-2 实测，**这条断言别删**）。

        同一条提示词：flash 级模型把四类「不是消费」8/8 判成账目（且 confidence
        0.95–1.0，会走自动入账），max 级 8/8 判对。默认 ``ai_provider_model`` 正是
        flash 级，所以本模板必须自带模型覆盖；有人把它改回 ``None`` 时，这条用例
        必须变红——那等于把「退款记成收入、信用卡还款记成消费」放回生产。
        """
        assert CAPTURE_PARSE.model, (
            "capture_parse 必须钉住有能力的模型（实测 flash 级四类反例 0/8）"
        )
        # 口述模板不受此限（它的输入是用户口述，非凭证语义判定）
        assert REGISTRY["ledger_parse"].model is None


# ── 端点契约 ────────────────────────────────────────────────────────────

class TestCaptureParseEndpoint:
    def test_requires_auth(self, capture_env):
        resp = capture_env.client.post(
            "/ai/parse-notification",
            json={"text": "支付成功 ¥32.00", "source_channel": "notification"},
        )
        assert resp.status_code == 401

    def test_empty_text_rejected(self, capture_env):
        user = _guest(capture_env)
        assert _parse(capture_env, user, text="").status_code == 422

    def test_overlong_text_rejected(self, capture_env):
        user = _guest(capture_env)
        assert _parse(capture_env, user, text="支" * 2001).status_code == 422

    def test_unknown_channel_rejected(self, capture_env):
        user = _guest(capture_env)
        assert _parse(capture_env, user, channel="sms").status_code == 422

    @pytest.mark.parametrize(("channel", "text"), CAPTURE_CORPUS)
    def test_corpus_texts_are_forwarded_and_shaped(self, capture_env, channel, text):
        """七条来源语料：文本原样进 user turn，响应是消毒后的固定形状。"""
        user = _guest(capture_env, device_id=f"dev-{uuid.uuid4().hex[:8]}")
        resp = _parse(capture_env, user, text=text, channel=channel)
        assert resp.status_code == 200
        body = resp.json()
        assert body["task"] == "capture_parse"
        out = body["output"]
        assert set(out) == {"kind", "reason", "type", "amountCents",
                            "category", "note", "bizDate", "confidence"}
        # 捕获文本原样送达模型（不做任何客户端式改写）
        sent = capture_env.provider.calls[-1]["messages"]
        assert sent[-1] == {"role": "user", "content": text}

    @pytest.mark.parametrize("reason", [
        "refund", "transfer", "credit_card_repayment", "investment", "red_packet",
    ])
    def test_not_transaction_response_carries_no_amount(self, capture_env, reason):
        """四类反例的端到端形态：模型判成非交易后，客户端拿不到任何金额。"""
        capture_env.provider.results = [_result({
            "kind": "not_transaction", "reason": reason, "type": "expense",
            "amountCents": 3200, "note": "退款到账", "confidence": 0.9,
        })]
        user = _guest(capture_env, device_id=f"dev-{uuid.uuid4().hex[:8]}")
        out = _parse(capture_env, user).json()["output"]
        assert out["kind"] == "not_transaction" and out["reason"] == reason
        assert out["amountCents"] == 0 and out["type"] == ""

    def test_context_sections_are_injected(self, capture_env):
        """注入面复用：today / categories 两段进 system（与 /ai/interpret 同口径）。"""
        user = _guest(capture_env)
        resp = _parse(capture_env, user, context={
            "today": "2026-10-13", "categories": ["餐饮", "交通"],
        })
        assert resp.status_code == 200
        system = capture_env.provider.calls[-1]["messages"][0]["content"]
        assert "今天日期：2026-10-13" in system
        assert "【用户现有分类名列表】" in system
        assert "餐饮" in system and "交通" in system

    def test_usage_row_is_recorded(self, capture_env):
        """成本可观测：解析调用计入 ai_usage（T21-2 改动清单 ④）。"""
        user = _guest(capture_env)
        assert _parse(capture_env, user).status_code == 200
        rows = _fetch_usage(capture_env, user["id"])
        assert len(rows) == 1
        assert rows[0].tokens == PROMPT_TOKENS + COMPLETION_TOKENS
        # 快路径行：conversation_id 留空（与 /ai/interpret 同一口径）
        assert rows[0].conversation_id is None

    def test_provider_failure_degrades_to_503(self, capture_env):
        """降级契约：provider 连续失败 → 503 ai_unavailable（客户端回退本地规则）。"""
        capture_env.provider.results = [
            ProviderError(status=503, retryable=True, reason="http_5xx"),
            ProviderError(status=503, retryable=True, reason="http_5xx"),
        ]
        user = _guest(capture_env)
        resp = _parse(capture_env, user)
        assert resp.status_code == 503
        assert resp.json()["code"] == "ai_unavailable"


# ── 配额与限流 ──────────────────────────────────────────────────────────

class TestCaptureParseLimits:
    def test_guest_daily_quota_applies(self, capture_env):
        user = _guest(capture_env)
        _seed_usage(capture_env, user["id"], 20)  # 配额上限（fixture 钉死 20）
        resp = _parse(capture_env, user)
        assert resp.status_code == 429
        assert resp.json()["code"] == "ai_quota_exceeded"

    def test_rate_limit_applies(self, capture_env):
        """与 /ai/interpret 共用每用户 60s 滑窗（10 次）：第 11 次 429。"""
        user = _guest(capture_env)
        for _ in range(10):
            assert _parse(capture_env, user).status_code == 200
        resp = _parse(capture_env, user)
        assert resp.status_code == 429
        assert resp.json()["code"] == "ai_rate_limited"
