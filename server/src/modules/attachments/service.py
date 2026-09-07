"""T-M4-04c: Attachment business logic — upload, list, download, delete, link.

Design notes:
- Files are stored locally under ``upload_dir`` (configurable; defaults
  to a /tmp sub-directory, mirroring the ``export_dir`` pattern). The
  object_key is ``<user_id>/<attachment_id>.<ext>``, which is
  S3-compatible for the future D-EXEC-05 migration.
- PENDING attachments auto-expire after 24 hours if unlinked. Linked
  (LINKED) or parsed (PARSED) attachments do not expire until a future
  task (T-M5-04c) adds post-parse cleanup.
- Ownership is enforced at every read/write: a user can only see,
  download, or delete their own attachments. Cross-user access returns
  404 (not 403) to avoid leaking existence.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.config import get_config
from src.common.errors import AppException
from src.models.attachment import Attachment, AttachmentStatus
from src.models.ledger import Book, Transaction

logger = logging.getLogger(__name__)

# T-M4-04c acceptance: 10 MB per-upload cap.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# T-M4-04c acceptance: format allowlist.
ALLOWED_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})

# PENDING attachments expire 24h after creation if not linked.
PENDING_EXPIRY_HOURS = 24

_MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def _get_upload_dir() -> Path:
    cfg = get_config()
    if cfg.upload_dir:
        return Path(cfg.upload_dir)
    return Path(tempfile.gettempdir()) / "aozora_uploads"


def _ensure_upload_dir() -> Path:
    upload_dir = _get_upload_dir()
    upload_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        try:
            os.chmod(upload_dir, 0o700)
        except OSError:
            logger.warning(
                "Could not enforce 0o700 on upload dir %s", upload_dir,
            )
    return upload_dir


async def cleanup_expired_files(db: AsyncSession) -> int:
    """Delete files and DB rows for expired PENDING/EXPIRED attachments.

    R-M4-39 fix: DB-driven cleanup — queries only PENDING/EXPIRED rows
    past their expiry timestamp, then removes the referenced files and
    DB rows.  LINKED and PARSED rows are never touched, so their files
    are safe regardless of age.

    Called at startup (and periodically in future) to reclaim disk.
    Returns the number of files deleted.
    """
    upload_dir = _get_upload_dir()
    now = datetime.now(UTC)
    result = await db.execute(
        select(Attachment).where(
            Attachment.status.in_([
                AttachmentStatus.PENDING,
                AttachmentStatus.EXPIRED,
            ]),
            Attachment.expires_at.isnot(None),
            Attachment.expires_at < now,
        )
    )
    expired_rows = list(result.scalars().all())
    deleted = 0
    for att in expired_rows:
        if att.object_key:
            path = upload_dir / att.object_key
            try:
                path.unlink(missing_ok=True)
                deleted += 1
            except OSError:
                logger.warning(
                    "Failed to delete expired attachment file %s", path,
                    exc_info=True,
                )
        await db.delete(att)
    await db.commit()
    return deleted


class AttachmentService:
    """Upload, list, download, delete, and link user-owned attachments."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------
    # Ownership helpers
    # ------------------------------------------------------------------

    async def _get_owned(self, attachment_id: uuid.UUID, user_id: uuid.UUID) -> Attachment:
        """Return the attachment if owned by ``user_id``; raise 404 otherwise."""
        result = await self.db.execute(
            select(Attachment).where(
                Attachment.id == attachment_id,
                Attachment.user_id == user_id,
            )
        )
        attachment = result.scalar_one_or_none()
        if attachment is None:
            raise AppException(404, "not_found", "Attachment not found")
        # Flip PENDING → EXPIRED lazily so every downstream code path
        # (link, delete, download) observes a consistent status.
        await self._mark_expired_if_needed(attachment)
        return attachment

    async def _verify_transaction_ownership(
        self, transaction_id: uuid.UUID, user_id: uuid.UUID,
    ) -> None:
        """Raise 404 if the user does not own the transaction."""
        result = await self.db.execute(
            select(Transaction).join(Book, Transaction.book_id == Book.id).where(
                Transaction.id == transaction_id,
                Book.owner_id == user_id,
                Book.deleted_at.is_(None),
                Transaction.deleted_at.is_(None),
            )
        )
        if result.scalar_one_or_none() is None:
            raise AppException(404, "not_found", "Transaction not found")

    # ------------------------------------------------------------------
    # Expiry
    # ------------------------------------------------------------------

    async def _mark_expired_if_needed(self, attachment: Attachment) -> None:
        """Flip PENDING rows to EXPIRED (and drop the file) once their
        24h window has elapsed. Mutates ``attachment`` in place and flushes.
        """
        if attachment.status != AttachmentStatus.PENDING:
            return
        if not attachment.expires_at:
            return
        expires = attachment.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires >= datetime.now(UTC):
            return

        attachment.status = AttachmentStatus.EXPIRED
        if attachment.object_key:
            path = _get_upload_dir() / attachment.object_key
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "Failed to delete expired attachment file %s", path,
                    exc_info=True,
                )
        await self.db.flush()

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    async def upload(
        self, user_id: uuid.UUID, upload: UploadFile,
    ) -> Attachment:
        """Persist an uploaded file and return the new Attachment row.

        Raises ``AppException`` (413 / 415) when size or MIME is rejected.
        """
        # --- Validate MIME (header first; the real check is the body sniff).
        mime = (upload.content_type or "").lower()
        if mime not in ALLOWED_MIME_TYPES:
            raise AppException(
                415, "unsupported_media_type",
                f"MIME type '{mime}' is not allowed; "
                f"expected one of {sorted(ALLOWED_MIME_TYPES)}",
            )

        # --- Read body in chunks with size cap (R-M4-41: avoid buffering
        # the entire upload before rejecting oversized payloads).
        chunk_size = 64 * 1024
        chunks: list[bytes] = []
        total_size = 0
        while True:
            chunk = await upload.read(chunk_size)
            if not chunk:
                break
            total_size += len(chunk)
            if total_size > MAX_UPLOAD_BYTES:
                raise AppException(
                    413, "payload_too_large",
                    f"File size exceeds limit {MAX_UPLOAD_BYTES} bytes",
                )
            chunks.append(chunk)
        if total_size == 0:
            raise AppException(400, "empty_file", "Uploaded file is empty")
        data = b"".join(chunks)

        # --- Sniff MIME from magic bytes (R-M4-40: unrecognisable magic
        # → 415, no fallback to the easily-spoofed Content-Type header).
        sniffed = _sniff_mime(data)
        if sniffed not in ALLOWED_MIME_TYPES:
            raise AppException(
                415, "unsupported_media_type",
                "File content does not match any allowed image format",
            )
        final_mime = sniffed

        # --- Write to disk under ``<user_id>/<new_uuid>.<ext>``.
        attachment_id = uuid.uuid4()
        ext = _MIME_TO_EXT.get(final_mime, mimetypes.guess_extension(final_mime) or ".bin")
        object_key = f"{user_id}/{attachment_id}{ext}"

        upload_dir = _ensure_upload_dir()
        dest = upload_dir / object_key
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Avoid clobbering an existing file (UUID collision is negligible,
        # but defensive).
        if dest.exists():
            raise AppException(409, "conflict", "Attachment id collision")
        # NEW-C1 fix: 磁盘满等 OSError 给出明确 507（而非泛 500）
        try:
            dest.write_bytes(data)
        except OSError as exc:
            # P3-5: write_bytes may leave a partial file on disk; clean it
            # up before surfacing 507 (mirrors NEW-2 DB-flush cleanup).
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "Failed to clean up partial file %s after write error",
                    dest, exc_info=True,
                )
            raise AppException(
                507, "insufficient_storage",
                f"Failed to write attachment file: {exc}",
            ) from exc

        # --- Persist DB row.
        # NEW-2 fix: if the DB write fails after the file has been written
        # to disk, the file becomes an orphan (no DB row → cleanup can
        # never find it). Wrap in try/except to delete the orphaned file
        # before re-raising.
        attachment = Attachment(
            id=attachment_id,
            user_id=user_id,
            object_key=object_key,
            mime_type=final_mime,
            file_size=len(data),
            status=AttachmentStatus.PENDING,
            expires_at=datetime.now(UTC) + timedelta(hours=PENDING_EXPIRY_HOURS),
        )
        self.db.add(attachment)
        try:
            await self.db.flush()
        except Exception:
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "Failed to clean up orphan file %s after DB error",
                    dest, exc_info=True,
                )
            raise
        await self.db.refresh(attachment)
        return attachment

    # ------------------------------------------------------------------
    # List / get / download
    # ------------------------------------------------------------------

    async def list_attachments(
        self, user_id: uuid.UUID, *, limit: int = 100,
    ) -> list[Attachment]:
        result = await self.db.execute(
            select(Attachment)
            .where(Attachment.user_id == user_id)
            .order_by(Attachment.created_at.desc())
            .limit(limit)
        )
        rows = list(result.scalars().all())
        for row in rows:
            await self._mark_expired_if_needed(row)
        return rows

    async def get_attachment(
        self, attachment_id: uuid.UUID, user_id: uuid.UUID,
    ) -> Attachment:
        # _get_owned already calls _mark_expired_if_needed, so no
        # redundant flip is needed here (R-M4-45 fix).
        return await self._get_owned(attachment_id, user_id)

    async def get_attachment_file(
        self, attachment_id: uuid.UUID, user_id: uuid.UUID,
    ) -> tuple[Path, str]:
        """Return (file_path, filename) for download with auth check."""
        attachment = await self.get_attachment(attachment_id, user_id)
        if attachment.status == AttachmentStatus.EXPIRED:
            raise AppException(410, "expired", "Attachment has expired")
        path = _get_upload_dir() / attachment.object_key
        if not path.exists():
            raise AppException(404, "file_missing", "Attachment file is missing")
        # Preserve the original extension in the download filename.
        filename = f"attachment-{attachment.id}{path.suffix}"
        return path, filename

    # ------------------------------------------------------------------
    # Delete / link
    # ------------------------------------------------------------------

    async def delete_attachment(
        self, attachment_id: uuid.UUID, user_id: uuid.UUID,
    ) -> None:
        attachment = await self._get_owned(attachment_id, user_id)
        # Best-effort file removal; the row is always deleted so stale
        # files are reclaimed by the startup cleanup sweep.
        if attachment.object_key:
            try:
                (_get_upload_dir() / attachment.object_key).unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "Failed to delete attachment file %s",
                    attachment.object_key, exc_info=True,
                )
        await self.db.delete(attachment)
        await self.db.flush()

    async def link_attachment(
        self,
        attachment_id: uuid.UUID,
        user_id: uuid.UUID,
        transaction_id: uuid.UUID,
    ) -> Attachment:
        """Associate a PENDING attachment with a transaction.

        Both the attachment and the transaction must belong to the user.
        On success the attachment status becomes LINKED and expires_at
        is cleared (linked attachments are retained for the life of the
        transaction).
        """
        attachment = await self._get_owned(attachment_id, user_id)
        if attachment.status == AttachmentStatus.EXPIRED:
            raise AppException(410, "expired", "Attachment has expired")
        if attachment.status == AttachmentStatus.LINKED:
            raise AppException(
                409, "already_linked",
                "Attachment is already linked to a transaction",
            )
        await self._verify_transaction_ownership(transaction_id, user_id)

        attachment.transaction_id = transaction_id
        attachment.status = AttachmentStatus.LINKED
        attachment.expires_at = None
        await self.db.flush()
        await self.db.refresh(attachment)
        return attachment


# ---------------------------------------------------------------------------
# MIME sniffing
# ---------------------------------------------------------------------------

def _sniff_mime(data: bytes) -> str | None:
    """Return a MIME type inferred from magic bytes, or ``None`` if the
    header is unrecognised. Used as a defense-in-depth check against
    spoofed ``Content-Type`` headers.
    """
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None
