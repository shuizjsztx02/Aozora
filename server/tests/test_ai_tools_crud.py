"""T-M7-05b: 记账域与账户账本域的全量 CRUD 工具（12 个新工具）。

验收映射（``docs/milestones/T-M7-05-P4-implementation-plan.md`` §三 05b）：
- 写工具一律产出**规范草案**（v2 op + payload），治理字段留给组装器 → 全文件
- **模型永不输出 UUID**：改 / 删 / 归档类工具只接受「定位条件」→ TestTransactionWriteTools
- 定位不唯一时**报可行动的错、绝不猜** → TestTransactionWriteTools
- 名 → id 的解析全部走 ``ToolContext``（服务端权威集合）→ TestCategoryWriteTools 等
- 每域工具数 ≤8 与 op 注册表一致性 → ``test_ai_tools.py`` 的注册表契约
- 设备类 op（``meta_set``）的语义 → TestMetaSetTool

这些用例跑真实 SQLite（工具处理器要查库：定位条件解析、版本读取），
**不经过 HTTP**——执行面（``/ai/execute`` 与 sync 登记）在
``test_ai_proposal_v2_execution.py``。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.models.base import Base
from src.models.ledger import Account, Book, Category, CategoryKind, Transaction, TxSource, TxType
from src.models.user import User
from src.modules.ai import tools as T


@pytest_asyncio.fixture()
async def seeded():
    """2 账本 / 5 分类 / 2 账户 / 5 笔交易（含一笔软删、一笔跨账本、一笔已归档分类）。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    user = User(id=uuid.uuid4(), nickname="crud-tester")
    book = Book(id=uuid.uuid4(), owner_id=user.id, name="日常")
    other_book = Book(id=uuid.uuid4(), owner_id=user.id, name="旅行")

    food = Category(id=uuid.uuid4(), owner_id=user.id, book_id=book.id,
                    kind=CategoryKind.EXPENSE, name="餐饮")
    traffic = Category(id=uuid.uuid4(), owner_id=user.id, book_id=book.id,
                       kind=CategoryKind.EXPENSE, name="交通")
    salary = Category(id=uuid.uuid4(), owner_id=user.id, book_id=book.id,
                      kind=CategoryKind.INCOME, name="工资")
    # 同 kind 同名的两条（一条本账本、一条全局）：定位要走本账本那条
    global_food = Category(id=uuid.uuid4(), owner_id=user.id, book_id=None,
                           kind=CategoryKind.EXPENSE, name="餐饮")
    archived = Category(id=uuid.uuid4(), owner_id=user.id, book_id=book.id,
                        kind=CategoryKind.EXPENSE, name="已归档")
    foreign_cat = Category(id=uuid.uuid4(), owner_id=user.id, book_id=other_book.id,
                           kind=CategoryKind.EXPENSE, name="住宿")

    card = Account(id=uuid.uuid4(), owner_id=user.id, name="招行卡")
    cash = Account(id=uuid.uuid4(), owner_id=user.id, name="现金")

    now = datetime.now(UTC)
    archived.archived_at = now

    def tx(kind, amount, category, account, biz_date, note, *, deleted=None, in_book=None):
        return Transaction(
            id=uuid.uuid4(), book_id=(in_book.id if in_book else book.id), type=kind,
            amount_cents=amount, category_id=category.id,
            account_id=account.id if account else None,
            note=note, biz_date=biz_date, source=TxSource.MANUAL,
            occurred_at=now, deleted_at=deleted,
        )

    lunch = tx(TxType.EXPENSE, 1250, food, card, date(2026, 9, 18), "午饭")
    # 第二笔「餐饮」：定位条件只给分类名时不唯一（重名同类的正常情形）
    supper = tx(TxType.EXPENSE, 2000, food, cash, date(2026, 9, 19), "夜宵")
    taxi = tx(TxType.EXPENSE, 3000, traffic, cash, date(2026, 9, 17), "打车")
    wage = tx(TxType.INCOME, 500000, salary, card, date(2026, 9, 5), "九月工资")
    deleted_tx = tx(TxType.EXPENSE, 7777, food, card, date(2026, 9, 16), "已删除", deleted=now)
    other_tx = tx(TxType.EXPENSE, 9999, foreign_cat, card, date(2026, 9, 15),
                  "旅行开销", in_book=other_book)

    async with factory() as session:
        session.add_all([
            user, book, other_book, food, traffic, salary, global_food, archived,
            foreign_cat, card, cash, lunch, supper, taxi, wage, deleted_tx, other_tx,
        ])
        await session.commit()

        ctx = await T.load_tool_context(session, user.id, book.id)
        yield {
            "ctx": ctx, "db": session, "user": user, "book": book,
            "other_book": other_book, "food": food, "traffic": traffic,
            "salary": salary, "global_food": global_food, "archived": archived,
            "foreign_cat": foreign_cat, "card": card, "cash": cash,
            "lunch": lunch, "taxi": taxi, "wage": wage,
        }

    await engine.dispose()


async def _draft(tool: str, ctx: T.ToolContext, params: dict) -> dict:
    """调一个写工具并断言「产出了提案」，返回草案。"""
    outcome = await T.invoke_tool(tool, ctx, params)
    assert outcome.kind == "proposal", (tool, outcome.kind, outcome.data)
    return outcome.data


def _assert_registry_consistency(tool_name: str, draft: dict) -> None:
    """草案自报的 action 必须与注册表条目一致（两处漂移会让车道路由错位）。"""
    assert draft["action"] == T.get_tool(tool_name).proposal_action, tool_name


# ── 记账域：改一笔 / 删一笔 ────────────────────────────────────────────────


class TestTransactionWriteTools:
    async def test_update_resolves_a_unique_target_and_carries_its_version(self, seeded):
        """定位条件唯一 → 草案带服务端读到的 id 与**当前版本**（乐观并发令牌）。"""
        draft = await _draft("transaction_update", seeded["ctx"], {
            "locator": {"bizDate": "2026-09-18", "amountCents": 1250},
            "changes": {"amountCents": 1500, "note": "午饭（改）"},
        })
        _assert_registry_consistency("transaction_update", draft)

        payload = draft["payload"]
        assert payload["transactionId"] == str(seeded["lunch"].id)
        assert payload["version"] == seeded["lunch"].version
        assert payload["amountCents"] == 1500
        assert payload["note"] == "午饭（改）"
        # 没提到的字段不进 payload（＝不改）
        assert "categoryId" not in payload and "bizDate" not in payload
        assert "¥12.50" in draft["message"] and "¥15.00" in draft["message"]

    async def test_locator_can_match_by_keyword_and_account(self, seeded):
        draft = await _draft("transaction_update", seeded["ctx"], {
            "locator": {"keyword": "打车", "accountName": "现金"},
            "changes": {"bizDate": "2026-09-16"},
        })
        assert draft["payload"]["transactionId"] == str(seeded["taxi"].id)

    async def test_ambiguous_locator_is_refused_not_guessed(self, seeded):
        """匹配到两笔 → ``ambiguous_transaction``，**绝不挑一笔**（改错账比改不动糟）。"""
        outcome = await T.invoke_tool("transaction_update", seeded["ctx"], {
            "locator": {"categoryName": "餐饮"},
            "changes": {"amountCents": 100},
        })
        assert outcome.kind == "error"
        assert outcome.data["code"] == T.ERR_AMBIGUOUS_TRANSACTION

    async def test_locator_matching_nothing_is_refused(self, seeded):
        outcome = await T.invoke_tool("transaction_delete", seeded["ctx"], {
            "locator": {"bizDate": "2026-09-01"},
        })
        assert outcome.kind == "error"
        assert outcome.data["code"] == T.ERR_UNKNOWN_TRANSACTION

    async def test_locator_never_sees_deleted_or_foreign_rows(self, seeded):
        """软删的与别的账本的行都不参与定位——否则模型能改到看不见的数据。"""
        for locator in ({"keyword": "已删除"}, {"keyword": "旅行开销"}):
            outcome = await T.invoke_tool("transaction_delete", seeded["ctx"], {
                "locator": locator,
            })
            assert outcome.data["code"] == T.ERR_UNKNOWN_TRANSACTION, locator

    async def test_type_change_resolves_the_category_on_the_new_side(self, seeded):
        """改收支方向时，分类必须按**新的** kind 解析（同名跨类型可以共存）。"""
        draft = await _draft("transaction_update", seeded["ctx"], {
            "locator": {"bizDate": "2026-09-18"},
            "changes": {"type": "income", "categoryName": "工资"},
        })
        assert draft["payload"]["type"] == "income"
        assert draft["payload"]["categoryId"] == str(seeded["salary"].id)

    async def test_unknown_category_in_changes_degrades_before_proposing(self, seeded):
        outcome = await T.invoke_tool("transaction_update", seeded["ctx"], {
            "locator": {"bizDate": "2026-09-18"},
            "changes": {"categoryName": "夜宵"},
        })
        assert outcome.kind == "error"
        assert outcome.data["code"] == T.ERR_UNKNOWN_CATEGORY

    async def test_delete_draft_carries_only_the_target_id(self, seeded):
        draft = await _draft("transaction_delete", seeded["ctx"], {
            "locator": {"bizDate": "2026-09-17", "amountCents": 3000},
        })
        _assert_registry_consistency("transaction_delete", draft)
        # 删除按对象身份生效（与 REST 的 DELETE 同口径）——不带 version
        assert draft["payload"] == {"transactionId": str(seeded["taxi"].id)}
        assert "¥30.00" in draft["message"]

    async def test_locator_requires_at_least_one_condition(self, seeded):
        outcome = await T.invoke_tool("transaction_delete", seeded["ctx"], {"locator": {}})
        assert outcome.kind == "error"
        assert outcome.data["code"] == T.ERR_INVALID_ARGUMENTS


# ── 分类域 ─────────────────────────────────────────────────────────────────


class TestCategoryWriteTools:
    async def test_create_resolves_the_parent_and_defaults_to_the_current_book(self, seeded):
        draft = await _draft("category_create", seeded["ctx"], {
            "name": "夜宵", "kind": "expense", "parentName": "餐饮",
        })
        _assert_registry_consistency("category_create", draft)
        assert draft["payload"] == {
            "name": "夜宵", "kind": "expense",
            "bookId": str(seeded["book"].id),
            # 本账本的「餐饮」优先于同名的全局分类
            "parentId": str(seeded["food"].id),
        }

    async def test_create_rejects_an_unknown_parent_and_book(self, seeded):
        bad_parent = await T.invoke_tool("category_create", seeded["ctx"], {
            "name": "夜宵", "kind": "expense", "parentName": "不存在",
        })
        assert bad_parent.data["code"] == T.ERR_UNKNOWN_CATEGORY

        bad_book = await T.invoke_tool("category_create", seeded["ctx"], {
            "name": "夜宵", "kind": "expense", "bookName": "不存在",
        })
        assert bad_book.data["code"] == T.ERR_UNKNOWN_BOOK

    async def test_update_carries_the_current_version(self, seeded):
        draft = await _draft("category_update", seeded["ctx"], {
            "categoryName": "交通", "kind": "expense", "newName": "通勤",
        })
        _assert_registry_consistency("category_update", draft)
        assert draft["payload"]["categoryId"] == str(seeded["traffic"].id)
        assert draft["payload"]["version"] == seeded["traffic"].version
        assert draft["payload"]["name"] == "通勤"

    async def test_update_without_any_change_is_refused(self, seeded):
        outcome = await T.invoke_tool("category_update", seeded["ctx"], {
            "categoryName": "交通", "kind": "expense",
        })
        assert outcome.kind == "error"
        assert outcome.data["code"] == T.ERR_INVALID_ARGUMENTS

    async def test_archive_warns_because_existing_transactions_keep_the_name(self, seeded):
        draft = await _draft("category_archive", seeded["ctx"], {
            "categoryName": "交通", "kind": "expense",
        })
        _assert_registry_consistency("category_archive", draft)
        assert draft["payload"] == {"categoryId": str(seeded["traffic"].id)}
        # 非空 warnings 会把置信度降档（02e），单步也强制出确认卡
        assert draft["warnings"], "归档对已有交易的影响必须写进 warnings"

    async def test_archived_category_can_no_longer_be_located(self, seeded):
        outcome = await T.invoke_tool("category_archive", seeded["ctx"], {
            "categoryName": "已归档", "kind": "expense",
        })
        assert outcome.data["code"] == T.ERR_UNKNOWN_CATEGORY

    async def test_another_books_category_is_not_reachable(self, seeded):
        """跨账本分类不在名字索引里（``load_tool_context`` 只装本账本 + 全局）。"""
        outcome = await T.invoke_tool("category_update", seeded["ctx"], {
            "categoryName": "住宿", "kind": "expense", "newName": "酒店",
        })
        assert outcome.data["code"] == T.ERR_UNKNOWN_CATEGORY


# ── 账户域 ─────────────────────────────────────────────────────────────────


class TestAccountWriteTools:
    async def test_create_carries_only_the_name(self, seeded):
        draft = await _draft("account_create", seeded["ctx"], {"name": "微信零钱"})
        _assert_registry_consistency("account_create", draft)
        assert draft["payload"] == {"name": "微信零钱"}

    async def test_update_resolves_the_account_and_its_version(self, seeded):
        draft = await _draft("account_update", seeded["ctx"], {
            "accountName": "招行卡", "newName": "工资卡",
        })
        assert draft["payload"]["accountId"] == str(seeded["card"].id)
        assert draft["payload"]["version"] == seeded["card"].version
        assert draft["payload"]["name"] == "工资卡"

    async def test_archive_carries_the_id_only(self, seeded):
        draft = await _draft("account_archive", seeded["ctx"], {"accountName": "现金"})
        assert draft["payload"] == {"accountId": str(seeded["cash"].id)}
        assert draft["warnings"], "归档对已有交易的影响必须写进 warnings"

    async def test_unknown_account_degrades(self, seeded):
        for tool in ("account_update", "account_archive"):
            outcome = await T.invoke_tool(tool, seeded["ctx"], {"accountName": "不存在"})
            assert outcome.data["code"] == T.ERR_UNKNOWN_ACCOUNT, tool


# ── 账本域 ─────────────────────────────────────────────────────────────────


class TestBookWriteTools:
    async def test_create_carries_only_the_name(self, seeded):
        draft = await _draft("book_create", seeded["ctx"], {"name": "蜜月"})
        _assert_registry_consistency("book_create", draft)
        assert draft["payload"] == {"name": "蜜月"}

    async def test_update_resolves_the_book_and_its_version(self, seeded):
        draft = await _draft("book_update", seeded["ctx"], {
            "bookName": "旅行", "newName": "蜜月",
        })
        assert draft["payload"]["bookId"] == str(seeded["other_book"].id)
        assert draft["payload"]["version"] == seeded["other_book"].version

    async def test_deleting_the_current_book_carries_a_warning(self, seeded):
        """删当前账本会让这一轮的上下文失效——工具不替用户决定，但必须说出来
        （warnings 非空 → 置信度降档 → 单步也出确认卡）。"""
        current = await _draft("book_delete", seeded["ctx"], {"bookName": "日常"})
        other = await _draft("book_delete", seeded["ctx"], {"bookName": "旅行"})

        assert current["payload"] == {"bookId": str(seeded["book"].id)}
        assert current["warnings"], "删当前账本必须给警告"
        assert other["warnings"] == [], "删别的账本不该无端警告"

    async def test_unknown_book_degrades(self, seeded):
        outcome = await T.invoke_tool("book_delete", seeded["ctx"], {"bookName": "不存在"})
        assert outcome.data["code"] == T.ERR_UNKNOWN_BOOK


# ── meta 域（执行位置＝设备）──────────────────────────────────────────────


class TestMetaSetTool:
    async def test_default_book_resolves_to_the_named_book(self, seeded):
        draft = await _draft("meta_set", seeded["ctx"], {
            "key": "default_book", "bookName": "旅行",
        })
        _assert_registry_consistency("meta_set", draft)
        assert draft["payload"] == {
            "key": "default_book", "bookId": str(seeded["other_book"].id),
        }

    async def test_default_book_without_a_name_means_the_current_book(self, seeded):
        draft = await _draft("meta_set", seeded["ctx"], {"key": "default_book"})
        assert draft["payload"]["bookId"] == str(seeded["book"].id)

    async def test_default_account_resolves_and_requires_a_name(self, seeded):
        draft = await _draft("meta_set", seeded["ctx"], {
            "key": "default_account", "accountName": "招行卡",
        })
        assert draft["payload"] == {
            "key": "default_account", "accountId": str(seeded["card"].id),
        }

        missing = await T.invoke_tool("meta_set", seeded["ctx"], {"key": "default_account"})
        assert missing.data["code"] == T.ERR_INVALID_ARGUMENTS

        unknown = await T.invoke_tool("meta_set", seeded["ctx"], {
            "key": "default_account", "accountName": "不存在",
        })
        assert unknown.data["code"] == T.ERR_UNKNOWN_ACCOUNT

    def test_meta_set_is_declared_as_a_device_executed_op(self):
        """服务端没有 ``sync_meta`` 的存储位置（桥 ``meta.set`` 明写「不上行」），
        所以它只能挂起、由客户端在设备上执行（05f）。"""
        assert "meta_set" in T.DEVICE_EXECUTED_OPS
