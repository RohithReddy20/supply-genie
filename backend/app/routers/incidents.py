from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import ActionRun, IncidentStatus, IncidentType
from app.models import ActionStatus as ActionStatusEnum
from app.schemas import (
    AbsenceEventIn,
    ActionsSummary,
    DelayEventIn,
    IncidentCreatedResponse,
    IncidentDetailOut,
    IncidentListOut,
    IncidentOut,
)
from app.services.action_dispatcher import get_queue_status, requeue_failed_action
from app.services.action_executor import retry_failed_actions
from app.services.incidents import (
    get_incident,
    ingest_absence,
    ingest_delay,
    list_incidents,
)

router = APIRouter(prefix="/incidents", tags=["incidents"])


def _build_actions_summary(incident) -> ActionsSummary:
    """Build action counts from eager-loaded actions relationship."""
    actions = incident.actions or []
    return ActionsSummary(
        total=len(actions),
        completed=sum(1 for a in actions if a.status == ActionStatusEnum.completed),
        needs_approval=sum(1 for a in actions if a.status == ActionStatusEnum.needs_approval),
    )


def _incident_out_with_summary(incident) -> IncidentOut:
    out = IncidentOut.model_validate(incident)
    out.actions_summary = _build_actions_summary(incident)
    return out


@router.post("/delay", response_model=IncidentCreatedResponse)
def create_delay_incident(
    payload: DelayEventIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> IncidentCreatedResponse:
    settings = get_settings()
    correlation_id = UUID(getattr(request.state, "correlation_id", None) or "00000000-0000-0000-0000-000000000000")

    incident, is_duplicate = ingest_delay(
        db=db,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        po_number=payload.po_number,
        supplier_id=payload.supplier_id,
        delay_reason=payload.delay_reason,
        new_eta=payload.new_eta,
        severity=payload.severity,
        source=payload.source,
        require_human_approval=settings.require_human_approval,
    )

    response.status_code = 200 if is_duplicate else 201
    return IncidentCreatedResponse(
        incident=_incident_out_with_summary(incident),
        is_duplicate=is_duplicate,
    )


@router.post("/absence", response_model=IncidentCreatedResponse)
def create_absence_incident(
    payload: AbsenceEventIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> IncidentCreatedResponse:
    settings = get_settings()
    correlation_id = UUID(getattr(request.state, "correlation_id", None) or "00000000-0000-0000-0000-000000000000")

    incident, is_duplicate = ingest_absence(
        db=db,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        worker_name=payload.worker_name,
        site_id=payload.site_id,
        shift_date=payload.shift_date,
        role=payload.role,
        reason=payload.reason,
        contractor_id=payload.contractor_id,
        contractor_phone=payload.contractor_phone,
        severity=payload.severity,
        source=payload.source,
        require_human_approval=settings.require_human_approval,
    )

    response.status_code = 200 if is_duplicate else 201
    return IncidentCreatedResponse(
        incident=_incident_out_with_summary(incident),
        is_duplicate=is_duplicate,
    )


@router.get("", response_model=IncidentListOut)
def list_all_incidents(
    db: Session = Depends(get_db),
    status: IncidentStatus | None = None,
    type: IncidentType | None = None,
    limit: int = 20,
    offset: int = 0,
) -> IncidentListOut:
    items, total = list_incidents(
        db, status=status, incident_type=type, limit=min(limit, 100), offset=offset,
    )
    return IncidentListOut(
        items=[_incident_out_with_summary(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{incident_id}", response_model=IncidentDetailOut)
def get_incident_detail(
    incident_id: UUID,
    db: Session = Depends(get_db),
) -> IncidentDetailOut:
    incident = get_incident(db, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    return IncidentDetailOut.model_validate(incident)


@router.post("/{incident_id}/retry")
def retry_incident_actions(
    incident_id: UUID,
    db: Session = Depends(get_db),
) -> dict:
    incident = get_incident(db, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    retried = retry_failed_actions(db, incident)
    return {
        "incident_id": str(incident_id),
        "retried_actions": [str(a.id) for a in retried],
        "count": len(retried),
    }


@router.get("/queue/status")
def action_queue_status(db: Session = Depends(get_db)) -> dict:
    """Queue and dead-letter counts for async action execution rollout."""
    return get_queue_status(db)


@router.get("/actions/dead-letter")
def list_dead_letter_actions(
    limit: int = 20,
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    capped_limit = min(max(limit, 1), 100)

    rows = (
        db.query(ActionRun)
        .filter(
            ActionRun.status == ActionStatusEnum.failed,
            ActionRun.retry_count >= settings.max_retries,
        )
        .order_by(desc(ActionRun.created_at))
        .limit(capped_limit)
        .all()
    )

    return {
        "items": [
            {
                "action_id": str(action.id),
                "incident_id": str(action.incident_id),
                "action_type": action.action_type.value,
                "status": action.status.value,
                "retry_count": action.retry_count,
                "error_message": action.error_message,
                "dead_lettered": bool((action.response_payload or {}).get("dead_lettered", False)),
                "created_at": action.created_at.isoformat() if action.created_at else None,
            }
            for action in rows
        ],
        "count": len(rows),
    }


@router.post("/actions/{action_id}/requeue")
def requeue_failed_action_endpoint(
    action_id: UUID,
    db: Session = Depends(get_db),
) -> dict:
    action = db.get(ActionRun, action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    if action.status != ActionStatusEnum.failed:
        raise HTTPException(status_code=409, detail="Only failed actions can be requeued")

    mode = requeue_failed_action(db, action)
    db.refresh(action)

    return {
        "action_id": str(action.id),
        "incident_id": str(action.incident_id),
        "dispatch_mode": mode,
        "next_status": action.status.value,
        "retry_count": action.retry_count,
    }
