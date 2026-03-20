from __future__ import annotations

from fastapi import FastAPI

from app.config import get_settings
from app.observability import configure_observability, correlation_id_middleware
from app.routers import approvals, connectors, health, incidents, orchestrator

settings = get_settings()
configure_observability(service_name="happy-robot-backend")

app = FastAPI(title=settings.app_name, version="0.1.0")
app.middleware("http")(correlation_id_middleware)

app.include_router(health.router)
app.include_router(incidents.router, prefix=settings.api_prefix)
app.include_router(orchestrator.router, prefix=settings.api_prefix)
app.include_router(approvals.router, prefix=settings.api_prefix)
app.include_router(connectors.router, prefix=settings.api_prefix)


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": settings.app_name, "status": "ok"}
