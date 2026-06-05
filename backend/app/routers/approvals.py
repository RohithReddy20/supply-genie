from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ActionStatus, Approval, ApprovalStatus
from app.services.action_dispatcher import dispatch_incident_actions

router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApprovalDecisionIn(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    decided_by: str = Field(min_length=2)
    reason: str | None = None


class ApprovalItemOut(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    action_run_id: UUID
    incident_id: UUID
    action_type: str
    status: str
    requested_at: datetime
    context: dict | None = None


@router.get("/pending")
def list_pending_approvals(db: Session = Depends(get_db)) -> dict:
    approvals = (
        db.query(Approval)
        .filter(Approval.status == ApprovalStatus.pending)
        .all()
    )

    items = []
    for a in approvals:
        action = a.action_run
        incident = a.incident
        items.append(
            {
                "id": str(a.id),
                "action_run_id": str(a.action_run_id),
                "incident_id": str(a.incident_id),
                "action_type": action.action_type.value,
                "status": a.status.value,
                "requested_at": a.requested_at.isoformat(),
                "context": incident.payload,
            }
        )

    return {"items": items}


@router.post("/{approval_id}/decide")
def decide_approval(
    approval_id: UUID,
    body: ApprovalDecisionIn,
    db: Session = Depends(get_db),
) -> dict:
    approval = db.get(Approval, approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")

    now = datetime.now(timezone.utc)
    new_status = (
        ApprovalStatus.approved if body.decision == "approved" else ApprovalStatus.rejected
    )

    # Atomic compare-and-swap: only update if still pending.
    # This prevents double-execution when two requests race.
    rows = db.execute(
        update(Approval)
        .where(Approval.id == approval_id, Approval.status == ApprovalStatus.pending)
        .values(
            status=new_status,
            decided_at=now,
            decided_by=body.decided_by,
            reason=body.reason,
        )
    ).rowcount
    db.flush()

    if rows == 0:
        # Re-read to get the current status for the error message
        db.refresh(approval)
        raise HTTPException(
            status_code=409,
            detail=f"Approval already decided: {approval.status.value}",
        )

    db.refresh(approval)
    action = approval.action_run

    if body.decision == "approved":
        action.status = ActionStatus.pending
        db.commit()

        incident = approval.incident
        db.refresh(incident)
        dispatch_incident_actions(db, incident)
        db.refresh(action)

        next_status = action.status.value
    else:
        action.status = ActionStatus.skipped
        db.commit()
        next_status = "skipped"

    return {
        "id": str(approval.id),
        "status": approval.status.value,
        "decided_at": approval.decided_at.isoformat(),
        "action_run_id": str(approval.action_run_id),
        "next_status": next_status,
    }
