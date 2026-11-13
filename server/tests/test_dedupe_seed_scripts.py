"""R-PROD-52 二次实例的回归：去重脚本归档时必须写 sync_changes。

2026-11-11 的 ``dedupe_seed_rows.py`` 直接改 ``archived_at`` / ``deleted_at``，
**没有写 sync_changes**。客户端 pull 只读增量事件、bootstrap 只在首启跑一次，
于是服务端已去重的数据在真机上永远停在活跃态（2026-11-13 实测：53 条分类与
2 个账本重启后仍重复）。

反向验证：删掉脚本里对应的 ``_record_delete`` 调用，本文件的断言必须变红。
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

from sqlalchemy import select

from src.common.database import get_session_factory
from src.models.ledger import Book, Category, CategoryKind
from src.models.sync import SyncChange, SyncOperation
from src.models.user import User


def _load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "dedupe_seed_rows.py"
    spec = importlib.util.spec_from_file_location("dedupe_seed_rows", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_dedupe_categories_writes_delete_change(client):
    script = _load_script()
    user_id = uuid.uuid4()
    async with get_session_factory()() as db:
        db.add(User(id=user_id))
        first = Category(
            id=uuid.uuid4(), owner_id=user_id, book_id=None, parent_id=None,
            kind=CategoryKind.EXPENSE, name="餐饮",
        )
        duplicate = Category(
            id=uuid.uuid4(), owner_id=user_id, book_id=None, parent_id=None,
            kind=CategoryKind.EXPENSE, name="餐饮",
        )
        db.add_all([first, duplicate])
        await db.commit()

        plan: list[str] = []
        backup: list[dict] = []
        await script._dedupe_categories(db, user_id, True, plan, backup)
        await db.commit()

        archived = next(
            cat for cat in (await db.get(Category, first.id), await db.get(Category, duplicate.id))
            if cat is not None and cat.archived_at is not None
        )
        changes = list((await db.scalars(
            select(SyncChange).where(SyncChange.user_id == user_id)
        )).all())

    assert len(plan) == 1, "同名分类应只归档一条"
    assert len(changes) == 1, "归档必须补发且只补发一条 sync_changes"
    assert changes[0].entity_type == "category"
    assert changes[0].operation == SyncOperation.DELETE
    assert str(changes[0].entity_id) == str(archived.id)


async def test_dedupe_books_writes_delete_change(client):
    script = _load_script()
    user_id = uuid.uuid4()
    async with get_session_factory()() as db:
        db.add(User(id=user_id))
        first = Book(id=uuid.uuid4(), owner_id=user_id, name="总账本")
        duplicate = Book(id=uuid.uuid4(), owner_id=user_id, name="总账本")
        db.add_all([first, duplicate])
        await db.commit()

        plan: list[str] = []
        backup: list[dict] = []
        await script._dedupe_books(db, user_id, True, plan, backup)
        await db.commit()

        archived = next(
            book for book in (await db.get(Book, first.id), await db.get(Book, duplicate.id))
            if book is not None and book.deleted_at is not None
        )
        changes = list((await db.scalars(
            select(SyncChange).where(SyncChange.user_id == user_id)
        )).all())

    assert len(plan) == 1, "同名且无交易的账本应只软删一条"
    assert len(changes) == 1, "软删必须补发且只补发一条 sync_changes"
    assert changes[0].entity_type == "book"
    assert changes[0].operation == SyncOperation.DELETE
    assert str(changes[0].entity_id) == str(archived.id)
