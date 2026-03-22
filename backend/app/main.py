from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from app.config import get_settings
from app.observability import configure_observability, correlation_id_middleware
from app.routers import approvals, chat, connectors, health, incidents, kpis, orchestrator, voice

logger = logging.getLogger("backend")
settings = get_settings()
configure_observability(service_name="happy-robot-backend")

app = FastAPI(title=settings.app_name, version="0.1.0")
FastAPIInstrumentor.instrument_app(app)


# ── Request timeout middleware ───────────────────────────────────────────

async def request_timeout_middleware(request: Request, call_next) -> Response:
    """Cancel requests that exceed the timeout budget."""
    # Skip timeout for WebSocket upgrades and voice streaming
    if request.url.path.startswith("/api/v1/voice"):
        return await call_next(request)

    try:
        return await asyncio.wait_for(
            call_next(request),
            timeout=settings.request_timeout_s,
        )
    except asyncio.TimeoutError:
        logger.warning("Request timed out: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=504,
            content={"detail": "Request timed out"},
        )


# Order matters: add_middleware builds outward (last added = outermost).
# CORS must be outermost so it intercepts OPTIONS preflight before routing.
app.middleware("http")(request_timeout_middleware)
app.middleware("http")(correlation_id_middleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001", "http://localhost:3002"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(incidents.router, prefix=settings.api_prefix)
app.include_router(orchestrator.router, prefix=settings.api_prefix)
app.include_router(approvals.router, prefix=settings.api_prefix)
app.include_router(connectors.router, prefix=settings.api_prefix)
app.include_router(chat.router, prefix=settings.api_prefix)
app.include_router(kpis.router, prefix=settings.api_prefix)
app.include_router(voice.router, prefix=settings.api_prefix)


# ── Lifecycle events ─────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    logger.info("Starting %s (env=%s)", settings.app_name, settings.environment)


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down %s — draining active voice sessions", settings.app_name)
    from app.services.voice_pipeline import get_active_sessions
    for sid, session in list(get_active_sessions().items()):
        if session._lifecycle:
            session._lifecycle.mark_closed()
    logger.info("Shutdown complete")


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": settings.app_name, "status": "ok"}
