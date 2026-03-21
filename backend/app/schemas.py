from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, Field

from app.models import (
    ActionStatus,
    ActionType,
    ApprovalStatus,
    IncidentStatus,
    IncidentType,
    Severity,
)


# ── Incident Ingestion ──────────────────────────────────────────────────

class DelayEventIn(BaseModel):
    po_number: str = Field(min_length=2)
    supplier_id: UUID
    delay_reason: str = Field(min_length=3)
    new_eta: str = Field(min_length=10, description="ISO date, e.g. 2026-04-15")
    severity: Severity = Severity.medium
    source: str = Field(default="tms_webhook", min_length=2)


class AbsenceEventIn(BaseModel):
    worker_name: str = Field(min_length=2)
    site_id: str = Field(min_length=2)
    shift_date: str = Field(min_length=10)
    role: str = Field(min_length=2)
    reason: str = Field(min_length=2)
    severity: Severity = Severity.medium
    source: str = Field(default="hr_webhook", min_length=2)


class IncidentOut(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    idempotency_key: str
    type: IncidentType
    status: IncidentStatus
    severity: Severity
    correlation_id: UUID
    created_at: datetime


class IncidentCreatedResponse(BaseModel):
    incident: IncidentOut
    is_duplicate: bool


# ── Action / Approval Detail ────────────────────────────────────────────

class ApprovalOut(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    status: ApprovalStatus
    requested_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None
    reason: str | None = None


class ActionRunOut(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    action_type: ActionType
    status: ActionStatus
    sequence: int
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    retry_count: int = 0
    approval: ApprovalOut | None = None


class IncidentDetailOut(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    idempotency_key: str
    type: IncidentType
    status: IncidentStatus
    severity: Severity
    source: str
    payload: dict | None = None
    correlation_id: UUID
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    actions: list[ActionRunOut] = []


class IncidentListOut(BaseModel):
    items: list[IncidentOut]
    total: int
    limit: int
    offset: int


# ── Legacy (kept for existing orchestration router) ─────────────────────

class ActionResult(BaseModel):
    action: str
    status: str
    detail: str


class DelayWorkflowResponse(BaseModel):
    workflow_id: str
    correlation_id: str
    actions: list[ActionResult]


# ── Voice / Transcript ──────────────────────────────────────────────────

class VoiceSessionOut(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    call_sid: str
    stream_sid: str | None = None
    incident_id: UUID | None = None
    correlation_id: UUID
    direction: str
    from_number: str
    to_number: str
    status: str
    started_at: datetime
    ended_at: datetime | None = None
    duration_seconds: int | None = None


class TranscriptEventOut(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    role: str
    content: str
    created_at: datetime


class OutboundCallRequest(BaseModel):
    to: str = Field(min_length=5)
    incident_id: UUID | None = None
    greeting: str = Field(default="")
