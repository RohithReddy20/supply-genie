"""Tests for Workflow B: Worker Absence end-to-end."""
from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
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
)
from app.services.connectors.labor_system import LaborUpdateResult, update_labor_record
from app.services.connectors.manager_notify import ManagerNotifyResult, notify_site_manager
from app.services.action_executor import execute_pending_actions

from fastapi.testclient import TestClient


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


@pytest.fixture
def supplier(db):
    s = Supplier(
        name="Acme Parts Inc.",
        contact_phone="+15551234567",
        contact_email="contact@acme.com",
        region="US-West",
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@pytest.fixture
def absence_incident(db):
    incident = Incident(
        idempotency_key="test-absence-wfb-001",
        type=IncidentType.worker_absence,
        status=IncidentStatus.in_progress,
        severity=Severity.high,
        source="hr_webhook",
        payload={
            "worker_name": "Carlos Mendez",
            "site_id": "SITE-TX-03",
            "shift_date": "2026-03-22",
            "role": "Forklift Operator",
            "contractor_phone": "+15551230000",
            "reason": "Family emergency",
        },
    )
    db.add(incident)
    db.commit()
    db.refresh(incident)

    actions_data = [
        (ActionType.update_labor, ActionStatus.pending, 1),
        (ActionType.call_contractor, ActionStatus.pending, 2),
        (ActionType.notify_manager, ActionStatus.pending, 3),
    ]
    for atype, status, seq in actions_data:
        ar = ActionRun(
            incident_id=incident.id,
            action_type=atype,
            status=status,
            sequence=seq,
        )
        db.add(ar)

    db.commit()
    db.refresh(incident)
    return incident


# ── Labor connector unit tests ───────────────────────────────────────────

class TestLaborSystemConnector:
    def test_update_success(self):
        result = update_labor_record(
            site_id="SITE-TX-03",
            worker_name="Carlos Mendez",
            shift_date="2026-03-22",
            role="Forklift Operator",
            reason="Family emergency",
        )
        assert result.ok is True
        assert result.site_id == "SITE-TX-03"
        assert result.worker_name == "Carlos Mendez"
        assert result.status == "absent"
        assert result.coverage_needed is True
        assert result.error is None

    def test_update_missing_site_id(self):
        result = update_labor_record(
            site_id="",
            worker_name="Carlos Mendez",
            shift_date="2026-03-22",
            role="Forklift Operator",
        )
        assert result.ok is False
        assert result.error is not None

    def test_update_missing_worker_name(self):
        result = update_labor_record(
            site_id="SITE-TX-03",
            worker_name="",
            shift_date="2026-03-22",
            role="Forklift Operator",
        )
        assert result.ok is False


# ── Manager notify connector unit tests ──────────────────────────────────

class TestManagerNotifyConnector:
    @patch("app.services.connectors.manager_notify.slack_send")
    def test_notify_success(self, mock_slack):
        from app.services.connectors.slack import SlackResult
        mock_slack.return_value = SlackResult(ok=True, channel="#ops-alerts", ts="1234.5678")

        result = notify_site_manager(
            site_id="SITE-TX-03",
            worker_name="Carlos Mendez",
            shift_date="2026-03-22",
            role="Forklift Operator",
            reason="Family emergency",
        )
        assert result.ok is True
        assert result.channel == "#ops-alerts"
        assert result.ts == "1234.5678"
        mock_slack.assert_called_once()

        # Verify message formatting
        call_kwargs = mock_slack.call_args
        message = call_kwargs.kwargs.get("message") or call_kwargs[1].get("message") or call_kwargs[0][1]
        assert "SITE-TX-03" in message
        assert "Carlos Mendez" in message
        assert "Forklift Operator" in message

    @patch("app.services.connectors.manager_notify.slack_send")
    def test_notify_slack_failure(self, mock_slack):
        from app.services.connectors.slack import SlackResult
        mock_slack.return_value = SlackResult(ok=False, channel="#ops-alerts", error="channel_not_found")

        result = notify_site_manager(
            site_id="SITE-TX-03",
            worker_name="Carlos Mendez",
            shift_date="2026-03-22",
            role="Forklift Operator",
        )
        assert result.ok is False
        assert "channel_not_found" in result.error


# ── Action executor integration tests ────────────────────────────────────

class TestAbsenceExecutorIntegration:
    @patch("app.services.action_executor.notify_site_manager")
    @patch("app.services.action_executor.make_call")
    @patch("app.services.action_executor.update_labor_record")
    def test_all_three_actions_execute(
        self, mock_labor, mock_call, mock_notify, db, absence_incident
    ):
        """All 3 absence playbook actions should execute and complete."""
        mock_labor.return_value = LaborUpdateResult(
            ok=True, site_id="SITE-TX-03", worker_name="Carlos Mendez",
            shift_date="2026-03-22",
        )

        from app.services.connectors.twilio_voice import CallResult
        mock_call.return_value = CallResult(
            ok=True, call_sid="CA_TEST_123", to="+15551234567",
            from_="+15559876543", status="queued",
        )

        mock_notify.return_value = ManagerNotifyResult(
            ok=True, channel="#ops-alerts", ts="1234.5678",
        )

        executed = execute_pending_actions(db, absence_incident)
        assert len(executed) == 3

        for action in absence_incident.actions:
            assert action.status == ActionStatus.completed, (
                f"{action.action_type.value} should be completed, got {action.status.value}"
            )
            assert action.completed_at is not None
            assert action.response_payload is not None

    @patch("app.services.action_executor.notify_site_manager")
    @patch("app.services.action_executor.make_call")
    @patch("app.services.action_executor.update_labor_record")
    def test_labor_failure_marks_failed(
        self, mock_labor, mock_call, mock_notify, db, absence_incident
    ):
        """If labor update fails, the action should be marked failed."""
        mock_labor.return_value = LaborUpdateResult(
            ok=False, site_id="SITE-TX-03", worker_name="Carlos Mendez",
            shift_date="2026-03-22", error="System unavailable",
        )

        from app.services.connectors.twilio_voice import CallResult
        mock_call.return_value = CallResult(
            ok=True, call_sid="CA_TEST_123", to="+15551234567",
            from_="+15559876543", status="queued",
        )

        mock_notify.return_value = ManagerNotifyResult(
            ok=True, channel="#ops-alerts", ts="1234.5678",
        )

        executed = execute_pending_actions(db, absence_incident)

        labor_action = next(a for a in absence_incident.actions if a.action_type == ActionType.update_labor)
        assert labor_action.status == ActionStatus.failed
        assert "System unavailable" in labor_action.error_message

    @patch("app.services.action_executor.notify_site_manager")
    @patch("app.services.action_executor.make_call")
    @patch("app.services.action_executor.update_labor_record")
    def test_action_payloads_recorded(
        self, mock_labor, mock_call, mock_notify, db, absence_incident
    ):
        """Request and response payloads should be recorded on each action."""
        mock_labor.return_value = LaborUpdateResult(
            ok=True, site_id="SITE-TX-03", worker_name="Carlos Mendez",
            shift_date="2026-03-22",
        )

        from app.services.connectors.twilio_voice import CallResult
        mock_call.return_value = CallResult(
            ok=True, call_sid="CA_TEST_123", to="+15551234567",
            from_="+15559876543", status="queued",
        )

        mock_notify.return_value = ManagerNotifyResult(
            ok=True, channel="#ops-alerts", ts="1234.5678",
        )

        execute_pending_actions(db, absence_incident)

        labor_action = next(a for a in absence_incident.actions if a.action_type == ActionType.update_labor)
        assert labor_action.request_payload is not None
        assert labor_action.request_payload["site_id"] == "SITE-TX-03"
        assert labor_action.response_payload["ok"] is True

        mgr_action = next(a for a in absence_incident.actions if a.action_type == ActionType.notify_manager)
        assert mgr_action.request_payload is not None
        assert mgr_action.response_payload["channel"] == "#ops-alerts"


# ── API endpoint integration tests ───────────────────────────────────────

class TestAbsenceEndpoint:
    @patch("app.services.action_executor.notify_site_manager")
    @patch("app.services.action_executor.make_call")
    @patch("app.services.action_executor.update_labor_record")
    def test_create_absence_incident(
        self, mock_labor, mock_call, mock_notify, client, db, supplier
    ):
        """POST /api/v1/incidents/absence should create incident with 3 actions."""
        mock_labor.return_value = LaborUpdateResult(
            ok=True, site_id="SITE-TX-03", worker_name="Carlos Mendez",
            shift_date="2026-03-22",
        )

        from app.services.connectors.twilio_voice import CallResult
        mock_call.return_value = CallResult(
            ok=True, call_sid="CA_TEST_123", to="+15551234567",
            from_="+15559876543", status="queued",
        )

        mock_notify.return_value = ManagerNotifyResult(
            ok=True, channel="#ops-alerts", ts="1234.5678",
        )

        r = client.post(
            "/api/v1/incidents/absence",
            headers={"Idempotency-Key": "test-api-absence-001"},
            json={
                "worker_name": "Carlos Mendez",
                "site_id": "SITE-TX-03",
                "shift_date": "2026-03-22",
                "role": "Forklift Operator",
                "reason": "Family emergency",
                "severity": "high",
                "source": "hr_webhook",
            },
        )
        assert r.status_code == 201
        data = r.json()
        assert data["incident"]["type"] == "worker_absence"
        assert data["is_duplicate"] is False

    @patch("app.services.action_executor.notify_site_manager")
    @patch("app.services.action_executor.make_call")
    @patch("app.services.action_executor.update_labor_record")
    def test_absence_idempotency(
        self, mock_labor, mock_call, mock_notify, client, db, supplier
    ):
        """Duplicate Idempotency-Key returns 200 with is_duplicate=True."""
        mock_labor.return_value = LaborUpdateResult(
            ok=True, site_id="SITE-TX-03", worker_name="Carlos Mendez",
            shift_date="2026-03-22",
        )

        from app.services.connectors.twilio_voice import CallResult
        mock_call.return_value = CallResult(
            ok=True, call_sid="CA_TEST_123", to="+15551234567",
            from_="+15559876543", status="queued",
        )

        mock_notify.return_value = ManagerNotifyResult(
            ok=True, channel="#ops-alerts", ts="1234.5678",
        )

        payload = {
            "worker_name": "Carlos Mendez",
            "site_id": "SITE-TX-03",
            "shift_date": "2026-03-22",
            "role": "Forklift Operator",
            "reason": "Family emergency",
            "severity": "high",
            "source": "hr_webhook",
        }
        key = "test-api-absence-dedup-001"

        r1 = client.post("/api/v1/incidents/absence", headers={"Idempotency-Key": key}, json=payload)
        assert r1.status_code == 201

        r2 = client.post("/api/v1/incidents/absence", headers={"Idempotency-Key": key}, json=payload)
        assert r2.status_code == 200
        assert r2.json()["is_duplicate"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
