from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import IncidentStatus, IncidentType
from app.schemas import (
    AbsenceEventIn,
    DelayEventIn,
    IncidentCreatedResponse,
    IncidentDetailOut,
    IncidentListOut,
    IncidentOut,
)
from app.services.action_executor import retry_failed_actions
from app.services.incidents import (
    get_incident,
    ingest_absence,
    ingest_delay,
    list_incidents,
)

router = APIRouter(prefix="/incidents", tags=["incidents"])


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
        incident=IncidentOut.model_validate(incident),
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
        severity=payload.severity,
        source=payload.source,
        require_human_approval=settings.require_human_approval,
    )

    response.status_code = 200 if is_duplicate else 201
    return IncidentCreatedResponse(
        incident=IncidentOut.model_validate(incident),
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
        items=[IncidentOut.model_validate(i) for i in items],
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
