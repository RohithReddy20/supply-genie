from __future__ import annotations

from app.schemas import DelayWorkflowResponse
from app.services.workflows.engine import DelayWorkflowRequest, get_workflow_engine


def run_delay_workflow(
    event: DelayEvent,
    correlation_id: str,
    require_human_approval: bool,
    approved_by_human: bool,
) -> DelayWorkflowResponse:
    req = DelayWorkflowRequest(
        po_number=event.po_number,
        supplier_name=event.supplier_name,
        eta_days=event.eta_days,
        correlation_id=correlation_id,
        require_human_approval=require_human_approval,
        approved_by_human=approved_by_human,
    )
    engine = get_workflow_engine()
    return engine.run_shipment_delay(req)
