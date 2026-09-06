"""T-M2-01: Aozora Production API — application entry point."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from src.common.config import get_config
from src.common.errors import register_exception_handlers
from src.common.health import router as health_router
from src.common.logging import setup_logging
from src.common.request_id import RequestIdMiddleware
from src.modules.auth.routes import me_router, router as auth_router
from src.modules.books.routes import router as books_router
from src.modules.categories.routes import router as categories_router
from src.modules.accounts.routes import router as accounts_router
from src.modules.transactions.routes import router as transactions_router
from src.modules.budgets.routes import router as budgets_router
from src.modules.statistics.routes import router as stats_router
from src.modules.exports.routes import router as exports_router
from src.modules.sync.routes import router as sync_router
from src.modules.ai.routes import router as ai_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown lifecycle."""
    cfg = get_config()
    setup_logging(cfg.log_level)
    logger.info(
        "Starting Aozora API",
        extra={
            "env": cfg.env,
            "api_prefix": cfg.api_prefix,
        },
    )
    # N-05: reclaim disk from export files that outlived the prior process.
    from src.modules.exports.service import cleanup_expired_files
    try:
        deleted = cleanup_expired_files()
        if deleted:
            logger.info("Cleaned up %d stale export files", deleted)
    except Exception:
        logger.warning("Export cleanup failed", exc_info=True)
    # T-M4-01: a missing provider key must not block startup (dev/staging
    # run without provider access); the gateway degrades to 503 at runtime.
    from src.modules.ai.provider import build_provider
    if build_provider(cfg) is None:
        logger.warning(
            "AI provider not configured (AI_PROVIDER_API_KEY empty) — "
            "/ai/interpret will return 503 ai_unavailable"
        )
    yield
    from src.common.database import get_engine

    try:
        engine = get_engine()
        await engine.dispose()
    except Exception:
        logger.warning("Error disposing engine during shutdown", exc_info=True)
    logger.info("Shutdown complete")


app = FastAPI(
    title="Aozora API",
    description="Aozora production ledger API",
    version="0.1.0",
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# -- Middleware (order matters: outermost first) --------------------------

app.add_middleware(RequestIdMiddleware)

cfg = get_config()
app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -- Exception handlers ---------------------------------------------------

register_exception_handlers(app)


# -- Request logging ------------------------------------------------------

@app.middleware("http")
async def log_requests(request: Request, call_next):  # noqa: ANN001
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    logger.info(
        "%s %s %d",
        request.method,
        request.url.path,
        response.status_code,
        extra={
            "request_id": getattr(request.state, "request_id", None),
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    return response


# -- Routers --------------------------------------------------------------
# Apply the configured api_prefix so reverse proxies can strip it cleanly
# and future v2 routes can coexist.

app.include_router(health_router, prefix=cfg.api_prefix)
app.include_router(auth_router, prefix=cfg.api_prefix)
app.include_router(me_router, prefix=cfg.api_prefix)
app.include_router(books_router, prefix=cfg.api_prefix)
app.include_router(categories_router, prefix=cfg.api_prefix)
app.include_router(accounts_router, prefix=cfg.api_prefix)
app.include_router(transactions_router, prefix=cfg.api_prefix)
app.include_router(budgets_router, prefix=cfg.api_prefix)
app.include_router(stats_router, prefix=cfg.api_prefix)
app.include_router(exports_router, prefix=cfg.api_prefix)
app.include_router(sync_router, prefix=cfg.api_prefix)
app.include_router(ai_router, prefix=cfg.api_prefix)


@app.get("/", tags=["meta"])
async def root() -> dict[str, str]:
    return {"message": "Aozora API", "docs": "/docs"}
