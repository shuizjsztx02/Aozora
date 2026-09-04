"""T-M2-01: Error models and exception handlers."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response models (published in OpenAPI)
# ---------------------------------------------------------------------------

class ErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str | None = None


class ValidationErrorResponse(BaseModel):
    code: str = "validation_error"
    message: str
    errors: list[dict]
    request_id: str | None = None


# ---------------------------------------------------------------------------
# Exception subclasses
# ---------------------------------------------------------------------------

class AppException(Exception):
    """Base application exception that maps to an HTTP error response."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach all exception handlers to *app*."""

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                code="http_error",
                message=exc.detail if isinstance(exc.detail, str) else str(exc.detail),
                request_id=_request_id(request),
            ).model_dump(),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError,
    ) -> JSONResponse:
        # ``exc.errors()`` can include non-serialisable objects (e.g. the
        # original ``ValueError`` raised by a ``field_validator``) inside
        # the ``ctx`` dict. Sanitise to strings so the JSON encoder
        # never sees a raw exception.
        safe_errors = []
        for err in exc.errors():
            clean = {k: v for k, v in err.items() if k != "ctx"}
            if "ctx" in err:
                clean["ctx"] = {
                    k: (str(v) if not isinstance(v, (str, int, float, bool, list, dict, type(None))) else v)
                    for k, v in err["ctx"].items()
                }
            safe_errors.append(clean)
        return JSONResponse(
            status_code=422,
            content=ValidationErrorResponse(
                message="Request validation failed",
                errors=safe_errors,
                request_id=_request_id(request),
            ).model_dump(),
        )

    @app.exception_handler(AppException)
    async def app_exception_handler(
        request: Request, exc: AppException,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                code=exc.code,
                message=exc.message,
                request_id=_request_id(request),
            ).model_dump(),
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_exception_handler(
        request: Request, exc: SQLAlchemyError,
    ) -> JSONResponse:
        logger.exception("Database error")
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                code="database_error",
                message="Internal database error",
                request_id=_request_id(request),
            ).model_dump(),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception,
    ) -> JSONResponse:
        logger.exception("Unhandled exception")
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                code="internal_error",
                message="Internal server error",
                request_id=_request_id(request),
            ).model_dump(),
        )
