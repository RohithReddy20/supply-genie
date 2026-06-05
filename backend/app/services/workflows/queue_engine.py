from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from app.schemas import ActionResult, DelayWorkflowResponse
from app.services.safety import check_human_approval_required

if TYPE_CHECKING:
    from app.services.workflows.engine import DelayWorkflowRequest


class QueueWorkflowEngine:
    """Queue-first workflow engine for short/simple orchestrations."""

    def run_shipment_delay(self, req: "DelayWorkflowRequest") -> DelayWorkflowResponse:
        workflow_id = str(uuid4())

        actions: list[ActionResult] = [
            ActionResult(
                action="slack",
                status="queued",
                detail=f"Will notify ops channel about PO {req.po_number}.",
            ),
            ActionResult(
                action="call_production",
                status="queued",
                detail=f"Will call supplier {req.supplier_name} for updated ETA.",
            ),
            ActionResult(
                action="update_po",
                status="queued",
                detail=f"Will update PO {req.po_number} with ETA +{req.eta_days} days.",
            ),
        ]

        decision = check_human_approval_required(
            require_human_approval=req.require_human_approval,
            is_customer_facing=True,
            approved_by_human=req.approved_by_human,
        )

        actions.append(
            ActionResult(
                action="email_customer",
                status="queued" if decision.allowed else "needs_approval",
                detail=decision.reason,
            )
        )

        return DelayWorkflowResponse(
            workflow_id=workflow_id,
            correlation_id=req.correlation_id,
            actions=actions,
        )
