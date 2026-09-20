"""T-M7-05j: sync tests for the user_preference cloud entity.

Covers ``user_preference`` across push (insert / update / delete / idempotent
insert), pull, and bootstrap — including the two properties that make the
rollout safe for clients that predate the entity:

* the legacy ``checksum`` keeps covering exactly the original five lists
  (so an old client's recomputation still matches),
* ``checksum_v2`` keeps covering exactly the original eight lists — unchanged
  by this card, so clients built before 05j keep matching, and
* ``checksum_v3`` covers all nine, agreeing with v2 when the preference list
  is empty.

与 ``test_sync_cloud_entities.py``（T-M7-01 的三实体）同模式，但偏好项无
``archived_at`` / ``deleted_at``：清除即物理删。
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
from src.models.user import DeviceSession, User
from src.models.user_preference import SYNCABLE_KEYS, UserPreference
from src.modules.sync.service import (
    compute_bootstrap_checksum,
    compute_bootstrap_checksum_v2,
    compute_bootstrap_checksum_v3,
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
async def pref_ctx(client, db_session):
    """Two users; user A 已登录、可推送偏好。"""
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
    }

    client.app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------


def _pref_payload(**over) -> dict:
    payload = {"key": "default_book_id", "value": str(uuid.uuid4())}
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
        "/sync/pull",
        json={"cursor": cursor, "limit": 100},
        headers=_auth_header(user.id),
    )


def _bootstrap(ctx, user):
    return ctx["client"].post("/sync/bootstrap", headers=_auth_header(user.id))


async def _await_all_prefs(db, user_id: uuid.UUID) -> list[UserPreference]:
    result = await db.execute(
        select(UserPreference).where(UserPreference.user_id == user_id)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Push
# ---------------------------------------------------------------------------


class TestPreferencePush:
    async def test_insert_default_book_persists_one_row(self, pref_ctx):
        """插入一条 default_book_id：表里该 user_id+key 应当恰好一行。"""
        ctx = pref_ctx
        user = ctx["user_a"]
        pref_id = uuid.uuid4()
        book_uuid = str(uuid.uuid4())

        resp = _push(ctx, user, [
            _mutation("user_preference", pref_id, {
                "key": "default_book_id", "value": book_uuid,
            }),
        ])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["results"][0]["status"] == "applied"

        prefs = await _await_all_prefs(ctx["db"], user.id)
        assert len(prefs) == 1
        assert prefs[0].key == "default_book_id"
        assert prefs[0].value == book_uuid
        assert prefs[0].id == pref_id

    async def test_insert_rejects_unknown_key(self, pref_ctx):
        """未在 ``SYNCABLE_KEYS`` 里的 key 返 422（不是 500）。"""
        ctx = pref_ctx
        user = ctx["user_a"]
        resp = _push(ctx, user, [
            _mutation("user_preference", uuid.uuid4(), {
                "key": "sync_cursor", "value": "42",
            }),
        ])
        assert resp.status_code == 422, resp.text

    async def test_insert_rejects_non_string_value(self, pref_ctx):
        """value 必须是字符串——数字直接传会返 422。"""
        ctx = pref_ctx
        user = ctx["user_a"]
        resp = _push(ctx, user, [
            _mutation("user_preference", uuid.uuid4(), {
                "key": "default_book_id", "value": 42,
            }),
        ])
        assert resp.status_code == 422, resp.text

    async def test_insert_rejects_overly_long_value(self, pref_ctx):
        """value 超过 16KB 返 422（与 ``meta_set`` 执行体的长度上限同源）。"""
        ctx = pref_ctx
        user = ctx["user_a"]
        resp = _push(ctx, user, [
            _mutation("user_preference", uuid.uuid4(), {
                "key": "widget_config", "value": "x" * 20_000,
            }),
        ])
        assert resp.status_code == 422, resp.text

    async def test_idempotent_insert_on_same_user_key_does_not_overwrite(
        self, pref_ctx,
    ):
        """两个设备对同一个 ``(user_id, key)`` 各推一次——云端权威优先，第二次
        幂等返 existing 行的版本（与 T-M7-01 三实体的幂等上行同模式）。"""
        ctx = pref_ctx
        user = ctx["user_a"]
        book_a, book_b = str(uuid.uuid4()), str(uuid.uuid4())
        id_1, id_2 = uuid.uuid4(), uuid.uuid4()

        resp1 = _push(ctx, user, [
            _mutation("user_preference", id_1, {
                "key": "default_book_id", "value": book_a,
            }),
        ])
        assert resp1.status_code == 200
        v1 = resp1.json()["results"][0]["version"]

        resp2 = _push(ctx, user, [
            _mutation("user_preference", id_2, {
                "key": "default_book_id", "value": book_b,
            }),
        ])
        assert resp2.status_code == 200
        second = resp2.json()["results"][0]
        # 第二次幂等命中 → 云端权威优先，第二次 push 的 book_b 不进表，版本不变。
        assert second["status"] == "applied"
        assert second["version"] == v1

        prefs = await _await_all_prefs(ctx["db"], user.id)
        assert len(prefs) == 1
        assert prefs[0].value == book_a  # 云端权威优先：保留 book_a
        assert prefs[0].id == id_1  # id 保留第一次的

    async def test_update_changes_value_and_bumps_version(self, pref_ctx):
        """update 改 value、version++。"""
        ctx = pref_ctx
        user = ctx["user_a"]
        pref_id = uuid.uuid4()
        book_a, book_b = str(uuid.uuid4()), str(uuid.uuid4())

        insert_resp = _push(ctx, user, [
            _mutation("user_preference", pref_id, {
                "key": "default_book_id", "value": book_a,
            }),
        ])
        v1 = insert_resp.json()["results"][0]["version"]

        update_resp = _push(ctx, user, [
            _mutation("user_preference", pref_id, {"value": book_b},
                      operation="update", version=v1),
        ])
        assert update_resp.status_code == 200, update_resp.text
        v2 = update_resp.json()["results"][0]["version"]
        assert v2 == v1 + 1

        prefs = await _await_all_prefs(ctx["db"], user.id)
        assert prefs[0].value == book_b

    async def test_update_rejects_key_change(self, pref_ctx):
        """update 只允许改 ``value``——改 key 不会生效（payload 里的 key 被忽略）。"""
        ctx = pref_ctx
        user = ctx["user_a"]
        pref_id = uuid.uuid4()

        insert_resp = _push(ctx, user, [
            _mutation("user_preference", pref_id, {
                "key": "default_book_id", "value": str(uuid.uuid4()),
            }),
        ])
        v1 = insert_resp.json()["results"][0]["version"]

        # update 里 payload 带 key 字段——但 service 只认 value。
        update_resp = _push(ctx, user, [
            _mutation("user_preference", pref_id,
                      {"key": "default_account_id", "value": "x"},
                      operation="update", version=v1),
        ])
        assert update_resp.status_code == 200, update_resp.text
        prefs = await _await_all_prefs(ctx["db"], user.id)
        assert prefs[0].key == "default_book_id"  # key 没变
        assert prefs[0].value == "x"  # value 变了

    async def test_delete_is_physical_not_soft(self, pref_ctx):
        """偏好项的 delete 是物理删——行从表里消失。"""
        ctx = pref_ctx
        user = ctx["user_a"]
        pref_id = uuid.uuid4()

        insert_resp = _push(ctx, user, [
            _mutation("user_preference", pref_id, {
                "key": "default_book_id", "value": str(uuid.uuid4()),
            }),
        ])
        v1 = insert_resp.json()["results"][0]["version"]

        del_resp = _push(ctx, user, [
            _mutation("user_preference", pref_id, {},
                      operation="delete", version=v1),
        ])
        assert del_resp.status_code == 200, del_resp.text

        prefs = await _await_all_prefs(ctx["db"], user.id)
        assert prefs == []

    async def test_user_b_cannot_touch_user_a_preferences(self, pref_ctx):
        """user B 推同一行 id 的 update 应返 404（归属校验拒绝，行归 user A）。"""
        ctx = pref_ctx
        user_a, user_b = ctx["user_a"], ctx["user_b"]
        pref_id = uuid.uuid4()

        insert_resp = _push(ctx, user_a, [
            _mutation("user_preference", pref_id, {
                "key": "default_book_id", "value": str(uuid.uuid4()),
            }),
        ])
        v1 = insert_resp.json()["results"][0]["version"]

        resp = _push(ctx, user_b, [
            _mutation("user_preference", pref_id,
                      {"value": "hijacked"},
                      operation="update", version=v1),
        ])
        # ownership 校验 → 整个请求 404（AppException 冒泡）
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Pull
# ---------------------------------------------------------------------------


class TestPreferencePull:
    async def test_pull_returns_the_change(self, pref_ctx):
        """push 一条 preference 后，pull 能拿到那条 change，entity 序列化含 key/value。"""
        ctx = pref_ctx
        user = ctx["user_a"]
        pref_id = uuid.uuid4()
        book_uuid = str(uuid.uuid4())

        _push(ctx, user, [
            _mutation("user_preference", pref_id, {
                "key": "default_book_id", "value": book_uuid,
            }),
        ])

        pull_resp = _pull(ctx, user)
        assert pull_resp.status_code == 200
        body = pull_resp.json()
        pref_changes = [
            c for c in body["changes"] if c["entity_type"] == "user_preference"
        ]
        assert len(pref_changes) == 1
        assert pref_changes[0]["entity_id"] == str(pref_id)
        assert pref_changes[0]["operation"] == "insert"
        assert pref_changes[0]["entity"]["key"] == "default_book_id"
        assert pref_changes[0]["entity"]["value"] == book_uuid

    async def test_delete_change_has_no_entity(self, pref_ctx):
        """偏好项 delete 后 pull 到的 change entity 为 null（与其他实体同口径）。"""
        ctx = pref_ctx
        user = ctx["user_a"]
        pref_id = uuid.uuid4()

        insert_resp = _push(ctx, user, [
            _mutation("user_preference", pref_id, {
                "key": "default_book_id", "value": str(uuid.uuid4()),
            }),
        ])
        v1 = insert_resp.json()["results"][0]["version"]

        _push(ctx, user, [
            _mutation("user_preference", pref_id, {},
                      operation="delete", version=v1),
        ])

        pull_resp = _pull(ctx, user)
        assert pull_resp.status_code == 200
        body = pull_resp.json()
        delete_changes = [
            c for c in body["changes"]
            if c["entity_type"] == "user_preference" and c["operation"] == "delete"
        ]
        assert len(delete_changes) == 1
        assert delete_changes[0]["entity"] is None


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


class TestPreferenceBootstrap:
    async def test_bootstrap_emits_preferences_and_updates_v3_checksum(
        self, pref_ctx,
    ):
        """push 一条 preference 后，bootstrap 的 ``user_preferences`` 列表有值，
        ``checksum_v3`` 反映它，而 ``checksum`` / ``checksum_v2`` 都不受影响
        —— 后两者是已发布客户端的比对基准，动了就是让它们 bootstrap 失配。"""
        ctx = pref_ctx
        user = ctx["user_a"]
        pref_id = uuid.uuid4()
        book_uuid = str(uuid.uuid4())

        _push(ctx, user, [
            _mutation("user_preference", pref_id, {
                "key": "default_book_id", "value": book_uuid,
            }),
        ])

        bs = _bootstrap(ctx, user)
        assert bs.status_code == 200
        body = bs.json()

        assert len(body["user_preferences"]) == 1
        pref = body["user_preferences"][0]
        assert pref["key"] == "default_book_id"
        assert pref["value"] == book_uuid
        assert pref["id"] == str(pref_id)

        # 旧 checksum 不包含 user_preferences：与 push 前重算的值一致。
        legacy_before = compute_bootstrap_checksum(
            books=[], categories=[], accounts=[],
            transactions=[], budgets=[], cursor=body["cursor"],
        )
        assert body["checksum"] == legacy_before

        # v2 也不包含 user_preferences：八类输入下与"无偏好"重算一致。
        v2 = compute_bootstrap_checksum_v2(
            books=[], categories=[], accounts=[],
            transactions=[], budgets=[],
            savings_goals=[], scheduled_rules=[], chat_personas=[],
            cursor=body["cursor"],
        )
        assert body["checksum_v2"] == v2

        # v3 包含 user_preferences：与"空偏好"的 v3 不同（有值 vs 无值）。
        v3_with_pref = compute_bootstrap_checksum_v3(
            books=[], categories=[], accounts=[],
            transactions=[], budgets=[],
            savings_goals=[], scheduled_rules=[], chat_personas=[],
            user_preferences=body["user_preferences"],
            cursor=body["cursor"],
        )
        assert body["checksum_v3"] == v3_with_pref

        v3_without = compute_bootstrap_checksum_v3(
            books=[], categories=[], accounts=[],
            transactions=[], budgets=[],
            savings_goals=[], scheduled_rules=[], chat_personas=[],
            user_preferences=[],
            cursor=body["cursor"],
        )
        assert v3_with_pref != v3_without
        # 空偏好时 v3 与 v2 逐字节相同（空列表不产出分段）——这正是 05j 最初
        # 把它并进 v2 时问题被掩盖的原因。
        assert v3_without == v2

    async def test_bootstrap_preferences_empty_for_fresh_user(self, pref_ctx):
        """user_b 没推过任何偏好：``user_preferences`` 列表为空，``checksum_v3``
        与"无偏好"重算一致（且等于 v2）。"""
        ctx = pref_ctx
        user = ctx["user_b"]

        bs = _bootstrap(ctx, user)
        assert bs.status_code == 200
        body = bs.json()

        assert body["user_preferences"] == []
        v3 = compute_bootstrap_checksum_v3(
            books=[], categories=[], accounts=[],
            transactions=[], budgets=[],
            savings_goals=[], scheduled_rules=[], chat_personas=[],
            user_preferences=[],
            cursor=body["cursor"],
        )
        assert body["checksum_v3"] == v3
        assert body["checksum_v3"] == body["checksum_v2"]


# ---------------------------------------------------------------------------
# Registry sanity
# ---------------------------------------------------------------------------


class TestSyncableKeys:
    def test_syncable_keys_is_frozen_and_contains_expected_keys(self):
        """``SYNCABLE_KEYS`` 是 frozenset 且包含 6 个上云键。"""
        assert isinstance(SYNCABLE_KEYS, frozenset)
        assert SYNCABLE_KEYS == frozenset({
            "default_book_id", "default_account_id",
            "book_order", "account_order",
            "widget_config", "user_profile",
        })

    def test_device_only_keys_are_not_syncable(self):
        """设备自管量（sync_cursor / master_book_id / 等）不在 SYNCABLE_KEYS。"""
        for device_key in (
            "sync_cursor", "master_book_id", "budget_carryover_mark",
            "personas_seeded", "cloud_entities_uploaded_v1",
            "avatar_uri", "display_name", "budget_carryover",
            "active_persona_id",
        ):
            assert device_key not in SYNCABLE_KEYS, (
                f"{device_key} 是设备自管量，不应上云"
            )
