"""T-M3-01: Sync business logic — push / pull / bootstrap."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.errors import AppException
from src.common.telemetry import record_duplicate_entry, record_sync_conflict
from src.models.budget import Budget, BudgetType
from src.models.ledger import (
    Account,
    Book,
    Category,
    CategoryKind,
    Transaction,
    TxSource,
    TxType,
)
from src.models.sync import SyncOperation

from .repository import SyncRepository
from .schemas import (
    BootstrapResponse,
    ChangeItem,
    MutationItem,
    MutationResult,
    PullResponse,
    PushResponse,
)

logger = logging.getLogger(__name__)

# Entity types that use archived_at (soft-archive) vs deleted_at (soft-delete).
_ARCHIVED_TYPES = {"category", "account"}


class SyncService:
    """Orchestrates push, pull and bootstrap operations."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = SyncRepository(db)

    # ------------------------------------------------------------------
    # Push
    # ------------------------------------------------------------------

    async def push(
        self, user_id: uuid.UUID, mutations: list[MutationItem],
    ) -> PushResponse:
        results: list[MutationResult] = []

        for mutation in mutations:
            # 1. Idempotency check
            existing = await self.repo.get_mutation(mutation.mutation_id, user_id)
            if existing is not None:
                record_duplicate_entry()
                results.append(MutationResult(
                    mutation_id=mutation.mutation_id,
                    status="duplicate",
                    cursor=existing.sync_cursor,
                    version=existing.result_version,
                ))
                continue

            # 2. Apply mutation.
            # F-M3-2: wrap each mutation in a savepoint so an IntegrityError
            # (e.g. duplicate entity_id PK violation) rolls back only that
            # mutation and lets the rest of the batch continue — mirroring
            # how _Conflict (version mismatch) is handled per-mutation.
            try:
                async with self.db.begin_nested():
                    result = await self._apply_mutation(user_id, mutation)
            except _Conflict as exc:
                results.append(MutationResult(
                    mutation_id=mutation.mutation_id,
                    status="conflict",
                    current_version=exc.current_version,
                ))
                record_sync_conflict()
                # Stop processing remaining mutations after first conflict.
                break
            except IntegrityError as exc:
                # PK / UNIQUE / FK violation within this mutation — the
                # savepoint has already been rolled back, so the session is
                # clean for the next iteration.  Report as conflict so the
                # client can reconcile.
                logger.debug(
                    "IntegrityError on mutation %s: %s",
                    mutation.mutation_id, exc,
                )
                results.append(MutationResult(
                    mutation_id=mutation.mutation_id,
                    status="conflict",
                ))
                continue
            except (KeyError, ValueError, TypeError) as exc:
                # Missing required payload field, or unparseable UUID /
                # date / datetime / enum. Convert to 422 rather than 500.
                raise AppException(
                    422, "validation_error", f"Invalid payload: {exc}",
                ) from exc

            results.append(result)

        # Determine latest cursor.
        cursor = await self.repo.get_latest_cursor(user_id)
        return PushResponse(results=results, cursor=cursor)

    async def _apply_mutation(
        self, user_id: uuid.UUID, mutation: MutationItem,
    ) -> MutationResult:
        """Apply a single mutation and return the result.

        Raises ``_Conflict`` on version mismatch.
        Raises ``AppException`` on validation / ownership errors.
        """
        etype = mutation.entity_type
        op = mutation.operation
        payload = mutation.payload

        if op == "insert":
            entity_id, version = await self._apply_insert(
                user_id, mutation.entity_id, etype, payload,
            )
        elif op == "update":
            if mutation.version is None:
                raise AppException(422, "validation_error", "version is required for update")
            entity_id, version = await self._apply_update(
                user_id, mutation.entity_id, etype, mutation.version, payload,
            )
        elif op == "delete":
            if mutation.version is None:
                raise AppException(422, "validation_error", "version is required for delete")
            entity_id, version = await self._apply_delete(
                user_id, mutation.entity_id, etype, mutation.version,
            )
        else:
            raise AppException(422, "validation_error", f"Unknown operation: {op}")

        # Write SyncChange.
        sync_op = SyncOperation(op)
        change = await self.repo.write_change(
            user_id=user_id,
            entity_type=etype,
            entity_id=entity_id,
            operation=sync_op,
            version=version,
        )

        # Record idempotency.
        await self.repo.record_mutation(
            mutation_id=mutation.mutation_id,
            user_id=user_id,
            entity_type=etype,
            entity_id=entity_id,
            sync_cursor=change.cursor,
            result_version=version,
        )

        return MutationResult(
            mutation_id=mutation.mutation_id,
            status="applied",
            cursor=change.cursor,
            version=version,
        )

    # -- Insert -----------------------------------------------------------

    async def _apply_insert(
        self,
        user_id: uuid.UUID,
        entity_id: uuid.UUID,
        entity_type: str,
        payload: dict,
    ) -> tuple[uuid.UUID, int]:
        """Create a new entity. Returns (entity_id, version)."""
        if entity_type == "book":
            _validate_name(payload["name"], 128, "name")
            if payload.get("icon") is not None:
                _validate_name(payload["icon"], 64, "icon")
            book = Book(
                id=entity_id,
                owner_id=user_id,
                name=payload["name"],
                icon=payload.get("icon"),
            )
            self.db.add(book)
            await self.db.flush()
            return book.id, book.version

        if entity_type == "category":
            book_id = uuid.UUID(payload["book_id"]) if isinstance(payload.get("book_id"), str) else payload.get("book_id")
            if book_id:
                await self._verify_book_ownership(book_id, user_id)
            parent_id = payload.get("parent_id")
            if parent_id:
                parent_id = uuid.UUID(parent_id) if isinstance(parent_id, str) else parent_id
                # Verify the parent category exists and belongs to the user,
                # mirroring the account_id check in transaction insert.
                await self._verify_category_ownership(parent_id, user_id)
            # Validate kind enum — invalid values raise ValueError → 422
            # via the global catch in push().
            CategoryKind(payload["kind"])
            _validate_name(payload["name"], 64, "name")
            cat = Category(
                id=entity_id,
                owner_id=user_id,
                book_id=book_id,
                parent_id=parent_id,
                kind=payload["kind"],
                name=payload["name"],
            )
            self.db.add(cat)
            await self.db.flush()
            return cat.id, cat.version

        if entity_type == "account":
            _validate_name(payload["name"], 64, "name")
            if payload.get("icon") is not None:
                _validate_name(payload["icon"], 64, "icon")
            acc = Account(
                id=entity_id,
                owner_id=user_id,
                name=payload["name"],
                icon=payload.get("icon"),
            )
            self.db.add(acc)
            await self.db.flush()
            return acc.id, acc.version

        if entity_type == "transaction":
            book_id = _to_uuid(payload["book_id"])
            category_id = _to_uuid(payload["category_id"])
            account_id = _to_uuid(payload["account_id"]) if payload.get("account_id") else None
            await self._verify_book_ownership(book_id, user_id)
            await self._verify_category_ownership(category_id, user_id)
            if account_id is not None:
                await self._verify_account_ownership(account_id, user_id)
            _validate_amount_cents(payload["amount_cents"])
            _validate_note(payload.get("note", ""))
            source = payload.get("source", "manual")
            if source not in _TX_SOURCE_VALUES:
                raise AppException(
                    422, "validation_error", f"Invalid source: {source}",
                )

            tx = Transaction(
                id=entity_id,
                book_id=book_id,
                type=TxType(payload["type"]),
                amount_cents=payload["amount_cents"],
                category_id=category_id,
                account_id=account_id,
                note=payload.get("note", ""),
                biz_date=_to_date(payload["biz_date"]),
                occurred_at=_to_datetime(payload["occurred_at"]),
                source=source,
            )
            self.db.add(tx)
            await self.db.flush()
            return tx.id, tx.version

        if entity_type == "budget":
            book_id = _to_uuid(payload["book_id"])
            await self._verify_book_ownership(book_id, user_id)
            category_id = _to_uuid(payload["category_id"]) if payload.get("category_id") else None
            if category_id is not None:
                await self._verify_category_ownership(category_id, user_id)
            _validate_amount_cents(payload["amount_cents"])
            # Validate budget_type enum — invalid values raise ValueError → 422
            # via the global catch in push().
            BudgetType(payload["budget_type"])
            budget = Budget(
                id=entity_id,
                book_id=book_id,
                budget_type=payload["budget_type"],
                amount_cents=payload["amount_cents"],
                category_id=category_id,
                period=payload["period"],
            )
            self.db.add(budget)
            await self.db.flush()
            return budget.id, budget.version

        raise AppException(422, "validation_error", f"Unknown entity_type: {entity_type}")

    # -- Update -----------------------------------------------------------

    async def _apply_update(
        self,
        user_id: uuid.UUID,
        entity_id: uuid.UUID,
        entity_type: str,
        expected_version: int,
        payload: dict,
    ) -> tuple[uuid.UUID, int]:
        """Update an existing entity. Returns (entity_id, new_version)."""
        entity = await self.repo.get_entity(entity_type, entity_id, user_id)
        if entity is None:
            raise AppException(404, "not_found", f"{entity_type} not found")
        if entity.version != expected_version:
            raise _Conflict(entity.version)

        if entity_type == "book":
            if "name" in payload:
                _validate_name(payload["name"], 128, "name")
                entity.name = payload["name"]
            if "icon" in payload:
                if payload["icon"] is not None:
                    _validate_name(payload["icon"], 64, "icon")
                entity.icon = payload["icon"]

        elif entity_type == "category":
            if "name" in payload:
                _validate_name(payload["name"], 64, "name")
                entity.name = payload["name"]
            if "parent_id" in payload:
                parent_id = _to_uuid(payload["parent_id"]) if payload["parent_id"] else None
                if parent_id is not None:
                    await self._verify_category_ownership(parent_id, user_id)
                entity.parent_id = parent_id

        elif entity_type == "account":
            if "name" in payload:
                _validate_name(payload["name"], 64, "name")
                entity.name = payload["name"]
            if "icon" in payload:
                if payload["icon"] is not None:
                    _validate_name(payload["icon"], 64, "icon")
                entity.icon = payload["icon"]

        elif entity_type == "transaction":
            if "type" in payload:
                entity.type = TxType(payload["type"])
            if "amount_cents" in payload:
                _validate_amount_cents(payload["amount_cents"])
                entity.amount_cents = payload["amount_cents"]
            if "category_id" in payload and payload["category_id"] is not None:
                cat_id = _to_uuid(payload["category_id"])
                await self._verify_category_ownership(cat_id, user_id)
                entity.category_id = cat_id
            if "account_id" in payload:
                account_id = _to_uuid(payload["account_id"]) if payload["account_id"] else None
                if account_id is not None:
                    await self._verify_account_ownership(account_id, user_id)
                entity.account_id = account_id
            if "note" in payload:
                _validate_note(payload["note"])
                entity.note = payload["note"]
            if "biz_date" in payload:
                entity.biz_date = _to_date(payload["biz_date"])
            # ``occurred_at`` is intentionally not updatable via sync. It
            # records when the transaction originally took place and is
            # treated as immutable after creation — the direct transactions
            # API (TransactionUpdate) makes the same design choice.

        elif entity_type == "budget":
            if "amount_cents" in payload:
                _validate_amount_cents(payload["amount_cents"])
                entity.amount_cents = payload["amount_cents"]
            if "category_id" in payload:
                category_id = _to_uuid(payload["category_id"]) if payload["category_id"] else None
                if category_id is not None:
                    await self._verify_category_ownership(category_id, user_id)
                entity.category_id = category_id

        else:
            raise AppException(422, "validation_error", f"Unknown entity_type: {entity_type}")

        entity.version += 1
        await self.db.flush()
        return entity.id, entity.version

    # -- Delete -----------------------------------------------------------

    async def _apply_delete(
        self,
        user_id: uuid.UUID,
        entity_id: uuid.UUID,
        entity_type: str,
        expected_version: int,
    ) -> tuple[uuid.UUID, int]:
        """Soft-delete an entity. Returns (entity_id, new_version).

        F-M3-1: if the entity is already soft-deleted / archived, return a
        ``_Conflict`` with its current version so the multi-device scenario
        (device A deletes → device B late-arriving delete) is idempotent
        rather than overwriting ``deleted_at`` and creating a redundant
        ``SyncChange``.
        """
        # include_deleted=True so we can detect "already deleted" and return
        # conflict rather than 404 — lets the client reconcile cleanly.
        entity = await self.repo.get_entity(
            entity_type, entity_id, user_id, include_deleted=True,
        )
        if entity is None:
            raise AppException(404, "not_found", f"{entity_type} not found")
        if entity.version != expected_version:
            raise _Conflict(entity.version)

        # Already soft-deleted / archived → idempotent no-op signalled as
        # conflict so the client re-syncs rather than re-mutates.
        already_deleted = (
            (entity_type in _ARCHIVED_TYPES and entity.archived_at is not None)
            or (entity_type not in _ARCHIVED_TYPES and entity.deleted_at is not None)
        )
        if already_deleted:
            raise _Conflict(entity.version)

        now = datetime.now(UTC)
        if entity_type in _ARCHIVED_TYPES:
            entity.archived_at = now
        else:
            # book, transaction, budget use deleted_at
            # (T-M3-02: budgets.deleted_at added in migration 0006)
            entity.deleted_at = now

        entity.version += 1
        await self.db.flush()
        return entity.id, entity.version

    # -- Ownership helpers ------------------------------------------------

    async def _verify_book_ownership(self, book_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Verify the user owns the book and it has not been soft-deleted.

        N-01: filter ``deleted_at`` so a soft-deleted book behaves as gone —
        writes via sync return 404 instead of succeeding silently against a
        zombie book. Mirrors ``transactions/service.py:_verify_book_ownership``.
        """
        result = await self.db.execute(
            select(Book).where(
                Book.id == book_id,
                Book.owner_id == user_id,
                Book.deleted_at.is_(None),
            )
        )
        if result.scalar_one_or_none() is None:
            raise AppException(404, "not_found", "Book not found")

    async def _verify_category_ownership(self, cat_id: uuid.UUID, user_id: uuid.UUID) -> None:
        cat = await self.repo.get_category(cat_id, user_id)
        if cat is None:
            raise AppException(404, "not_found", "Category not found")

    async def _verify_account_ownership(self, acc_id: uuid.UUID, user_id: uuid.UUID) -> None:
        acc = await self.repo.get_account(acc_id, user_id)
        if acc is None:
            raise AppException(404, "not_found", "Account not found")

    # ------------------------------------------------------------------
    # Pull
    # ------------------------------------------------------------------

    async def pull(
        self, user_id: uuid.UUID, cursor: int, limit: int = 100,
    ) -> PullResponse:
        changes_raw = await self.repo.get_changes_since(user_id, cursor, limit)
        has_more = len(changes_raw) > limit
        changes_raw = changes_raw[:limit]

        changes: list[ChangeItem] = []
        for sc in changes_raw:
            entity_data = None
            if str(sc.operation) != SyncOperation.DELETE.value:
                # P2-11 fix: pull needs include_deleted=True to reconstruct history
                # for entities that were later deleted
                entity = await self.repo.get_entity(
                    sc.entity_type, sc.entity_id, user_id, include_deleted=True,
                )
                if entity is not None:
                    entity_data = _serialize_entity(entity)

            changes.append(ChangeItem(
                cursor=sc.cursor,
                entity_type=sc.entity_type,
                entity_id=sc.entity_id,
                operation=str(sc.operation),
                version=sc.version,
                changed_at=sc.changed_at,
                entity=entity_data,
            ))

        latest = changes[-1].cursor if changes else cursor
        return PullResponse(changes=changes, cursor=latest, has_more=has_more)

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    async def bootstrap(self, user_id: uuid.UUID) -> BootstrapResponse:
        """Return a full data snapshot plus a deterministic checksum.

        T-M3-03: the checksum is SHA-256 of the canonical JSON serialization
        of all entity lists + cursor, computed over the same data the client
        will receive. Clients recompute it locally after rebuild and compare;
        mismatch triggers a retry or error state.
        """
        entities = await self.repo.get_all_entities(user_id)
        cursor = await self.repo.get_latest_cursor(user_id)

        books = [_serialize_entity(b) for b in entities["books"]]
        categories = [_serialize_entity(c) for c in entities["categories"]]
        accounts = [_serialize_entity(a) for a in entities["accounts"]]
        transactions = [_serialize_entity(t) for t in entities["transactions"]]
        budgets = [_serialize_entity(b) for b in entities["budgets"]]

        checksum = compute_bootstrap_checksum(
            books=books,
            categories=categories,
            accounts=accounts,
            transactions=transactions,
            budgets=budgets,
            cursor=cursor,
        )

        return BootstrapResponse(
            books=books,
            categories=categories,
            accounts=accounts,
            transactions=transactions,
            budgets=budgets,
            cursor=cursor,
            checksum=checksum,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TX_SOURCE_VALUES = frozenset(s.value for s in TxSource)


def _validate_amount_cents(value: object) -> None:
    """Reject non-positive or non-integer ``amount_cents`` with 422."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AppException(
            422, "validation_error", "amount_cents must be a positive integer",
        )


def _validate_note(value: object) -> None:
    """Reject non-string or over-long ``note`` with 422."""
    if not isinstance(value, str) or len(value) > 2000:
        raise AppException(
            422, "validation_error",
            "note must be a string of at most 2000 characters",
        )


def _validate_name(value: object, max_length: int, field: str = "name") -> None:
    """Reject non-string or over-long name fields with 422.

    ``field`` parameter allows reuse for ``book.name``, ``category.name``,
    ``account.name``, etc., each with its own column length limit.
    """
    if not isinstance(value, str) or len(value) > max_length:
        raise AppException(
            422, "validation_error",
            f"{field} must be a string of at most {max_length} characters",
        )


class _Conflict(Exception):
    """Internal signal for version conflict."""

    def __init__(self, current_version: int):
        self.current_version = current_version
        super().__init__(f"Version conflict: current={current_version}")


def _to_uuid(value: str | uuid.UUID) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _to_date(value: str | date) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value)).date()


def _to_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _serialize_entity(entity: object) -> dict:
    """Convert an ORM entity to a JSON-serialisable dict."""
    if isinstance(entity, Book):
        return {
            "id": str(entity.id),
            "name": entity.name,
            "icon": entity.icon,
            "version": entity.version,
            "created_at": entity.created_at.isoformat(),
            "updated_at": entity.updated_at.isoformat(),
            # Book uses deleted_at for soft-delete (SoftDeleteMixin).
            # Book.archived_at exists in the model but is never written by
            # any API — sync and the direct books module only set deleted_at.
            "deleted_at": entity.deleted_at.isoformat() if entity.deleted_at else None,
        }
    if isinstance(entity, Category):
        return {
            "id": str(entity.id),
            "name": entity.name,
            "kind": str(entity.kind),
            "book_id": str(entity.book_id) if entity.book_id else None,
            "parent_id": str(entity.parent_id) if entity.parent_id else None,
            "version": entity.version,
            "created_at": entity.created_at.isoformat(),
            "updated_at": entity.updated_at.isoformat(),
            "archived_at": entity.archived_at.isoformat() if entity.archived_at else None,
        }
    if isinstance(entity, Account):
        return {
            "id": str(entity.id),
            "name": entity.name,
            "icon": entity.icon,
            "version": entity.version,
            "created_at": entity.created_at.isoformat(),
            "updated_at": entity.updated_at.isoformat(),
            "archived_at": entity.archived_at.isoformat() if entity.archived_at else None,
        }
    if isinstance(entity, Transaction):
        return {
            "id": str(entity.id),
            "book_id": str(entity.book_id),
            "type": str(entity.type),
            "amount_cents": entity.amount_cents,
            "category_id": str(entity.category_id),
            "account_id": str(entity.account_id) if entity.account_id else None,
            "note": entity.note,
            "biz_date": entity.biz_date.isoformat(),
            "occurred_at": entity.occurred_at.isoformat(),
            "source": str(entity.source),
            "version": entity.version,
            "created_at": entity.created_at.isoformat(),
            "updated_at": entity.updated_at.isoformat(),
            "deleted_at": entity.deleted_at.isoformat() if entity.deleted_at else None,
        }
    if isinstance(entity, Budget):
        return {
            "id": str(entity.id),
            "book_id": str(entity.book_id),
            "budget_type": str(entity.budget_type),
            "category_id": str(entity.category_id) if entity.category_id else None,
            "period": entity.period,
            "amount_cents": entity.amount_cents,
            "version": entity.version,
            "created_at": entity.created_at.isoformat(),
            "updated_at": entity.updated_at.isoformat(),
            "deleted_at": entity.deleted_at.isoformat() if entity.deleted_at else None,
        }
    raise TypeError(f"Cannot serialize {type(entity)}")


# ---------------------------------------------------------------------------
# T-M3-03: Bootstrap checksum
# ---------------------------------------------------------------------------

def compute_bootstrap_checksum(
    *,
    books: list[dict],
    categories: list[dict],
    accounts: list[dict],
    transactions: list[dict],
    budgets: list[dict],
    cursor: int,
) -> str:
    """Compute deterministic SHA-256 over bootstrap payload.

    Canonical form:
    - Entity lists sorted by id (UUID string, stable).
    - Keys within each entity dict sorted alphabetically.
    - JSON serialized with separators=(",", ":") and sort_keys=True,
      encoded as UTF-8.
    - Concatenated in fixed order: books|categories|accounts|transactions|
      budgets|cursor.

    Returns lowercase 64-char hex string. Clients must implement the same
    algorithm to verify.
    """
    parts: list[str] = []

    def _append_list(items: list[dict]) -> None:
        sorted_items = sorted(items, key=lambda x: x.get("id", ""))
        for item in sorted_items:
            parts.append(json.dumps(item, sort_keys=True, separators=(",", ":")))

    _append_list(books)
    _append_list(categories)
    _append_list(accounts)
    _append_list(transactions)
    _append_list(budgets)
    parts.append(f"cursor:{cursor}")

    payload = "|".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
