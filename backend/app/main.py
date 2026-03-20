from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.observability import configure_observability, correlation_id_middleware
from app.routers import approvals, chat, connectors, health, incidents, orchestrator

settings = get_settings()
configure_observability(service_name="happy-robot-backend")

app = FastAPI(title=settings.app_name, version="0.1.0")

# Order matters: add_middleware builds outward (last added = outermost).
# CORS must be outermost so it intercepts OPTIONS preflight before routing.
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


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": settings.app_name, "status": "ok"}
