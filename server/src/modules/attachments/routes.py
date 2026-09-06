"""T-M4-04c: Attachment API routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database import get_db_session
from src.models.user import User
from src.modules.auth.dependencies import get_current_user

from .schemas import AttachmentLinkRequest, AttachmentResponse
from .service import AttachmentService

router = APIRouter(prefix="/attachments", tags=["attachments"])


@router.post("/upload", response_model=AttachmentResponse, status_code=201)
async def upload_attachment(
    file: Annotated[UploadFile, File(...)],
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    service = AttachmentService(db)
    attachment = await service.upload(current_user.id, file)
    return AttachmentResponse.model_validate(attachment)


@router.get("", response_model=list[AttachmentResponse])
async def list_attachments(
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    service = AttachmentService(db)
    attachments = await service.list_attachments(current_user.id)
    return [AttachmentResponse.model_validate(a) for a in attachments]


@router.get("/{attachment_id}", response_model=AttachmentResponse)
async def get_attachment(
    attachment_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    service = AttachmentService(db)
    attachment = await service.get_attachment(attachment_id, current_user.id)
    return AttachmentResponse.model_validate(attachment)


@router.get("/{attachment_id}/download")
async def download_attachment(
    attachment_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    service = AttachmentService(db)
    file_path, filename = await service.get_attachment_file(
        attachment_id, current_user.id,
    )
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="application/octet-stream",
    )


@router.delete("/{attachment_id}", status_code=204)
async def delete_attachment(
    attachment_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    service = AttachmentService(db)
    await service.delete_attachment(attachment_id, current_user.id)


@router.post(
    "/{attachment_id}/link",
    response_model=AttachmentResponse,
)
async def link_attachment(
    attachment_id: uuid.UUID,
    body: AttachmentLinkRequest,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    service = AttachmentService(db)
    attachment = await service.link_attachment(
        attachment_id, current_user.id, body.transaction_id,
    )
    return AttachmentResponse.model_validate(attachment)
