"""T-M2-03: FastAPI dependencies for authentication."""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database import get_db_session
from src.common.errors import AppException
from src.models.user import User, UserStatus

from .utils import decode_access_token

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer()

# In-memory rate limiter — keyed by client_ip only.
# Replaced by Redis-backed limiter in multi-instance deployments.
# NOTE (R-M2-04): this dict is bounded by ``_MAX_ENTRIES``. Stale entries
# (timestamps older than the window) are evicted periodically and when
# the dict exceeds the cap, so memory use stays bounded even under
# traffic from many distinct IPs.
# NOTE (N-10): keyed by IP alone so all auth endpoints share a single
# bucket per client. Previously keyed by (ip, path), which multiplied
# the effective limit by the number of auth endpoints (4 × 10 = 40/min).
_rate_limits: dict[str, list[float]] = defaultdict(list)
_last_cleanup: float = 0.0
_MAX_ENTRIES = 10_000

MAX_AUTH_REQUESTS = 10
RATE_WINDOW_SECONDS = 60
CLEANUP_INTERVAL_SECONDS = 60


def _get_client_ip(request: Request) -> str:
    """Extract real client IP, honoring ``X-Forwarded-For`` when present.

    R-M2-18: behind a reverse proxy ``request.client.host`` is the proxy IP;
    the first hop in ``X-Forwarded-For`` is the original client.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # Take the first IP in the chain (original client).
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


def _cleanup_expired(now: float) -> None:
    """Remove entries whose timestamps have all expired; cap dict size."""
    expired_keys = [
        key for key, timestamps in _rate_limits.items()
        if all(now - t >= RATE_WINDOW_SECONDS for t in timestamps)
    ]
    for key in expired_keys:
        _rate_limits.pop(key, None)
    # If still too large (many distinct active IPs), drop the oldest.
    if len(_rate_limits) > _MAX_ENTRIES:
        for key in sorted(
            _rate_limits,
            key=lambda k: _rate_limits[k][-1] if _rate_limits[k] else 0.0,
        )[: len(_rate_limits) - _MAX_ENTRIES]:
            _rate_limits.pop(key, None)


def check_auth_rate_limit(request: Request) -> None:
    """Simple per-IP rate limit for auth endpoints.

    Synchronous on purpose — the implementation is pure in-memory work
    (no IO), so there is nothing to await. Keeping it sync also lets
    tests exercise it without an event loop.

    N-10: all auth endpoints share a single bucket per IP — the previous
    per-(ip, path) keying multiplied the effective limit by the number
    of auth endpoints.
    """
    global _last_cleanup
    client_ip = _get_client_ip(request)
    now = time.time()
    key = client_ip

    # Periodic cleanup (cheap — only scans dict keys)
    if now - _last_cleanup > CLEANUP_INTERVAL_SECONDS:
        _cleanup_expired(now)
        _last_cleanup = now

    # Prune this entry's expired timestamps
    _rate_limits[key] = [t for t in _rate_limits[key] if now - t < RATE_WINDOW_SECONDS]

    if len(_rate_limits[key]) >= MAX_AUTH_REQUESTS:
        raise AppException(429, "rate_limited", "Too many requests, please try again later")

    _rate_limits[key].append(now)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> User:
    """Decode access token and return the authenticated user.

    Raises 401 for invalid/expired tokens, 403 for deactivated/deleted users.
    """
    try:
        payload = decode_access_token(credentials.credentials)
    except Exception:
        raise AppException(401, "invalid_token", "Invalid or expired access token")

    user_id = payload.get("sub")
    if not user_id:
        raise AppException(401, "invalid_token", "Invalid token payload")

    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise AppException(401, "invalid_token", "Invalid token subject")

    try:
        result = await db.execute(select(User).where(User.id == uid))
    except SQLAlchemyError:
        logger.exception("Database error while resolving access token")
        raise AppException(503, "service_unavailable", "Authentication temporarily unavailable")
    user = result.scalar_one_or_none()

    if user is None:
        raise AppException(401, "user_not_found", "User not found")
    # T-M5-04c: FROZEN users can still authenticate (so they can call
    # GET /me / DELETE /me / POST /me/deletion-cancel), but per-endpoint
    # guards in sync/stats reject data-plane operations. DEACTIVATED
    # and DELETED are fully blocked.
    if user.status in (UserStatus.DEACTIVATED, UserStatus.DELETED):
        raise AppException(403, "account_disabled", "Account is deactivated or deleted")

    return user
