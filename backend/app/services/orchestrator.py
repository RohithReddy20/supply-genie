from __future__ import annotations

from uuid import uuid4

from app.schemas import ActionResult, DelayEvent, DelayWorkflowResponse
from app.services.safety import check_human_approval_required


def run_delay_workflow(
    event: DelayEvent,
    correlation_id: str,
    require_human_approval: bool,
    approved_by_human: bool,
) -> DelayWorkflowResponse:
    workflow_id = str(uuid4())

    actions: list[ActionResult] = [
        ActionResult(
            action="slack",
            status="queued",
            detail=f"Will notify ops channel about PO {event.po_number}.",
        ),
        ActionResult(
            action="call_production",
            status="queued",
            detail=f"Will call supplier {event.supplier_name} for updated ETA.",
        ),
        ActionResult(
            action="update_po",
            status="queued",
            detail=f"Will update PO {event.po_number} with ETA +{event.eta_days} days.",
        ),
    ]

    decision = check_human_approval_required(
        require_human_approval=require_human_approval,
        is_customer_facing=True,
        approved_by_human=approved_by_human,
    )

    email_status = "queued" if decision.allowed else "needs_approval"
    actions.append(
        ActionResult(
            action="email_customer",
            status=email_status,
            detail=decision.reason,
        )
    )

    return DelayWorkflowResponse(
        workflow_id=workflow_id,
        correlation_id=correlation_id,
        actions=actions,
    )
