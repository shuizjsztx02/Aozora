"""数据修复：为「直改库归档」补发 sync_changes（delete 事件）。

背景：``dedupe_seed_rows.py``（2026-11-11，R-PROD-52 修复）直接改了
``archived_at`` / ``deleted_at``，**没有写 sync_changes**。客户端 pull 只读
增量事件、bootstrap 只在首启跑一次，所以服务端已经去重的 53 条分类与若干账本
在设备上仍然活跃 —— 重启 App 不会自愈。

为什么补 ``delete`` 而不是 ``update``：客户端 ``SyncModels.toCategory`` /
``toBook`` 把 ``archivedAt`` 硬编码为 null（只有 ``toChatPersona`` 读归档字段），
update 事件无法让行从列表里消失；客户端的 pull 把 ``operation=delete`` 映射为
「归档」（``Repository.applyServerChanges`` → ``applyServerArchive``）。

规则（先 dry-run 看计划，再 ``--apply``）：

- 只处理 **当前已归档/已软删** 的 category / book；
- 已有同实体 delete 事件的跳过（幂等，可重复执行）；
- ``--orphan-book <uuid>``：**服务端不存在**、只活在客户端 Room 里的账本
  （服务端幂等 insert 拦下后，客户端不会回写服务端 id，本地留下孤儿行）。
  补一条 delete 事件让设备把它归档，不需要在服务端造实体。

用法（容器内）::

    python scripts/repair_dedupe_sync_changes.py --user <uuid>            # dry-run
    python scripts/repair_dedupe_sync_changes.py --user <uuid> --apply    # 执行
    python scripts/repair_dedupe_sync_changes.py --user <uuid> \\
        --orphan-book <uuid> --apply
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

from sqlalchemy import select

from src.common.database import get_session_factory
from src.models.ledger import Book, Category
from src.models.sync import SyncChange, SyncOperation


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True, help="目标用户 UUID")
    ap.add_argument("--apply", action="store_true", help="真正写入（默认 dry-run）")
    ap.add_argument(
        "--orphan-book", action="append", default=[], metavar="UUID",
        help="服务端不存在、仅存于客户端 Room 的账本 id（可多次）",
    )
    args = ap.parse_args()
    user_id = uuid.UUID(args.user)

    plan: list[str] = []
    async with get_session_factory()() as db:
        existing = {
            (row[0], row[1])
            for row in (await db.execute(
                select(SyncChange.entity_type, SyncChange.entity_id).where(
                    SyncChange.user_id == user_id,
                    SyncChange.operation == SyncOperation.DELETE,
                )
            )).all()
        }

        def add(entity_type: str, entity_id: uuid.UUID, version: int, label: str) -> None:
            if (entity_type, entity_id) in existing:
                return
            plan.append(f"{entity_type} delete {label} ({entity_id}) v{version}")
            db.add(SyncChange(
                user_id=user_id,
                entity_type=entity_type,
                entity_id=entity_id,
                operation=SyncOperation.DELETE,
                version=version,
            ))

        cats = (await db.scalars(
            select(Category).where(
                Category.owner_id == user_id,
                Category.archived_at.is_not(None),
            ).order_by(Category.created_at, Category.id)
        )).all()
        for cat in cats:
            add("category", cat.id, cat.version, f"L2 {cat.name!r}" if cat.parent_id else f"L1 {cat.name!r}")

        books = (await db.scalars(
            select(Book).where(
                Book.owner_id == user_id,
                Book.deleted_at.is_not(None),
            ).order_by(Book.created_at, Book.id)
        )).all()
        for book in books:
            add("book", book.id, book.version, f"软删 {book.name!r}")

        for raw in args.orphan_book:
            add("book", uuid.UUID(raw), 1, "客户端孤儿账本")

        if args.apply:
            await db.commit()

    print(f"{'APPLIED' if args.apply else 'DRY-RUN'}: {len(plan)} 条补发事件")
    for line in plan:
        print("  " + line)


if __name__ == "__main__":
    asyncio.run(main())
