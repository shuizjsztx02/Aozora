"""T-M8-04：admin 鉴权、密码哈希与只读会话依赖。

三件事在这里落地（`docs/architecture/admin-console.md` §6–§7）：

1. **独立 JWT**：单独 secret（``ADMIN_JWT_SECRET``）+ 单独 audience
   （:data:`ADMIN_JWT_AUDIENCE`）。用户端 token 签名对不上 admin secret，
   管理员 token 也对不上用户端 secret，两个方向都过不去。
2. **密码哈希**：标准库 ``hashlib.scrypt`` + 随机 salt，格式自描述
   （``scrypt$n$r$p$salt$digest``），**不新增依赖**。
3. **只读事务**：读端点共用的 session 在 PostgreSQL 上先执行
   ``SET TRANSACTION READ ONLY``；SQLite 测试后端没有等价语句，退化为普通
   只读用法（本模块的查询层 ``queries.py`` 也只构造 ``select``）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.config import get_config
from src.common.database import get_session_factory
from src.common.errors import AppException
from src.models.admin import AdminAccount

#: 管理员 token 的 audience。**与 C 端 token 无共有部分**——即便 secret 配错成
#: 同一个值，audience 校验仍会拒绝跨端使用（08-04 的双向 401 验收）。
ADMIN_JWT_AUDIENCE = "aozora-admin"

#: ``auto_error=False``：缺 token 时由 :func:`require_admin` 统一报 401
#: （``HTTPBearer`` 的默认行为是 403，而「未认证」应当是 401）。
admin_bearer = HTTPBearer(auto_error=False)

# scrypt 参数：N=2**14 / r=8 / p=1 ≈ 16 MiB 内存、几十毫秒一次。取值来自
# Python 文档的推荐下限方向——登录是低频操作，不需要为吞吐再降低强度。
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_SCHEME = "scrypt"


# ---------------------------------------------------------------------------
# 密码哈希（标准库，不引入依赖）
# ---------------------------------------------------------------------------

def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _scrypt(password: str, salt: bytes, *, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode(), salt=salt, n=n, r=r, p=p, dklen=_SCRYPT_DKLEN,
    )


def hash_admin_password(password: str) -> str:
    """``scrypt$n$r$p$salt$digest``——参数随行存储，未来调整强度不需要迁移。"""
    if not password:
        raise ValueError("admin password must not be empty")
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return "$".join((
        _SCRYPT_SCHEME, str(_SCRYPT_N), str(_SCRYPT_R), str(_SCRYPT_P),
        _b64encode(salt), _b64encode(digest),
    ))


def verify_admin_password(password: str, encoded: str) -> bool:
    """校验密码。任何格式异常都返回 ``False``（不区分「哈希坏了」与「密码错」）。"""
    try:
        scheme, n_raw, r_raw, p_raw, salt_raw, digest_raw = encoded.split("$")
        if scheme != _SCRYPT_SCHEME:
            return False
        digest = _scrypt(
            password, _b64decode(salt_raw),
            n=int(n_raw), r=int(r_raw), p=int(p_raw),
        )
        return hmac.compare_digest(digest, _b64decode(digest_raw))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def create_admin_token(admin_id: uuid.UUID | str) -> str:
    """签发管理员 token（独立 secret + audience）。"""
    cfg = get_config()
    now = datetime.now(UTC)
    payload = {
        "sub": str(admin_id),
        "aud": ADMIN_JWT_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(minutes=cfg.admin_token_expire_minutes),
    }
    return jwt.encode(payload, cfg.admin_jwt_secret, algorithm=cfg.jwt_algorithm)


def decode_admin_token(token: str) -> dict:
    """解码并校验管理员 token；失败抛 ``jwt`` 的异常（调用方统一转 401）。"""
    cfg = get_config()
    return jwt.decode(
        token,
        cfg.admin_jwt_secret,
        algorithms=[cfg.jwt_algorithm],
        audience=ADMIN_JWT_AUDIENCE,
    )


# ---------------------------------------------------------------------------
# 客户端 IP
# ---------------------------------------------------------------------------

def client_ip(request: Request) -> str:
    """真实客户端 IP：反向代理后 ``request.client.host`` 是代理地址，取
    ``X-Forwarded-For`` 的第一跳（与 C 端审计口径一致）。"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# 只读会话
# ---------------------------------------------------------------------------

async def begin_read_only(session: AsyncSession) -> None:
    """把当前事务钉成只读。

    PostgreSQL 用 ``SET TRANSACTION READ ONLY``（必须是事务里的第一条语句；
    这里在 session 刚创建时调用，满足该前提）。SQLite 测试后端没有等价语句，
    退化为普通会话——「误写在只读事务里报错」因此在 SQLite 上不可验证，
    保证由 PG 生产环境承担（架构 §5「读端点用只读事务」）。
    """
    bind = session.bind
    if bind is not None and bind.dialect.name == "postgresql":
        await session.execute(text("SET TRANSACTION READ ONLY"))


async def get_admin_read_session() -> AsyncIterator[AsyncSession]:
    """读端点的 session 依赖：只读事务 + 结束即回滚（不提交任何东西）。"""
    factory = get_session_factory()
    async with factory() as session:
        await begin_read_only(session)
        try:
            yield session
        finally:
            await session.rollback()


# ---------------------------------------------------------------------------
# 鉴权依赖
# ---------------------------------------------------------------------------

async def require_admin(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(admin_bearer),
    ],
    db: Annotated[AsyncSession, Depends(get_admin_read_session)],
) -> AdminAccount:
    """解管理员 token → 查 ``admin_accounts``（且 ``status=active``）。

    与 C 端 :func:`src.modules.auth.dependencies.get_current_user` 同构，但
    **互不通用**：secret 与 audience 都不同（架构 §6）。
    """
    if credentials is None:
        raise AppException(
            401, "missing_admin_token", "Administrator credentials required",
        )
    try:
        payload = decode_admin_token(credentials.credentials)
    except Exception:
        raise AppException(
            401, "invalid_admin_token", "Invalid or expired admin token",
        ) from None

    subject = payload.get("sub")
    try:
        admin_id = uuid.UUID(str(subject))
    except (TypeError, ValueError):
        raise AppException(
            401, "invalid_admin_token", "Invalid token subject",
        ) from None

    admin = await db.scalar(
        select(AdminAccount).where(AdminAccount.id == admin_id)
    )
    if admin is None or admin.status != "active":
        raise AppException(
            401, "invalid_admin_token", "Administrator not found or inactive",
        )
    return admin
