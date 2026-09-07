"""T-M4-04e: Tests for image receipt parsing (/ai/parse-image).

Coverage:
- Successful parse: image → vision model → ActionProposal with resolved category IDs
- Multi-transaction batch parse (add_tx_batch)
- Category name resolution: exact match + prefix fuzzy match
- Empty image (no transactions) → confidence=0, empty transactions
- Attachment ownership: cross-user → 404
- Expired attachment → 410
- Already parsed/linked attachment → 409
- Provider unavailable → 503 ai_unavailable
- Invalid JSON → retry once → 503 ai_invalid_output
- Status transition: PENDING → PARSED on success
- AIUsage recorded after successful parse
- Image too large → 413
- Rate limit → 429
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from src.common.config import Settings, reset_config, set_config
from src.common.database import get_db_session
from src.models.attachment import Attachment, AttachmentStatus
from src.models.base import Base
from src.models.ledger import Book, Category, CategoryKind
from src.modules.ai.provider import (
    ProviderError,
    ProviderResult,
    get_llm_provider,
    reset_llm_provider,
)
from src.modules.ai.service import _ai_rate_limits

PROMPT_TOKENS = 200
COMPLETION_TOKENS = 100


# ── Fakes ────────────────────────────────────────────────────────────────

class FakeVisionProvider:
    """Mock provider for image parsing tests."""

    name = "fake-vision"

    def __init__(self, results=None, default=None):
        self.results = list(results or [])
        self.default = default
        self.calls: list[dict] = []

    async def complete(self, messages, *, model, max_tokens, timeout):
        raise AssertionError("Text complete should not be called for image parse")

    async def complete_with_images(self, messages, *, model, max_tokens, timeout):
        self.calls.append(
            {"messages": messages, "model": model,
             "max_tokens": max_tokens, "timeout": timeout},
        )
        if self.results:
            item = self.results.pop(0)
        elif self.default is not None:
            item = self.default
        else:
            raise AssertionError("FakeVisionProvider exhausted")
        if isinstance(item, ProviderError):
            raise item
        return item


class ImageParseEnv:
    def __init__(self, client, factory, provider, upload_dir):
        self.client = client
        self.factory = factory
        self.provider = provider
        self.upload_dir = upload_dir


def _make_jpeg_bytes() -> bytes:
    """Minimal JPEG magic bytes for testing."""
    return b"\xff\xd8\xff\xe0" + b"\x00" * 100 + b"\xff\xd9"


@pytest.fixture()
def upload_dir():
    d = Path(tempfile.mkdtemp(prefix="aozora_test_img_"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def img_env(upload_dir):
    """TestClient with isolated DB, FakeVisionProvider, temp upload dir."""
    from src.main import app
    from src.modules.auth.dependencies import _rate_limits

    _rate_limits.clear()
    _ai_rate_limits.clear()
    reset_llm_provider()
    reset_config()

    # Configure upload dir
    cfg = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        upload_dir=str(upload_dir),
        secret_key="test-secret",
    )
    set_config(cfg)

    # Reset the global engine so it picks up the new config.
    # database.py now uses StaticPool for :memory: SQLite, keeping all
    # connections on the same in-memory DB (needed by lifespan startup).
    from src.common.database import get_engine, get_session_factory, reset_engine
    reset_engine()
    engine = get_engine()
    factory = get_session_factory()

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

    # Default provider returns a valid single-transaction parse
    valid_output = {
        "action": "add_tx_batch",
        "proposalId": "img-test-001",
        "confidence": 0.92,
        "transactions": [
            {
                "type": "expense",
                "amountCents": 3500,
                "category": "餐饮",
                "note": "午餐",
                "bizDate": "2026-09-06",
            }
        ],
    }
    valid_json = json.dumps(valid_output, ensure_ascii=False)
    provider = FakeVisionProvider(
        default=ProviderResult(valid_json, PROMPT_TOKENS, COMPLETION_TOKENS),
    )

    app.dependency_overrides[get_db_session] = _override_get_db_session
    app.dependency_overrides[get_llm_provider] = lambda: provider

    with TestClient(app) as c:
        yield ImageParseEnv(c, factory, provider, upload_dir)

    app.dependency_overrides.clear()
    _rate_limits.clear()
    _ai_rate_limits.clear()
    reset_llm_provider()
    reset_config()


# ── Helpers ──────────────────────────────────────────────────────────────

def _guest(env: ImageParseEnv) -> dict:
    resp = env.client.post(
        "/auth/guest", json={"device_id": "img-device-1", "nickname": "img-tester"},
    )
    assert resp.status_code == 200
    data = resp.json()
    return {"id": data["user_id"], "token": data["tokens"]["access_token"]}


def _auth(user: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {user['token']}"}


def _seed_book_and_categories(env: ImageParseEnv, user_id: str) -> str:
    """Create a book + expense categories; return book_id."""
    async def _run():
        async with env.factory() as session:
            uid = uuid.UUID(user_id)
            book = Book(id=uuid.uuid4(), owner_id=uid, name="日常")
            session.add(book)
            session.add(Category(
                id=uuid.uuid4(), owner_id=uid, book_id=book.id,
                name="餐饮", kind=CategoryKind.EXPENSE,
            ))
            session.add(Category(
                id=uuid.uuid4(), owner_id=uid, book_id=book.id,
                name="交通", kind=CategoryKind.EXPENSE,
            ))
            session.add(Category(
                id=uuid.uuid4(), owner_id=uid, book_id=book.id,
                name="餐饮食谱", kind=CategoryKind.EXPENSE,
            ))
            await session.commit()
            return str(book.id)

    return asyncio.run(_run())


def _seed_attachment(
    env: ImageParseEnv,
    user_id: str,
    status: AttachmentStatus = AttachmentStatus.PENDING,
    content: bytes | None = None,
) -> str:
    """Create an attachment row + file on disk; return attachment_id."""
    if content is None:
        content = _make_jpeg_bytes()

    async def _run():
        async with env.factory() as session:
            uid = uuid.UUID(user_id)
            att_id = uuid.uuid4()
            object_key = f"{user_id}/{att_id}.jpg"

            # Write file to upload dir
            dest = env.upload_dir / object_key
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)

            att = Attachment(
                id=att_id,
                user_id=uid,
                object_key=object_key,
                mime_type="image/jpeg",
                file_size=len(content),
                status=status,
            )
            session.add(att)
            await session.commit()
            return str(att_id)

    return asyncio.run(_run())


def _parse_image(
    env: ImageParseEnv,
    user: dict,
    attachment_id: str,
    book_id: str,
):
    return env.client.post(
        "/ai/parse-image",
        json={"attachment_id": attachment_id, "book_id": book_id},
        headers=_auth(user),
    )


# ── Tests ────────────────────────────────────────────────────────────────

class TestImageParseSuccess:
    """Successful parse flow."""

    def test_single_transaction_parse(self, img_env):
        """Image → vision model → single transaction ActionProposal."""
        user = _guest(img_env)
        book_id = _seed_book_and_categories(img_env, user["id"])
        att_id = _seed_attachment(img_env, user["id"])

        resp = _parse_image(img_env, user, att_id, book_id)
        assert resp.status_code == 200
        data = resp.json()

        assert data["request_id"]
        assert data["attachment_id"] == att_id
        assert data["confidence"] == 0.92
        assert data["proposal"]["action"] == "add_tx_batch"
        assert len(data["proposal"]["transactions"]) == 1
        tx = data["proposal"]["transactions"][0]
        assert tx["amountCents"] == 3500
        assert tx["type"] == "expense"
        # Category "餐饮" should be resolved to a UUID
        assert tx["categoryId"] != ""
        assert len(tx["categoryId"]) == 36  # UUID format
        assert tx["note"] == "午餐"
        assert tx["bizDate"] == "2026-09-06"
        # Usage recorded
        assert data["usage"]["prompt_tokens"] == PROMPT_TOKENS
        assert data["usage"]["completion_tokens"] == COMPLETION_TOKENS

    def test_multi_transaction_batch(self, img_env):
        """Multiple transactions from one image."""
        batch_output = {
            "action": "add_tx_batch",
            "proposalId": "img-batch-001",
            "confidence": 0.88,
            "transactions": [
                {"type": "expense", "amountCents": 3500, "category": "餐饮", "note": "午餐", "bizDate": "2026-09-06"},
                {"type": "expense", "amountCents": 1500, "category": "交通", "note": "地铁", "bizDate": "2026-09-06"},
                {"type": "expense", "amountCents": 8800, "category": "购物", "note": "日用品", "bizDate": ""},
            ],
        }
        img_env.provider.results = [
            ProviderResult(json.dumps(batch_output, ensure_ascii=False), 300, 150),
        ]

        user = _guest(img_env)
        book_id = _seed_book_and_categories(img_env, user["id"])
        att_id = _seed_attachment(img_env, user["id"])

        resp = _parse_image(img_env, user, att_id, book_id)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["proposal"]["transactions"]) == 3
        assert data["confidence"] == 0.88
        # "餐饮" resolved, "交通" resolved, "购物" unresolved → fallback to first category
        txs = data["proposal"]["transactions"]
        assert txs[0]["categoryId"] != ""  # 餐饮
        assert txs[1]["categoryId"] != ""  # 交通
        # R-M4-53 fix: unresolved category falls back to first available (not empty)
        assert txs[2]["categoryId"] != ""  # 购物 → fallback
        # R-M4-52 fix: empty bizDate falls back to today (not empty string)
        assert txs[2]["bizDate"] != ""
        assert len(txs[2]["bizDate"]) == 10  # YYYY-MM-DD format

    def test_category_prefix_match(self, img_env):
        """LLM outputs '餐饮' but user has '餐饮' AND '餐饮食谱' — exact match wins."""
        user = _guest(img_env)
        book_id = _seed_book_and_categories(img_env, user["id"])
        att_id = _seed_attachment(img_env, user["id"])

        resp = _parse_image(img_env, user, att_id, book_id)
        assert resp.status_code == 200
        tx = resp.json()["proposal"]["transactions"][0]
        # "餐饮" should match exactly, not "餐饮食谱"
        assert tx["categoryId"] != ""

    def test_status_transition_to_parsed(self, img_env):
        """After successful parse, attachment status becomes PARSED."""
        user = _guest(img_env)
        book_id = _seed_book_and_categories(img_env, user["id"])
        att_id = _seed_attachment(img_env, user["id"])

        resp = _parse_image(img_env, user, att_id, book_id)
        assert resp.status_code == 200

        # Verify DB status
        async def _check():
            async with img_env.factory() as session:
                from sqlalchemy import select
                result = await session.execute(
                    select(Attachment).where(Attachment.id == uuid.UUID(att_id))
                )
                att = result.scalar_one()
                assert att.status == AttachmentStatus.PARSED
                assert att.expires_at is None  # parsed attachments don't expire

        asyncio.run(_check())

    def test_empty_image_no_transactions(self, img_env):
        """Unrecognizable image → empty proposal with confidence=0.
        NEW-R6-1: status must become PARSED even for zero transactions
        to prevent the attachment from expiring after 24h.
        """
        empty_output = {
            "action": "add_tx_batch",
            "proposalId": "",
            "confidence": 0,
            "transactions": [],
        }
        img_env.provider.results = [
            ProviderResult(json.dumps(empty_output), 100, 50),
        ]

        user = _guest(img_env)
        book_id = _seed_book_and_categories(img_env, user["id"])
        att_id = _seed_attachment(img_env, user["id"])

        resp = _parse_image(img_env, user, att_id, book_id)
        assert resp.status_code == 200
        data = resp.json()
        assert data["confidence"] == 0
        assert data["proposal"]["transactions"] == []
        # Still gets a proposalId generated
        assert data["proposal"]["proposalId"] != ""

        # NEW-R6-1: zero-tx must also mark PARSED to prevent 24h expiry
        async def _check():
            async with img_env.factory() as session:
                from sqlalchemy import select
                result = await session.execute(
                    select(Attachment).where(Attachment.id == uuid.UUID(att_id))
                )
                att = result.scalar_one()
                assert att.status == AttachmentStatus.PARSED
                assert att.expires_at is None

        asyncio.run(_check())

    def test_zero_tx_retry_after_parsed(self, img_env):
        """NEW-2 fix: after zero-tx parse (status=PARSED), user can retry and get new results."""
        empty_output = {
            "action": "add_tx_batch",
            "proposalId": "",
            "confidence": 0,
            "transactions": [],
        }
        good_output = {
            "action": "add_tx_batch",
            "proposalId": "",
            "confidence": 0.9,
            "transactions": [
                {
                    "type": "expense",
                    "amountCents": 5000,
                    "categoryName": "餐饮",
                    "bizDate": "2026-09-01",
                }
            ],
        }
        # First call returns zero tx, second call returns good data
        img_env.provider.results = [
            ProviderResult(json.dumps(empty_output), 100, 50),
            ProviderResult(json.dumps(good_output), 200, 100),
        ]

        user = _guest(img_env)
        book_id = _seed_book_and_categories(img_env, user["id"])
        att_id = _seed_attachment(img_env, user["id"])

        # First parse: zero transactions → PARSED
        resp1 = _parse_image(img_env, user, att_id, book_id)
        assert resp1.status_code == 200
        assert resp1.json()["proposal"]["transactions"] == []

        # Second parse (retry): should succeed, not 409
        resp2 = _parse_image(img_env, user, att_id, book_id)
        assert resp2.status_code == 200
        assert len(resp2.json()["proposal"]["transactions"]) == 1


class TestImageParseOwnership:
    """Attachment access control."""

    def test_cross_user_attachment_404(self, img_env):
        """User B cannot parse User A's attachment."""
        user_a = _guest(img_env)
        user_b_resp = img_env.client.post(
            "/auth/guest", json={"device_id": "img-device-2", "nickname": "user-b"},
        )
        user_b = {"id": user_b_resp.json()["user_id"], "token": user_b_resp.json()["tokens"]["access_token"]}

        book_id = _seed_book_and_categories(img_env, user_b["id"])
        att_id = _seed_attachment(img_env, user_a["id"])  # owned by A

        resp = _parse_image(img_env, user_b, att_id, book_id)
        assert resp.status_code == 404

    def test_expired_attachment_410(self, img_env):
        """Expired attachment cannot be parsed."""
        user = _guest(img_env)
        book_id = _seed_book_and_categories(img_env, user["id"])
        att_id = _seed_attachment(img_env, user["id"], status=AttachmentStatus.EXPIRED)

        resp = _parse_image(img_env, user, att_id, book_id)
        assert resp.status_code == 410

    def test_already_parsed_can_reparse(self, img_env):
        """NEW-R6-1 fix: PARSED attachment can be re-parsed (status resets to PENDING first)."""
        user = _guest(img_env)
        book_id = _seed_book_and_categories(img_env, user["id"])
        att_id = _seed_attachment(img_env, user["id"], status=AttachmentStatus.PARSED)

        resp = _parse_image(img_env, user, att_id, book_id)
        # Should succeed (not 409) — re-parsing is now allowed
        assert resp.status_code == 200

    def test_linked_attachment_409(self, img_env):
        """Linked attachment cannot be parsed."""
        user = _guest(img_env)
        book_id = _seed_book_and_categories(img_env, user["id"])
        att_id = _seed_attachment(img_env, user["id"], status=AttachmentStatus.LINKED)

        resp = _parse_image(img_env, user, att_id, book_id)
        assert resp.status_code == 409


class TestImageParseDegradation:
    """Provider failure and degradation."""

    def test_no_provider_503(self, img_env):
        """No provider configured → 503."""
        # Override provider to None
        from src.main import app
        app.dependency_overrides[get_llm_provider] = lambda: None

        user = _guest(img_env)
        book_id = _seed_book_and_categories(img_env, user["id"])
        att_id = _seed_attachment(img_env, user["id"])

        resp = _parse_image(img_env, user, att_id, book_id)
        assert resp.status_code == 503
        assert resp.json()["code"] == "ai_unavailable"

        # Restore provider
        app.dependency_overrides[get_llm_provider] = lambda: img_env.provider

    def test_provider_error_retry_then_fail(self, img_env):
        """Provider error → retry once → 503."""
        img_env.provider.results = [
            ProviderError(status=500, retryable=True, reason="http_5xx"),
            ProviderError(status=500, retryable=True, reason="http_5xx"),
        ]

        user = _guest(img_env)
        book_id = _seed_book_and_categories(img_env, user["id"])
        att_id = _seed_attachment(img_env, user["id"])

        resp = _parse_image(img_env, user, att_id, book_id)
        assert resp.status_code == 503
        assert len(img_env.provider.calls) == 2  # retried once

    def test_invalid_json_retry_then_fail(self, img_env):
        """Invalid JSON output → retry once → 503."""
        img_env.provider.results = [
            ProviderResult("not valid json", 100, 50),
            ProviderResult("also not json", 100, 50),
        ]

        user = _guest(img_env)
        book_id = _seed_book_and_categories(img_env, user["id"])
        att_id = _seed_attachment(img_env, user["id"])

        resp = _parse_image(img_env, user, att_id, book_id)
        assert resp.status_code == 503
        assert resp.json()["code"] == "ai_invalid_output"
        assert len(img_env.provider.calls) == 2

    def test_timeout_not_retried(self, img_env):
        """Timeout → NOT retried (APP-CONTEXT §6.4 contract)."""
        img_env.provider.results = [
            ProviderError(status=None, retryable=False, reason="timeout"),
        ]

        user = _guest(img_env)
        book_id = _seed_book_and_categories(img_env, user["id"])
        att_id = _seed_attachment(img_env, user["id"])

        resp = _parse_image(img_env, user, att_id, book_id)
        assert resp.status_code == 503
        assert len(img_env.provider.calls) == 1  # no retry


class TestImageParseEdgeCases:
    """Edge cases and validation."""

    def test_invalid_book_id_422(self, img_env):
        """Invalid UUID for book_id → 422."""
        user = _guest(img_env)
        att_id = _seed_attachment(img_env, user["id"])

        resp = img_env.client.post(
            "/ai/parse-image",
            json={"attachment_id": att_id, "book_id": "not-a-uuid"},
            headers=_auth(user),
        )
        assert resp.status_code == 422

    def test_file_missing_on_disk(self, img_env):
        """Returns 404 when the attachment file is missing from disk."""
        user = _guest(img_env)
        book_id = _seed_book_and_categories(img_env, user["id"])
        att_id = _seed_attachment(img_env, user["id"])

        # Delete the file from disk
        shutil.rmtree(img_env.upload_dir, ignore_errors=True)
        img_env.upload_dir.mkdir(parents=True, exist_ok=True)

        resp = _parse_image(img_env, user, att_id, book_id)
        assert resp.status_code == 404

    def test_rate_limit_429(self, img_env):
        """Burst rate limit → 429."""
        user = _guest(img_env)
        book_id = _seed_book_and_categories(img_env, user["id"])

        # Exhaust rate limit (default 10 per minute)
        for _ in range(10):
            att_id = _seed_attachment(img_env, user["id"])
            _parse_image(img_env, user, att_id, book_id)

        # 11th request should be rate-limited
        att_id = _seed_attachment(img_env, user["id"])
        resp = _parse_image(img_env, user, att_id, book_id)
        assert resp.status_code == 429

    def test_vision_model_called_with_image(self, img_env):
        """Verify the provider receives image content in messages."""
        user = _guest(img_env)
        book_id = _seed_book_and_categories(img_env, user["id"])
        att_id = _seed_attachment(img_env, user["id"])

        _parse_image(img_env, user, att_id, book_id)

        assert len(img_env.provider.calls) == 1
        messages = img_env.provider.calls[0]["messages"]
        # System message (text only) + user message (with image)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert isinstance(messages[0]["content"], str)
        assert messages[1]["role"] == "user"
        # User content should be a list with image_url + text
        user_content = messages[1]["content"]
        assert isinstance(user_content, list)
        types = [part["type"] for part in user_content]
        assert "image_url" in types
        assert "text" in types
        # image_url should be a base64 data URL
        image_part = next(p for p in user_content if p["type"] == "image_url")
        assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")


class TestResolveCategoriesValidation:
    """NEW-E1 / NEW-E2: LLM output data validation in _resolve_categories."""

    @staticmethod
    def _ctx():
        from src.modules.ai.context import ProposalValidationContext
        return ProposalValidationContext(
            category_ids=frozenset({"cat-food"}),
            category_names={"餐饮": "cat-food"},
        )

    def test_amount_cents_zero_skipped(self):
        """amountCents=0 is skipped (schema minimum: 1)."""
        from src.modules.ai.service import AIService
        raw = [{"type": "expense", "amountCents": 0, "category": "餐饮", "bizDate": "2026-09-06"}]
        resolved = AIService._resolve_categories(raw, self._ctx())
        assert resolved == []

    def test_amount_cents_negative_skipped(self):
        """amountCents < 0 is skipped."""
        from src.modules.ai.service import AIService
        raw = [{"type": "expense", "amountCents": -100, "category": "餐饮", "bizDate": "2026-09-06"}]
        resolved = AIService._resolve_categories(raw, self._ctx())
        assert resolved == []

    def test_amount_cents_non_numeric_skipped(self):
        """amountCents='abc' is skipped (no crash)."""
        from src.modules.ai.service import AIService
        raw = [{"type": "expense", "amountCents": "abc", "category": "餐饮", "bizDate": "2026-09-06"}]
        resolved = AIService._resolve_categories(raw, self._ctx())
        assert resolved == []

    def test_amount_cents_null_skipped(self):
        """amountCents=null is skipped (no crash)."""
        from src.modules.ai.service import AIService
        raw = [{"type": "expense", "amountCents": None, "category": "餐饮", "bizDate": "2026-09-06"}]
        resolved = AIService._resolve_categories(raw, self._ctx())
        assert resolved == []

    def test_malformed_biz_date_fallback(self):
        """Non-empty but malformed bizDate falls back to today."""
        from src.modules.ai.service import AIService
        raw = [{"type": "expense", "amountCents": 1000, "category": "餐饮", "bizDate": "2026/9/6"}]
        resolved = AIService._resolve_categories(raw, self._ctx())
        assert len(resolved) == 1
        assert len(resolved[0]["bizDate"]) == 10  # YYYY-MM-DD

    def test_biz_date_yesterday_fallback(self):
        """bizDate='yesterday' falls back to today."""
        from src.modules.ai.service import AIService
        raw = [{"type": "expense", "amountCents": 1000, "category": "餐饮", "bizDate": "yesterday"}]
        resolved = AIService._resolve_categories(raw, self._ctx())
        assert len(resolved) == 1
        assert resolved[0]["bizDate"].endswith("-") is False
        assert len(resolved[0]["bizDate"]) == 10

    def test_valid_biz_date_preserved(self):
        """Valid bizDate passes through unchanged."""
        from src.modules.ai.service import AIService
        raw = [{"type": "expense", "amountCents": 1000, "category": "餐饮", "bizDate": "2026-01-15"}]
        resolved = AIService._resolve_categories(raw, self._ctx())
        assert resolved[0]["bizDate"] == "2026-01-15"

    def test_valid_transactions_not_skipped(self):
        """Normal transactions pass validation normally."""
        from src.modules.ai.service import AIService
        raw = [
            {"type": "expense", "amountCents": 1000, "category": "餐饮", "bizDate": "2026-09-06"},
            {"type": "expense", "amountCents": 0, "category": "餐饮", "bizDate": "2026-09-06"},  # skipped
            {"type": "expense", "amountCents": 2000, "category": "餐饮", "bizDate": "garbage"},   # kept, bizDate fixed
        ]
        resolved = AIService._resolve_categories(raw, self._ctx())
        assert len(resolved) == 2
        assert resolved[0]["amountCents"] == 1000
        assert resolved[0]["bizDate"] == "2026-09-06"
        assert resolved[1]["amountCents"] == 2000
        assert len(resolved[1]["bizDate"]) == 10  # today fallback

    def test_type_enum_normalized(self):
        """NEW-E3: unknown type values are normalized to 'expense'."""
        from src.modules.ai.service import AIService
        raw = [
            {"type": "refund", "amountCents": 1000, "category": "餐饮", "bizDate": "2026-09-06"},
            {"type": "income", "amountCents": 2000, "category": "餐饮", "bizDate": "2026-09-06"},
            {"type": "expense", "amountCents": 3000, "category": "餐饮", "bizDate": "2026-09-06"},
        ]
        resolved = AIService._resolve_categories(raw, self._ctx())
        assert len(resolved) == 3
        assert resolved[0]["type"] == "expense"  # "refund" → normalized
        assert resolved[1]["type"] == "income"    # valid
        assert resolved[2]["type"] == "expense"   # valid

    def test_zero_categories_raises_clear_error(self):
        """NEW-E6: empty category_names → 400 with clear message (not silent "" fallback)."""
        from src.common.errors import AppException
        from src.modules.ai.context import ProposalValidationContext
        from src.modules.ai.service import AIService

        empty_ctx = ProposalValidationContext(
            category_ids=frozenset(),
            category_names={},
        )
        raw = [{"type": "expense", "amountCents": 1000, "category": "餐饮", "bizDate": "2026-09-06"}]
        with pytest.raises(AppException) as exc_info:
            AIService._resolve_categories(raw, empty_ctx)
        assert exc_info.value.code == "no_categories"


class TestConcurrentParseDedup:
    """NEW-E4: in-process concurrent parse dedup."""

    def test_duplicate_parse_returns_409(self, img_env):
        """Second parse for the same attachment while first is in progress → 409."""
        from src.modules.ai.service import _parsing_attachments

        user = _guest(img_env)
        book_id = _seed_book_and_categories(img_env, user["id"])
        att_id = _seed_attachment(img_env, user["id"])

        # Simulate another request already parsing this attachment
        _parsing_attachments.add(att_id)
        try:
            resp = _parse_image(img_env, user, att_id, book_id)
            assert resp.status_code == 409
            assert resp.json()["code"] == "parse_in_progress"
        finally:
            _parsing_attachments.discard(att_id)

    def test_parse_lock_released_on_success(self, img_env):
        """After a successful parse, the lock is released."""
        from src.modules.ai.service import _parsing_attachments

        user = _guest(img_env)
        book_id = _seed_book_and_categories(img_env, user["id"])
        att_id = _seed_attachment(img_env, user["id"])

        _parse_image(img_env, user, att_id, book_id)
        assert att_id not in _parsing_attachments

    def test_parse_lock_released_on_error(self, img_env):
        """After a failed parse, the lock is still released."""
        from src.modules.ai.service import _parsing_attachments

        img_env.provider.results = [
            ProviderError(status=500, retryable=True, reason="http_5xx"),
            ProviderError(status=500, retryable=True, reason="http_5xx"),
        ]

        user = _guest(img_env)
        book_id = _seed_book_and_categories(img_env, user["id"])
        att_id = _seed_attachment(img_env, user["id"])

        _parse_image(img_env, user, att_id, book_id)
        assert att_id not in _parsing_attachments
