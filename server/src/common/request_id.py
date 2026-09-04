"""T-M2-01: Request-ID middleware.

Every response carries an ``X-Request-Id`` header.  If the client
supplies one the server re-uses it; otherwise a UUID-v4 is generated.
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

HEADER = "X-Request-Id"


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id: str = (
            request.headers.get(HEADER)
            or request.headers.get(HEADER.lower())
            or uuid.uuid4().hex
        )
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers[HEADER] = request_id
        return response
