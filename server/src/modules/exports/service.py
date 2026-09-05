"""T-M2-05: Export business logic — JSON/CSV generation with authorization."""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.errors import AppException
from src.models.export import Export, ExportFormat, ExportStatus
from src.models.ledger import Book, Category, Transaction


# Export storage directory
EXPORT_DIR = Path(tempfile.gettempdir()) / "aozora_exports"


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

    async def create_export(self, book_id: uuid.UUID, user_id: uuid.UUID, fmt: str) -> Export:
        """Create an export task and generate the file synchronously."""
        await self._verify_book_ownership(book_id, user_id)

        export = Export(
            user_id=user_id,
            book_id=book_id,
            format=ExportFormat(fmt),
            status=ExportStatus.PENDING,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )
        self.db.add(export)
        await self.db.flush()

        try:
            # Generate file
            file_path, file_size = await self._generate_file(export)
            export.file_path = file_path
            export.file_size = file_size
            export.status = ExportStatus.COMPLETED
        except Exception:
            export.status = ExportStatus.FAILED
            raise

        await self.db.flush()
        await self.db.refresh(export)
        return export

    async def _generate_file(self, export: Export) -> tuple[str, int]:
        """Generate the export file and return (path, size)."""
        # Fetch all non-deleted transactions for the book
        result = await self.db.execute(
            select(Transaction, Category)
            .join(Category, Transaction.category_id == Category.id)
            .where(
                Transaction.book_id == export.book_id,
                Transaction.deleted_at.is_(None),
            )
            .order_by(Transaction.biz_date.desc())
        )
        rows = result.all()

        # Ensure export directory exists
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)

        # Helper: SQLite returns plain strings for str-Enum columns
        def _enum_val(v) -> str:
            return v.value if hasattr(v, "value") else v

        ext = "json" if export.format == ExportFormat.JSON else "csv"
        file_path = EXPORT_DIR / f"{export.id}.{ext}"

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
            content = json.dumps(data, ensure_ascii=False, indent=2)
            file_path.write_text(content, encoding="utf-8")
        else:
            output = io.StringIO()
            writer = csv.writer(output)
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
            file_path.write_text(output.getvalue(), encoding="utf-8")

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
        return export

    async def list_exports(self, user_id: uuid.UUID) -> list[Export]:
        """List all exports for a user."""
        result = await self.db.execute(
            select(Export)
            .where(Export.user_id == user_id)
            .order_by(Export.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_export_file(self, export_id: uuid.UUID, user_id: uuid.UUID) -> tuple[str, str]:
        """Get the file path for download, with authorization check.

        Returns (file_path, filename).
        """
        export = await self.get_export(export_id, user_id)

        if export.status != ExportStatus.COMPLETED:
            raise AppException(400, "not_ready", "Export is not ready for download")

        # Check expiration — SQLite returns naive datetimes, normalise to UTC-aware
        if export.expires_at:
            expires = export.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires < datetime.now(timezone.utc):
                raise AppException(410, "expired", "Export has expired")

        if not export.file_path or not Path(export.file_path).exists():
            raise AppException(404, "file_missing", "Export file is missing")

        ext = "json" if export.format == ExportFormat.JSON else "csv"
        filename = f"export-{export.book_id}.{ext}"
        return export.file_path, filename
