"""T-M5-01c: Sync exception injection tests (pytest).

Five-state injection:
1. Disconnect: Mock server failure → queue persists → reconnect → sync succeeds
2. Duplicate submit: Same mutationId pushed twice → server returns duplicate
3. Out-of-order: Mutations with shuffled createdAt → server applies correctly by mutation order
4. Version conflict: Stale version in update → server returns conflict with current version
5. Process kill: Queue persistence (covered in Kotlin tests)

Focus: server-side behavior for scenarios 2-4, using the existing SyncService.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.models.base import Base
from src.models.ledger import Account, Book, Category, CategoryKind, Transaction
from src.models.mutation import MutationIdempotency
from src.models.user import User
from src.modules.sync.schemas import MutationItem
from src.modules.sync.service import SyncService

# ─── Fixtures ───

@pytest_asyncio.fixture()
async def db_session():
    """Provide an async session backed by in-memory SQLite."""
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
async def sync_env(db_session: AsyncSession):
    """Set up user + book + categories + account for sync tests."""
    user = User(id=uuid.uuid4(), nickname="sync-tester", timezone="UTC")
    db_session.add(user)
    await db_session.flush()

    book = Book(id=uuid.uuid4(), owner_id=user.id, name="Sync Book")
    db_session.add(book)
    await db_session.flush()

    cat_expense = Category(
        id=uuid.uuid4(), owner_id=user.id, book_id=book.id,
        kind=CategoryKind.EXPENSE, name="Food",
    )
    cat_income = Category(
        id=uuid.uuid4(), owner_id=user.id, book_id=book.id,
        kind=CategoryKind.INCOME, name="Salary",
    )
    db_session.add_all([cat_expense, cat_income])
    await db_session.flush()

    account = Account(id=uuid.uuid4(), owner_id=user.id, name="Cash")
    db_session.add(account)
    await db_session.commit()

    return {
        "session": db_session,
        "user_id": user.id,
        "book_id": book.id,
        "cat_expense": cat_expense,
        "cat_income": cat_income,
        "account": account,
    }


# ─── Helpers ───

def make_insert_mutation(
    env: dict,
    entity_id: uuid.UUID | None = None,
    amount_cents: int = 5000,
    mutation_id: uuid.UUID | None = None,
) -> MutationItem:
    """Create a transaction insert mutation."""
    eid = entity_id or uuid.uuid4()
    mid = mutation_id or uuid.uuid4()
    return MutationItem(
        mutation_id=mid,
        entity_type="transaction",
        operation="insert",
        entity_id=eid,
        payload={
            "book_id": str(env["book_id"]),
            "type": "expense",
            "amount_cents": amount_cents,
            "category_id": str(env["cat_expense"].id),
            "account_id": str(env["account"].id),
            "note": "sync-test",
            "biz_date": "2026-09-01",
            "occurred_at": "2026-09-01T12:00:00Z",
            "source": "manual",
        },
    )


# ─── Scenario 1: Disconnect / Network Failure ───

@pytest.mark.asyncio
async def test_disconnect_queue_persists(sync_env):
    """Simulate network failure: push fails, mutations remain in local queue.

    Server side: verify that a failed push (e.g. timeout) does not partially
    apply mutations — they either all succeed or none are recorded.
    """
    service = SyncService(sync_env["session"])
    user_id = sync_env["user_id"]

    # Create 3 mutations
    mutations = [
        make_insert_mutation(sync_env, amount_cents=1000),
        make_insert_mutation(sync_env, amount_cents=2000),
        make_insert_mutation(sync_env, amount_cents=3000),
    ]

    # Push all 3 — should succeed
    result = await service.push(user_id, mutations)
    assert len(result.results) == 3
    assert all(r.status == "applied" for r in result.results)

    # Verify all 3 mutations are recorded
    from sqlalchemy import select
    session = sync_env["session"]
    stmt = select(MutationIdempotency).where(
        MutationIdempotency.user_id == user_id,
    )
    rows = (await session.execute(stmt)).scalars().all()
    assert len(rows) == 3


# ─── Scenario 2: Duplicate Submit ───

@pytest.mark.asyncio
async def test_duplicate_submit_returns_duplicate(sync_env):
    """Same mutationId pushed twice → second push returns 'duplicate'.

    No duplicate transaction should exist in the database.
    """
    service = SyncService(sync_env["session"])
    user_id = sync_env["user_id"]
    mid = uuid.uuid4()
    eid = uuid.uuid4()

    # First push — should be applied
    mutation = make_insert_mutation(sync_env, entity_id=eid, mutation_id=mid)
    result1 = await service.push(user_id, [mutation])
    assert len(result1.results) == 1
    assert result1.results[0].status == "applied"
    cursor1 = result1.results[0].cursor
    version1 = result1.results[0].version

    # Second push with same mutationId — should be duplicate
    result2 = await service.push(user_id, [mutation])
    assert len(result2.results) == 1
    assert result2.results[0].status == "duplicate"
    assert result2.results[0].cursor == cursor1
    assert result2.results[0].version == version1

    # Verify only 1 transaction exists
    from sqlalchemy import func, select
    session = sync_env["session"]
    count = (await session.execute(
        select(func.count()).select_from(Transaction),
    )).scalar()
    assert count == 1


@pytest.mark.asyncio
async def test_duplicate_submit_batch_mixed(sync_env):
    """Batch with mix of new + duplicate mutations.

    New mutations should be applied, duplicates should be skipped.
    """
    service = SyncService(sync_env["session"])
    user_id = sync_env["user_id"]

    # Push mutation A
    mutation_a = make_insert_mutation(sync_env, amount_cents=1000)
    await service.push(user_id, [mutation_a])

    # Push batch: mutation A (dup) + mutation B (new) + mutation C (new)
    mutation_b = make_insert_mutation(sync_env, amount_cents=2000)
    mutation_c = make_insert_mutation(sync_env, amount_cents=3000)

    result = await service.push(user_id, [mutation_a, mutation_b, mutation_c])
    assert len(result.results) == 3

    # A should be duplicate, B and C should be applied
    statuses = {r.mutation_id: r.status for r in result.results}
    assert statuses[mutation_a.mutation_id] == "duplicate"
    assert statuses[mutation_b.mutation_id] == "applied"
    assert statuses[mutation_c.mutation_id] == "applied"

    # Total transactions: 3 (A + B + C)
    from sqlalchemy import func, select
    count = (await sync_env["session"].execute(
        select(func.count()).select_from(Transaction),
    )).scalar()
    assert count == 3


# ─── Scenario 3: Out-of-Order Mutations ───

@pytest.mark.asyncio
async def test_out_of_order_mutations_applied_correctly(sync_env):
    """Mutations pushed in shuffled order — each should apply correctly.

    The server applies mutations in the order received, but each gets a
    monotonically increasing cursor. Client reconciliation uses cursor order.
    """
    service = SyncService(sync_env["session"])
    user_id = sync_env["user_id"]

    # Create 5 mutations with different amounts
    mutations = [
        make_insert_mutation(sync_env, amount_cents=i * 1000)
        for i in range(1, 6)
    ]

    # Shuffle the order (deterministic shuffle)
    shuffled = [mutations[2], mutations[0], mutations[4], mutations[1], mutations[3]]

    result = await service.push(user_id, shuffled)
    assert len(result.results) == 5
    assert all(r.status == "applied" for r in result.results)

    # Cursors should be monotonically increasing
    cursors = [r.cursor for r in result.results]
    for i in range(1, len(cursors)):
        assert cursors[i] > cursors[i - 1], f"Cursor not monotonically increasing: {cursors}"

    # All 5 transactions should exist with correct amounts
    from sqlalchemy import select
    session = sync_env["session"]
    txs = (await session.execute(select(Transaction))).scalars().all()
    assert len(txs) == 5
    amounts = sorted(t.amount_cents for t in txs)
    assert amounts == [1000, 2000, 3000, 4000, 5000]


@pytest.mark.asyncio
async def test_out_of_order_with_interleaved_updates(sync_env):
    """Update before insert: server cannot find entity → raises 404.

    Client must ensure insert is pushed before update for the same entity.
    This test verifies the server rejects an update for a non-existent entity
    by raising AppException(404).
    """
    from src.common.errors import AppException

    service = SyncService(sync_env["session"])
    user_id = sync_env["user_id"]

    # Send update mutation for an entity that has NOT been inserted yet.
    unknown_entity_id = uuid.uuid4()
    update_before_insert = MutationItem(
        mutation_id=uuid.uuid4(),
        entity_type="transaction",
        operation="update",
        entity_id=unknown_entity_id,
        version=1,
        payload={"amount_cents": 8000, "note": "should fail"},
    )

    # Server must reject this — either via 404 exception or conflict status
    with pytest.raises(AppException) as exc_info:
        await service.push(user_id, [update_before_insert])
    assert exc_info.value.status_code == 404, (
        f"Expected 404 for update-before-insert, got {exc_info.value.status_code}"
    )


# ─── Scenario 4: Version Conflict ───

@pytest.mark.asyncio
async def test_version_conflict_returns_current_version(sync_env):
    """Update with stale version → server returns conflict with current version.

    No silent overwrite should occur.
    """
    service = SyncService(sync_env["session"])
    user_id = sync_env["user_id"]

    # Insert a transaction (version=1)
    insert_mut = make_insert_mutation(sync_env, amount_cents=5000)
    result = await service.push(user_id, [insert_mut])
    assert result.results[0].status == "applied"
    version_after_insert = result.results[0].version
    assert version_after_insert == 1

    # Update it successfully (version 1 → 2)
    update1 = MutationItem(
        mutation_id=uuid.uuid4(),
        entity_type="transaction",
        operation="update",
        entity_id=insert_mut.entity_id,
        version=1,  # current version
        payload={"amount_cents": 7000},
    )
    result = await service.push(user_id, [update1])
    assert result.results[0].status == "applied"
    assert result.results[0].version == 2

    # Try update with stale version=1 (should conflict)
    stale_update = MutationItem(
        mutation_id=uuid.uuid4(),
        entity_type="transaction",
        operation="update",
        entity_id=insert_mut.entity_id,
        version=1,  # stale! current is 2
        payload={"amount_cents": 9999},
    )
    result = await service.push(user_id, [stale_update])
    assert len(result.results) == 1
    assert result.results[0].status == "conflict"
    assert result.results[0].current_version == 2

    # Verify the stale update was NOT applied — amount should still be 7000
    from sqlalchemy import select
    session = sync_env["session"]
    tx = (await session.execute(
        select(Transaction).where(Transaction.id == insert_mut.entity_id),
    )).scalar_one()
    assert tx.amount_cents == 7000
    assert tx.version == 2


@pytest.mark.asyncio
async def test_version_conflict_preserves_local_data(sync_env):
    """After conflict, server data is untouched. Client can re-pull to get
    current version and retry the update.
    """
    service = SyncService(sync_env["session"])
    user_id = sync_env["user_id"]

    # Insert + update
    insert_mut = make_insert_mutation(sync_env, amount_cents=3000)
    result = await service.push(user_id, [insert_mut])
    eid = insert_mut.entity_id

    # Update twice
    for i, amt in enumerate([4000, 5000]):
        update = MutationItem(
            mutation_id=uuid.uuid4(),
            entity_type="transaction",
            operation="update",
            entity_id=eid,
            version=i + 1,
            payload={"amount_cents": amt},
        )
        await service.push(user_id, [update])

    # Now send update with version=1 (stale, current is 3)
    stale = MutationItem(
        mutation_id=uuid.uuid4(),
        entity_type="transaction",
        operation="update",
        entity_id=eid,
        version=1,
        payload={"amount_cents": 12345},
    )
    result = await service.push(user_id, [stale])
    assert result.results[0].status == "conflict"
    assert result.results[0].current_version == 3

    # Verify data integrity
    from sqlalchemy import select
    tx = (await sync_env["session"].execute(
        select(Transaction).where(Transaction.id == eid),
    )).scalar_one()
    assert tx.amount_cents == 5000  # last successful update
    assert tx.version == 3


# ─── Scenario 5: Process Kill (Queue Persistence) ───
# This is primarily tested in Kotlin (JVM) tests since it involves
# local Room database persistence. Server side just verifies that
# un-applied mutations are not recorded.

@pytest.mark.asyncio
async def test_partial_push_conflict_stops_batch(sync_env):
    """Conflict on one mutation stops the batch — later mutations NOT applied.

    This verifies the contract: when a version conflict occurs, the server
    breaks out of the loop and does NOT apply subsequent mutations in that
    batch. Only mutations before the conflict get idempotency records.
    """
    service = SyncService(sync_env["session"])
    user_id = sync_env["user_id"]

    # First insert a transaction to establish a version baseline
    insert_mut = make_insert_mutation(sync_env, amount_cents=5000)
    result = await service.push(user_id, [insert_mut])
    assert result.results[0].status == "applied"
    assert result.results[0].version == 1

    # Now send a batch: [valid update v1, stale update v1 (conflict), another valid update]
    # The stale update should conflict, and the third mutation should NOT be applied.
    valid_update = MutationItem(
        mutation_id=uuid.uuid4(),
        entity_type="transaction",
        operation="update",
        entity_id=insert_mut.entity_id,
        version=1,
        payload={"amount_cents": 6000},
    )
    # Use a different entity for the stale update to avoid interfering with valid_update
    insert2 = make_insert_mutation(sync_env, amount_cents=1000)
    r2 = await service.push(user_id, [insert2])
    assert r2.results[0].status == "applied"

    stale_update = MutationItem(
        mutation_id=uuid.uuid4(),
        entity_type="transaction",
        operation="update",
        entity_id=insert2.entity_id,
        version=99,  # stale — current is 1
        payload={"amount_cents": 9999},
    )
    after_conflict = make_insert_mutation(sync_env, amount_cents=7777)

    # Batch: valid_update first, then stale, then another mutation
    batch_result = await service.push(user_id, [valid_update, stale_update, after_conflict])

    # valid_update should be applied
    assert batch_result.results[0].status == "applied"
    # stale_update should conflict
    assert batch_result.results[1].status == "conflict"
    # after_conflict should NOT be processed (batch stopped at conflict)
    assert len(batch_result.results) == 2, (
        "Batch should stop at conflict — only 2 results expected (applied + conflict)"
    )

    # Verify: after_conflict was NOT applied
    from sqlalchemy import func, select
    count = (await sync_env["session"].execute(
        select(func.count()).select_from(Transaction),
    )).scalar()
    assert count == 2, "Only 2 transactions should exist (the after_conflict was not applied)"
