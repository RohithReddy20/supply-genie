from __future__ import annotations

from fastapi import APIRouter, Request

from app.config import get_settings
from app.schemas import DelayEvent, DelayWorkflowResponse
from app.services.orchestrator import run_delay_workflow

router = APIRouter(prefix="/orchestration", tags=["orchestration"])


@router.post("/delay", response_model=DelayWorkflowResponse)
async def coordinate_delay(
    payload: DelayEvent,
    request: Request,
    approved_by_human: bool = False,
) -> DelayWorkflowResponse:
    settings = get_settings()
    correlation_id = getattr(request.state, "correlation_id", "n/a")
    return run_delay_workflow(
        event=payload,
        correlation_id=correlation_id,
        require_human_approval=settings.require_human_approval,
        approved_by_human=approved_by_human,
    )
