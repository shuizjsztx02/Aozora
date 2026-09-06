"""T-M4-04c: Pydantic schemas for Attachment API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class AttachmentResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    transaction_id: uuid.UUID | None = None
    object_key: str
    mime_type: str
    file_size: int | None = None
    status: str
    created_at: datetime
    expires_at: datetime | None = None

    model_config = {"from_attributes": True}


class AttachmentLinkRequest(BaseModel):
    """POST /attachments/{id}/link — associate an attachment with a transaction."""
    transaction_id: uuid.UUID
