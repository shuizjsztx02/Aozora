"""T-M7-01: sync tests for the three cloud entities.

Covers ``savings_goal`` / ``scheduled_rule`` / ``chat_persona`` across
push (insert / update / delete / idempotent insert), pull, and bootstrap —
including the two properties that make the rollout safe for clients that
predate the entities:

* the legacy ``checksum`` keeps covering exactly the original five lists
  (so an old client's recomputation still matches), and
* ``checksum_v2`` covers all eight, agreeing with the legacy value when the
  three lists are empty.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.models.base import Base
from src.models.consent import UserConsent
from src.models.ledger import Account, Book, Category, CategoryKind
from src.models.persona import ChatPersona
from src.models.savings import SavingsGoal
from src.models.scheduled import ScheduledRule
from src.models.user import DeviceSession, User
from src.modules.sync.service import (
    compute_bootstrap_checksum,
    compute_bootstrap_checksum_v2,
)


def _token(user_id: uuid.UUID) -> str:
    from src.modules.auth.utils import create_access_token
    return create_access_token(str(user_id))


def _auth_header(user_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(user_id)}"}


@pytest_asyncio.fixture()
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture()
async def cloud_ctx(client, db_session):
    """Two users; user A owns a book / category / account to reference."""
    user_a = User(id=uuid.uuid4(), nickname="alice", status="active")
    user_b = User(id=uuid.uuid4(), nickname="bob", status="active")
    db_session.add_all([user_a, user_b])
    await db_session.flush()

    device_a, device_b = "dev-alice", "dev-bob"
    db_session.add_all([
        DeviceSession(
            id=uuid.uuid4(), user_id=user_a.id, device_id=device_a,
            refresh_token_hash="x", expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        ),
        DeviceSession(
            id=uuid.uuid4(), user_id=user_b.id, device_id=device_b,
            refresh_token_hash="x", expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        ),
        UserConsent(device_id=device_a, policy_version="v1",
                    consented_at=datetime(2026, 1, 1, tzinfo=UTC)),
        UserConsent(device_id=device_b, policy_version="v1",
                    consented_at=datetime(2026, 1, 1, tzinfo=UTC)),
    ])

    book_a = Book(id=uuid.uuid4(), owner_id=user_a.id, name="Alice Book")
    db_session.add(book_a)
    await db_session.flush()
    cat_a = Category(
        id=uuid.uuid4(), owner_id=user_a.id, book_id=book_a.id,
        kind=CategoryKind.EXPENSE, name="餐饮",
    )
    acc_a = Account(id=uuid.uuid4(), owner_id=user_a.id, name="现金")
    db_session.add_all([cat_a, acc_a])
    await db_session.commit()

    from src.common.database import get_db_session

    async def _override():
        yield db_session

    client.app.dependency_overrides[get_db_session] = _override

    yield {
        "client": client,
        "db": db_session,
        "user_a": user_a,
        "user_b": user_b,
        "book_a": book_a,
        "cat_a": cat_a,
        "acc_a": acc_a,
    }

    client.app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------

def _goal_payload(**over) -> dict:
    payload = {
        "name": "旅行基金",
        "target_cents": 500_000,
        "current_cents": 120_000,
        "icon": "🎯",
        "period_value": 6,
        "period_unit": "month",
    }
    payload.update(over)
    return payload


def _rule_payload(ctx, **over) -> dict:
    payload = {
        "type": "expense",
        "name": "房租",
        "amount_cents": 350_000,
        "book_id": str(ctx["book_a"].id),
        "category_id": str(ctx["cat_a"].id),
        "account_id": str(ctx["acc_a"].id),
        "include_in_budget": True,
        "note": "每月一号",
        "repeat": "monthly",
        "start_date": "2026-01-01",
        "end_date": None,
        "enabled": True,
        "last_run_date": "2026-09-01",
        "run_hour": 9,
        "run_minute": 30,
    }
    payload.update(over)
    return payload


def _persona_payload(**over) -> dict:
    payload = {"name": "小贝", "avatar": "", "persona": "你是小贝。"}
    payload.update(over)
    return payload


def _push(ctx, user, mutations: list[dict]):
    return ctx["client"].post(
        "/sync/push",
        json={"mutations": mutations},
        headers=_auth_header(user.id),
    )


def _mutation(entity_type: str, entity_id: uuid.UUID, payload: dict, **over) -> dict:
    mutation = {
        "mutation_id": str(uuid.uuid4()),
        "entity_type": entity_type,
        "operation": "insert",
        "entity_id": str(entity_id),
        "payload": payload,
    }
    mutation.update(over)
    return mutation


def _pull(ctx, user, cursor: int = 0):
    return ctx["client"].post(
        "/sync/pull", json={"cursor": cursor, "limit": 100},
        headers=_auth_header(user.id),
    )


def _bootstrap(ctx, user):
    return ctx["client"].post("/sync/bootstrap", headers=_auth_header(user.id))


# ---------------------------------------------------------------------------
# Push: insert
# ---------------------------------------------------------------------------

class TestCloudEntityInsert:

    @pytest.mark.asyncio
    async def test_insert_savings_goal(self, cloud_ctx):
        ctx = cloud_ctx
        goal_id = uuid.uuid4()
        resp = _push(ctx, ctx["user_a"], [_mutation("savings_goal", goal_id, _goal_payload())])
        assert resp.status_code == 200
        result = resp.json()["results"][0]
        assert result["status"] == "applied"
        assert result["version"] == 1

        row = (await ctx["db"].execute(
            select(SavingsGoal).where(SavingsGoal.id == goal_id)
        )).scalar_one()
        assert row.owner_id == ctx["user_a"].id
        assert row.name == "旅行基金"
        assert row.target_cents == 500_000
        assert row.period_value == 6 and row.period_unit == "month"
        assert row.archived_at is None

    @pytest.mark.asyncio
    async def test_insert_scheduled_rule(self, cloud_ctx):
        ctx = cloud_ctx
        rule_id = uuid.uuid4()
        resp = _push(ctx, ctx["user_a"], [
            _mutation("scheduled_rule", rule_id, _rule_payload(ctx)),
        ])
        assert resp.status_code == 200
        assert resp.json()["results"][0]["status"] == "applied"

        row = (await ctx["db"].execute(
            select(ScheduledRule).where(ScheduledRule.id == rule_id)
        )).scalar_one()
        assert row.book_id == ctx["book_a"].id
        assert row.category_id == ctx["cat_a"].id
        assert row.account_id == ctx["acc_a"].id
        assert row.repeat == "monthly"
        assert row.include_in_budget is True
        assert row.start_date.isoformat() == "2026-01-01"
        assert row.end_date is None
        assert row.last_run_date.isoformat() == "2026-09-01"
        assert row.run_hour == 9 and row.run_minute == 30
        assert row.archived_at is None

    @pytest.mark.asyncio
    async def test_insert_chat_persona(self, cloud_ctx):
        ctx = cloud_ctx
        persona_id = uuid.uuid4()
        resp = _push(ctx, ctx["user_a"], [
            _mutation("chat_persona", persona_id, _persona_payload()),
        ])
        assert resp.status_code == 200
        assert resp.json()["results"][0]["status"] == "applied"

        row = (await ctx["db"].execute(
            select(ChatPersona).where(ChatPersona.id == persona_id)
        )).scalar_one()
        assert row.owner_id == ctx["user_a"].id
        assert row.name == "小贝"
        assert row.persona == "你是小贝。"

    @pytest.mark.asyncio
    async def test_three_types_in_one_batch(self, cloud_ctx):
        """客户端一次 push 混排三类是常态（迁移上行尤其如此）。"""
        ctx = cloud_ctx
        resp = _push(ctx, ctx["user_a"], [
            _mutation("savings_goal", uuid.uuid4(), _goal_payload()),
            _mutation("scheduled_rule", uuid.uuid4(), _rule_payload(ctx)),
            _mutation("chat_persona", uuid.uuid4(), _persona_payload()),
        ])
        assert resp.status_code == 200
        assert [r["status"] for r in resp.json()["results"]] == ["applied"] * 3

    @pytest.mark.asyncio
    async def test_insert_defaults_applied(self, cloud_ctx):
        """可选字段缺省：current_cents=0 / include_in_budget·enabled=True / run 8:00。"""
        ctx = cloud_ctx
        goal_id, rule_id = uuid.uuid4(), uuid.uuid4()
        _push(ctx, ctx["user_a"], [
            _mutation("savings_goal", goal_id, {
                "name": "简版目标", "target_cents": 1000,
            }),
            _mutation("scheduled_rule", rule_id, {
                "type": "income", "name": "补贴", "amount_cents": 20000,
                "book_id": str(ctx["book_a"].id),
                "category_id": str(ctx["cat_a"].id),
                "repeat": "weekly", "start_date": "2026-03-02",
            }),
        ])
        goal = (await ctx["db"].execute(
            select(SavingsGoal).where(SavingsGoal.id == goal_id)
        )).scalar_one()
        assert goal.current_cents == 0
        assert goal.period_value is None and goal.period_unit is None

        rule = (await ctx["db"].execute(
            select(ScheduledRule).where(ScheduledRule.id == rule_id)
        )).scalar_one()
        assert rule.include_in_budget is True
        assert rule.enabled is True
        assert rule.run_hour == 8 and rule.run_minute == 0
        assert rule.note == ""
        assert rule.account_id is None


# ---------------------------------------------------------------------------
# Push: migration-friendly idempotent insert
# ---------------------------------------------------------------------------

class TestCloudEntityIdempotentInsert:
    """A second device re-uploads the same local ids during the one-time
    migration. The insert must be a no-op that returns the cloud version,
    not a conflict that leaves a dead entry in the client's queue."""

    @pytest.mark.asyncio
    async def test_reinsert_same_id_is_idempotent(self, cloud_ctx):
        ctx = cloud_ctx
        goal_id = uuid.uuid4()
        first = _push(ctx, ctx["user_a"], [
            _mutation("savings_goal", goal_id, _goal_payload()),
        ])
        assert first.json()["results"][0]["status"] == "applied"

        # Second device: same id, different (stale) values
        second = _push(ctx, ctx["user_a"], [
            _mutation("savings_goal", goal_id, _goal_payload(name="本地旧名字")),
        ])
        assert second.status_code == 200
        result = second.json()["results"][0]
        assert result["status"] == "applied"
        assert result["version"] == 1

        # Cloud copy is authoritative — the second upload did not overwrite it
        row = (await ctx["db"].execute(
            select(SavingsGoal).where(SavingsGoal.id == goal_id)
        )).scalar_one()
        assert row.name == "旅行基金"

    @pytest.mark.asyncio
    async def test_reinsert_all_three_types_is_idempotent(self, cloud_ctx):
        ctx = cloud_ctx
        ids = {"savings_goal": uuid.uuid4(), "scheduled_rule": uuid.uuid4(),
               "chat_persona": uuid.uuid4()}
        payloads = {
            "savings_goal": _goal_payload(),
            "scheduled_rule": _rule_payload(ctx),
            "chat_persona": _persona_payload(),
        }
        for entity_type, eid in ids.items():
            resp = _push(ctx, ctx["user_a"], [_mutation(entity_type, eid, payloads[entity_type])])
            assert resp.json()["results"][0]["status"] == "applied"
            again = _push(ctx, ctx["user_a"], [_mutation(entity_type, eid, payloads[entity_type])])
            assert again.status_code == 200
            assert again.json()["results"][0]["status"] == "applied"

    @pytest.mark.asyncio
    async def test_archived_entity_reinsert_stays_archived(self, cloud_ctx):
        """上行一条本地已归档的 id 不会把云端行复活。"""
        ctx = cloud_ctx
        goal_id = uuid.uuid4()
        _push(ctx, ctx["user_a"], [_mutation("savings_goal", goal_id, _goal_payload())])
        _push(ctx, ctx["user_a"], [_mutation(
            "savings_goal", goal_id, {}, operation="delete", version=1,
        )])
        _push(ctx, ctx["user_a"], [_mutation("savings_goal", goal_id, _goal_payload())])

        row = (await ctx["db"].execute(
            select(SavingsGoal).where(SavingsGoal.id == goal_id)
        )).scalar_one()
        assert row.archived_at is not None

    @pytest.mark.asyncio
    async def test_idempotent_reinsert_emits_update_change(self, cloud_ctx):
        """幂等命中记一条 **update** 变更（既不是 insert，也不是什么都不记）。

        - 不记变更 → 发起方本地副本停在自己的 version（如 5）而云端是 1，
          下一次 update 带 expectedVersion=5 必判 conflict → 终态失败、改动丢失。
        - 记成 insert → 客户端 pull 的 insert 分支按"新增"落库，已归档行会被复活。
        """
        ctx = cloud_ctx
        goal_id = uuid.uuid4()
        _push(ctx, ctx["user_a"], [_mutation("savings_goal", goal_id, _goal_payload())])
        cursor = _pull(ctx, ctx["user_a"]).json()["cursor"]

        _push(ctx, ctx["user_a"], [_mutation("savings_goal", goal_id, _goal_payload())])
        changes = _pull(ctx, ctx["user_a"], cursor=cursor).json()["changes"]
        assert len(changes) == 1
        assert changes[0]["entity_type"] == "savings_goal"
        assert changes[0]["operation"] == "update"
        assert changes[0]["version"] == 1
        # 云端副本未被覆盖（幂等 = 不覆盖）
        assert changes[0]["entity"]["name"] == "旅行基金"

    @pytest.mark.asyncio
    async def test_idempotent_reinsert_of_archived_carries_archived_at(self, cloud_ctx):
        """已归档行被重复上行时，变更携带 archived_at —— 客户端据此按归档合并。"""
        ctx = cloud_ctx
        goal_id = uuid.uuid4()
        _push(ctx, ctx["user_a"], [_mutation("savings_goal", goal_id, _goal_payload())])
        _push(ctx, ctx["user_a"], [_mutation(
            "savings_goal", goal_id, {}, operation="delete", version=1,
        )])
        cursor = _pull(ctx, ctx["user_a"]).json()["cursor"]

        # 第二台设备仍持有活跃副本，迁移时重复上行
        _push(ctx, ctx["user_a"], [_mutation("savings_goal", goal_id, _goal_payload())])
        changes = _pull(ctx, ctx["user_a"], cursor=cursor).json()["changes"]
        assert len(changes) == 1
        assert changes[0]["version"] == 2
        assert changes[0]["entity"]["archived_at"] is not None
        # 云端仍是归档态（未被这次上行复活）
        row = (await ctx["db"].execute(
            select(SavingsGoal).where(SavingsGoal.id == goal_id)
        )).scalar_one()
        assert row.archived_at is not None

    @pytest.mark.asyncio
    async def test_legacy_types_still_conflict_on_duplicate_insert(self, cloud_ctx):
        """幂等 insert 只对三个上云实体生效 —— 既有五类的语义不变。"""
        ctx = cloud_ctx
        book_id = uuid.uuid4()
        _push(ctx, ctx["user_a"], [_mutation("book", book_id, {"name": "新账本"})])
        dup = _push(ctx, ctx["user_a"], [_mutation("book", book_id, {"name": "重复"})])
        assert dup.status_code == 200
        assert dup.json()["results"][0]["status"] == "conflict"


# ---------------------------------------------------------------------------
# Push: update / delete
# ---------------------------------------------------------------------------

class TestCloudEntityUpdateDelete:

    @pytest.mark.asyncio
    async def test_update_savings_goal(self, cloud_ctx):
        ctx = cloud_ctx
        goal_id = uuid.uuid4()
        _push(ctx, ctx["user_a"], [_mutation("savings_goal", goal_id, _goal_payload())])
        resp = _push(ctx, ctx["user_a"], [_mutation(
            "savings_goal", goal_id,
            {"current_cents": 200_000, "name": "改名后的目标"},
            operation="update", version=1,
        )])
        assert resp.json()["results"][0]["status"] == "applied"
        assert resp.json()["results"][0]["version"] == 2
        row = (await ctx["db"].execute(
            select(SavingsGoal).where(SavingsGoal.id == goal_id)
        )).scalar_one()
        assert row.current_cents == 200_000
        assert row.name == "改名后的目标"
        assert row.target_cents == 500_000  # 未下发字段保持不变

    @pytest.mark.asyncio
    async def test_update_scheduled_rule_engine_managed_columns(self, cloud_ctx):
        """last_run_date / enabled / run_hour 可经 sync 上行（跨设备去重锚点）。"""
        ctx = cloud_ctx
        rule_id = uuid.uuid4()
        _push(ctx, ctx["user_a"], [_mutation("scheduled_rule", rule_id, _rule_payload(ctx))])
        resp = _push(ctx, ctx["user_a"], [_mutation(
            "scheduled_rule", rule_id,
            {"last_run_date": "2026-09-17", "enabled": False, "run_hour": 7, "run_minute": 5},
            operation="update", version=1,
        )])
        assert resp.json()["results"][0]["status"] == "applied"
        row = (await ctx["db"].execute(
            select(ScheduledRule).where(ScheduledRule.id == rule_id)
        )).scalar_one()
        assert row.last_run_date.isoformat() == "2026-09-17"
        assert row.enabled is False
        assert row.run_hour == 7 and row.run_minute == 5

    @pytest.mark.asyncio
    async def test_update_chat_persona(self, cloud_ctx):
        ctx = cloud_ctx
        persona_id = uuid.uuid4()
        _push(ctx, ctx["user_a"], [_mutation("chat_persona", persona_id, _persona_payload())])
        resp = _push(ctx, ctx["user_a"], [_mutation(
            "chat_persona", persona_id, {"persona": "新的人设"}, operation="update", version=1,
        )])
        assert resp.json()["results"][0]["status"] == "applied"
        row = (await ctx["db"].execute(
            select(ChatPersona).where(ChatPersona.id == persona_id)
        )).scalar_one()
        assert row.persona == "新的人设"
        assert row.name == "小贝"

    @pytest.mark.asyncio
    async def test_delete_archives_not_purges(self, cloud_ctx):
        ctx = cloud_ctx
        goal_id, rule_id, persona_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        _push(ctx, ctx["user_a"], [
            _mutation("savings_goal", goal_id, _goal_payload()),
            _mutation("scheduled_rule", rule_id, _rule_payload(ctx)),
            _mutation("chat_persona", persona_id, _persona_payload()),
        ])
        resp = _push(ctx, ctx["user_a"], [
            _mutation("savings_goal", goal_id, {}, operation="delete", version=1),
            _mutation("scheduled_rule", rule_id, {}, operation="delete", version=1),
            _mutation("chat_persona", persona_id, {}, operation="delete", version=1),
        ])
        assert [r["status"] for r in resp.json()["results"]] == ["applied"] * 3

        for model, eid in ((SavingsGoal, goal_id), (ScheduledRule, rule_id),
                           (ChatPersona, persona_id)):
            row = (await ctx["db"].execute(select(model).where(model.id == eid))).scalar_one()
            assert row.archived_at is not None
            assert row.version == 2

    @pytest.mark.asyncio
    async def test_delete_already_archived_is_conflict(self, cloud_ctx):
        """重复 delete 幂等：返回 conflict 让客户端重新对齐，而非再写一次归档。"""
        ctx = cloud_ctx
        persona_id = uuid.uuid4()
        _push(ctx, ctx["user_a"], [_mutation("chat_persona", persona_id, _persona_payload())])
        _push(ctx, ctx["user_a"], [_mutation(
            "chat_persona", persona_id, {}, operation="delete", version=1,
        )])
        again = _push(ctx, ctx["user_a"], [_mutation(
            "chat_persona", persona_id, {}, operation="delete", version=1,
        )])
        assert again.status_code == 200
        assert again.json()["results"][0]["status"] == "conflict"
        assert again.json()["results"][0]["current_version"] == 2

    @pytest.mark.asyncio
    async def test_update_stale_version_is_conflict(self, cloud_ctx):
        ctx = cloud_ctx
        goal_id = uuid.uuid4()
        _push(ctx, ctx["user_a"], [_mutation("savings_goal", goal_id, _goal_payload())])
        resp = _push(ctx, ctx["user_a"], [_mutation(
            "savings_goal", goal_id, {"current_cents": 1}, operation="update", version=99,
        )])
        result = resp.json()["results"][0]
        assert result["status"] == "conflict"
        assert result["current_version"] == 1


# ---------------------------------------------------------------------------
# Push: validation & ownership
# ---------------------------------------------------------------------------

class TestCloudEntityValidation:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("payload,label", [
        ({"name": "x" * 129, "target_cents": 100}, "name over 128"),
        ({"name": "ok", "target_cents": 0}, "target_cents not positive"),
        ({"name": "ok", "target_cents": 100, "current_cents": -1}, "current_cents negative"),
        ({"name": "ok", "target_cents": 100, "period_value": 3}, "period value without unit"),
        ({"name": "ok", "target_cents": 100, "period_unit": "week"}, "period unit without value"),
        ({"name": "ok", "target_cents": 100, "period_value": 3, "period_unit": "decade"},
         "invalid period unit"),
    ])
    async def test_savings_goal_validation_rejects(self, cloud_ctx, payload, label):
        ctx = cloud_ctx
        resp = _push(ctx, ctx["user_a"], [
            _mutation("savings_goal", uuid.uuid4(), payload),
        ])
        assert resp.status_code == 422, label

    @pytest.mark.asyncio
    @pytest.mark.parametrize("over,label", [
        ({"repeat": "fortnightly"}, "invalid repeat"),
        ({"run_hour": 24}, "run_hour out of range"),
        ({"run_minute": 60}, "run_minute out of range"),
        ({"start_date": "2026-09-10", "end_date": "2026-09-09"}, "end before start"),
        ({"amount_cents": 0}, "amount not positive"),
        ({"type": "transfer"}, "invalid type"),
        ({"note": "x" * 2001}, "note over 2000"),
        ({"name": "x" * 65}, "name over 64"),
    ])
    async def test_scheduled_rule_validation_rejects(self, cloud_ctx, over, label):
        ctx = cloud_ctx
        resp = _push(ctx, ctx["user_a"], [
            _mutation("scheduled_rule", uuid.uuid4(), _rule_payload(ctx, **over)),
        ])
        assert resp.status_code == 422, label

    @pytest.mark.asyncio
    async def test_chat_persona_persona_over_2000_rejected(self, cloud_ctx):
        ctx = cloud_ctx
        resp = _push(ctx, ctx["user_a"], [
            _mutation("chat_persona", uuid.uuid4(), _persona_payload(persona="x" * 2001)),
        ])
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_scheduled_rule_rejects_foreign_book(self, cloud_ctx):
        """book/category/account 归属校验：引用他人实体必须 404。"""
        ctx = cloud_ctx
        other_book = Book(id=uuid.uuid4(), owner_id=ctx["user_b"].id, name="Bob Book")
        ctx["db"].add(other_book)
        await ctx["db"].flush()
        resp = _push(ctx, ctx["user_a"], [
            _mutation("scheduled_rule", uuid.uuid4(),
                      _rule_payload(ctx, book_id=str(other_book.id))),
        ])
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_scheduled_rule_rejects_foreign_category(self, cloud_ctx):
        ctx = cloud_ctx
        other_book = Book(id=uuid.uuid4(), owner_id=ctx["user_b"].id, name="Bob Book")
        ctx["db"].add(other_book)
        await ctx["db"].flush()
        other_cat = Category(
            id=uuid.uuid4(), owner_id=ctx["user_b"].id, book_id=other_book.id,
            kind=CategoryKind.EXPENSE, name="Bob 分类",
        )
        ctx["db"].add(other_cat)
        await ctx["db"].flush()
        resp = _push(ctx, ctx["user_a"], [
            _mutation("scheduled_rule", uuid.uuid4(),
                      _rule_payload(ctx, category_id=str(other_cat.id))),
        ])
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_cross_user_visibility_isolated(self, cloud_ctx):
        """A 的 savings goal 对 B 既不可 pull 也不可 update。"""
        ctx = cloud_ctx
        goal_id = uuid.uuid4()
        _push(ctx, ctx["user_a"], [_mutation("savings_goal", goal_id, _goal_payload())])

        pulled = _pull(ctx, ctx["user_b"])
        assert all(c["entity_type"] != "savings_goal" for c in pulled.json()["changes"])

        # B 用同一 id 上行：幂等检查按 (id, owner) 查不到 A 的行 → 走真实 insert
        # → PK 冲突被捕获为 conflict（不泄露"该 id 已存在"的归属信息）
        resp = _push(ctx, ctx["user_b"], [_mutation("savings_goal", goal_id, _goal_payload())])
        assert resp.status_code == 200
        assert resp.json()["results"][0]["status"] == "conflict"

        # B 对 A 的行发 update → 404（归属校验拒绝）
        upd = _push(ctx, ctx["user_b"], [_mutation(
            "savings_goal", goal_id, {"current_cents": 999}, operation="update", version=1,
        )])
        assert upd.status_code == 404


# ---------------------------------------------------------------------------
# Pull
# ---------------------------------------------------------------------------

class TestCloudEntityPull:

    @pytest.mark.asyncio
    async def test_pull_returns_cloud_entities_with_serialized_fields(self, cloud_ctx):
        ctx = cloud_ctx
        goal_id, rule_id, persona_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        _push(ctx, ctx["user_a"], [
            _mutation("savings_goal", goal_id, _goal_payload()),
            _mutation("scheduled_rule", rule_id, _rule_payload(ctx)),
            _mutation("chat_persona", persona_id, _persona_payload()),
        ])
        data = _pull(ctx, ctx["user_a"]).json()
        by_type = {c["entity_type"]: c for c in data["changes"]}
        assert set(by_type) == {"savings_goal", "scheduled_rule", "chat_persona"}

        goal = by_type["savings_goal"]["entity"]
        assert goal["target_cents"] == 500_000
        assert goal["period_unit"] == "month"
        assert goal["archived_at"] is None
        assert goal["created_at"]  # ISO audit timestamps present

        rule = by_type["scheduled_rule"]["entity"]
        assert rule["repeat"] == "monthly"
        assert rule["start_date"] == "2026-01-01"
        assert rule["run_hour"] == 9
        assert rule["include_in_budget"] is True
        assert rule["last_run_date"] == "2026-09-01"

        persona = by_type["chat_persona"]["entity"]
        assert persona["name"] == "小贝"
        assert persona["persona"] == "你是小贝。"

    @pytest.mark.asyncio
    async def test_pull_delete_has_null_entity(self, cloud_ctx):
        ctx = cloud_ctx
        persona_id = uuid.uuid4()
        _push(ctx, ctx["user_a"], [_mutation("chat_persona", persona_id, _persona_payload())])
        cursor = _pull(ctx, ctx["user_a"]).json()["cursor"]
        _push(ctx, ctx["user_a"], [_mutation(
            "chat_persona", persona_id, {}, operation="delete", version=1,
        )])
        data = _pull(ctx, ctx["user_a"], cursor=cursor).json()
        change = data["changes"][0]
        assert change["operation"] == "delete"
        assert change["entity"] is None


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

class TestCloudEntityBootstrap:

    @pytest.mark.asyncio
    async def test_bootstrap_includes_cloud_entities(self, cloud_ctx):
        ctx = cloud_ctx
        _push(ctx, ctx["user_a"], [
            _mutation("savings_goal", uuid.uuid4(), _goal_payload()),
            _mutation("scheduled_rule", uuid.uuid4(), _rule_payload(ctx)),
            _mutation("chat_persona", uuid.uuid4(), _persona_payload()),
        ])
        data = _bootstrap(ctx, ctx["user_a"]).json()
        assert len(data["savings_goals"]) == 1
        assert len(data["scheduled_rules"]) == 1
        assert len(data["chat_personas"]) == 1
        assert data["savings_goals"][0]["name"] == "旅行基金"
        assert data["chat_personas"][0]["name"] == "小贝"
        assert len(data["checksum_v2"]) == 64

    @pytest.mark.asyncio
    async def test_bootstrap_excludes_archived_cloud_entities(self, cloud_ctx):
        ctx = cloud_ctx
        goal_id = uuid.uuid4()
        _push(ctx, ctx["user_a"], [_mutation("savings_goal", goal_id, _goal_payload())])
        _push(ctx, ctx["user_a"], [_mutation(
            "savings_goal", goal_id, {}, operation="delete", version=1,
        )])
        data = _bootstrap(ctx, ctx["user_a"]).json()
        assert data["savings_goals"] == []

    @pytest.mark.asyncio
    async def test_checksums_cover_expected_lists(self, cloud_ctx):
        """legacy 只覆盖五类、v2 覆盖八类。

        用公开 helper 反算服务端内联产出的两个 checksum：既证明"上云实体不参与
        legacy"（旧客户端的重算继续命中），也证明两处实现没有漂移。
        """
        ctx = cloud_ctx
        _push(ctx, ctx["user_a"], [
            _mutation("savings_goal", uuid.uuid4(), _goal_payload()),
            _mutation("scheduled_rule", uuid.uuid4(), _rule_payload(ctx)),
            _mutation("chat_persona", uuid.uuid4(), _persona_payload()),
        ])
        data = _bootstrap(ctx, ctx["user_a"]).json()
        assert len(data["savings_goals"]) == 1

        # legacy = 五类 + cursor（与上云实体无关）
        assert compute_bootstrap_checksum(
            books=data["books"], categories=data["categories"], accounts=data["accounts"],
            transactions=data["transactions"], budgets=data["budgets"],
            cursor=data["cursor"],
        ) == data["checksum"]

        # v2 = 八类 + cursor
        assert compute_bootstrap_checksum_v2(
            books=data["books"], categories=data["categories"], accounts=data["accounts"],
            transactions=data["transactions"], budgets=data["budgets"],
            savings_goals=data["savings_goals"],
            scheduled_rules=data["scheduled_rules"],
            chat_personas=data["chat_personas"],
            cursor=data["cursor"],
        ) == data["checksum_v2"]

        # 有上云实体时两者必然不同（否则说明实体没进 v2 的输入）
        assert data["checksum"] != data["checksum_v2"]

    @pytest.mark.asyncio
    async def test_checksums_agree_when_cloud_lists_empty(self, cloud_ctx):
        ctx = cloud_ctx
        data = _bootstrap(ctx, ctx["user_a"]).json()
        assert data["savings_goals"] == []
        assert data["checksum"] == data["checksum_v2"]

    def test_checksum_v2_matches_helper_with_cloud_lists(self):
        """bootstrap 内联算出的 v2 与公开 helper 一致（防止两处算法漂移）。"""
        goal = {"id": "g1", "name": "旅行基金", "target_cents": 500_000}
        rule = {"id": "r1", "name": "房租", "repeat": "monthly"}
        persona = {"id": "p1", "name": "小贝"}
        assert compute_bootstrap_checksum_v2(
            books=[], categories=[], accounts=[], transactions=[], budgets=[],
            savings_goals=[goal], scheduled_rules=[rule], chat_personas=[persona],
            cursor=5,
        ) != compute_bootstrap_checksum(
            books=[], categories=[], accounts=[], transactions=[], budgets=[], cursor=5,
        )
        assert compute_bootstrap_checksum_v2(
            books=[], categories=[], accounts=[], transactions=[], budgets=[],
            savings_goals=[], scheduled_rules=[], chat_personas=[], cursor=5,
        ) == compute_bootstrap_checksum(
            books=[], categories=[], accounts=[], transactions=[], budgets=[], cursor=5,
        )


# ---------------------------------------------------------------------------
# End-to-end: device migration round trip
# ---------------------------------------------------------------------------

class TestCloudEntityDeviceMigration:
    """T-M7-01 的核心用户故事：旧设备上行 → 新设备 bootstrap 复原。"""

    @pytest.mark.asyncio
    async def test_upload_then_bootstrap_round_trips(self, cloud_ctx):
        ctx = cloud_ctx
        goal_id, rule_id, persona_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        # 设备 1：把本地存量数据一次性上行
        up = _push(ctx, ctx["user_a"], [
            _mutation("savings_goal", goal_id, _goal_payload()),
            _mutation("scheduled_rule", rule_id, _rule_payload(ctx)),
            _mutation("chat_persona", persona_id, _persona_payload()),
        ])
        assert all(r["status"] == "applied" for r in up.json()["results"])

        # 设备 2（换机）: bootstrap 拿回三实体，id 与内容一致
        snapshot = _bootstrap(ctx, ctx["user_a"]).json()
        assert snapshot["savings_goals"][0]["id"] == str(goal_id)
        assert snapshot["savings_goals"][0]["target_cents"] == 500_000
        assert snapshot["scheduled_rules"][0]["id"] == str(rule_id)
        assert snapshot["scheduled_rules"][0]["repeat"] == "monthly"
        assert snapshot["chat_personas"][0]["id"] == str(persona_id)
        assert snapshot["chat_personas"][0]["name"] == "小贝"

    @pytest.mark.asyncio
    async def test_second_device_reupload_does_not_duplicate(self, cloud_ctx):
        """两台设备各自持有同一批本地数据时，第二台上行不产生第二份云端行。"""
        ctx = cloud_ctx
        goal_id = uuid.uuid4()
        _push(ctx, ctx["user_a"], [_mutation("savings_goal", goal_id, _goal_payload())])
        _push(ctx, ctx["user_a"], [_mutation("savings_goal", goal_id, _goal_payload())])
        rows = (await ctx["db"].execute(
            select(SavingsGoal).where(SavingsGoal.owner_id == ctx["user_a"].id)
        )).scalars().all()
        assert len(rows) == 1
