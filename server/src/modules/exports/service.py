"""T-M2-05: Export business logic — JSON/CSV generation with authorization."""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.config import get_config
from src.common.errors import AppException
from src.models.export import Export, ExportFormat, ExportStatus
from src.models.ledger import Book, Category, Transaction

logger = logging.getLogger(__name__)


def _get_export_dir() -> Path:
    """Resolve the export directory from config, falling back to /tmp."""
    cfg = get_config()
    if cfg.export_dir:
        return Path(cfg.export_dir)
    return Path(tempfile.gettempdir()) / "aozora_exports"


def _ensure_export_dir() -> Path:
    """Create (if needed) and return the export directory.

    N-05: exports contain full financial detail, so the directory is
    forced to 0o700 on POSIX — ``mkdir``'s mode alone is masked by the
    umask and the directory may already exist with looser permissions.
    """
    export_dir = _get_export_dir()
    export_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        try:
            os.chmod(export_dir, 0o700)
        except OSError:
            logger.warning("Could not enforce 0o700 on export dir %s", export_dir)
    return export_dir


def cleanup_expired_files() -> int:
    """Delete files for expired exports from disk.

    Called at startup to reclaim space from files that outlived a prior
    process. Returns the number of files deleted.
    """
    export_dir = _get_export_dir()
    if not export_dir.exists():
        return 0
    deleted = 0
    now = datetime.now(UTC)
    for path in export_dir.iterdir():
        if not path.is_file():
            continue
        # Only delete files that look like exports (UUID + ext).
        if path.suffix not in (".json", ".csv"):
            continue
        # Heuristic: file mtime older than 24h + grace.
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        except OSError:
            continue
        if now - mtime > timedelta(hours=25):
            try:
                path.unlink()
                deleted += 1
            except OSError:
                logger.warning("Failed to delete stale export file %s", path, exc_info=True)
    return deleted


class ExportService:
    """Generate and serve data exports with authorization."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _verify_book_ownership(self, book_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Verify the user owns the book and it has not been soft-deleted.

        N-01: filter ``deleted_at`` so soft-deleted books cannot be exported.
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

    async def _mark_expired_if_needed(self, export: Export) -> None:
        """N-06: lazily flip status to EXPIRED and delete the file when
        ``expires_at`` has passed. Mutates *export* in place and flushes.
        """
        if export.status == ExportStatus.EXPIRED:
            return
        if not export.expires_at:
            return
        expires = export.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires >= datetime.now(UTC):
            return

        export.status = ExportStatus.EXPIRED
        # Delete the file from disk (N-05).
        if export.file_path:
            try:
                Path(export.file_path).unlink(missing_ok=True)
            except OSError:
                logger.warning("Failed to delete expired export file %s", export.file_path, exc_info=True)
            export.file_path = None
        await self.db.flush()

    async def create_export(self, book_id: uuid.UUID, user_id: uuid.UUID, fmt: str) -> Export:
        """Create an export task and generate the file.

        N-07: the synchronous file-generation work is offloaded to a
        thread-pool so the event loop stays responsive for other
        requests while a large export is being written.
        """
        await self._verify_book_ownership(book_id, user_id)

        export = Export(
            user_id=user_id,
            book_id=book_id,
            format=ExportFormat(fmt),
            status=ExportStatus.PENDING,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
        self.db.add(export)
        await self.db.flush()

        try:
            # Async query, then sync file I/O in a worker thread.
            rows = await self._fetch_export_rows(export)
            file_path, file_size = await asyncio.to_thread(
                self._generate_file, export, rows,
            )
            export.file_path = file_path
            export.file_size = file_size
            export.status = ExportStatus.COMPLETED
        except Exception:
            export.status = ExportStatus.FAILED
            raise

        await self.db.flush()
        await self.db.refresh(export)
        return export

    async def _fetch_export_rows(self, export: Export) -> list:
        """Async fetch of non-deleted transactions for the export."""
        result = await self.db.execute(
            select(Transaction, Category)
            .join(Category, Transaction.category_id == Category.id)
            .where(
                Transaction.book_id == export.book_id,
                Transaction.deleted_at.is_(None),
            )
            .order_by(Transaction.biz_date.desc())
        )
        return result.all()

    def _generate_file(self, export: Export, rows: list) -> tuple[str, int]:
        """Generate the export file synchronously (runs in worker thread).

        Helper: SQLite returns plain strings for str-Enum columns.
        """
        def _enum_val(v) -> str:
            return v.value if hasattr(v, "value") else v

        export_dir = _ensure_export_dir()

        ext = "json" if export.format == ExportFormat.JSON else "csv"
        file_path = export_dir / f"{export.id}.{ext}"

        if export.format == ExportFormat.JSON:
            data = [
                {
                    "id": str(tx.id),
                    "type": _enum_val(tx.type),
                    "amount_cents": tx.amount_cents,
                    "category": cat.name,
                    "category_id": str(tx.category_id),
                    "account_id": str(tx.account_id) if tx.account_id else None,
                    "note": tx.note,
                    "biz_date": tx.biz_date.isoformat(),
                    "occurred_at": tx.occurred_at.isoformat(),
                    "source": _enum_val(tx.source),
                    "created_at": tx.created_at.isoformat(),
                }
                for tx, cat in rows
            ]
            # N-07: stream straight to the file instead of materializing
            # the whole document as one string first.
            with file_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        else:
            # csv.writer streams row by row; newline="" keeps the writer's
            # own line terminator intact (recommended by the csv docs).
            with file_path.open("w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "id", "type", "amount_cents", "category", "category_id",
                    "account_id", "note", "biz_date", "occurred_at", "source", "created_at",
                ])
                for tx, cat in rows:
                    writer.writerow([
                        tx.id, _enum_val(tx.type), tx.amount_cents, cat.name, tx.category_id,
                        tx.account_id or "", tx.note, tx.biz_date.isoformat(),
                        tx.occurred_at.isoformat(), _enum_val(tx.source), tx.created_at.isoformat(),
                    ])

        return str(file_path), file_path.stat().st_size

    async def get_export(self, export_id: uuid.UUID, user_id: uuid.UUID) -> Export:
        """Get an export, ensuring it belongs to the user."""
        result = await self.db.execute(
            select(Export).where(
                Export.id == export_id,
                Export.user_id == user_id,
            )
        )
        export = result.scalar_one_or_none()
        if export is None:
            raise AppException(404, "not_found", "Export not found")
        await self._mark_expired_if_needed(export)
        return export

    async def list_exports(self, user_id: uuid.UUID) -> list[Export]:
        """List all exports for a user."""
        result = await self.db.execute(
            select(Export)
            .where(Export.user_id == user_id)
            .order_by(Export.created_at.desc())
        )
        exports = list(result.scalars().all())
        # N-06: lazily mark any expired exports.
        for export in exports:
            await self._mark_expired_if_needed(export)
        return exports

    async def get_export_file(self, export_id: uuid.UUID, user_id: uuid.UUID) -> tuple[str, str]:
        """Get the file path for download, with authorization check.

        Returns (file_path, filename).
        """
        export = await self.get_export(export_id, user_id)
        # _mark_expired_if_needed already ran inside get_export; if the
        # export was just flipped to EXPIRED, stop here.
        if export.status == ExportStatus.EXPIRED:
            raise AppException(410, "expired", "Export has expired")

        if export.status != ExportStatus.COMPLETED:
            raise AppException(400, "not_ready", "Export is not ready for download")

        if not export.file_path or not Path(export.file_path).exists():
            raise AppException(404, "file_missing", "Export file is missing")

        ext = "json" if export.format == ExportFormat.JSON else "csv"
        filename = f"export-{export.book_id}.{ext}"
        return export.file_path, filename
