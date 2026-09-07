"""T-M5-02b: Security headers and request-body size limit middleware.

Adds defence-in-depth HTTP headers (HSTS, CSP, frame-deny, etc.) and
a global request-body size cap to protect non-upload JSON endpoints.
"""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

_SECURITY_HEADERS: dict[str, str] = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": "default-src 'self'",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject security headers into every response."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        for header, value in _SECURITY_HEADERS.items():
            response.headers[header] = value
        return response


# ---------------------------------------------------------------------------
# Request-body size limit
# ---------------------------------------------------------------------------

class RequestBodyLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose body exceeds *max_bytes*.

    If ``Content-Length`` is present and over the limit the request is
    rejected immediately (413).  Requests without ``Content-Length``
    (e.g. chunked transfer encoding) are **not** size-limited here —
    downstream parsers are expected to enforce their own caps (FastAPI's
    JSON body parser, Pydantic model field limits, etc.).

    Paths listed in *exclude_paths* bypass the check (they have their
    own streaming limit — e.g. the attachment upload endpoint).
    """

    def __init__(
        self,
        app: Starlette,
        max_bytes: int = 10 * 1024 * 1024,
        exclude_paths: list[str] | None = None,
    ) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes
        self.exclude_paths = exclude_paths or []

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.method not in ("POST", "PUT", "PATCH"):
            return await call_next(request)

        # Skip excluded paths (e.g. attachment uploads with own limit).
        for prefix in self.exclude_paths:
            if request.url.path.startswith(prefix):
                return await call_next(request)

        # Fast-path: Content-Length header present.
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_bytes:
                    return JSONResponse(
                        status_code=413,
                        content={"error": {"code": "payload_too_large",
                                           "message": "Request body exceeds the maximum allowed size"}},
                    )
            except ValueError:
                pass  # Malformed Content-Length; fall through to chunked check.

        return await call_next(request)
