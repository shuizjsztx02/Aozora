"""T-M4-04c: Tests for the Attachment API.

Coverage:
- upload: success, rejected MIME (415), oversized (413), empty (400),
  spoofed Content-Type sniffed via magic bytes → 415 (R-M4-40)
- list / get / download / delete: ownership (404 cross-user)
- download expired → 410
- link: success, cross-user transaction → 404, already linked → 409,
  expired attachment → 410
- expiry: PENDING past 24h auto-flips to EXPIRED; file deleted
- cleanup_expired_files: DB-driven; removes only expired PENDING/EXPIRED
  rows and their files; LINKED/PARSED rows are never touched (R-M4-39)
"""

from __future__ import annotations

import io
import shutil
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.models.attachment import Attachment, AttachmentStatus
from src.models.base import Base
from src.models.ledger import (
    Book,
    Category,
    CategoryKind,
    Transaction,
    TxSource,
    TxType,
)
from src.models.user import User
from src.modules.attachments.service import (
    MAX_UPLOAD_BYTES,
    AttachmentService,
    _sniff_mime,
    cleanup_expired_files,
)

# ---------------------------------------------------------------------------
# Temp-dir fixture (avoids pytest's tmp_path on Windows where it can
# hit PermissionError on the shared pytest-of-* parent directory).
# ---------------------------------------------------------------------------


@pytest.fixture()
def upload_dir():
    d = Path(tempfile.mkdtemp(prefix="aozora_test_uploads_"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Async DB fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def db():
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
async def users(db: AsyncSession) -> dict:
    alice = User(id=uuid.uuid4(), nickname="alice")
    bob = User(id=uuid.uuid4(), nickname="bob")
    db.add_all([alice, bob])
    await db.flush()
    return {"alice": alice, "bob": bob}


@pytest_asyncio.fixture()
async def alice_book(db: AsyncSession, users: dict) -> Book:
    book = Book(id=uuid.uuid4(), owner_id=users["alice"].id, name="日常")
    db.add(book)
    await db.flush()
    return book


@pytest_asyncio.fixture()
async def alice_tx(db: AsyncSession, alice_book: Book, users: dict) -> Transaction:
    cat = Category(
        id=uuid.uuid4(), owner_id=users["alice"].id, book_id=alice_book.id,
        kind=CategoryKind.EXPENSE, name="餐饮",
    )
    db.add(cat)
    await db.flush()
    tx = Transaction(
        id=uuid.uuid4(), book_id=alice_book.id, category_id=cat.id,
        type=TxType.EXPENSE, amount_cents=1000,
        biz_date=datetime.now(UTC).date(),
        occurred_at=datetime.now(UTC),
        source=TxSource.MANUAL,
    )
    db.add(tx)
    await db.flush()
    return tx


# ---------------------------------------------------------------------------
# Tiny JPEG / PNG / non-image fixtures (header bytes only)
# ---------------------------------------------------------------------------

FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
FAKE_WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 64
NOT_IMAGE = b"\x00\x00\x00\x00NOT_AN_IMAGE"


def _upload_bytes(data: bytes, content_type: str, filename: str = "x.jpg"):
    """Duck-typed UploadFile for service unit tests.

    Supports chunked ``read(size)`` (R-M4-41) as well as full ``read()``.
    """
    class _F:
        def __init__(self, data: bytes, content_type: str, filename: str):
            self._data = data
            self._pos = 0
            self.content_type = content_type
            self.filename = filename

        async def read(self, size: int = -1) -> bytes:
            if size < 0:
                chunk = self._data[self._pos:]
                self._pos = len(self._data)
            else:
                chunk = self._data[self._pos:self._pos + size]
                self._pos += len(chunk)
            return chunk

    return _F(data, content_type, filename)


# ---------------------------------------------------------------------------
# Unit tests (service layer)
# ---------------------------------------------------------------------------


class TestMimeSniffing:
    def test_sniff_jpeg(self):
        assert _sniff_mime(FAKE_JPEG) == "image/jpeg"

    def test_sniff_png(self):
        assert _sniff_mime(FAKE_PNG) == "image/png"

    def test_sniff_webp(self):
        assert _sniff_mime(FAKE_WEBP) == "image/webp"

    def test_sniff_unknown(self):
        assert _sniff_mime(NOT_IMAGE) is None


class TestUploadValidation:
    @pytest.mark.asyncio
    async def test_rejects_unsupported_mime(self, db: AsyncSession, users: dict):
        svc = AttachmentService(db)
        upload = _upload_bytes(FAKE_JPEG, "application/pdf")
        with pytest.raises(Exception) as excinfo:
            await svc.upload(users["alice"].id, upload)
        assert excinfo.value.status_code == 415

    @pytest.mark.asyncio
    async def test_rejects_oversized(self, db: AsyncSession, users: dict):
        svc = AttachmentService(db)
        big = b"\xff\xd8\xff\xe0" + b"\x00" * (MAX_UPLOAD_BYTES + 1)
        upload = _upload_bytes(big, "image/jpeg")
        with pytest.raises(Exception) as excinfo:
            await svc.upload(users["alice"].id, upload)
        assert excinfo.value.status_code == 413

    @pytest.mark.asyncio
    async def test_rejects_empty(self, db: AsyncSession, users: dict):
        svc = AttachmentService(db)
        upload = _upload_bytes(b"", "image/jpeg")
        with pytest.raises(Exception) as excinfo:
            await svc.upload(users["alice"].id, upload)
        assert excinfo.value.status_code == 400

    @pytest.mark.asyncio
    async def test_unknown_magic_bytes_rejected(
        self, db: AsyncSession, users: dict, upload_dir: Path, monkeypatch,
    ):
        """R-M4-40: When magic bytes are unrecognised the upload is
        rejected with 415 — no fallback to the Content-Type header.
        """
        from src.modules.attachments import service as svc_mod
        monkeypatch.setattr(svc_mod, "_get_upload_dir", lambda: upload_dir)

        svc = AttachmentService(db)
        # Unrecognised body even though header claims image/jpeg.
        upload = _upload_bytes(NOT_IMAGE, "image/jpeg")
        with pytest.raises(Exception) as excinfo:
            await svc.upload(users["alice"].id, upload)
        assert excinfo.value.status_code == 415


class TestUploadSuccess:
    @pytest.mark.asyncio
    async def test_upload_persists_row_and_file(
        self, db: AsyncSession, users: dict, upload_dir: Path, monkeypatch,
    ):
        from src.modules.attachments import service as svc_mod
        monkeypatch.setattr(svc_mod, "_get_upload_dir", lambda: upload_dir)

        svc = AttachmentService(db)
        upload = _upload_bytes(FAKE_JPEG, "image/jpeg")
        attachment = await svc.upload(users["alice"].id, upload)

        assert attachment.user_id == users["alice"].id
        assert attachment.mime_type == "image/jpeg"
        assert attachment.status == AttachmentStatus.PENDING
        assert attachment.file_size == len(FAKE_JPEG)
        assert attachment.expires_at is not None
        assert (upload_dir / attachment.object_key).exists()

    @pytest.mark.asyncio
    async def test_rejects_spoofed_header_when_body_is_not_image(
        self, db: AsyncSession, users: dict, upload_dir: Path, monkeypatch,
    ):
        """R-M4-40: Claiming image/jpeg with a non-image body is rejected
        because the sniffer returns None → 415 (no header fallback).
        """
        from src.modules.attachments import service as svc_mod
        monkeypatch.setattr(svc_mod, "_get_upload_dir", lambda: upload_dir)

        svc = AttachmentService(db)
        upload = _upload_bytes(NOT_IMAGE, "image/jpeg")
        with pytest.raises(Exception) as excinfo:
            await svc.upload(users["alice"].id, upload)
        assert excinfo.value.status_code == 415

    @pytest.mark.asyncio
    async def test_png_header_rejected_without_magic_match(
        self, db: AsyncSession, users: dict,
    ):
        """Non-allowed Content-Type is rejected even if body has PNG magic."""
        svc = AttachmentService(db)
        upload = _upload_bytes(FAKE_PNG, "application/octet-stream")
        with pytest.raises(Exception) as excinfo:
            await svc.upload(users["alice"].id, upload)
        assert excinfo.value.status_code == 415


class TestOwnership:
    @pytest.mark.asyncio
    async def test_list_only_own(
        self, db: AsyncSession, users: dict, upload_dir: Path, monkeypatch,
    ):
        from src.modules.attachments import service as svc_mod
        monkeypatch.setattr(svc_mod, "_get_upload_dir", lambda: upload_dir)

        svc = AttachmentService(db)
        a_alice = await svc.upload(users["alice"].id, _upload_bytes(FAKE_JPEG, "image/jpeg"))
        a_bob = await svc.upload(users["bob"].id, _upload_bytes(FAKE_PNG, "image/png"))

        alice_list = await svc.list_attachments(users["alice"].id)
        bob_list = await svc.list_attachments(users["bob"].id)
        assert {a.id for a in alice_list} == {a_alice.id}
        assert {a.id for a in bob_list} == {a_bob.id}

    @pytest.mark.asyncio
    async def test_get_cross_user_returns_404(
        self, db: AsyncSession, users: dict, upload_dir: Path, monkeypatch,
    ):
        from src.modules.attachments import service as svc_mod
        monkeypatch.setattr(svc_mod, "_get_upload_dir", lambda: upload_dir)

        svc = AttachmentService(db)
        att = await svc.upload(users["alice"].id, _upload_bytes(FAKE_JPEG, "image/jpeg"))
        with pytest.raises(Exception) as excinfo:
            await svc.get_attachment(att.id, users["bob"].id)
        assert excinfo.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_cross_user_returns_404(
        self, db: AsyncSession, users: dict, upload_dir: Path, monkeypatch,
    ):
        from src.modules.attachments import service as svc_mod
        monkeypatch.setattr(svc_mod, "_get_upload_dir", lambda: upload_dir)

        svc = AttachmentService(db)
        att = await svc.upload(users["alice"].id, _upload_bytes(FAKE_JPEG, "image/jpeg"))
        with pytest.raises(Exception) as excinfo:
            await svc.delete_attachment(att.id, users["bob"].id)
        assert excinfo.value.status_code == 404


class TestDownload:
    @pytest.mark.asyncio
    async def test_download_returns_file(
        self, db: AsyncSession, users: dict, upload_dir: Path, monkeypatch,
    ):
        from src.modules.attachments import service as svc_mod
        monkeypatch.setattr(svc_mod, "_get_upload_dir", lambda: upload_dir)

        svc = AttachmentService(db)
        att = await svc.upload(users["alice"].id, _upload_bytes(FAKE_JPEG, "image/jpeg"))
        path, filename = await svc.get_attachment_file(att.id, users["alice"].id)
        assert path.exists()
        assert path.read_bytes() == FAKE_JPEG
        assert str(att.id) in filename

    @pytest.mark.asyncio
    async def test_download_expired_returns_410(
        self, db: AsyncSession, users: dict, upload_dir: Path, monkeypatch,
    ):
        from src.modules.attachments import service as svc_mod
        monkeypatch.setattr(svc_mod, "_get_upload_dir", lambda: upload_dir)

        svc = AttachmentService(db)
        att = await svc.upload(users["alice"].id, _upload_bytes(FAKE_JPEG, "image/jpeg"))
        att.expires_at = datetime.now(UTC) - timedelta(hours=25)
        await db.flush()

        with pytest.raises(Exception) as excinfo:
            await svc.get_attachment_file(att.id, users["alice"].id)
        assert excinfo.value.status_code == 410


class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_removes_file_and_row(
        self, db: AsyncSession, users: dict, upload_dir: Path, monkeypatch,
    ):
        from src.modules.attachments import service as svc_mod
        monkeypatch.setattr(svc_mod, "_get_upload_dir", lambda: upload_dir)

        svc = AttachmentService(db)
        att = await svc.upload(users["alice"].id, _upload_bytes(FAKE_JPEG, "image/jpeg"))
        file_path = upload_dir / att.object_key
        assert file_path.exists()

        await svc.delete_attachment(att.id, users["alice"].id)
        assert not file_path.exists()
        result = await db.execute(select(Attachment).where(Attachment.id == att.id))
        assert result.scalar_one_or_none() is None


class TestLink:
    @pytest.mark.asyncio
    async def test_link_success(
        self, db: AsyncSession, users: dict, upload_dir: Path, monkeypatch, alice_tx,
    ):
        from src.modules.attachments import service as svc_mod
        monkeypatch.setattr(svc_mod, "_get_upload_dir", lambda: upload_dir)

        svc = AttachmentService(db)
        att = await svc.upload(users["alice"].id, _upload_bytes(FAKE_JPEG, "image/jpeg"))
        linked = await svc.link_attachment(att.id, users["alice"].id, alice_tx.id)

        assert linked.status == AttachmentStatus.LINKED
        assert linked.transaction_id == alice_tx.id
        assert linked.expires_at is None

    @pytest.mark.asyncio
    async def test_link_cross_user_transaction_returns_404(
        self, db: AsyncSession, users: dict, upload_dir: Path, monkeypatch,
    ):
        from src.modules.attachments import service as svc_mod
        monkeypatch.setattr(svc_mod, "_get_upload_dir", lambda: upload_dir)

        bob_book = Book(id=uuid.uuid4(), owner_id=users["bob"].id, name="bob's")
        db.add(bob_book)
        await db.flush()
        bob_cat = Category(
            id=uuid.uuid4(), owner_id=users["bob"].id, book_id=bob_book.id,
            kind=CategoryKind.EXPENSE, name="x",
        )
        db.add(bob_cat)
        await db.flush()
        bob_tx = Transaction(
            id=uuid.uuid4(), book_id=bob_book.id, category_id=bob_cat.id,
            type=TxType.EXPENSE, amount_cents=500,
            biz_date=datetime.now(UTC).date(),
            occurred_at=datetime.now(UTC),
            source=TxSource.MANUAL,
        )
        db.add(bob_tx)
        await db.flush()

        svc = AttachmentService(db)
        att = await svc.upload(users["alice"].id, _upload_bytes(FAKE_JPEG, "image/jpeg"))
        with pytest.raises(Exception) as excinfo:
            await svc.link_attachment(att.id, users["alice"].id, bob_tx.id)
        assert excinfo.value.status_code == 404

    @pytest.mark.asyncio
    async def test_link_already_linked_returns_409(
        self, db: AsyncSession, users: dict, upload_dir: Path, monkeypatch, alice_tx,
    ):
        from src.modules.attachments import service as svc_mod
        monkeypatch.setattr(svc_mod, "_get_upload_dir", lambda: upload_dir)

        svc = AttachmentService(db)
        att = await svc.upload(users["alice"].id, _upload_bytes(FAKE_JPEG, "image/jpeg"))
        await svc.link_attachment(att.id, users["alice"].id, alice_tx.id)
        with pytest.raises(Exception) as excinfo:
            await svc.link_attachment(att.id, users["alice"].id, alice_tx.id)
        assert excinfo.value.status_code == 409

    @pytest.mark.asyncio
    async def test_link_expired_returns_410(
        self, db: AsyncSession, users: dict, upload_dir: Path, monkeypatch, alice_tx,
    ):
        from src.modules.attachments import service as svc_mod
        monkeypatch.setattr(svc_mod, "_get_upload_dir", lambda: upload_dir)

        svc = AttachmentService(db)
        att = await svc.upload(users["alice"].id, _upload_bytes(FAKE_JPEG, "image/jpeg"))
        att.expires_at = datetime.now(UTC) - timedelta(hours=25)
        await db.flush()

        # _get_owned (called inside link_attachment) lazily flips
        # PENDING → EXPIRED via _mark_expired_if_needed, so the link
        # call below sees EXPIRED and raises 410.  The download path
        # also flips, so we exercise it first to verify independently.
        with pytest.raises(Exception) as excinfo:
            await svc.get_attachment_file(att.id, users["alice"].id)
        assert excinfo.value.status_code == 410

        # Now link should also reject (EXPIRED status is visible to link).
        with pytest.raises(Exception) as excinfo:
            await svc.link_attachment(att.id, users["alice"].id, alice_tx.id)
        assert excinfo.value.status_code == 410


class TestExpiry:
    @pytest.mark.asyncio
    async def test_pending_expiry_flips_status_and_deletes_file(
        self, db: AsyncSession, users: dict, upload_dir: Path, monkeypatch,
    ):
        from src.modules.attachments import service as svc_mod
        monkeypatch.setattr(svc_mod, "_get_upload_dir", lambda: upload_dir)

        svc = AttachmentService(db)
        att = await svc.upload(users["alice"].id, _upload_bytes(FAKE_JPEG, "image/jpeg"))
        file_path = upload_dir / att.object_key
        assert file_path.exists()

        att.expires_at = datetime.now(UTC) - timedelta(hours=25)
        await db.flush()

        # Trigger expiry flip via download path (which raises 410).
        with pytest.raises(Exception) as excinfo:
            await svc.get_attachment_file(att.id, users["alice"].id)
        assert excinfo.value.status_code == 410

        await db.refresh(att)
        assert att.status == AttachmentStatus.EXPIRED
        assert not file_path.exists()


class TestCleanup:
    """R-M4-39: DB-driven cleanup — only expired PENDING/EXPIRED rows are
    removed; LINKED/PARSED rows and their files are never touched.
    """

    @pytest.mark.asyncio
    async def test_cleanup_removes_expired_rows_and_files(
        self, db: AsyncSession, users: dict, upload_dir: Path, monkeypatch,
    ):
        from src.modules.attachments import service as svc_mod
        monkeypatch.setattr(svc_mod, "_get_upload_dir", lambda: upload_dir)

        svc = AttachmentService(db)

        # 1. A PENDING attachment that has expired.
        att_expired = await svc.upload(
            users["alice"].id, _upload_bytes(FAKE_JPEG, "image/jpeg"),
        )
        att_expired.expires_at = datetime.now(UTC) - timedelta(hours=25)
        await db.flush()
        expired_file = upload_dir / att_expired.object_key
        assert expired_file.exists()

        # 2. A PENDING attachment still within its window — must survive.
        att_fresh = await svc.upload(
            users["alice"].id, _upload_bytes(FAKE_PNG, "image/png"),
        )
        fresh_file = upload_dir / att_fresh.object_key
        assert fresh_file.exists()

        deleted = await cleanup_expired_files(db)
        assert deleted == 1
        assert not expired_file.exists()
        assert fresh_file.exists()

        # The expired row is gone from DB; the fresh row survives.
        result = await db.execute(
            select(Attachment).where(Attachment.id == att_expired.id),
        )
        assert result.scalar_one_or_none() is None
        result = await db.execute(
            select(Attachment).where(Attachment.id == att_fresh.id),
        )
        assert result.scalar_one_or_none() is not None

    @pytest.mark.asyncio
    async def test_cleanup_preserves_linked_files(
        self, db: AsyncSession, users: dict, upload_dir: Path, monkeypatch,
        alice_tx,
    ):
        """R-M4-39: LINKED attachments are never deleted, even if old."""
        from src.modules.attachments import service as svc_mod
        monkeypatch.setattr(svc_mod, "_get_upload_dir", lambda: upload_dir)

        svc = AttachmentService(db)
        att = await svc.upload(
            users["alice"].id, _upload_bytes(FAKE_JPEG, "image/jpeg"),
        )
        await svc.link_attachment(att.id, users["alice"].id, alice_tx.id)
        file_path = upload_dir / att.object_key
        assert file_path.exists()

        # Backdate created_at to simulate an old LINKED attachment.
        att.created_at = datetime.now(UTC) - timedelta(days=30)
        await db.flush()

        deleted = await cleanup_expired_files(db)
        assert deleted == 0
        assert file_path.exists()

        result = await db.execute(
            select(Attachment).where(Attachment.id == att.id),
        )
        assert result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# Integration via TestClient
# ---------------------------------------------------------------------------


class TestAttachmentAPI:
    """End-to-end via FastAPI TestClient — exercises routes, auth, schemas."""

    @pytest_asyncio.fixture()
    async def attachment_client(self, upload_dir: Path, monkeypatch):
        """TestClient with tables initialized and upload dir redirected."""
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import StaticPool

        from src.main import app
        from src.models.base import Base
        from src.modules.attachments import service as svc_mod
        monkeypatch.setattr(svc_mod, "_get_upload_dir", lambda: upload_dir)

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async def _override_get_db():
            async with factory() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        from src.common.database import get_db_session
        app.dependency_overrides[get_db_session] = _override_get_db

        from starlette.testclient import TestClient
        with TestClient(app) as c:
            yield c

        app.dependency_overrides.clear()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    def test_upload_and_list(self, attachment_client, upload_dir):
        client = attachment_client
        resp = client.post("/auth/guest", json={"timezone": "UTC", "device_id": "d1"})
        assert resp.status_code in (200, 201), resp.text
        body = resp.json()
        headers = {"Authorization": f"Bearer {body['tokens']['access_token']}"}
        user_id = body["user_id"]

        files = {"file": ("receipt.jpg", io.BytesIO(FAKE_JPEG), "image/jpeg")}
        resp = client.post("/attachments/upload", headers=headers, files=files)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["user_id"] == user_id
        assert body["mime_type"] == "image/jpeg"
        assert body["status"] == "pending"
        att_id = body["id"]

        resp = client.get("/attachments", headers=headers)
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["id"] == att_id

        resp = client.get(f"/attachments/{att_id}/download", headers=headers)
        assert resp.status_code == 200
        assert resp.content == FAKE_JPEG

        resp = client.delete(f"/attachments/{att_id}", headers=headers)
        assert resp.status_code == 204

        resp = client.get("/attachments", headers=headers)
        assert resp.json() == []

    def test_unauthenticated_rejected(self, attachment_client):
        resp = attachment_client.get("/attachments")
        assert resp.status_code in (401, 403)

    def test_oversized_rejected(self, attachment_client):
        client = attachment_client
        resp = client.post("/auth/guest", json={"timezone": "UTC", "device_id": "d1"})
        headers = {"Authorization": f"Bearer {resp.json()['tokens']['access_token']}"}

        big = b"\xff\xd8\xff\xe0" + b"\x00" * (MAX_UPLOAD_BYTES + 1)
        files = {"file": ("big.jpg", io.BytesIO(big), "image/jpeg")}
        resp = client.post("/attachments/upload", headers=headers, files=files)
        assert resp.status_code == 413

    def test_wrong_mime_rejected(self, attachment_client):
        client = attachment_client
        resp = client.post("/auth/guest", json={"timezone": "UTC", "device_id": "d1"})
        headers = {"Authorization": f"Bearer {resp.json()['tokens']['access_token']}"}

        files = {"file": ("x.pdf", io.BytesIO(NOT_IMAGE), "application/pdf")}
        resp = client.post("/attachments/upload", headers=headers, files=files)
        assert resp.status_code == 415

    def test_cross_user_isolation(self, attachment_client):
        client = attachment_client
        r1 = client.post("/auth/guest", json={"timezone": "UTC", "device_id": "d1"})
        h1 = {"Authorization": f"Bearer {r1.json()['tokens']['access_token']}"}
        r2 = client.post("/auth/guest", json={"timezone": "UTC", "device_id": "d2"})
        h2 = {"Authorization": f"Bearer {r2.json()['tokens']['access_token']}"}

        files = {"file": ("a.jpg", io.BytesIO(FAKE_JPEG), "image/jpeg")}
        resp = client.post("/attachments/upload", headers=h1, files=files)
        assert resp.status_code == 201
        att_id = resp.json()["id"]

        assert client.get(f"/attachments/{att_id}", headers=h2).status_code == 404
        assert client.get(f"/attachments/{att_id}/download", headers=h2).status_code == 404


# ---------------------------------------------------------------------------
# NEW-2: Orphan file cleanup on DB flush failure
# ---------------------------------------------------------------------------

class TestOrphanCleanup:
    """NEW-2: When the DB flush fails after writing the file, the orphan
    file must be cleaned up so it does not linger on disk without a row.
    """

    @pytest.mark.asyncio
    async def test_upload_db_failure_cleans_up_file(
        self, db: AsyncSession, users: dict, upload_dir: Path, monkeypatch,
    ):
        from src.modules.attachments import service as svc_mod
        monkeypatch.setattr(svc_mod, "_get_upload_dir", lambda: upload_dir)

        # Force flush to fail by breaking the session.
        async def _broken_flush(*a, **kw):
            raise RuntimeError("simulated DB failure")

        monkeypatch.setattr(db, "flush", _broken_flush)

        svc = AttachmentService(db)
        upload = _upload_bytes(FAKE_JPEG, "image/jpeg")

        with pytest.raises(RuntimeError, match="simulated DB failure"):
            await svc.upload(users["alice"].id, upload)

        # The file must NOT remain on disk (orphan).
        files_on_disk = list(upload_dir.rglob("*"))
        files_on_disk = [f for f in files_on_disk if f.is_file()]
        assert len(files_on_disk) == 0, (
            f"Orphan files left on disk after DB failure: {files_on_disk}"
        )
