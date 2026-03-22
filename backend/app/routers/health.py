from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

from app.database import SessionLocal
from app.resilience import get_all_circuit_breakers

logger = logging.getLogger("backend.health")
router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def readiness() -> dict[str, Any]:
    checks: dict[str, Any] = {}

    # Database check
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"

    # Circuit breaker states
    breakers = get_all_circuit_breakers()
    cb_status = {}
    for name, cb in breakers.items():
        cb_status[name] = cb.state.value
    checks["circuit_breakers"] = cb_status

    # Voice sessions
    try:
        from app.services.voice_session import _active_sessions
    except ImportError:
        _active_sessions = {}
    checks["active_voice_sessions"] = len(_active_sessions)

    all_ok = checks["database"] == "ok" and all(
        s == "closed" for s in cb_status.values()
    )

    return {
        "status": "ready" if all_ok else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }
