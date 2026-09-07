"""T-M5-03b seed script: load basic fixture into the drill database.

Called by tools/test-drill.sh; DATABASE_URL must be set in environment.
The DRILL_PORT environment variable is available (for diagnostics only).
"""

import asyncio
import json
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.models.base import Base  # noqa: F401  (ensures metadata registered)
from src.models.ledger import (
    Account,
    Book,
    Category,
    CategoryKind,
    Transaction,
    TxType,
)
from src.models.user import User

FIXTURE = Path("../contracts/fixtures/reconciliation/basic/seed.json")
seed = json.loads(FIXTURE.read_text(encoding="utf-8"))


async def main() -> None:
    database_url = __import__("os").environ["DATABASE_URL"]
    engine = create_async_engine(database_url)
    async with AsyncSession(engine) as session:
        # User (uses nickname, no password_hash)
        user = User(
            id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            nickname="reconciler",
            timezone=seed["user"].get("timezone", "UTC"),
        )
        session.add(user)
        await session.flush()
        user_id = user.id

        # Books (owner_id)
        book_ids: dict = {}
        for b in seed.get("books", []):
            bid = uuid.uuid4()
            book_ids[b["id"]] = bid
            session.add(
                Book(id=bid, owner_id=user_id, name=b["name"], icon=b.get("icon"))
            )

        # Categories (owner_id)
        cat_ids: dict = {}
        for c in seed.get("categories", []):
            cid = uuid.uuid4()
            cat_ids[c["id"]] = cid
            session.add(
                Category(
                    id=cid,
                    owner_id=user_id,
                    book_id=book_ids.get(c["bookId"]),
                    kind=CategoryKind.EXPENSE if c["kind"] == "expense" else CategoryKind.INCOME,
                    name=c["name"],
                )
            )

        # Accounts (owner_id)
        acct_ids: dict = {}
        for a in seed.get("accounts", []):
            aid = uuid.uuid4()
            acct_ids[a["id"]] = aid
            session.add(
                Account(id=aid, owner_id=user_id, name=a["name"], icon=a.get("icon"))
            )

        # Transactions (no owner_id; book_id/category_id/account_id use remapped UUIDs)
        for tx in seed.get("transactions", []):
            session.add(
                Transaction(
                    id=uuid.uuid4(),
                    book_id=book_ids[tx["bookId"]],
                    type=TxType.EXPENSE if tx["type"] == "expense" else TxType.INCOME,
                    amount_cents=tx["amountCents"],
                    category_id=cat_ids[tx["categoryId"]],
                    account_id=acct_ids.get(tx["accountId"]) if tx.get("accountId") else None,
                    note=tx.get("note", ""),
                    biz_date=date.fromisoformat(tx["bizDate"]),
                    occurred_at=datetime.fromtimestamp(tx["occurredAt"] / 1000, tz=timezone.utc),
                    source=tx.get("source", "manual"),
                )
            )
        await session.commit()
        tx_count = len(seed.get("transactions", []))
        print(
            f"  Seeded: {tx_count} tx, {len(cat_ids)} cats, "
            f"{len(acct_ids)} accts, {len(book_ids)} books"
        )


asyncio.run(main())
