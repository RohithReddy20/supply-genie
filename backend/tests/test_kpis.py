"""Tests for KPI service and endpoint."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    ActionRun,
    ActionStatus,
    ActionType,
    Incident,
    IncidentStatus,
    IncidentType,
    Severity,
    Supplier,
    VoiceSession,
)
from app.services.kpi import compute_kpis


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db):
    def _get_test_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _get_test_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _make_incident(db, *, key: str, itype: IncidentType, status: IncidentStatus, resolved_at=None):
    now = datetime.now(timezone.utc)
    inc = Incident(
        idempotency_key=key,
        type=itype,
        status=status,
        severity=Severity.medium,
        source="test",
        payload={},
        created_at=now - timedelta(hours=1),
        resolved_at=resolved_at,
    )
    db.add(inc)
    db.flush()
    return inc


def _make_action(db, incident, *, seq: int, atype: ActionType, status: ActionStatus, duration_s=None):
    now = datetime.now(timezone.utc)
    started = now - timedelta(seconds=5) if duration_s is not None else None
    completed = started + timedelta(seconds=duration_s) if duration_s is not None else None
    ar = ActionRun(
        incident_id=incident.id,
        action_type=atype,
        status=status,
        sequence=seq,
        started_at=started,
        completed_at=completed if status == ActionStatus.completed else None,
    )
    db.add(ar)
    db.flush()
    return ar


# ── Tests ─────────────────────────────────────────────────────────────────


class TestKPIServiceEmpty:
    def test_empty_db_returns_zeroes(self, db):
        kpis = compute_kpis(db)
        assert kpis["incidents"]["total"] == 0
        assert kpis["actions"]["total"] == 0
        assert kpis["voice"]["total_sessions"] == 0
        assert kpis["incidents"]["auto_resolution_rate"] == 0.0

    def test_generated_at_present(self, db):
        kpis = compute_kpis(db)
        assert "generated_at" in kpis


class TestIncidentKPIs:
    def test_counts_by_status(self, db):
        _make_incident(db, key="a", itype=IncidentType.shipment_delay, status=IncidentStatus.in_progress)
        _make_incident(db, key="b", itype=IncidentType.shipment_delay, status=IncidentStatus.resolved,
                       resolved_at=datetime.now(timezone.utc))
        _make_incident(db, key="c", itype=IncidentType.worker_absence, status=IncidentStatus.escalated)
        db.commit()

        kpis = compute_kpis(db)
        inc = kpis["incidents"]
        assert inc["total"] == 3
        assert inc["by_status"]["in_progress"] == 1
        assert inc["by_status"]["resolved"] == 1
        assert inc["by_status"]["escalated"] == 1

    def test_auto_resolution_rate(self, db):
        _make_incident(db, key="r1", itype=IncidentType.shipment_delay, status=IncidentStatus.resolved,
                       resolved_at=datetime.now(timezone.utc))
        _make_incident(db, key="r2", itype=IncidentType.shipment_delay, status=IncidentStatus.in_progress)
        db.commit()

        kpis = compute_kpis(db)
        assert kpis["incidents"]["auto_resolution_rate"] == 0.5

    def test_escalation_rate(self, db):
        _make_incident(db, key="e1", itype=IncidentType.shipment_delay, status=IncidentStatus.escalated)
        _make_incident(db, key="e2", itype=IncidentType.shipment_delay, status=IncidentStatus.resolved,
                       resolved_at=datetime.now(timezone.utc))
        _make_incident(db, key="e3", itype=IncidentType.worker_absence, status=IncidentStatus.in_progress)
        db.commit()

        kpis = compute_kpis(db)
        assert abs(kpis["incidents"]["escalation_rate"] - 1 / 3) < 0.01

    def test_by_type(self, db):
        _make_incident(db, key="t1", itype=IncidentType.shipment_delay, status=IncidentStatus.open)
        _make_incident(db, key="t2", itype=IncidentType.worker_absence, status=IncidentStatus.open)
        _make_incident(db, key="t3", itype=IncidentType.worker_absence, status=IncidentStatus.open)
        db.commit()

        kpis = compute_kpis(db)
        assert kpis["incidents"]["by_type"]["shipment_delay"] == 1
        assert kpis["incidents"]["by_type"]["worker_absence"] == 2


class TestActionKPIs:
    def test_success_and_failure_rates(self, db):
        inc = _make_incident(db, key="a1", itype=IncidentType.shipment_delay, status=IncidentStatus.in_progress)
        _make_action(db, inc, seq=1, atype=ActionType.slack_notify, status=ActionStatus.completed, duration_s=0.5)
        _make_action(db, inc, seq=2, atype=ActionType.call_production, status=ActionStatus.completed, duration_s=1.0)
        _make_action(db, inc, seq=3, atype=ActionType.update_po, status=ActionStatus.failed)
        db.commit()

        kpis = compute_kpis(db)
        act = kpis["actions"]
        assert act["total"] == 3
        assert act["completed"] == 2
        assert act["failed"] == 1
        assert abs(act["success_rate"] - 2 / 3) < 0.01
        assert abs(act["failure_rate"] - 1 / 3) < 0.01

    def test_action_type_breakdown(self, db):
        inc = _make_incident(db, key="b1", itype=IncidentType.shipment_delay, status=IncidentStatus.in_progress)
        _make_action(db, inc, seq=1, atype=ActionType.slack_notify, status=ActionStatus.completed, duration_s=0.4)
        _make_action(db, inc, seq=2, atype=ActionType.slack_notify, status=ActionStatus.completed, duration_s=0.6)
        _make_action(db, inc, seq=3, atype=ActionType.call_production, status=ActionStatus.failed)
        db.commit()

        kpis = compute_kpis(db)
        breakdown = {b["action_type"]: b for b in kpis["action_breakdown"]}
        assert breakdown["slack_notify"]["completed"] == 2
        assert breakdown["slack_notify"]["success_rate"] == 1.0
        assert breakdown["call_production"]["failed"] == 1
        assert breakdown["call_production"]["success_rate"] == 0.0

    def test_pending_and_approval_counts(self, db):
        inc = _make_incident(db, key="c1", itype=IncidentType.shipment_delay, status=IncidentStatus.in_progress)
        _make_action(db, inc, seq=1, atype=ActionType.slack_notify, status=ActionStatus.pending)
        _make_action(db, inc, seq=2, atype=ActionType.email_customer, status=ActionStatus.needs_approval)
        db.commit()

        kpis = compute_kpis(db)
        assert kpis["actions"]["pending"] == 1
        assert kpis["actions"]["needs_approval"] == 1


class TestVoiceKPIs:
    def test_voice_sessions(self, db):
        for i, status in enumerate(["completed", "completed", "connected", "mock"]):
            vs = VoiceSession(
                call_sid=f"CA{i:032d}",
                status=status,
                direction="outbound",
            )
            db.add(vs)
        db.commit()

        kpis = compute_kpis(db)
        v = kpis["voice"]
        assert v["total_sessions"] == 4
        assert v["completed_sessions"] == 3  # completed + mock
        assert v["answer_rate"] == 0.75


class TestKPIEndpoint:
    def test_get_kpis_returns_200(self, client):
        resp = client.get("/api/v1/kpis")
        assert resp.status_code == 200
        data = resp.json()
        assert "incidents" in data
        assert "actions" in data
        assert "action_breakdown" in data
        assert "voice" in data
        assert "generated_at" in data

    def test_kpis_with_data(self, client, db):
        _make_incident(db, key="api1", itype=IncidentType.shipment_delay, status=IncidentStatus.resolved,
                       resolved_at=datetime.now(timezone.utc))
        db.commit()

        resp = client.get("/api/v1/kpis")
        assert resp.status_code == 200
        data = resp.json()
        assert data["incidents"]["total"] == 1
        assert data["incidents"]["auto_resolution_rate"] == 1.0
