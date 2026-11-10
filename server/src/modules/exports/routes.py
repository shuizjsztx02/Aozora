"""T-M2-05: Export API routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database import get_db_session
from src.common.telemetry import record_export
from src.models.user import User
from src.modules.auth.dependencies import get_current_user
from src.modules.auth.frozen import require_active as _require_active
from src.modules.auth.suspended import require_not_suspended as _require_not_suspended

from .schemas import ExportCreate, ExportResponse
from .service import ExportService

router = APIRouter(prefix="/exports", tags=["exports"])


@router.post("", response_model=ExportResponse, status_code=201)
async def create_export(
    body: ExportCreate,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    _require_active(current_user)
    _require_not_suspended(current_user)
    service = ExportService(db)
    export = await service.create_export(body.book_id, current_user.id, body.format)
    record_export()
    return ExportResponse.model_validate(export)


@router.get("", response_model=list[ExportResponse])
async def list_exports(
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    _require_active(current_user)
    service = ExportService(db)
    exports = await service.list_exports(current_user.id)
    return [ExportResponse.model_validate(e) for e in exports]


@router.get("/{export_id}", response_model=ExportResponse)
async def get_export(
    export_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    _require_active(current_user)
    service = ExportService(db)
    export = await service.get_export(export_id, current_user.id)
    return ExportResponse.model_validate(export)


@router.get("/{export_id}/download")
async def download_export(
    export_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db_session)] = ...,
):
    _require_active(current_user)
    service = ExportService(db)
    file_path, filename = await service.get_export_file(export_id, current_user.id)
    media_type = "application/json" if file_path.endswith(".json") else "text/csv"
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type=media_type,
    )
