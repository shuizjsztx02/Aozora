"""M8 数据修复：清理「重复播种」产生的同名行（R-PROD-52 的一个实例）。

背景：同一账号被客户端种子补齐执行了两次（多设备/重装），产生两套
books / categories / chat_personas。PG 的唯一约束对 ``book_id IS NULL`` 的
全局分类不生效，账本与角色没有名字约束，因此同名行可以并存。

规则（先 dry-run 看计划，再 ``--apply``）：

- **账本**：同名保留最早一条；其余**若名下有交易则跳过并报告**（不迁移交易），
  无交易则软删（``deleted_at``）。
- **角色**：同名保留最早一条，其余归档（``archived_at``）。
- **分类**：先一级（``parent_id IS NULL``）按 ``(kind, name)`` 保留最早，建立
  「kind+name → 保留 id」映射；把二级分类的 ``parent_id`` 重写到保留父；再按
  ``(kind, parent_id, name)`` 保留最早，其余归档。

所有被改动的行先导出到 ``--backup``（默认 ``/app/dedupe-backup.json``），
回滚方式：把对应 ``deleted_at`` / ``archived_at`` 置回 NULL，并把二级分类的
``parent_id`` 按备份里的 ``before`` 还原。

用法（容器内）::

    python scripts/dedupe_seed_rows.py --user <uuid>            # dry-run
    python scripts/dedupe_seed_rows.py --user <uuid> --apply    # 执行
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update

from src.common.database import get_session_factory
from src.models.budget import Budget
from src.models.ledger import Book, Category, Transaction
from src.models.persona import ChatPersona
from src.models.scheduled import ScheduledRule
from src.models.sync import SyncChange, SyncOperation


def _record_delete(db, user_id, entity_type: str, entity) -> None:
    """归档/软删必须同时写 sync_changes —— 客户端 pull 只读增量事件。

    漏写这一步时，设备上的行会永远停在活跃态（2026-11-13 的二次事故：
    11-11 跑完本脚本后，53 条分类与 2 个账本在真机上仍显示重复）。客户端
    `toCategory` / `toBook` 把 archivedAt 硬编码为 null，只认 delete 事件
    （映射为本地归档），故这里写 delete 而不是 update。
    """
    db.add(SyncChange(
        user_id=user_id,
        entity_type=entity_type,
        entity_id=entity.id,
        operation=SyncOperation.DELETE,
        version=entity.version,
    ))


async def _dedupe_books(db, user_id, apply, plan, backup):
    rows = list((await db.scalars(
        select(Book).where(Book.owner_id == user_id, Book.deleted_at.is_(None))
        .order_by(Book.created_at, Book.id)
    )).all())
    keep: dict[str, Book] = {}
    for book in rows:
        first = keep.get(book.name)
        if first is None:
            keep[book.name] = book
            continue
        tx = (await db.scalar(
            select(Transaction.id).where(
                Transaction.book_id == book.id, Transaction.deleted_at.is_(None),
            ).limit(1)
        ))
        if tx is not None:
            plan.append(f"SKIP book {book.name!r} ({book.id}) —— 名下有交易，需人工合并")
            continue
        plan.append(f"DELETE book {book.name!r} ({book.id})，保留 {first.id}")
        backup.append({"table": "books", "id": str(book.id), "op": "deleted_at"})
        if apply:
            book.deleted_at = datetime.now(UTC)
            _record_delete(db, user_id, "book", book)


async def _dedupe_personas(db, user_id, apply, plan, backup):
    rows = list((await db.scalars(
        select(ChatPersona).where(
            ChatPersona.owner_id == user_id, ChatPersona.archived_at.is_(None),
        ).order_by(ChatPersona.created_at, ChatPersona.id)
    )).all())
    keep: dict[str, ChatPersona] = {}
    for persona in rows:
        first = keep.get(persona.name)
        if first is None:
            keep[persona.name] = persona
            continue
        plan.append(f"ARCHIVE persona {persona.name!r} ({persona.id})，保留 {first.id}")
        backup.append({"table": "chat_personas", "id": str(persona.id), "op": "archived_at"})
        if apply:
            persona.archived_at = datetime.now(UTC)
            _record_delete(db, user_id, "chat_persona", persona)


async def _dedupe_categories(db, user_id, apply, plan, backup):
    rows = list((await db.scalars(
        select(Category).where(
            Category.owner_id == user_id, Category.archived_at.is_(None),
        ).order_by(Category.created_at, Category.id)
    )).all())

    #: 被归档分类 → 保留分类。归档前必须把引用（交易/预算/定时）重写到保留行，
    #: 否则那些记录会指向一条已归档的分类（UI 显示「分类已删除」）。
    mapping: dict[uuid.UUID, uuid.UUID] = {}

    top = [c for c in rows if c.parent_id is None]
    keep_top: dict[tuple[str, str], Category] = {}
    for cat in top:
        key = (cat.kind.value if hasattr(cat.kind, "value") else str(cat.kind), cat.name)
        first = keep_top.get(key)
        if first is None:
            keep_top[key] = cat
            continue
        plan.append(f"ARCHIVE category L1 {cat.name!r} ({cat.id})，保留 {first.id}")
        backup.append({"table": "categories", "id": str(cat.id), "op": "archived_at"})
        mapping[cat.id] = first.id
        if apply:
            cat.archived_at = datetime.now(UTC)
            _record_delete(db, user_id, "category", cat)

    # 二级：先把 parent_id 重写到保留父，再按 (kind, parent, name) 去重
    for cat in rows:
        if cat.parent_id is None:
            continue
        parent = next((p for p in top if p.id == cat.parent_id), None)
        if parent is None:
            continue
        key = (cat.kind.value if hasattr(cat.kind, "value") else str(cat.kind), parent.name)
        target = keep_top.get(key)
        if target is not None and target.id != cat.parent_id:
            plan.append(f"REPARENT category {cat.name!r} ({cat.id}) → {target.id}")
            backup.append({"table": "categories", "id": str(cat.id),
                           "op": "parent_id", "before": str(cat.parent_id)})
            if apply:
                cat.parent_id = target.id

    children = [c for c in rows if c.parent_id is not None]
    keep_child: dict[tuple[str, uuid.UUID, str], Category] = {}
    for cat in children:
        kind = cat.kind.value if hasattr(cat.kind, "value") else str(cat.kind)
        key = (kind, cat.parent_id, cat.name)
        first = keep_child.get(key)
        if first is None:
            keep_child[key] = cat
            continue
        plan.append(f"ARCHIVE category L2 {cat.name!r} ({cat.id})，保留 {first.id}")
        backup.append({"table": "categories", "id": str(cat.id), "op": "archived_at"})
        mapping[cat.id] = first.id
        if apply:
            cat.archived_at = datetime.now(UTC)
            _record_delete(db, user_id, "category", cat)

    # 引用重写：交易 / 预算 / 定时规则都指向 categories.id。
    for old_id, new_id in mapping.items():
        moved = 0
        for model in (Transaction, Budget, ScheduledRule):
            result = await db.execute(
                update(model)
                .where(model.category_id == old_id)
                .values(category_id=new_id)
            )
            moved += result.rowcount or 0
        if moved:
            plan.append(f"REWRITE refs {old_id} → {new_id}（{moved} 行）")
            backup.append({"table": "refs", "op": "category_id",
                           "from": str(old_id), "to": str(new_id)})


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True, help="目标用户 UUID")
    ap.add_argument("--apply", action="store_true", help="真正写入（默认 dry-run）")
    ap.add_argument("--backup", default="/app/dedupe-backup.json")
    args = ap.parse_args()
    user_id = uuid.UUID(args.user)

    plan: list[str] = []
    backup: list[dict] = []
    async with get_session_factory()() as db:
        await _dedupe_books(db, user_id, args.apply, plan, backup)
        await _dedupe_personas(db, user_id, args.apply, plan, backup)
        await _dedupe_categories(db, user_id, args.apply, plan, backup)
        if args.apply:
            await db.commit()

    print(f"{'APPLIED' if args.apply else 'DRY-RUN'}: {len(plan)} 项")
    for line in plan:
        print("  " + line)
    if args.apply:
        with open(args.backup, "w", encoding="utf-8") as fh:
            json.dump(backup, fh, ensure_ascii=False, indent=1)
        print(f"备份已写入 {args.backup}（{len(backup)} 行）")


if __name__ == "__main__":
    asyncio.run(main())
