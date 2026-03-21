from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ActionRun,
    ActionStatus,
    ActionType,
    Approval,
    ApprovalStatus,
    Incident,
    IncidentStatus,
    IncidentType,
    Severity,
)
from app.observability import record_incident_created
from app.services.safety import check_human_approval_required

# ── Playbook Definitions ────────────────────────────────────────────────

DELAY_PLAYBOOK: list[tuple[ActionType, bool]] = [
    (ActionType.slack_notify, False),
    (ActionType.call_production, False),
    (ActionType.update_po, False),
    (ActionType.email_customer, True),  # requires approval
]

ABSENCE_PLAYBOOK: list[tuple[ActionType, bool]] = [
    (ActionType.update_labor, False),
    (ActionType.call_contractor, False),
    (ActionType.notify_manager, False),
]


def _get_playbook(incident_type: IncidentType) -> list[tuple[ActionType, bool]]:
    if incident_type == IncidentType.shipment_delay:
        return DELAY_PLAYBOOK
    return ABSENCE_PLAYBOOK


# ── Idempotent Ingestion ────────────────────────────────────────────────

def find_by_idempotency_key(db: Session, key: str) -> Incident | None:
    return db.scalars(
        select(Incident).where(Incident.idempotency_key == key)
    ).first()


def ingest_delay(
    db: Session,
    idempotency_key: str,
    correlation_id: UUID,
    po_number: str,
    supplier_id: UUID,
    delay_reason: str,
    new_eta: str,
    severity: Severity,
    source: str,
    require_human_approval: bool,
) -> tuple[Incident, bool]:
    existing = find_by_idempotency_key(db, idempotency_key)
    if existing:
        return existing, True

    incident = Incident(
        idempotency_key=idempotency_key,
        type=IncidentType.shipment_delay,
        status=IncidentStatus.in_progress,
        severity=severity,
        source=source,
        correlation_id=correlation_id,
        payload={
            "po_number": po_number,
            "supplier_id": str(supplier_id),
            "delay_reason": delay_reason,
            "new_eta": new_eta,
        },
    )
    db.add(incident)
    db.flush()

    _create_action_runs(
        db,
        incident=incident,
        require_human_approval=require_human_approval,
    )

    db.commit()
    db.refresh(incident)
    record_incident_created("shipment_delay")

    from app.services.action_executor import execute_pending_actions
    execute_pending_actions(db, incident)
    db.refresh(incident)

    return incident, False


def ingest_absence(
    db: Session,
    idempotency_key: str,
    correlation_id: UUID,
    worker_name: str,
    site_id: str,
    shift_date: str,
    role: str,
    reason: str,
    severity: Severity,
    source: str,
    require_human_approval: bool,
) -> tuple[Incident, bool]:
    existing = find_by_idempotency_key(db, idempotency_key)
    if existing:
        return existing, True

    incident = Incident(
        idempotency_key=idempotency_key,
        type=IncidentType.worker_absence,
        status=IncidentStatus.in_progress,
        severity=severity,
        source=source,
        correlation_id=correlation_id,
        payload={
            "worker_name": worker_name,
            "site_id": site_id,
            "shift_date": shift_date,
            "role": role,
            "reason": reason,
        },
    )
    db.add(incident)
    db.flush()

    _create_action_runs(
        db,
        incident=incident,
        require_human_approval=require_human_approval,
    )

    db.commit()
    db.refresh(incident)
    record_incident_created("worker_absence")

    from app.services.action_executor import execute_pending_actions
    execute_pending_actions(db, incident)
    db.refresh(incident)

    return incident, False


# ── Internal Helpers ────────────────────────────────────────────────────

def _create_action_runs(
    db: Session,
    incident: Incident,
    require_human_approval: bool,
) -> None:
    playbook = _get_playbook(incident.type)

    for seq, (action_type, is_customer_facing) in enumerate(playbook, start=1):
        decision = check_human_approval_required(
            require_human_approval=require_human_approval,
            is_customer_facing=is_customer_facing,
            approved_by_human=False,
        )

        status = ActionStatus.needs_approval if not decision.allowed else ActionStatus.pending

        action_run = ActionRun(
            incident_id=incident.id,
            action_type=action_type,
            status=status,
            sequence=seq,
        )
        db.add(action_run)
        db.flush()

        if not decision.allowed:
            approval = Approval(
                action_run_id=action_run.id,
                incident_id=incident.id,
                status=ApprovalStatus.pending,
            )
            db.add(approval)


# ── Query Helpers ───────────────────────────────────────────────────────

def get_incident(db: Session, incident_id: UUID) -> Incident | None:
    return db.get(Incident, incident_id)


def list_incidents(
    db: Session,
    *,
    status: IncidentStatus | None = None,
    incident_type: IncidentType | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Incident], int]:
    query = select(Incident).order_by(Incident.created_at.desc())
    count_query = select(Incident)

    if status:
        query = query.where(Incident.status == status)
        count_query = count_query.where(Incident.status == status)
    if incident_type:
        query = query.where(Incident.type == incident_type)
        count_query = count_query.where(Incident.type == incident_type)

    total = len(db.scalars(count_query).all())
    items = list(db.scalars(query.limit(limit).offset(offset)).all())
    return items, total
