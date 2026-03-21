from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> uuid.UUID:
    return uuid.uuid4()


# ── Enums ────────────────────────────────────────────────────────────────

class ShipmentStatus(str, enum.Enum):
    on_track = "on_track"
    at_risk = "at_risk"
    delayed = "delayed"
    delivered = "delivered"


class POStatus(str, enum.Enum):
    open = "open"
    amended = "amended"
    closed = "closed"
    cancelled = "cancelled"


class IncidentType(str, enum.Enum):
    shipment_delay = "shipment_delay"
    worker_absence = "worker_absence"


class IncidentStatus(str, enum.Enum):
    open = "open"
    in_progress = "in_progress"
    resolved = "resolved"
    escalated = "escalated"


class Severity(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class ActionType(str, enum.Enum):
    slack_notify = "slack_notify"
    call_production = "call_production"
    call_contractor = "call_contractor"
    update_po = "update_po"
    update_labor = "update_labor"
    email_customer = "email_customer"
    notify_manager = "notify_manager"
    escalate_ticket = "escalate_ticket"


class ActionStatus(str, enum.Enum):
    pending = "pending"
    queued = "queued"
    in_progress = "in_progress"
    completed = "completed"
    failed = "failed"
    needs_approval = "needs_approval"
    skipped = "skipped"


class ApprovalStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


# ── Models ───────────────────────────────────────────────────────────────

class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_phone: Mapped[str] = mapped_column(String(50), nullable=False)
    contact_email: Mapped[str] = mapped_column(String(255), nullable=False)
    region: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    shipments: Mapped[list[Shipment]] = relationship(back_populates="supplier")
    purchase_orders: Mapped[list[PurchaseOrder]] = relationship(back_populates="supplier")


class Shipment(Base):
    __tablename__ = "shipments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    po_number: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    status: Mapped[ShipmentStatus] = mapped_column(Enum(ShipmentStatus, name="shipment_status"), default=ShipmentStatus.on_track)
    original_eta: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_eta: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    customer_email: Mapped[str] = mapped_column(String(255), nullable=False)
    customer_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    supplier: Mapped[Supplier] = relationship(back_populates="shipments")
    incidents: Mapped[list[Incident]] = relationship(back_populates="shipment")


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    po_number: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    status: Mapped[POStatus] = mapped_column(Enum(POStatus, name="po_status"), default=POStatus.open)
    line_items: Mapped[dict | None] = mapped_column(JSON, default=None)
    version: Mapped[int] = mapped_column(Integer, default=1)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    supplier: Mapped[Supplier] = relationship(back_populates="purchase_orders")


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    type: Mapped[IncidentType] = mapped_column(Enum(IncidentType, name="incident_type"), nullable=False)
    status: Mapped[IncidentStatus] = mapped_column(Enum(IncidentStatus, name="incident_status"), default=IncidentStatus.open)
    severity: Mapped[Severity] = mapped_column(Enum(Severity, name="severity"), default=Severity.medium)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, default=None)
    shipment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shipments.id"), nullable=True)
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=_new_uuid)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    shipment: Mapped[Shipment | None] = relationship(back_populates="incidents")
    actions: Mapped[list[ActionRun]] = relationship(back_populates="incident", order_by="ActionRun.sequence")


class ActionRun(Base):
    __tablename__ = "action_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id"), nullable=False)
    action_type: Mapped[ActionType] = mapped_column(Enum(ActionType, name="action_type"), nullable=False)
    status: Mapped[ActionStatus] = mapped_column(Enum(ActionStatus, name="action_status"), default=ActionStatus.pending)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    request_payload: Mapped[dict | None] = mapped_column(JSON, default=None)
    response_payload: Mapped[dict | None] = mapped_column(JSON, default=None)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    incident: Mapped[Incident] = relationship(back_populates="actions")
    approval: Mapped[Approval | None] = relationship(back_populates="action_run", uselist=False)


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    action_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("action_runs.id"), unique=True, nullable=False)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id"), nullable=False)
    status: Mapped[ApprovalStatus] = mapped_column(Enum(ApprovalStatus, name="approval_status"), default=ApprovalStatus.pending)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    action_run: Mapped[ActionRun] = relationship(back_populates="approval")
    incident: Mapped[Incident] = relationship()


class VoiceSession(Base):
    __tablename__ = "voice_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    call_sid: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    stream_sid: Mapped[str | None] = mapped_column(String(100), nullable=True)
    incident_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("incidents.id"), nullable=True)
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=_new_uuid)
    direction: Mapped[str] = mapped_column(String(20), nullable=False, default="inbound")  # inbound | outbound
    from_number: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    to_number: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="connected")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON, default=None)

    incident: Mapped[Incident | None] = relationship()
    transcript_events: Mapped[list[TranscriptEvent]] = relationship(back_populates="voice_session", order_by="TranscriptEvent.created_at")


class TranscriptEvent(Base):
    __tablename__ = "transcript_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    voice_session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("voice_sessions.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # assistant | caller | system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    voice_session: Mapped[VoiceSession] = relationship(back_populates="transcript_events")
