"""T-M2-03: Hashing and JWT utilities.

- Refresh tokens are hashed with SHA-256 before storage.
- Access tokens are JWTs signed with HS256.
- Bcrypt is available for future password-based auth.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from src.common.config import get_config


# ---------------------------------------------------------------------------
# Refresh token hashing
# ---------------------------------------------------------------------------

def hash_refresh_token(token: str) -> str:
    """SHA-256 hash for database storage (refresh tokens never stored in plaintext)."""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_refresh_token() -> str:
    """Generate a cryptographically secure random refresh token (64 hex chars)."""
    return secrets.token_hex(32)


# ---------------------------------------------------------------------------
# Bcrypt (available for future password-based auth)
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def create_access_token(user_id: str) -> str:
    """Create a short-lived JWT access token."""
    cfg = get_config()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(minutes=cfg.access_token_expire_minutes),
    }
    return jwt.encode(payload, cfg.secret_key, algorithm=cfg.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """Decode and validate a JWT access token. Raises on failure."""
    cfg = get_config()
    return jwt.decode(token, cfg.secret_key, algorithms=[cfg.jwt_algorithm])
