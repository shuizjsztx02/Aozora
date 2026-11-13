"""T-M7-05c: 预算存钱 / 定时 / 角色三域的 v2 执行面（``/ai/validate`` + ``/ai/execute``）。

验收映射（P4 计划 §三 05c）：
- 三域的写 op 经 v2 提案与审批门、**服务端真的执行得了** → 各测试类
- **每次权威直写都同事务登一条 sync change**（T14-8 口径）→ 各测试类 + Pull 用例
- **离线语义**：写入不依赖设备在场，设备 pull 到同一条变更 → TestOfflineSemantics
- 设备类 op（``persona_switch``）服务端不执行、有自己的拒绝码 → TestDeviceOp
- 伪造 payload 的形状不对一律 400、别人的 id 一律 403（结构通过不是执行许可）→ 各文件

用例全部走真实端点 + 真实 SQLite（临时文件库，不用 ``tmp_path``——本机 basetemp
权限已坏）。实体用 **/sync/push** 造（那是它们的真实上行路径），提案是手工构造的
v2 信封（客户端可达的那一面）。
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.testclient import TestClient


@pytest.fixture()
def v2_env():
    """TestClient + 隔离库（与 ``test_ai_proposal_v2_execution.py`` 同形）。"""
    from src.main import app
    from src.modules.ai.service import _ai_rate_limits
    from src.modules.auth.dependencies import _rate_limits

    _rate_limits.clear()
    _ai_rate_limits.clear()

    db_dir = Path(tempfile.mkdtemp(prefix="aozora_v2_05c_db_"))
    engine = create_async_engine(f"sqlite+aiosqlite:///{(db_dir / 'test.db').as_posix()}")
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _create_tables():
        from src.models.base import Base
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

    app.dependency_overrides.clear()
    from src.common.database import get_db_session
    app.dependency_overrides[get_db_session] = _override_get_db_session

    with TestClient(app) as c:
        yield c, factory

    asyncio.run(engine.dispose())
    shutil.rmtree(db_dir, ignore_errors=True)
    app.dependency_overrides.clear()
    _rate_limits.clear()
    _ai_rate_limits.clear()


# ── 客户端与实体 ───────────────────────────────────────────────────────────


def _guest(env, device_id="v2-05c-device") -> dict:
    c, _ = env
    # 隐私同意先于一切同步：``/sync/push`` 对未同意的设备回 403 consent_required。
    consented = c.post("/consent", json={
        "device_id": device_id,
        "policy_version": "2026-01-01",
        "consented_at": "2026-01-01T00:00:00+00:00",
    })
    assert consented.status_code == 201, consented.text
    resp = c.post("/auth/guest", json={"device_id": device_id, "nickname": "05c-tester"})
    assert resp.status_code == 200
    data = resp.json()
    return {"id": data["user_id"], "token": data["tokens"]["access_token"]}


def _auth(user: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {user['token']}"}


def _v2(op: str, payload: dict, *, proposal_id: str | None = None):
    return {
        "contractVersion": 2,
        "proposalId": proposal_id or f"v2-{uuid.uuid4().hex[:12]}",
        "action": op,
        "confidence": 0.95,
        "requiresConfirmation": True,
        "message": "（测试提案）",
        "warnings": [],
        "payload": payload,
    }


def _validate(env, user, book_id, proposal):
    c, _ = env
    return c.post("/ai/validate", json={
        "proposal": proposal, "book_id": str(book_id),
    }, headers=_auth(user))


def _execute(env, user, book_id, proposal, token="confirm"):
    c, _ = env
    return c.post("/ai/execute", json={
        "proposal": proposal, "book_id": str(book_id), "confirmation_token": token,
    }, headers=_auth(user))


def _mk_book(c, user, name="日常") -> uuid.UUID:
    resp = c.post("/books", json={"name": name}, headers=_auth(user))
    assert resp.status_code == 201
    return uuid.UUID(resp.json()["id"])


def _mk_category(c, user, kind="expense", name="餐饮") -> uuid.UUID:
    resp = c.post("/categories", json={"name": name, "kind": kind}, headers=_auth(user))
    assert resp.status_code == 201
    return uuid.UUID(resp.json()["id"])


def _mk_account(c, user, name="招行卡") -> uuid.UUID:
    resp = c.post("/accounts", json={"name": name}, headers=_auth(user))
    assert resp.status_code == 201
    return uuid.UUID(resp.json()["id"])


def _push(c, user, mutations: list[dict]):
    """走**真实上行路径**造三实体（AI 工具改的是它们，不是测试直接插的行）。"""
    resp = c.post("/sync/push", json={"mutations": mutations}, headers=_auth(user))
    assert resp.status_code == 200, resp.text
    return resp.json()


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


def _mk_savings(c, user, **over) -> uuid.UUID:
    goal_id = uuid.uuid4()
    payload = {
        "name": "旅行基金", "target_cents": 500_000, "current_cents": 120_000,
        "icon": "🎯", "period_value": 6, "period_unit": "month",
    }
    payload.update(over)
    _push(c, user, [_mutation("savings_goal", goal_id, payload)])
    return goal_id


def _mk_rule(c, user, book_id, category_id, **over) -> uuid.UUID:
    rule_id = uuid.uuid4()
    payload = {
        "type": "expense", "name": "房租", "amount_cents": 350_000,
        "book_id": str(book_id), "category_id": str(category_id),
        "note": "每月一号", "repeat": "monthly", "start_date": "2026-01-01",
        "run_hour": 9, "run_minute": 30,
    }
    payload.update(over)
    _push(c, user, [_mutation("scheduled_rule", rule_id, payload)])
    return rule_id


def _mk_persona(c, user, **over) -> uuid.UUID:
    persona_id = uuid.uuid4()
    payload = {"name": "小贝", "avatar": "🐻", "persona": "你是小贝。"}
    payload.update(over)
    _push(c, user, [_mutation("chat_persona", persona_id, payload)])
    return persona_id


def _rows(factory, model, **filters):
    async def _run():
        async with factory() as session:
            stmt = select(model)
            for key, value in filters.items():
                stmt = stmt.where(getattr(model, key) == value)
            return list((await session.execute(stmt)).scalars().all())
    return asyncio.run(_run())


def _changes(factory, entity_id: uuid.UUID):
    from src.models.sync import SyncChange
    rows = _rows(factory, SyncChange, entity_id=entity_id)
    return [(str(ch.operation), ch.version) for ch in rows]


def _pull(c, user, cursor: int = 0):
    resp = c.post("/sync/pull", json={"cursor": cursor, "limit": 100}, headers=_auth(user))
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── 预算域 ─────────────────────────────────────────────────────────────────


class TestV2BudgetOps:
    def test_budget_set_creates_a_row_and_registers_a_sync_change(self, v2_env):
        from src.models.budget import Budget

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        proposal = _v2("budget_set", {"period": "2026-09", "amountCents": 500_000})
        assert _validate(v2_env, user, book_id, proposal).json()["valid"] is True

        resp = _execute(v2_env, user, book_id, proposal)
        assert resp.status_code == 200, resp.text
        budget_id = uuid.UUID(resp.json()["transaction_ids"][0])

        rows = _rows(factory, Budget, id=budget_id)
        assert (rows[0].amount_cents, rows[0].period, rows[0].category_id) == (
            500_000, "2026-09", None,
        )
        assert _changes(factory, budget_id) == [("insert", 1)]
        assert ("budget", rows[0].book_id) == ("budget", book_id)

    def test_setting_the_same_budget_again_updates_it(self, v2_env):
        from src.models.budget import Budget

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        first = _execute(v2_env, user, book_id, _v2(
            "budget_set", {"period": "2026-09", "amountCents": 500_000},
        )).json()
        budget_id = uuid.UUID(first["transaction_ids"][0])
        version = _rows(factory, Budget, id=budget_id)[0].version

        second = _execute(v2_env, user, book_id, _v2(
            "budget_set", {"period": "2026-09", "amountCents": 700_000, "version": version},
        ))
        assert second.status_code == 200
        assert second.json()["transaction_ids"] == [str(budget_id)], "同一条被更新，不是新建"
        assert len(_rows(factory, Budget, period="2026-09")) == 1
        assert _changes(factory, budget_id) == [("insert", 1), ("update", 2)]

    def test_a_stale_budget_version_is_a_409(self, v2_env):
        from src.models.budget import Budget

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        created = _execute(v2_env, user, book_id, _v2(
            "budget_set", {"period": "2026-09", "amountCents": 500_000},
        )).json()
        budget_id = uuid.UUID(created["transaction_ids"][0])

        stale = _execute(v2_env, user, book_id, _v2(
            "budget_set", {"period": "2026-09", "amountCents": 1, "version": 99},
        ))
        assert stale.status_code == 409, stale.text
        assert stale.json()["code"] == "version_conflict"
        assert _rows(factory, Budget, id=budget_id)[0].amount_cents == 500_000

    def test_budget_set_category_binds_the_category(self, v2_env):
        from src.models.budget import Budget

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)
        category_id = _mk_category(c, user)

        resp = _execute(v2_env, user, book_id, _v2("budget_set_category", {
            "period": "2026-09", "categoryId": str(category_id), "amountCents": 150_000,
        }))
        assert resp.status_code == 200, resp.text
        budget_id = uuid.UUID(resp.json()["transaction_ids"][0])
        assert _rows(factory, Budget, id=budget_id)[0].category_id == category_id

    def test_budget_delete_soft_deletes_and_can_be_set_again(self, v2_env):
        """删掉再设：**新行**而不是把已删的那行改活。

        ``BudgetService.upsert_budget`` 的查询不过滤 ``deleted_at``——走它会让
        「删了再设」改到那条已删的行上（行在、版本涨了、金额变了，用户永远看不到）。
        """
        from src.models.budget import Budget

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        first = _execute(v2_env, user, book_id, _v2(
            "budget_set", {"period": "2026-09", "amountCents": 500_000},
        )).json()
        old_id = uuid.UUID(first["transaction_ids"][0])

        deleted = _execute(v2_env, user, book_id, _v2(
            "budget_delete", {"period": "2026-09"},
        ))
        assert deleted.status_code == 200, deleted.text
        assert _rows(factory, Budget, id=old_id)[0].deleted_at is not None
        assert _changes(factory, old_id) == [("insert", 1), ("delete", 2)]

        again = _execute(v2_env, user, book_id, _v2(
            "budget_set", {"period": "2026-09", "amountCents": 800_000},
        ))
        assert again.status_code == 200, again.text
        new_id = uuid.UUID(again.json()["transaction_ids"][0])
        assert new_id != old_id, "删掉的那条不该被复用"
        live = [b for b in _rows(factory, Budget, period="2026-09") if b.deleted_at is None]
        assert [b.amount_cents for b in live] == [800_000]

    def test_deleting_a_budget_that_is_not_there_is_a_404(self, v2_env):
        c, _ = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        resp = _execute(v2_env, user, book_id, _v2(
            "budget_delete", {"period": "2026-09"},
        ))
        assert resp.status_code == 404, resp.text

    def test_a_malformed_period_is_a_400(self, v2_env):
        c, _ = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        resp = _execute(v2_env, user, book_id, _v2(
            "budget_set", {"period": "2026/09", "amountCents": 100},
        ))
        assert resp.status_code == 400
        assert resp.json()["code"] == "ai_invalid_payload"

    def test_another_users_category_is_rejected_by_authorization(self, v2_env):
        c, _ = v2_env
        user = _guest(v2_env, device_id="owner-device")
        other = _guest(v2_env, device_id="intruder-device")
        book_id = _mk_book(c, user)
        foreign_category = _mk_category(c, other, name="别人的分类")

        proposal = _v2("budget_set_category", {
            "period": "2026-09", "categoryId": str(foreign_category), "amountCents": 100,
        })
        verdict = _validate(v2_env, user, book_id, proposal).json()
        assert verdict["valid"] is False
        assert verdict["verdict"] == "rejected_hallucination"
        assert _execute(v2_env, user, book_id, proposal).status_code == 403


# ── 存钱域 ─────────────────────────────────────────────────────────────────


class TestV2SavingsOps:
    def test_create_writes_the_goal_and_registers_a_sync_change(self, v2_env):
        from src.models.savings import SavingsGoal

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        resp = _execute(v2_env, user, book_id, _v2("savings_create", {
            "name": "相机基金", "targetCents": 800_000, "periodValue": 3, "periodUnit": "month",
        }))
        assert resp.status_code == 200, resp.text
        goal_id = uuid.UUID(resp.json()["transaction_ids"][0])

        goal = _rows(factory, SavingsGoal, id=goal_id)[0]
        assert (goal.name, goal.target_cents, goal.current_cents) == ("相机基金", 800_000, 0)
        assert (goal.period_value, goal.period_unit) == (3, "month")
        assert _changes(factory, goal_id) == [("insert", 1)]

    def test_a_deposit_accumulates_on_the_server(self, v2_env):
        """**存入是服务端累加**（铁律 2）：payload 只带「加多少」，不带「加完是多少」。"""
        from src.models.savings import SavingsGoal

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)
        goal_id = _mk_savings(c, user, current_cents=120_000)
        version = _rows(factory, SavingsGoal, id=goal_id)[0].version

        resp = _execute(v2_env, user, book_id, _v2("savings_update", {
            "savingsId": str(goal_id), "version": version, "addAmountCents": 50_000,
        }))
        assert resp.status_code == 200, resp.text
        goal = _rows(factory, SavingsGoal, id=goal_id)[0]
        assert goal.current_cents == 170_000
        assert _changes(factory, goal_id)[-1] == ("update", version + 1)

    def test_setting_and_adding_at_once_is_refused(self, v2_env):
        from src.models.savings import SavingsGoal

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)
        goal_id = _mk_savings(c, user)
        version = _rows(factory, SavingsGoal, id=goal_id)[0].version

        resp = _execute(v2_env, user, book_id, _v2("savings_update", {
            "savingsId": str(goal_id), "version": version,
            "addAmountCents": 100, "currentCents": 200,
        }))
        assert resp.status_code == 400
        assert resp.json()["code"] == "ai_invalid_payload"

    def test_a_stale_version_is_a_409(self, v2_env):
        from src.models.savings import SavingsGoal

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)
        goal_id = _mk_savings(c, user, current_cents=120_000)

        resp = _execute(v2_env, user, book_id, _v2("savings_update", {
            "savingsId": str(goal_id), "version": 99, "addAmountCents": 100,
        }))
        assert resp.status_code == 409
        assert _rows(factory, SavingsGoal, id=goal_id)[0].current_cents == 120_000

    def test_half_a_period_is_a_400_not_a_silent_no_op(self, v2_env):
        from src.models.savings import SavingsGoal

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)
        goal_id = _mk_savings(c, user)
        version = _rows(factory, SavingsGoal, id=goal_id)[0].version

        resp = _execute(v2_env, user, book_id, _v2("savings_update", {
            "savingsId": str(goal_id), "version": version, "periodValue": 2,
        }))
        assert resp.status_code == 400
        assert resp.json()["code"] == "ai_invalid_payload"

    def test_clearing_the_period_takes_both_nulls(self, v2_env):
        from src.models.savings import SavingsGoal

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)
        goal_id = _mk_savings(c, user, period_value=6, period_unit="month")
        version = _rows(factory, SavingsGoal, id=goal_id)[0].version

        resp = _execute(v2_env, user, book_id, _v2("savings_update", {
            "savingsId": str(goal_id), "version": version,
            "periodValue": None, "periodUnit": None,
        }))
        assert resp.status_code == 200, resp.text
        goal = _rows(factory, SavingsGoal, id=goal_id)[0]
        assert (goal.period_value, goal.period_unit) == (None, None)

    def test_an_overlong_name_is_a_400(self, v2_env):
        c, _ = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        resp = _execute(v2_env, user, book_id, _v2(
            "savings_create", {"name": "x" * 200, "targetCents": 1000},
        ))
        assert resp.status_code == 400
        assert resp.json()["code"] == "ai_invalid_payload"

    def test_archive_keeps_the_row_and_registers_a_delete(self, v2_env):
        from src.models.savings import SavingsGoal

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)
        goal_id = _mk_savings(c, user)

        resp = _execute(v2_env, user, book_id, _v2(
            "savings_archive", {"savingsId": str(goal_id)},
        ))
        assert resp.status_code == 200, resp.text
        goal = _rows(factory, SavingsGoal, id=goal_id)[0]
        assert goal.archived_at is not None
        assert _changes(factory, goal_id)[-1][0] == "delete"

    def test_another_users_goal_is_rejected_by_authorization(self, v2_env):
        c, _ = v2_env
        user = _guest(v2_env, device_id="owner-device-2")
        other = _guest(v2_env, device_id="intruder-device-2")
        book_id = _mk_book(c, user)
        foreign_goal = _mk_savings(c, other, name="别人的目标")

        proposal = _v2("savings_archive", {"savingsId": str(foreign_goal)})
        assert _validate(v2_env, user, book_id, proposal).json()["valid"] is False
        assert _execute(v2_env, user, book_id, proposal).status_code == 403


# ── 定时域 ─────────────────────────────────────────────────────────────────


class TestV2ScheduledOps:
    def test_create_writes_the_rule_with_the_sync_defaults(self, v2_env):
        from src.models.scheduled import ScheduledRule

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)
        category_id = _mk_category(c, user)

        resp = _execute(v2_env, user, book_id, _v2("scheduled_create", {
            "name": "早餐", "type": "expense", "amountCents": 1500,
            "categoryId": str(category_id), "repeat": "daily", "startDate": "2026-10-01",
        }))
        assert resp.status_code == 200, resp.text
        rule_id = uuid.UUID(resp.json()["transaction_ids"][0])

        rule = _rows(factory, ScheduledRule, id=rule_id)[0]
        assert (rule.name, rule.repeat, rule.book_id) == ("早餐", "daily", book_id)
        assert (rule.run_hour, rule.run_minute) == (8, 0), "缺省值与 sync 的 _apply_insert 一致"
        assert rule.include_in_budget is True and rule.enabled is True
        assert _changes(factory, rule_id) == [("insert", 1)]

    def test_create_without_a_required_field_is_a_400(self, v2_env):
        c, _ = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        resp = _execute(v2_env, user, book_id, _v2("scheduled_create", {
            "name": "早餐", "type": "expense", "amountCents": 1500,
        }))
        assert resp.status_code == 400
        assert resp.json()["code"] == "ai_invalid_payload"

    def test_a_bad_repeat_enum_is_a_400(self, v2_env):
        c, _ = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)
        category_id = _mk_category(c, user)

        resp = _execute(v2_env, user, book_id, _v2("scheduled_create", {
            "name": "早餐", "type": "expense", "amountCents": 1500,
            "categoryId": str(category_id), "repeat": "每个工作日",
            "startDate": "2026-10-01",
        }))
        assert resp.status_code == 400

    def test_update_changes_fields_and_bumps_the_version(self, v2_env):
        from src.models.scheduled import ScheduledRule

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)
        category_id = _mk_category(c, user)
        rule_id = _mk_rule(c, user, book_id, category_id, amount_cents=350_000)
        version = _rows(factory, ScheduledRule, id=rule_id)[0].version

        resp = _execute(v2_env, user, book_id, _v2("scheduled_update", {
            "ruleId": str(rule_id), "version": version,
            "amountCents": 400_000, "enabled": False,
        }))
        assert resp.status_code == 200, resp.text
        rule = _rows(factory, ScheduledRule, id=rule_id)[0]
        assert (rule.amount_cents, rule.enabled) == (400_000, False)
        assert _changes(factory, rule_id)[-1] == ("update", version + 1)

    def test_an_end_date_before_the_start_is_a_400(self, v2_env):
        from src.models.scheduled import ScheduledRule

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)
        category_id = _mk_category(c, user)
        rule_id = _mk_rule(c, user, book_id, category_id)
        version = _rows(factory, ScheduledRule, id=rule_id)[0].version

        resp = _execute(v2_env, user, book_id, _v2("scheduled_update", {
            "ruleId": str(rule_id), "version": version, "endDate": "2025-12-31",
        }))
        assert resp.status_code == 400
        assert resp.json()["code"] == "ai_invalid_payload"

    def test_a_rule_in_another_book_is_rejected(self, v2_env):
        c, _ = v2_env
        user = _guest(v2_env)
        book_a = _mk_book(c, user, name="账本A")
        book_b = _mk_book(c, user, name="账本B")
        category_id = _mk_category(c, user)
        rule_in_a = _mk_rule(c, user, book_a, category_id)

        proposal = _v2("scheduled_update", {
            "ruleId": str(rule_in_a), "version": 1, "amountCents": 1,
        })
        assert _validate(v2_env, user, book_b, proposal).json()["valid"] is False
        assert _execute(v2_env, user, book_b, proposal).status_code == 403

    def test_archive_keeps_the_row(self, v2_env):
        from src.models.scheduled import ScheduledRule

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)
        category_id = _mk_category(c, user)
        rule_id = _mk_rule(c, user, book_id, category_id)

        resp = _execute(v2_env, user, book_id, _v2(
            "scheduled_archive", {"ruleId": str(rule_id)},
        ))
        assert resp.status_code == 200, resp.text
        rule = _rows(factory, ScheduledRule, id=rule_id)[0]
        assert rule.archived_at is not None
        assert _changes(factory, rule_id)[-1][0] == "delete"


# ── 角色域 ─────────────────────────────────────────────────────────────────


class TestV2PersonaOps:
    def test_create_update_and_archive_register_sync_changes(self, v2_env):
        from src.models.persona import ChatPersona

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        created = _execute(v2_env, user, book_id, _v2("persona_create", {
            "name": "猫娘", "avatar": "🐱", "persona": "说话带喵",
        }))
        assert created.status_code == 200, created.text
        persona_id = uuid.UUID(created.json()["transaction_ids"][0])
        assert _rows(factory, ChatPersona, id=persona_id)[0].persona == "说话带喵"

        version = _rows(factory, ChatPersona, id=persona_id)[0].version
        updated = _execute(v2_env, user, book_id, _v2("persona_update", {
            "personaId": str(persona_id), "version": version, "persona": "说话带喵，偶尔傲娇",
        }))
        assert updated.status_code == 200, updated.text
        assert _rows(factory, ChatPersona, id=persona_id)[0].persona == "说话带喵，偶尔傲娇"

        archived = _execute(v2_env, user, book_id, _v2(
            "persona_archive", {"personaId": str(persona_id)},
        ))
        assert archived.status_code == 200, archived.text
        assert _rows(factory, ChatPersona, id=persona_id)[0].archived_at is not None

        assert _changes(factory, persona_id) == [
            ("insert", 1), ("update", 2), ("delete", 3),
        ]

    def test_creating_a_same_name_persona_reuses_the_existing_row(self, v2_env):
        """R-PROD-52 二次实例：AI 路径与 sync push 同一口径（同 owner 同名幂等）。

        2026-11-13 实测：测试脚本经 AI 反复建内置角色，产出两对同名活跃行
        （「小助手」「猫娘」各两条）——11-11 的兜底只加在 ``_apply_insert`` 上。
        反向验证：删掉 ``_execute_persona_create`` 里的查重，本用例必须变红。
        """
        from src.models.persona import ChatPersona

        c, factory = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        first = _execute(v2_env, user, book_id, _v2("persona_create", {
            "name": "猫娘", "avatar": "🐱", "persona": "说话带喵",
        }))
        assert first.status_code == 200, first.text
        first_id = uuid.UUID(first.json()["transaction_ids"][0])

        second = _execute(v2_env, user, book_id, _v2("persona_create", {
            "name": "猫娘", "avatar": "🐱", "persona": "第二次创建",
        }))
        assert second.status_code == 200, second.text
        assert second.json()["transaction_ids"] == [str(first_id)]

        active = [
            p for p in _rows(factory, ChatPersona, owner_id=uuid.UUID(user["id"]))
            if p.archived_at is None
        ]
        assert len(active) == 1, "同名角色不应产生第二行"
        assert active[0].persona == "说话带喵", "复用既有行，不被第二次的 payload 覆盖"
        assert _changes(factory, first_id) == [("insert", 1)], "幂等命中不登记新变更"

    def test_an_overlong_persona_text_is_a_400(self, v2_env):
        c, _ = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        resp = _execute(v2_env, user, book_id, _v2(
            "persona_create", {"name": "啰嗦", "persona": "x" * 2001},
        ))
        assert resp.status_code == 400
        assert resp.json()["code"] == "ai_invalid_payload"

    def test_a_stale_persona_version_is_a_409(self, v2_env):
        c, _ = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)
        persona_id = _mk_persona(c, user)

        resp = _execute(v2_env, user, book_id, _v2("persona_update", {
            "personaId": str(persona_id), "version": 99, "name": "改名",
        }))
        assert resp.status_code == 409


class TestDeviceOp:
    def test_persona_switch_is_not_executable_on_the_server(self, v2_env):
        """当前角色指针是设备本地的 ``sync_meta`` 键——客户端在设备上执行它。"""
        c, _ = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)
        persona_id = _mk_persona(c, user)

        resp = _execute(v2_env, user, book_id, _v2(
            "persona_switch", {"personaId": str(persona_id)},
        ))
        assert resp.status_code == 400
        assert resp.json()["code"] == "ai_device_op_not_executable"

    def test_an_archived_persona_cannot_be_switched_to(self, v2_env):
        """授权口径不因执行位置放松：已归档 / 别人的角色都不能进 payload。"""
        c, _ = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)
        persona_id = _mk_persona(c, user, name="旧角色")

        assert _execute(v2_env, user, book_id, _v2(
            "persona_archive", {"personaId": str(persona_id)},
        )).status_code == 200

        proposal = _v2("persona_switch", {"personaId": str(persona_id)})
        assert _validate(v2_env, user, book_id, proposal).json()["valid"] is False
        assert _execute(v2_env, user, book_id, proposal).status_code == 403


# ── 离线语义：写入不依赖设备在场，pull 得到 ─────────────────────────────────


class TestOfflineSemantics:
    """三实体在设备上都有本地副本（Room 表 + 双向同步）。AI 从服务端写它们时，
    写入的是**云端权威**并同事务登 sync change——设备在线就 pull 到、离线就等
    上线后 pull 到，**不存在第二条「先写本地再合并」的路径**（那会造出两个真相)。
    """

    def test_a_server_side_savings_write_shows_up_in_the_pull(self, v2_env):
        c, _ = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        resp = _execute(v2_env, user, book_id, _v2("savings_create", {
            "name": "离线也能看到", "targetCents": 100_000,
        }))
        assert resp.status_code == 200
        goal_id = resp.json()["transaction_ids"][0]

        pulled = _pull(c, user)["changes"]
        item = next(ch for ch in pulled if ch["entity_id"] == goal_id)
        assert item["entity_type"] == "savings_goal"
        assert item["operation"] == "insert"
        # pull 的实体正文是 **sync 的线上形状**（snake_case），不是提案的 camelCase
        assert item["entity"]["name"] == "离线也能看到"
        assert item["entity"]["target_cents"] == 100_000

    def test_a_server_side_archive_shows_up_as_a_delete(self, v2_env):
        c, _ = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)
        rule_id = _mk_rule(c, user, book_id, _mk_category(c, user))

        _execute(v2_env, user, book_id, _v2("scheduled_archive", {"ruleId": str(rule_id)}))

        item = next(
            ch for ch in _pull(c, user)["changes"] if ch["entity_id"] == str(rule_id)
            and ch["operation"] == "delete"
        )
        assert item["entity_type"] == "scheduled_rule"
        assert item["entity"] is None, "删除类变更不带实体正文（客户端按归档合并）"

    def test_a_server_side_budget_write_shows_up_in_the_pull(self, v2_env):
        c, _ = v2_env
        user = _guest(v2_env)
        book_id = _mk_book(c, user)

        resp = _execute(v2_env, user, book_id, _v2(
            "budget_set", {"period": "2026-09", "amountCents": 500_000},
        ))
        budget_id = resp.json()["transaction_ids"][0]

        item = next(ch for ch in _pull(c, user)["changes"] if ch["entity_id"] == budget_id)
        assert item["entity_type"] == "budget"
        assert item["entity"]["amount_cents"] == 500_000
