#!/usr/bin/env python3
"""infra/cleanup/attachment-expiry.py — T-M5-04c 解析后 24h 删除原图。

定期（建议每小时）扫描 PARSED 状态且 ``keep_after_parse=false`` 且
updated_at <= now - 24h 的附件：
  - 删除磁盘文件（unlink）
  - 将行状态翻为 EXPIRED，并清空 object_key（避免未来被误下载）

运行方式：
    docker compose exec api \
      python -m infra.cleanup.attachment-expiry

或作为独立 cron：
    DATABASE_URL=postgresql+asyncpg://... python infra/cleanup/attachment-expiry.py

幂等性：仅扫描 PARSED 行；已是 EXPIRED 的行不重复处理。
退出码：0 成功（含 0 条删除），1 运行时错误。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "server") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "server"))

from sqlalchemy import select, update  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from src.common.database import get_session_factory  # noqa: E402
from src.models.attachment import Attachment, AttachmentStatus  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("attachment-expiry")

# Parsed attachments are kept for 24h to allow late AI retries / user
# review before the on-disk object is purged.
PARSED_RETENTION_HOURS = 24


def _get_upload_dir() -> Path:
    """Mirror the path used by ``attachments.service._get_upload_dir``."""
    raw = os.environ.get("UPLOAD_DIR")
    if raw:
        return Path(raw)
    # Default matches the attachments service fallback.
    return Path(tempfile.gettempdir()) / "aozora_uploads"


async def expire_parsed_attachments(session: AsyncSession) -> int:
    """Mark parsed attachments older than 24h as EXPIRED and unlink files."""
    cutoff = datetime.now(UTC) - timedelta(hours=PARSED_RETENTION_HOURS)
    result = await session.execute(
        select(Attachment).where(
            Attachment.status == AttachmentStatus.PARSED,
            Attachment.keep_after_parse.is_(False),
            Attachment.updated_at <= cutoff,
        )
    )
    stale = list(result.scalars().all())

    upload_dir = _get_upload_dir()
    expired = 0
    for att in stale:
        # Remove the disk object first — if this fails we keep the row
        # intact (so the next sweep can retry) rather than orphaning
        # the DB record pointing at nothing.
        if att.object_key:
            path = upload_dir / att.object_key
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "Failed to unlink %s — skipping row %s", path, att.id,
                )
                continue

        att.status = AttachmentStatus.EXPIRED
        att.object_key = ""  # prevent future download of a missing file
        expired += 1

    await session.commit()
    return expired


async def main() -> int:
    logger.info(
        "Attachment-expiry sweep starting (parsed retention=%dh)",
        PARSED_RETENTION_HOURS,
    )
    factory = get_session_factory()
    async with factory() as session:
        expired = await expire_parsed_attachments(session)
    logger.info(
        "Attachment-expiry sweep complete — %d attachments expired", expired,
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
