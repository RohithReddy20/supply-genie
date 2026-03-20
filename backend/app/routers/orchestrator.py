from __future__ import annotations

from fastapi import APIRouter, Request

from app.config import get_settings
from app.schemas import ActionResult, DelayWorkflowResponse
from app.services.orchestrator import run_delay_workflow

router = APIRouter(prefix="/orchestration", tags=["orchestration"])


class _LegacyDelayEvent:
    """Thin wrapper kept for the legacy orchestration endpoint."""

    def __init__(self, po_number: str, supplier_name: str, eta_days: int):
        self.po_number = po_number
        self.supplier_name = supplier_name
        self.eta_days = eta_days


@router.post("/delay", response_model=DelayWorkflowResponse, deprecated=True)
async def coordinate_delay(
    payload: dict,
    request: Request,
    approved_by_human: bool = False,
) -> DelayWorkflowResponse:
    settings = get_settings()
    correlation_id = getattr(request.state, "correlation_id", "n/a")
    event = _LegacyDelayEvent(
        po_number=payload.get("po_number", ""),
        supplier_name=payload.get("supplier_name", ""),
        eta_days=payload.get("eta_days", 1),
    )
    return run_delay_workflow(
        event=event,
        correlation_id=correlation_id,
        require_human_approval=settings.require_human_approval,
        approved_by_human=approved_by_human,
    )
