"""T-M2-01: Aozora Production API — application entry point."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.common.config import get_config
from src.common.errors import register_exception_handlers
from src.common.health import router as health_router
from src.common.logging import setup_logging
from src.common.request_id import RequestIdMiddleware
from src.common.security import RequestBodyLimitMiddleware, SecurityHeadersMiddleware
from src.common.telemetry import record_request
from src.modules.accounts.routes import router as accounts_router
from src.modules.admin.routes import router as admin_router
from src.modules.ai.routes import router as ai_router
from src.modules.attachments.routes import router as attachments_router
from src.modules.auth.routes import me_router
from src.modules.auth.routes import router as auth_router
from src.modules.books.routes import router as books_router
from src.modules.budgets.routes import router as budgets_router
from src.modules.categories.routes import router as categories_router
from src.modules.consent.routes import router as consent_router
from src.modules.deletion.routes import router as deletion_router
from src.modules.exports.routes import router as exports_router
from src.modules.statistics.routes import router as stats_router
from src.modules.sync.routes import router as sync_router
from src.modules.telemetry.routes import router as telemetry_router
from src.modules.transactions.routes import router as transactions_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown lifecycle."""
    cfg = get_config()
    setup_logging(cfg.log_level)
    # T-M5-05a: initialize OpenTelemetry SDK (traces → Tempo, metrics → Prometheus)
    from src.common.telemetry import setup_telemetry
    setup_telemetry(
        service_name="aozora-api",
        otlp_endpoint=cfg.otel_endpoint,
        enable_tracing=cfg.enable_telemetry,
        enable_metrics=cfg.enable_telemetry,
    )
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
    # T-M4-04c: reclaim disk from expired PENDING attachment uploads.
    # R-M4-39 fix: now DB-driven — needs an async session.
    from src.modules.attachments.service import (
        cleanup_expired_files as cleanup_attachments,
    )
    try:
        from src.common.database import get_session_factory
        factory = get_session_factory()
        async with factory() as att_session:
            att_deleted = await cleanup_attachments(att_session)
        if att_deleted:
            logger.info("Cleaned up %d stale attachment files", att_deleted)
    except Exception:
        logger.warning("Attachment cleanup failed", exc_info=True)
    # T-M4-01: in dev/staging a missing provider key must not block startup
    # (production refuses earlier, at Settings validation — T-M4-01a); the
    # gateway degrades to 503 at runtime. Check the key directly rather than
    # calling build_provider(): constructing a provider just for this check
    # would open a pooled HTTP client that is never used or closed.
    if not cfg.ai_provider_api_key:
        logger.warning(
            "AI provider not configured (AI_PROVIDER_API_KEY empty) — "
            "/ai/interpret will return 503 ai_unavailable"
        )
    # T-M5-05b follow-up: in-process daily D1/D7 retention refresh.
    # The OTel gauges live in this process, so the job must too. Gated by
    # RETENTION_JOB_ENABLED (default off) — an explicitly enabled deployment
    # opts in; test suites never run it (a background DB task racing against
    # the StaticPool shared connection used by :memory: SQLite fixtures).
    retention_task: asyncio.Task | None = None
    if cfg.enable_telemetry and cfg.retention_job_enabled:
        from src.common.database import get_session_factory
        from src.modules.telemetry.retention import retention_loop

        retention_task = asyncio.create_task(
            retention_loop(get_session_factory()),
            name="retention-loop",
        )
    # T-M7-04d: 会话事件日志的保留期任务。**刻意不挂 enable_telemetry**——
    # ``security.md`` 的保留期是一条隐私义务，不能被埋点开关（prod compose 当前
    # 就是关的）顺带关掉。默认关闭 + 部署侧显式开启，口径同 RETENTION_JOB_ENABLED。
    conversation_retention_task: asyncio.Task | None = None
    if cfg.ai_conversation_retention_enabled:
        from src.common.database import get_session_factory
        from src.modules.ai.retention import retention_loop as conversation_retention_loop

        conversation_retention_task = asyncio.create_task(
            conversation_retention_loop(get_session_factory()),
            name="conversation-retention-loop",
        )
    yield
    from src.common.database import get_engine

    if retention_task is not None:
        retention_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await retention_task

    if conversation_retention_task is not None:
        conversation_retention_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await conversation_retention_task

    # P1#1: close the pooled AI provider HTTP client before disposing the engine.
    from src.modules.ai.provider import close_llm_provider
    try:
        await close_llm_provider()
    except Exception:
        logger.warning("Error closing AI provider during shutdown", exc_info=True)

    try:
        engine = get_engine()
        await engine.dispose()
    except Exception:
        logger.warning("Error disposing engine during shutdown", exc_info=True)
    logger.info("Shutdown complete")


cfg = get_config()

app = FastAPI(
    title="Aozora API",
    description="Aozora production ledger API",
    version="0.1.0",
    # T-M5-02b: hide Swagger/ReDoc/OpenAPI in production.
    openapi_url=None if cfg.env == "production" else "/openapi.json",
    docs_url=None if cfg.env == "production" else "/docs",
    redoc_url=None if cfg.env == "production" else "/redoc",
    lifespan=lifespan,
)

# -- Middleware (order matters: outermost LAST — add_middleware wraps) -----

app.add_middleware(RequestIdMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type", "X-Request-Id"],
    # T-M8-09：导出前端要读附件文件名（跨源 dev 下不暴露该头时浏览器读不到）。
    expose_headers=["Content-Disposition"],
)
# T-M5-02b: request-body size cap (inner).
app.add_middleware(
    RequestBodyLimitMiddleware,
    max_bytes=cfg.max_request_size,
    exclude_paths=[f"{cfg.api_prefix}/attachments"],
)
# T-M5-02b / R-T-M5-02-3: security headers must be the outermost middleware
# so they attach even when RequestBodyLimitMiddleware short-circuits with 413.
app.add_middleware(SecurityHeadersMiddleware)

# -- Exception handlers ---------------------------------------------------

register_exception_handlers(app)


# -- Request logging ------------------------------------------------------

@app.middleware("http")
async def log_requests(request: Request, call_next):
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
    # T-M5-05a: record request metrics (counter, error counter, duration histogram)
    # T-M5-05ab fix (S2-NEW-3): use the route template (e.g. /api/v1/books/{book_id})
    # instead of the raw URL path — the latter contains UUIDs and drives
    # unbounded cardinality in Prometheus (one series per unique path).
    route_path = request.scope.get("route").path if request.scope.get("route") else request.url.path
    record_request(request.method, route_path, response.status_code, duration_ms)
    return response


# -- Routers --------------------------------------------------------------
# Apply the configured api_prefix so reverse proxies can strip it cleanly
# and future v2 routes can coexist.

app.include_router(health_router, prefix=cfg.api_prefix)
app.include_router(auth_router, prefix=cfg.api_prefix)
app.include_router(me_router, prefix=cfg.api_prefix)
# T-M8-04: 运营后台读端点。独立前缀（架构 §5）：``{API_PREFIX}/admin``。
app.include_router(admin_router, prefix=cfg.api_prefix + "/admin")
app.include_router(books_router, prefix=cfg.api_prefix)
app.include_router(categories_router, prefix=cfg.api_prefix)
app.include_router(accounts_router, prefix=cfg.api_prefix)
app.include_router(transactions_router, prefix=cfg.api_prefix)
app.include_router(budgets_router, prefix=cfg.api_prefix)
app.include_router(stats_router, prefix=cfg.api_prefix)
app.include_router(exports_router, prefix=cfg.api_prefix)
app.include_router(sync_router, prefix=cfg.api_prefix)
app.include_router(ai_router, prefix=cfg.api_prefix)
app.include_router(attachments_router, prefix=cfg.api_prefix)
app.include_router(consent_router, prefix=cfg.api_prefix)
app.include_router(deletion_router, prefix=cfg.api_prefix)
app.include_router(telemetry_router, prefix=cfg.api_prefix)

# M8-10：运营后台静态站点。**不依赖任何 nginx 改动**——复用现有 /aozora-api/
# 反代：nginx 原样透传 → SharedProxyPrefixMiddleware 剥离前缀 → /aozora-admin/。
# 目录由 compose 以只读 volume 挂到 ADMIN_STATIC_DIR；不存在时不挂载（本机开发、
# 未构建前端时都走这条分支，不影响 API 启动）。
_admin_static_dir = os.getenv("ADMIN_STATIC_DIR", "/app/admin-dist")
if os.path.isdir(_admin_static_dir):
    app.mount(
        "/aozora-admin",
        StaticFiles(directory=_admin_static_dir, html=True),
        name="aozora-admin",
    )


@app.get("/", tags=["meta"])
async def root() -> dict[str, str]:
    return {"message": "Aozora API", "docs": "/docs"}


# -- Shared reverse-proxy path prefix ---------------------------------------
# A 机的 Nginx 是多业务共享配置，`location /aozora-api/` 无法改为剥离前缀，
# 由应用入口在 ASGI 最外层接受带前缀的请求：/aozora-api/... → /...。
# 剥离后走的中间件/路由/指标链路与直连完全一致（RequestBodyLimit 的
# exclude_paths、Prometheus route label 均维持 cfg.api_prefix 规范路径）；
# 不带前缀的请求（回环探活、容器 healthcheck、本机开发）原样透传，两形态共存。

SHARED_PROXY_PREFIX = "/aozora-api"


class SharedProxyPrefixMiddleware:
    """Pure-ASGI middleware: strips ``SHARED_PROXY_PREFIX`` from http path/raw_path."""

    def __init__(self, asgi_app) -> None:
        self._app = asgi_app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            original: str = scope["path"]
            if original == SHARED_PROXY_PREFIX:
                stripped: str | None = "/"
            elif original.startswith(SHARED_PROXY_PREFIX + "/"):
                stripped = original[len(SHARED_PROXY_PREFIX):]
            else:
                stripped = None
            if stripped is not None:
                scope = {**scope, "path": stripped}
                raw = scope.get("raw_path")
                if isinstance(raw, bytes):
                    prefix_b = SHARED_PROXY_PREFIX.encode()
                    if raw == prefix_b:
                        scope["raw_path"] = b"/"
                    elif raw.startswith(prefix_b + b"/"):
                        scope["raw_path"] = raw[len(prefix_b):]
        await self._app(scope, receive, send)


# add_middleware 最后添加 = 用户中间件最外层（在 SecurityHeaders 之外），
# 且保持 app 仍是 FastAPI 实例（测试与 uvicorn 均直接引用 src.main:app）。
app.add_middleware(SharedProxyPrefixMiddleware)
