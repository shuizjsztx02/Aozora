"""第十八轮 REQ-70（T18-7）：记账联动账户余额（服务端侧）。

三条面：

1. **跨端契约** —— 符号是「收入为正、支出为负」，客户端 `Repository.balanceDelta`
   用的是同一式。两边各有用例钉住，任一端改了符号这条会红。
2. **REST 写路径** —— `TransactionService` 是余额联动的**单点实现**，REST 路由与
   AI 执行面都从这里进来。五种写路径（create / batch / update / delete / undo）的
   余额不变量在这里逐一验证。
3. **AI 执行面** —— `/ai/execute` 的 v2 提案改余额之后，**账户的 sync change 必须
   已登记**：服务端权威直写不登 sync change，设备 pull 永远看不到（T14-8 的同一条
   教训，交易自身早就这么做了，账户是这一卡新增的第二个被联动实体）。

用例全部走真实端点 + 真实 SQLite（临时文件库，不用 `tmp_path`——本机 basetemp
权限已坏）。
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
def env():
    """TestClient + 隔离库（与 ``test_ai_proposal_v2_execution.py`` 同形）。"""
    from src.main import app
    from src.modules.ai.service import _ai_rate_limits
    from src.modules.auth.dependencies import _rate_limits

    _rate_limits.clear()
    _ai_rate_limits.clear()

    db_dir = Path(tempfile.mkdtemp(prefix="aozora_balance_db_"))
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


# ── 脚手架 ──────────────────────────────────────────────────────────────


def _guest(env) -> dict:
    c, _ = env
    resp = c.post("/auth/guest", json={"device_id": "balance-device", "nickname": "b"})
    assert resp.status_code == 200
    data = resp.json()
    return {"id": data["user_id"], "token": data["tokens"]["access_token"]}


def _auth(user: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {user['token']}"}


def _v2(op: str, payload: dict):
    return {
        "contractVersion": 2,
        "proposalId": f"v2-{uuid.uuid4().hex[:12]}",
        "action": op,
        "confidence": 0.95,
        "requiresConfirmation": True,
        "message": "（测试提案）",
        "warnings": [],
        "payload": payload,
    }


def _execute(env, user, book_id, proposal):
    c, _ = env
    return c.post("/ai/execute", json={
        "proposal": proposal, "book_id": str(book_id), "confirmation_token": "confirm",
    }, headers=_auth(user))


def _mk_book(c, user, name="日常") -> uuid.UUID:
    resp = c.post("/books", json={"name": name}, headers=_auth(user))
    assert resp.status_code == 201
    return uuid.UUID(resp.json()["id"])


def _mk_category(c, user, kind="expense", name="餐饮") -> uuid.UUID:
    resp = c.post("/categories", json={"name": name, "kind": kind}, headers=_auth(user))
    assert resp.status_code == 201
    return uuid.UUID(resp.json()["id"])


def _mk_account(c, user, name="招行卡", balance_cents=0) -> uuid.UUID:
    resp = c.post(
        "/accounts",
        json={"name": name, "balance_cents": balance_cents},
        headers=_auth(user),
    )
    assert resp.status_code == 201
    return uuid.UUID(resp.json()["id"])


def _rows(factory, model, **filters):
    async def _run():
        async with factory() as session:
            stmt = select(model)
            for key, value in filters.items():
                stmt = stmt.where(getattr(model, key) == value)
            return list((await session.execute(stmt)).scalars().all())
    return asyncio.run(_run())


def _balance(factory, account_id: uuid.UUID) -> int:
    from src.models.ledger import Account
    rows = _rows(factory, Account, id=account_id)
    assert rows, f"账户 {account_id} 不见了"
    return rows[0].balance_cents


def _tx(c, user, book_id, category_id, *, amount_cents=1250, type_="expense",
        account_id=None, note="午饭", biz_date="2026-09-23"):
    """走 REST ``POST /transactions`` 记一笔（服务端侧最直的那条写路径）。"""
    payload = {
        "book_id": str(book_id), "type": type_, "amount_cents": amount_cents,
        "category_id": str(category_id), "note": note, "biz_date": biz_date,
        "occurred_at": f"{biz_date}T12:00:00+00:00",
    }
    if account_id is not None:
        payload["account_id"] = str(account_id)
    resp = c.post("/transactions", json=payload, headers=_auth(user))
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── 1. 跨端契约 ─────────────────────────────────────────────────────────


class TestBalanceDeltaContract:
    """符号约定 —— 与客户端 ``Repository.balanceDelta`` 必须逐字同式。"""

    def test_income_is_positive(self):
        from src.modules.transactions.service import balance_delta
        assert balance_delta("income", 1250) == 1250

    def test_expense_is_negative(self):
        from src.modules.transactions.service import balance_delta
        assert balance_delta("expense", 1250) == -1250


# ── 2. REST 写路径（TransactionService 单点实现） ────────────────────────


class TestRestWritePaths:
    def test_create_expense_decreases_balance_by_exactly_the_amount(self, env):
        c, factory = env
        user = _guest(env)
        book_id, category_id = _mk_book(c, user), _mk_category(c, user)
        account_id = _mk_account(c, user, balance_cents=100_000)

        _tx(c, user, book_id, category_id, amount_cents=2500, account_id=account_id)
        assert _balance(factory, account_id) == 97_500

    def test_create_income_increases_balance(self, env):
        c, factory = env
        user = _guest(env)
        book_id = _mk_book(c, user)
        category_id = _mk_category(c, user, kind="income", name="工资")
        account_id = _mk_account(c, user, balance_cents=100_000)

        _tx(c, user, book_id, category_id, type_="income", amount_cents=2500,
            account_id=account_id)
        assert _balance(factory, account_id) == 102_500

    def test_create_without_account_leaves_every_account_untouched(self, env):
        c, factory = env
        user = _guest(env)
        book_id, category_id = _mk_book(c, user), _mk_category(c, user)
        account_id = _mk_account(c, user, balance_cents=100_000)

        _tx(c, user, book_id, category_id, amount_cents=2500, account_id=None)
        assert _balance(factory, account_id) == 100_000

    def test_balance_may_go_negative(self, env):
        """信用卡欠款是常态：余额没有下限（与 ``savings.current_cents`` 不同）。"""
        c, factory = env
        user = _guest(env)
        book_id, category_id = _mk_book(c, user), _mk_category(c, user)
        account_id = _mk_account(c, user, balance_cents=0)

        _tx(c, user, book_id, category_id, amount_cents=2500, account_id=account_id)
        assert _balance(factory, account_id) == -2_500

    def test_update_amount_adjusts_by_new_minus_old(self, env):
        c, factory = env
        user = _guest(env)
        book_id, category_id = _mk_book(c, user), _mk_category(c, user)
        account_id = _mk_account(c, user, balance_cents=100_000)

        tx = _tx(c, user, book_id, category_id, amount_cents=2500, account_id=account_id)
        resp = c.patch(f"/transactions/{tx['id']}", json={
            "amount_cents": 4000, "version": tx["version"],
        }, headers=_auth(user))
        assert resp.status_code == 200, resp.text
        assert _balance(factory, account_id) == 96_000

    def test_update_type_flips_in_one_step(self, env):
        """支出改收入：差额是两倍金额（旧值回补 + 新值加计都在同一账户上）。"""
        c, factory = env
        user = _guest(env)
        book_id, category_id = _mk_book(c, user), _mk_category(c, user)
        account_id = _mk_account(c, user, balance_cents=100_000)

        tx = _tx(c, user, book_id, category_id, amount_cents=2500, account_id=account_id)
        resp = c.patch(f"/transactions/{tx['id']}", json={
            "type": "income", "version": tx["version"],
        }, headers=_auth(user))
        assert resp.status_code == 200, resp.text
        assert _balance(factory, account_id) == 102_500

    def test_update_switching_account_refunds_old_and_charges_new(self, env):
        c, factory = env
        user = _guest(env)
        book_id, category_id = _mk_book(c, user), _mk_category(c, user)
        old_acc = _mk_account(c, user, name="旧卡", balance_cents=100_000)
        new_acc = _mk_account(c, user, name="新卡", balance_cents=50_000)

        tx = _tx(c, user, book_id, category_id, amount_cents=2500, account_id=old_acc)
        assert _balance(factory, old_acc) == 97_500

        resp = c.patch(f"/transactions/{tx['id']}", json={
            "account_id": str(new_acc), "version": tx["version"],
        }, headers=_auth(user))
        assert resp.status_code == 200, resp.text
        assert _balance(factory, old_acc) == 100_000
        assert _balance(factory, new_acc) == 47_500

    def test_delete_refunds_account(self, env):
        c, factory = env
        user = _guest(env)
        book_id, category_id = _mk_book(c, user), _mk_category(c, user)
        account_id = _mk_account(c, user, balance_cents=100_000)

        tx = _tx(c, user, book_id, category_id, amount_cents=2500, account_id=account_id)
        assert _balance(factory, account_id) == 97_500

        resp = c.delete(f"/transactions/{tx['id']}", headers=_auth(user))
        assert resp.status_code == 204
        assert _balance(factory, account_id) == 100_000

    def test_undo_delete_charges_account_again(self, env):
        """撤销删除 = 这笔钱重新占用账户 —— REST 有这条入口，漏掉它不变量就破了。"""
        c, factory = env
        user = _guest(env)
        book_id, category_id = _mk_book(c, user), _mk_category(c, user)
        account_id = _mk_account(c, user, balance_cents=100_000)

        tx = _tx(c, user, book_id, category_id, amount_cents=2500, account_id=account_id)
        c.delete(f"/transactions/{tx['id']}", headers=_auth(user))
        assert _balance(factory, account_id) == 100_000

        resp = c.post(f"/transactions/{tx['id']}/undo", headers=_auth(user))
        assert resp.status_code == 200, resp.text
        assert _balance(factory, account_id) == 97_500

    def test_rest_paths_do_not_touch_other_accounts(self, env):
        c, factory = env
        user = _guest(env)
        book_id, category_id = _mk_book(c, user), _mk_category(c, user)
        used = _mk_account(c, user, name="用到的", balance_cents=100_000)
        idle = _mk_account(c, user, name="没用到", balance_cents=77_000)

        _tx(c, user, book_id, category_id, amount_cents=2500, account_id=used)
        assert _balance(factory, idle) == 77_000


# ── 3. AI 执行面 ────────────────────────────────────────────────────────


class TestAiExecutionSurface:
    def _account_changes(self, factory, account_id):
        from src.models.sync import SyncChange
        return _rows(factory, SyncChange, entity_id=account_id)

    def test_transaction_add_links_balance_and_registers_account_change(self, env):
        c, factory = env
        user = _guest(env)
        book_id, category_id = _mk_book(c, user), _mk_category(c, user)
        account_id = _mk_account(c, user, balance_cents=100_000)

        proposal = _v2("transaction_add", {"transactions": [{
            "type": "expense", "amountCents": 2500, "categoryId": str(category_id),
            "accountId": str(account_id), "note": "午饭", "bizDate": "2026-09-23",
        }]})
        resp = _execute(env, user, book_id, proposal)
        assert resp.status_code == 200, resp.text

        assert _balance(factory, account_id) == 97_500
        changes = self._account_changes(factory, account_id)
        assert [(str(ch.operation), ch.version) for ch in changes] == [("update", 2)], (
            "余额被联动的账户必须登 sync change，否则设备 pull 不到（T14-8 口径）"
        )

    def test_transaction_add_groups_multiple_rows_on_one_account(self, env):
        """同一账户两笔 → 账户行只查改一次、version 只 +1、只有一条 change。"""
        c, factory = env
        user = _guest(env)
        book_id, category_id = _mk_book(c, user), _mk_category(c, user)
        account_id = _mk_account(c, user, balance_cents=100_000)

        rows = [
            {"type": "expense", "amountCents": amt, "categoryId": str(category_id),
             "accountId": str(account_id), "note": f"第{i}笔", "bizDate": "2026-09-23"}
            for i, amt in enumerate((2500, 1000), start=1)
        ]
        resp = _execute(env, user, book_id, _v2("transaction_add", {"transactions": rows}))
        assert resp.status_code == 200, resp.text

        assert _balance(factory, account_id) == 96_500
        assert len(self._account_changes(factory, account_id)) == 1

    def test_transaction_update_changes_balance_and_registers_change(self, env):
        c, factory = env
        user = _guest(env)
        book_id, category_id = _mk_book(c, user), _mk_category(c, user)
        account_id = _mk_account(c, user, balance_cents=100_000)

        tx = _tx(c, user, book_id, category_id, amount_cents=2500, account_id=account_id)
        assert _balance(factory, account_id) == 97_500

        resp = _execute(env, user, book_id, _v2("transaction_update", {
            "transactionId": tx["id"], "version": tx["version"], "amountCents": 4000,
        }))
        assert resp.status_code == 200, resp.text

        assert _balance(factory, account_id) == 96_000
        # 记一笔时走的是 REST 路径（不登 change），改这一笔走 AI 路径 → 两条：
        # 交易自身 + 被联动的账户
        from src.models.sync import SyncChange
        ops = {(str(ch.entity_type), str(ch.operation)) for ch in _rows(factory, SyncChange)}
        assert ("account", "update") in ops

    def test_transaction_update_switching_account_registers_both(self, env):
        """换账户要**两个账户各登一条** —— 旧账户被回补了，它同样变了。"""
        c, factory = env
        user = _guest(env)
        book_id, category_id = _mk_book(c, user), _mk_category(c, user)
        old_acc = _mk_account(c, user, name="旧卡", balance_cents=100_000)
        new_acc = _mk_account(c, user, name="新卡", balance_cents=50_000)

        tx = _tx(c, user, book_id, category_id, amount_cents=2500, account_id=old_acc)

        resp = _execute(env, user, book_id, _v2("transaction_update", {
            "transactionId": tx["id"], "version": tx["version"],
            "accountId": str(new_acc),
        }))
        assert resp.status_code == 200, resp.text

        assert _balance(factory, old_acc) == 100_000
        assert _balance(factory, new_acc) == 47_500
        assert len(self._account_changes(factory, old_acc)) == 1
        assert len(self._account_changes(factory, new_acc)) == 1

    def test_transaction_delete_refunds_and_registers_change(self, env):
        c, factory = env
        user = _guest(env)
        book_id, category_id = _mk_book(c, user), _mk_category(c, user)
        account_id = _mk_account(c, user, balance_cents=100_000)

        tx = _tx(c, user, book_id, category_id, amount_cents=2500, account_id=account_id)
        assert _balance(factory, account_id) == 97_500

        resp = _execute(env, user, book_id, _v2("transaction_delete", {
            "transactionId": tx["id"],
        }))
        assert resp.status_code == 200, resp.text

        assert _balance(factory, account_id) == 100_000
        assert len(self._account_changes(factory, account_id)) == 1

    def test_transaction_without_account_registers_no_account_change(self, env):
        c, factory = env
        user = _guest(env)
        book_id, category_id = _mk_book(c, user), _mk_category(c, user)

        proposal = _v2("transaction_add", {"transactions": [{
            "type": "expense", "amountCents": 2500, "categoryId": str(category_id),
            "note": "无账户", "bizDate": "2026-09-23",
        }]})
        resp = _execute(env, user, book_id, proposal)
        assert resp.status_code == 200, resp.text

        from src.models.sync import SyncChange
        account_changes = [ch for ch in _rows(factory, SyncChange)
                           if str(ch.entity_type) == "account"]
        assert account_changes == []

    def test_replaying_the_same_proposal_does_not_double_count(self, env):
        """幂等：同一个 proposalId 再执行一次，余额与 change 都不再增长。"""
        c, factory = env
        user = _guest(env)
        book_id, category_id = _mk_book(c, user), _mk_category(c, user)
        account_id = _mk_account(c, user, balance_cents=100_000)

        proposal = _v2("transaction_add", {"transactions": [{
            "type": "expense", "amountCents": 2500, "categoryId": str(category_id),
            "accountId": str(account_id), "note": "午饭", "bizDate": "2026-09-23",
        }]})
        assert _execute(env, user, book_id, proposal).status_code == 200
        assert _balance(factory, account_id) == 97_500

        assert _execute(env, user, book_id, proposal).status_code == 200
        assert _balance(factory, account_id) == 97_500, "重放不得重复扣减"
        assert len(self._account_changes(factory, account_id)) == 1
