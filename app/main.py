"""SW Couriers Backend — FastAPI app factory.

Modular monolith with versioned APIs, three-layer RBAC,
optimistic locking, and full audit trail.
"""

from contextlib import asynccontextmanager

try:
    import tzdata  # noqa: F401 — registers IANA zones for ``zoneinfo`` (Windows / minimal images).
except ImportError:
    pass

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

import app.models  # noqa: F401 — register all ORM models for relationship resolution
from app.common.constants import API_VERSION
from app.common.enums import LogEvent
from app.common.exceptions import register_exception_handlers
from app.core.config import settings
from app.core.database import close_db, get_async_session, init_db
from app.core.logging import setup_logging
from app.core.rate_limit import limiter, rate_limit_exceeded_handler
from app.core.redis import close_redis, init_redis
from app.core.swagger import register_docs_routes
from app.middleware.security import SecurityHeadersMiddleware
from app.modules.holidays.bootstrap import seed_universal_uk_holidays_for_current_year
from app.router import api_router

setup_logging()
logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize and tear down resources."""
    # ── Startup ──────────────────────────────
    if not settings.is_test and not (settings.DATABASE_URL or "").strip():
        raise RuntimeError("DATABASE_URL must be set when APP_ENV is not 'test'")

    await init_db()
    logger.info(LogEvent.DATABASE_CONNECTED)

    if not settings.is_test:
        try:
            await init_redis()
        except Exception as exc:
            if settings.is_production:
                logger.error(LogEvent.REDIS_CONNECTION_FAILED, error=str(exc))
                raise
            logger.warning(LogEvent.REDIS_CONNECTION_FAILED, error=str(exc))
        async with get_async_session() as session:
            seeded_count = await seed_universal_uk_holidays_for_current_year(session)
            if seeded_count > 0:
                logger.info("holiday.bootstrap.seeded", count=seeded_count)

    logger.info(LogEvent.APP_STARTED, env=settings.APP_ENV, version=API_VERSION)

    yield

    # ── Shutdown ─────────────────────────────
    await close_redis()
    await close_db()
    logger.info(LogEvent.APP_STOPPED)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="SW Couriers API",
        description=(
            "Backend for SW Couriers — courier logistics platform. "
            "Caller identity (admin, customer, warehouse, driver) is determined by the **X-Client-Type** header "
            "for cookie naming and refresh-token handling."
            "\n\n Driver app uses Bearer token for authentication., All other web clients use cookies."
            "\n\nAll responses follow a standard envelope: success + data (or message) on success, success + message + error on failure."
            "\n\nSuccess with data: `{success: true, data: {...}}`"
            "\n\nSuccess message-only: `{success: true, message: '...'}`"
            "\n\nError: `{success: false, message: '...', error: {code: '...'}}`"
        ),
        version=API_VERSION,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    # ── Swagger/ReDoc ─────────────────────────
    register_docs_routes(app)

    # ── Rate limiting (slowapi — Redis-backed, per-route decorators + global default)
    # NOTE: FastAPI reverses middleware order — last add_middleware = outermost layer.
    # SlowAPI is added first so it ends up inner; CORS is added last so it is outermost.
    # This ensures OPTIONS preflight gets Access-Control-* headers before SlowAPI
    # can intercept and return a non-2xx response.
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=settings.cors_allow_methods_list,
        allow_headers=settings.cors_allow_headers_list,
    )

    # ── Security headers (HSTS, CSP, and related) ────────────
    # We only enable the security middleware in production so that
    # local development and automated tests are not blocked by HTTPS
    # requirements or a very strict CSP.
    if settings.is_production:
        app.add_middleware(SecurityHeadersMiddleware)

    # ── Exception handlers ───────────────────
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    register_exception_handlers(app)

    # ── Routers ──────────────────────────────
    app.include_router(api_router, prefix=settings.API_PREFIX)

    Instrumentator(
        excluded_handlers=["/metrics", "/docs", "/redoc", "/openapi.json"],
    ).instrument(
        app
    ).expose(app, include_in_schema=False)

    return app


app = create_app()
