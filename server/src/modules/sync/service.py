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
from src.models.persona import ChatPersona
from src.models.savings import SavingsGoal
from src.models.scheduled import ScheduledRule
from src.models.sync import SyncOperation
from src.models.user_preference import SYNCABLE_KEYS, UserPreference

from .repository import ARCHIVED_TYPES, SyncRepository
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
# T-M7-01: 单一来源改为 repository.ARCHIVED_TYPES（含三个上云实体）。
_ARCHIVED_TYPES = ARCHIVED_TYPES

# T-M7-01: 三实体的**上行迁移友好**语义。旧版本客户端把它们仅存本地，升级后
# 需一次性上行；多设备场景下第二台设备会推同一批 id。insert 命中已存在实体时
# 幂等返回现有版本（云端权威优先），而不是 IntegrityError → conflict 垃圾队列。
_MIGRATION_IDEMPOTENT_TYPES = frozenset({
    "savings_goal", "scheduled_rule", "chat_persona",
    # T-M7-05j: 偏好项的上行迁移同理——旧版本客户端把 sync_meta 里的偏好键
    # 仅存本地，升级后多设备推同一批 (user_id, key) 不能互踩。命中已存在行时
    # 不覆盖（云端权威优先），记一条 update 变更让发起方按云端值合并回本地。
    "user_preference",
})

# 定时规则的可变枚举（应用层校验，避免 DB CheckConstraint 报 IntegrityError
# 被误报为 conflict）。
_RULE_REPEATS = frozenset({"daily", "weekly", "monthly", "yearly"})
_PERIOD_UNITS = frozenset({"week", "month"})


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

        # T-M7-01: 三实体的上行迁移幂等语义（见 _MIGRATION_IDEMPOTENT_TYPES）。
        # 命中已存在（含已归档）实体时**不覆盖云端副本**，但记一条 **update** 变更：
        # 发起方的 echo-pull 借此把云端版本（version / archived_at）合并回本地。
        #   - 不记变更 = 本地副本停在自己的 version（如 5）而云端是 1，下一次
        #     update 带 expectedVersion=5 必被判 conflict → 终态失败、用户改动丢失。
        #   - 记成 insert = 客户端 pull 的 insert/update 分支会把实体按"新增"落库；
        #     客户端 toSavingsGoal/toScheduledRule/toChatPersona 会读 archived_at，
        #     故已归档行按归档合并（不复活），活跃行按云端版本对齐。
        if op == "insert" and etype in _MIGRATION_IDEMPOTENT_TYPES:
            existing = await self.repo.get_entity(
                etype, mutation.entity_id, user_id, include_deleted=True,
            )
            if existing is not None:
                change = await self.repo.write_change(
                    user_id=user_id,
                    entity_type=etype,
                    entity_id=existing.id,
                    operation=SyncOperation.UPDATE,
                    version=existing.version,
                )
                await self.repo.record_mutation(
                    mutation_id=mutation.mutation_id,
                    user_id=user_id,
                    entity_type=etype,
                    entity_id=existing.id,
                    sync_cursor=change.cursor,
                    result_version=existing.version,
                )
                return MutationResult(
                    mutation_id=mutation.mutation_id,
                    status="applied",
                    cursor=change.cursor,
                    version=existing.version,
                )

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

        # ── T-M7-01: 三实体上云 ──────────────────────────────────────────

        if entity_type == "savings_goal":
            _validate_name(payload["name"], 128, "name")
            if payload.get("icon") is not None:
                _validate_name(payload["icon"], 64, "icon")
            _validate_amount_cents(payload["target_cents"])
            current_cents = payload.get("current_cents", 0)
            _validate_current_cents(current_cents)
            period_value, period_unit = _validate_savings_period(
                payload.get("period_value"), payload.get("period_unit"),
            )
            goal = SavingsGoal(
                id=entity_id,
                owner_id=user_id,
                name=payload["name"],
                target_cents=payload["target_cents"],
                current_cents=current_cents,
                icon=payload.get("icon"),
                period_value=period_value,
                period_unit=period_unit,
            )
            self.db.add(goal)
            await self.db.flush()
            return goal.id, goal.version

        if entity_type == "scheduled_rule":
            book_id = _to_uuid(payload["book_id"])
            category_id = _to_uuid(payload["category_id"])
            account_id = _to_uuid(payload["account_id"]) if payload.get("account_id") else None
            await self._verify_book_ownership(book_id, user_id)
            await self._verify_category_ownership(category_id, user_id)
            if account_id is not None:
                await self._verify_account_ownership(account_id, user_id)
            _validate_amount_cents(payload["amount_cents"])
            _validate_name(payload["name"], 64, "name")
            _validate_note(payload.get("note", ""))
            # 枚举经 ValueError → 422（与 transaction.type 同一路径）
            TxType(payload["type"])
            repeat = payload["repeat"]
            if repeat not in _RULE_REPEATS:
                raise AppException(422, "validation_error", f"Invalid repeat: {repeat}")
            start_date = _to_date(payload["start_date"])
            end_date = _to_date(payload["end_date"]) if payload.get("end_date") else None
            if end_date is not None and end_date < start_date:
                raise AppException(
                    422, "validation_error", "end_date must not precede start_date",
                )
            last_run_date = (
                _to_date(payload["last_run_date"]) if payload.get("last_run_date") else None
            )
            rule = ScheduledRule(
                id=entity_id,
                owner_id=user_id,
                type=payload["type"],
                name=payload["name"],
                amount_cents=payload["amount_cents"],
                book_id=book_id,
                category_id=category_id,
                account_id=account_id,
                include_in_budget=_require_bool(
                    payload.get("include_in_budget", True), "include_in_budget",
                ),
                note=payload.get("note", ""),
                repeat=repeat,
                start_date=start_date,
                end_date=end_date,
                enabled=_require_bool(payload.get("enabled", True), "enabled"),
                last_run_date=last_run_date,
                run_hour=_require_int_in_range(payload.get("run_hour", 8), 0, 23, "run_hour"),
                run_minute=_require_int_in_range(payload.get("run_minute", 0), 0, 59, "run_minute"),
            )
            self.db.add(rule)
            await self.db.flush()
            return rule.id, rule.version

        if entity_type == "chat_persona":
            _validate_name(payload["name"], 64, "name")
            if payload.get("avatar") is not None:
                _validate_name(payload["avatar"], 64, "avatar")
            _validate_note(payload.get("persona", ""), field="persona")
            persona = ChatPersona(
                id=entity_id,
                owner_id=user_id,
                name=payload["name"],
                avatar=payload.get("avatar"),
                persona=payload.get("persona", ""),
            )
            self.db.add(persona)
            await self.db.flush()
            return persona.id, persona.version

        # ── T-M7-05j: 偏好项上云 ─────────────────────────────────────────
        # 与三实体不同：偏好没有 ``owner_id`` 列（用 user_id 归属），也没有
        # archived_at / deleted_at——清除＝删行。push 的 ``entity_id`` 由客户端
        # 取上行行的 ``id``（首次 insert 时客户端自生成 UUID 当 entity_id，与服务端
        # 分配一致）。
        if entity_type == "user_preference":
            key = payload.get("key")
            if not isinstance(key, str) or key not in SYNCABLE_KEYS:
                raise AppException(
                    422, "validation_error",
                    f"Invalid preference key: {key}",
                )
            value = payload.get("value")
            if not isinstance(value, str):
                raise AppException(
                    422, "validation_error", "value must be a string",
                )
            # 校验值长度（与客户端 MetaWhitelist 的 4KB/8KB 上限对齐留余量）。
            if len(value) > 16_000:
                raise AppException(
                    422, "validation_error", "value too long",
                )
            # 命中已存在（幂等上行迁移）由调用方在 _MIGRATION_IDEMPOTENT_TYPES
            # 分支里处理——走到这里是真 insert。
            pref = await self._upsert_preference(user_id, entity_id, key, value)
            return pref.id, pref.version

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
            # T-M7-01 收口：归档/软删实体在 include_deleted=False 下读不到，但它是
            # "存在、只是不可写"，不是"不存在"。此前回 404（AppException 不被 push()
            # 的单条 except 捕获 → 冒泡成整请求 HTTP 错误 → 客户端视为可重试 →
            # 一条归档实体的 update 让整批 push 永远失败）。现改为回 conflict（带
            # current_version），与 _apply_delete 对"已归档"的处理同口径：批内其余
            # mutation 继续处理，该条由客户端标记终态失败。
            # 真不存在、或归属他人，仍回 404（不泄露存在性）。
            existing = await self.repo.get_entity(
                entity_type, entity_id, user_id, include_deleted=True,
            )
            if existing is None:
                raise AppException(404, "not_found", f"{entity_type} not found")
            raise _Conflict(existing.version)
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

        # ── T-M7-01: 三实体上云 ──────────────────────────────────────────

        elif entity_type == "savings_goal":
            if "name" in payload:
                _validate_name(payload["name"], 128, "name")
                entity.name = payload["name"]
            if "target_cents" in payload:
                _validate_amount_cents(payload["target_cents"])
                entity.target_cents = payload["target_cents"]
            if "current_cents" in payload:
                _validate_current_cents(payload["current_cents"])
                entity.current_cents = payload["current_cents"]
            if "icon" in payload:
                if payload["icon"] is not None:
                    _validate_name(payload["icon"], 64, "icon")
                entity.icon = payload["icon"]
            # 期限成对语义：客户端 update 恒同时下发两键（值或 null），
            # 任一出现即按成对校验（镜像 Repository.requireValidSavingsPeriod）。
            if "period_value" in payload or "period_unit" in payload:
                period_value, period_unit = _validate_savings_period(
                    payload.get("period_value"), payload.get("period_unit"),
                )
                entity.period_value = period_value
                entity.period_unit = period_unit

        elif entity_type == "scheduled_rule":
            if "type" in payload:
                entity.type = TxType(payload["type"])
            if "name" in payload:
                _validate_name(payload["name"], 64, "name")
                entity.name = payload["name"]
            if "amount_cents" in payload:
                _validate_amount_cents(payload["amount_cents"])
                entity.amount_cents = payload["amount_cents"]
            if "book_id" in payload:
                book_id = _to_uuid(payload["book_id"])
                # T-M7-01 收口：归属校验只在**该字段实际变化**时执行。客户端 update
                # 恒下发全字段（见 Repository.scheduledRuleToJson），若无条件校验，
                # 则"规则挂靠的分类/账户/账本已被归档"这一与本次编辑无关的既成事实
                # 会让每次 update 都被 404 拦下；而那是 AppException，会冒泡成整请求
                # 失败（客户端视为可重试 → 毒丸），规则从此既改不动又拖垮整批同步。
                if book_id != entity.book_id:
                    await self._verify_book_ownership(book_id, user_id)
                entity.book_id = book_id
            if "category_id" in payload:
                category_id = _to_uuid(payload["category_id"])
                if category_id != entity.category_id:
                    await self._verify_category_ownership(category_id, user_id)
                entity.category_id = category_id
            if "account_id" in payload:
                account_id = _to_uuid(payload["account_id"]) if payload["account_id"] else None
                if account_id is not None and account_id != entity.account_id:
                    await self._verify_account_ownership(account_id, user_id)
                entity.account_id = account_id
            if "include_in_budget" in payload:
                entity.include_in_budget = _require_bool(
                    payload["include_in_budget"], "include_in_budget",
                )
            if "note" in payload:
                _validate_note(payload["note"])
                entity.note = payload["note"]
            if "repeat" in payload:
                if payload["repeat"] not in _RULE_REPEATS:
                    raise AppException(
                        422, "validation_error", f"Invalid repeat: {payload['repeat']}",
                    )
                entity.repeat = payload["repeat"]
            if "start_date" in payload:
                entity.start_date = _to_date(payload["start_date"])
            if "end_date" in payload:
                entity.end_date = _to_date(payload["end_date"]) if payload["end_date"] else None
            if "enabled" in payload:
                entity.enabled = _require_bool(payload["enabled"], "enabled")
            # last_run_date 由引擎自管；sync 侧接受上行（跨设备去重锚点）。
            if "last_run_date" in payload:
                entity.last_run_date = (
                    _to_date(payload["last_run_date"]) if payload["last_run_date"] else None
                )
            if "run_hour" in payload:
                entity.run_hour = _require_int_in_range(payload["run_hour"], 0, 23, "run_hour")
            if "run_minute" in payload:
                entity.run_minute = _require_int_in_range(payload["run_minute"], 0, 59, "run_minute")
            # 成序校验按**更新后的最终值**判定（镜像客户端 requireValidScheduledRule）
            if entity.end_date is not None and entity.end_date < entity.start_date:
                raise AppException(
                    422, "validation_error", "end_date must not precede start_date",
                )

        elif entity_type == "chat_persona":
            if "name" in payload:
                _validate_name(payload["name"], 64, "name")
                entity.name = payload["name"]
            if "avatar" in payload:
                if payload["avatar"] is not None:
                    _validate_name(payload["avatar"], 64, "avatar")
                entity.avatar = payload["avatar"]
            if "persona" in payload:
                _validate_note(payload["persona"], field="persona")
                entity.persona = payload["persona"]

        # ── T-M7-05j: 偏好项上云 ─────────────────────────────────────────
        # 偏好的 update 只允许改 ``value``（``key`` 是行身份的一部分）。
        elif entity_type == "user_preference":
            if "value" in payload:
                new_value = payload["value"]
                if not isinstance(new_value, str):
                    raise AppException(
                        422, "validation_error", "value must be a string",
                    )
                if len(new_value) > 16_000:
                    raise AppException(
                        422, "validation_error", "value too long",
                    )
                entity.value = new_value

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
            or (
                entity_type not in _ARCHIVED_TYPES
                and entity_type != "user_preference"  # 偏好无 deleted_at
                and entity.deleted_at is not None
            )
        )
        if already_deleted:
            raise _Conflict(entity.version)

        now = datetime.now(UTC)
        if entity_type == "user_preference":
            # 偏好项无软删——清除即物理删。幂等：不存在时上面 get_entity 已返
            # 回 404（不会落到此分支），多设备同键 delete 由 version conflict 挡住。
            await self.db.delete(entity)
        elif entity_type in _ARCHIVED_TYPES:
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

    # T-M7-05j: 偏好项 upsert（insert 与幂等上行迁移共用）。
    # 命中 (user_id, key) 已存在时**不覆盖**（云端权威优先）——与三实体的幂等语义
    # 一致。调用方（insert 分支）在命中已存在时已经走 _MIGRATION_IDEMPOTENT_TYPES
    # 路径返回 "applied"，本 helper 只在真 insert 时被调用。
    async def _upsert_preference(
        self,
        user_id: uuid.UUID,
        entity_id: uuid.UUID,
        key: str,
        value: str,
    ) -> UserPreference:
        # 先查是否已存在（按 user_id + key）——命中则直接返回现有行（不覆盖）。
        existing_result = await self.db.execute(
            select(UserPreference).where(
                UserPreference.user_id == user_id,
                UserPreference.key == key,
            )
        )
        existing = existing_result.scalar_one_or_none()
        if existing is not None:
            return existing
        pref = UserPreference(
            id=entity_id, user_id=user_id, key=key, value=value,
        )
        self.db.add(pref)
        await self.db.flush()
        return pref

    # ------------------------------------------------------------------
    # Pull
    # ------------------------------------------------------------------

    async def pull(
        self, user_id: uuid.UUID, cursor: int, limit: int = 100,
    ) -> PullResponse:
        changes_raw = await self.repo.get_changes_since(user_id, cursor, limit)
        has_more = len(changes_raw) > limit
        changes_raw = changes_raw[:limit]

        # F-M3-3 fix: 按 entity_type 分组批量加载，替代逐条查询（N→5 次 DB call）
        from collections import defaultdict
        type_to_ids: dict[str, set[uuid.UUID]] = defaultdict(set)
        for sc in changes_raw:
            if str(sc.operation) != SyncOperation.DELETE.value:
                type_to_ids[sc.entity_type].add(sc.entity_id)

        batch_cache: dict[str, dict[uuid.UUID, object]] = {}
        for etype, ids in type_to_ids.items():
            batch_cache[etype] = await self.repo.get_entities_batch(
                etype, list(ids), user_id, include_deleted=True,
            )

        changes: list[ChangeItem] = []
        for sc in changes_raw:
            entity_data = None
            if str(sc.operation) != SyncOperation.DELETE.value:
                cache = batch_cache.get(sc.entity_type, {})
                entity = cache.get(sc.entity_id)
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
        savings_goals = [_serialize_entity(g) for g in entities["savings_goals"]]
        scheduled_rules = [_serialize_entity(r) for r in entities["scheduled_rules"]]
        chat_personas = [_serialize_entity(p) for p in entities["chat_personas"]]
        # T-M7-05j: 偏好项上云。
        user_preferences = [_serialize_entity(p) for p in entities["user_preferences"]]

        # legacy checksum 的输入保持五个列表不变 —— 旧客户端的重算必须继续命中。
        checksum = compute_bootstrap_checksum(
            books=books,
            categories=categories,
            accounts=accounts,
            transactions=transactions,
            budgets=budgets,
            cursor=cursor,
        )
        checksum_v2 = compute_bootstrap_checksum_v2(
            books=books,
            categories=categories,
            accounts=accounts,
            transactions=transactions,
            budgets=budgets,
            savings_goals=savings_goals,
            scheduled_rules=scheduled_rules,
            chat_personas=chat_personas,
            user_preferences=user_preferences,
            cursor=cursor,
        )

        return BootstrapResponse(
            books=books,
            categories=categories,
            accounts=accounts,
            transactions=transactions,
            budgets=budgets,
            savings_goals=savings_goals,
            scheduled_rules=scheduled_rules,
            chat_personas=chat_personas,
            user_preferences=user_preferences,
            cursor=cursor,
            checksum=checksum,
            checksum_v2=checksum_v2,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TX_SOURCE_VALUES = frozenset(s.value for s in TxSource)

# JS Number.MAX_SAFE_INTEGER：客户端以 Long 承载"分"，其上限即此。超过它的值在 DB 层
# （BigInteger）会抛 DataError，而那不在 push() 捕获的 IntegrityError/KeyError/
# ValueError 之列，会冒泡成 500。故在应用层前置拦截，与客户端 `require(x in 1..MAX)`
# 同口径（T-M7-01 收口：避免畸形 payload 打成 500）。
_MAX_SAFE_INT = 9007199254740991


def _validate_amount_cents(value: object) -> None:
    """Reject non-positive / non-integer / out-of-range ``amount_cents`` with 422."""
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > _MAX_SAFE_INT
    ):
        raise AppException(
            422, "validation_error",
            f"amount_cents must be an integer in 1..{_MAX_SAFE_INT}",
        )


def _validate_current_cents(value: object) -> None:
    """Reject negative / non-integer / out-of-range ``current_cents`` with 422."""
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > _MAX_SAFE_INT
    ):
        raise AppException(
            422, "validation_error",
            f"current_cents must be an integer in 0..{_MAX_SAFE_INT}",
        )


def _require_bool(value: object, field: str) -> bool:
    """Reject non-boolean values for a boolean payload field with 422.

    Python 的 ``bool("false")`` 是 ``True``，直接 ``bool()`` 强转会静默把字符串
    旗标翻成真值。要求真正的 JSON 布尔（客户端发的就是布尔，见
    Repository.scheduledRuleToJson）。
    """
    if not isinstance(value, bool):
        raise AppException(422, "validation_error", f"{field} must be a boolean")
    return value


def _validate_note(value: object, field: str = "note") -> None:
    """Reject non-string or over-long text fields with 422.

    ``field`` allows reuse for other 2000-char columns (chat_personas.persona)。
    """
    if not isinstance(value, str) or len(value) > 2000:
        raise AppException(
            422, "validation_error",
            f"{field} must be a string of at most 2000 characters",
        )


def _validate_savings_period(
    period_value: object, period_unit: object,
) -> tuple[int | None, str | None]:
    """Validate the paired savings-goal period fields, returning them.

    ``period_value`` / ``period_unit`` must be provided together (both null or
    both set) and the unit must be 'week' | 'month' — mirrors the client's
    ``Repository.requireValidSavingsPeriod``.  The client's bridge layer caps
    value at 1–521 (week) / 1–120 (month); the server accepts any positive
    integer and lets the app-level contract own the range.
    """
    has_value = period_value is not None
    has_unit = period_unit is not None
    if has_value != has_unit:
        raise AppException(
            422, "validation_error",
            "period_value/period_unit must be provided together",
        )
    if not has_value:
        return None, None
    if isinstance(period_value, bool) or not isinstance(period_value, int) or period_value < 1:
        raise AppException(
            422, "validation_error", "period_value must be a positive integer",
        )
    if period_unit not in _PERIOD_UNITS:
        raise AppException(
            422, "validation_error", f"Invalid period_unit: {period_unit}",
        )
    return period_value, period_unit


def _require_int_in_range(value: object, low: int, high: int, field: str) -> int:
    """Reject non-integer or out-of-range values with 422 (mirrors DB CHECK)."""
    if isinstance(value, bool) or not isinstance(value, int) or not (low <= value <= high):
        raise AppException(
            422, "validation_error",
            f"{field} must be an integer between {low} and {high}",
        )
    return value


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
    # ── T-M7-01: 三实体上云 ──────────────────────────────────────────────
    # 日期类字段（start_date / last_run_date）以 ISO 'YYYY-MM-DD' 出网；
    # 客户端 Room 用同形字符串存储，无需 epoch 转换（区别于 occurred_at）。
    if isinstance(entity, SavingsGoal):
        return {
            "id": str(entity.id),
            "name": entity.name,
            "target_cents": entity.target_cents,
            "current_cents": entity.current_cents,
            "icon": entity.icon,
            "period_value": entity.period_value,
            "period_unit": entity.period_unit,
            "version": entity.version,
            "created_at": entity.created_at.isoformat(),
            "updated_at": entity.updated_at.isoformat(),
            "archived_at": entity.archived_at.isoformat() if entity.archived_at else None,
        }
    if isinstance(entity, ScheduledRule):
        return {
            "id": str(entity.id),
            "type": str(entity.type),
            "name": entity.name,
            "amount_cents": entity.amount_cents,
            "book_id": str(entity.book_id),
            "category_id": str(entity.category_id),
            "account_id": str(entity.account_id) if entity.account_id else None,
            "include_in_budget": entity.include_in_budget,
            "note": entity.note,
            "repeat": entity.repeat,
            "start_date": entity.start_date.isoformat(),
            "end_date": entity.end_date.isoformat() if entity.end_date else None,
            "enabled": entity.enabled,
            "last_run_date": entity.last_run_date.isoformat() if entity.last_run_date else None,
            "run_hour": entity.run_hour,
            "run_minute": entity.run_minute,
            "version": entity.version,
            "created_at": entity.created_at.isoformat(),
            "updated_at": entity.updated_at.isoformat(),
            "archived_at": entity.archived_at.isoformat() if entity.archived_at else None,
        }
    if isinstance(entity, ChatPersona):
        return {
            "id": str(entity.id),
            "name": entity.name,
            "avatar": entity.avatar,
            "persona": entity.persona,
            "version": entity.version,
            "created_at": entity.created_at.isoformat(),
            "updated_at": entity.updated_at.isoformat(),
            "archived_at": entity.archived_at.isoformat() if entity.archived_at else None,
        }
    # ── T-M7-05j: 偏好项上云 ──────────────────────────────────────────────
    # 与三实体不同：无 created_at/updated_at（VersionedMixin 不带 audit 时间戳）、
    # 无 archived_at——简洁的 (key, value, version) 三元组。
    if isinstance(entity, UserPreference):
        return {
            "id": str(entity.id),
            "key": entity.key,
            "value": entity.value,
            "version": entity.version,
        }
    raise TypeError(f"Cannot serialize {type(entity)}")


# ---------------------------------------------------------------------------
# T-M3-03: Bootstrap checksum
# ---------------------------------------------------------------------------

def _compute_checksum(entity_lists: list[list[dict]], cursor: int) -> str:
    """SHA-256 over entity lists + cursor, in the given order.

    Canonical form (shared by the legacy and v2 checksums):
    - Entity lists in the caller's fixed order.
    - Each list sorted by ``id`` (UUID string, stable).
    - Keys within each entity dict sorted alphabetically.
    - JSON serialized with separators=(",", ":") and sort_keys=True, UTF-8.
    - Parts joined with ``|``; a final ``cursor:{cursor}`` part.

    Returns lowercase 64-char hex.
    """
    parts: list[str] = []
    for items in entity_lists:
        for item in sorted(items, key=lambda x: x.get("id", "")):
            parts.append(json.dumps(item, sort_keys=True, separators=(",", ":")))
    parts.append(f"cursor:{cursor}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def compute_bootstrap_checksum(
    *,
    books: list[dict],
    categories: list[dict],
    accounts: list[dict],
    transactions: list[dict],
    budgets: list[dict],
    cursor: int,
) -> str:
    """Legacy bootstrap checksum — five lists + cursor.

    Kept byte-for-byte compatible with pre-T-M7-01 servers: clients built
    before the cloud entities existed recompute exactly this hash and must
    keep matching (see ``BootstrapResponse.checksum``).
    """
    return _compute_checksum(
        [books, categories, accounts, transactions, budgets], cursor,
    )


def compute_bootstrap_checksum_v2(
    *,
    books: list[dict],
    categories: list[dict],
    accounts: list[dict],
    transactions: list[dict],
    budgets: list[dict],
    savings_goals: list[dict],
    scheduled_rules: list[dict],
    chat_personas: list[dict],
    user_preferences: list[dict] | None = None,
    cursor: int,
) -> str:
    """T-M7-01 checksum — all eight lists + cursor, for current clients.

    T-M7-05j: 增加 ``user_preferences``（默认 None，向后兼容旧调用方）。
    旧调用方（包括测试 fixture）继续拿到同一份 hash；新调用方传偏好列表后，
    hash 覆盖九类列表。
    """
    return _compute_checksum(
        [
            books, categories, accounts, transactions, budgets,
            savings_goals, scheduled_rules, chat_personas,
            user_preferences or [],
        ],
        cursor,
    )
