"""
AI Code Breaker — FastAPI application entrypoint.

Exposes /health plus the /submissions contract endpoints. No database, no
code execution, and no AI calls yet — /submissions/validate only checks and
normalizes a submission's shape.
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.middleware import BodySizeLimitMiddleware
from app.api.routes.submissions import router as submissions_router
from app.core.config import get_settings
from app.core.logging_config import configure_logging, redact_url

settings = get_settings()

# Structured logging must be configured before the app starts handling
# requests. Format/level come from env (json+INFO in production).
configure_logging(level=settings.log_level, fmt=settings.log_format)
logger = logging.getLogger("acb.api")

app = FastAPI(
    title="AI Code Breaker",
    description=(
        "Finds behavioral differences between a candidate and a reference "
        "Python implementation for a given natural-language specification."
    ),
    version="0.1.0",
)

# Reject oversized request bodies early (413) before they're buffered — a
# coarse DoS guard complementing the per-field schema limits.
app.add_middleware(
    BodySizeLimitMiddleware, max_body_bytes=settings.max_request_body_bytes
)

# CORS: the Next.js dev server runs on a different origin (port) than the
# FastAPI dev server, so the browser will block requests without this. The
# allowed origins are config-driven (never "*", which would be unsafe with
# credentials), and only the methods/headers actually used are allowed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", settings.client_id_header],
)


app.include_router(submissions_router)


@app.on_event("startup")
def _log_startup() -> None:
    # A single structured startup line. URLs are redacted of credentials and
    # the API key is never included — only whether one is configured.
    logger.info(
        "api starting",
        extra={
            "database": redact_url(settings.database_url),
            "redis": redact_url(settings.redis_url),
            "execution_backend": settings.execution_backend,
            "queue_eager": settings.queue_eager,
            "anthropic_configured": settings.anthropic_configured,
            "log_format": settings.log_format,
        },
    )


@app.get("/health")
def health() -> dict:
    """
    Liveness check. Returns a fixed shape as long as the process is up and
    can serve requests. Deliberately does NOT touch the database or Redis —
    an orchestrator uses this to decide whether to restart the container, and
    a transient dependency blip shouldn't cause a restart loop. Use /ready
    for dependency health.
    """
    return {"status": "ok", "service": "ai-code-breaker-backend"}


@app.get("/ready")
def ready() -> JSONResponse:
    """
    Readiness check. Verifies the API can actually serve traffic by checking
    its dependencies: the database and Redis. Returns 200 when both are
    reachable, 503 otherwise, with a per-dependency breakdown. A load
    balancer uses this to decide whether to route traffic to this instance.

    Checks are best-effort and bounded; they never leak credentials (any
    connection URL is redacted before appearing in a log or response).
    """
    checks: dict[str, str] = {}
    ok = True

    # Database connectivity.
    try:
        from sqlalchemy import text

        from app.db.base import engine

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 - report status, don't crash
        ok = False
        checks["database"] = "unavailable"
        logger.warning(
            "readiness: database check failed",
            extra={"dependency": "database", "error_type": type(exc).__name__},
        )

    # Redis connectivity (used by the job queue). In eager mode there's no
    # Redis dependency, so it's reported as not-applicable.
    if settings.queue_eager:
        checks["redis"] = "skipped (eager mode)"
    else:
        try:
            from app.queue import get_redis_connection

            get_redis_connection(settings.redis_url).ping()
            checks["redis"] = "ok"
        except Exception as exc:  # noqa: BLE001
            ok = False
            checks["redis"] = "unavailable"
            logger.warning(
                "readiness: redis check failed",
                extra={"dependency": "redis", "error_type": type(exc).__name__},
            )

    status_code = 200 if ok else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": "ready" if ok else "not_ready", "checks": checks},
    )
