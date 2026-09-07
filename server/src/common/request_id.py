"""T-M5-05a: Request-ID middleware with OTel tracing integration.

Every response carries an ``X-Request-Id`` header.  If the client
supplies one the server re-uses it; otherwise a UUID-v4 is generated.

T-M5-05a: request_id is now propagated as a span attribute for distributed
tracing correlation.
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from src.common.telemetry import get_tracer

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

        # T-M5-05a: add request_id as span attribute for tracing
        tracer = get_tracer()
        with tracer.start_as_current_span(f"{request.method} {request.url.path}") as span:
            span.set_attribute("request.id", request_id)
            span.set_attribute("http.method", request.method)
            span.set_attribute("http.url", str(request.url))

            response = await call_next(request)
            response.headers[HEADER] = request_id
            return response
