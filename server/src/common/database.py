"""T-M2-01: Async database engine and session dependency."""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from src.common.config import get_config

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return (or create) the async SQLAlchemy engine."""
    global _engine
    if _engine is None:
        cfg = get_config()
        kwargs: dict = {"pool_pre_ping": True}
        # In-memory SQLite needs StaticPool so every connection sees the
        # same database (each new connection to :memory: would otherwise
        # get a fresh, empty database).
        if "sqlite" in cfg.database_url and ":memory:" in cfg.database_url:
            kwargs["poolclass"] = StaticPool
        _engine = create_async_engine(cfg.database_url, **kwargs)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
        )
    return _session_factory


async def get_db_session() -> AsyncSession:  # type: ignore[misc]
    """FastAPI dependency — yields one DB session per request."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def check_health_db() -> bool:
    """Return True if the database is reachable."""
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.warning("Health DB check failed", exc_info=True)
        return False


def reset_engine() -> None:
    """Dispose engine and clear caches (used in tests)."""
    global _engine, _session_factory
    if _engine is not None:
        # Synchronous disposal — close the sync pool if available.
        sync_pool = getattr(_engine.sync_engine.pool, "_pool", None)
        if sync_pool is not None:
            try:
                sync_pool.dispose()
            except Exception:
                pass
    _engine = None
    _session_factory = None
