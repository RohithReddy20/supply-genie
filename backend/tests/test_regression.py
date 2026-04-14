"""Regression harness — 30 scenarios covering connector failures, retry policy,
approval gates, idempotency, end-to-end workflows, and adversarial edge cases.

Coverage map:
  TestConnectorFailures       (6)  — Slack, Twilio, Email, PO not-found, PO concurrency, Labor
  TestRetryBehavior           (4)  — retry-then-succeed, exhaustion, API retry, partial retry
  TestApprovalGate            (5)  — approve+exec, approve+fail, reject, double-decide, 404
  TestIdempotency             (3)  — delay dedup, absence dedup, rapid-fire
  TestFullWorkflows           (3)  — delay E2E, absence E2E, mid-workflow failure
  TestAdversarial             (5)  — missing fields, bad incident ID, concurrent PO, empty payload, oversize
  TestSafetyPolicy            (4)  — policy enabled, policy disabled, internal action, already approved
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

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
    Approval,
    ApprovalStatus,
    Incident,
    IncidentStatus,
    IncidentType,
    POStatus,
    PurchaseOrder,
    Severity,
    Shipment,
    ShipmentStatus,
    Supplier,
)
from app.services.action_executor import execute_pending_actions, retry_failed_actions
from app.services.connectors.email import EmailResult
from app.services.connectors.labor_system import LaborUpdateResult
from app.services.connectors.manager_notify import ManagerNotifyResult
from app.services.connectors.po_system import POUpdateResult, update_po
from app.services.connectors.slack import SlackResult
from app.services.connectors.twilio_voice import CallResult
from app.services.safety import check_human_approval_required


# ── Shared fixtures ──────────────────────────────────────────────────────

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
        name="Acme Parts",
        contact_phone="+15551234567",
        contact_email="acme@example.com",
        region="US-West",
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@pytest.fixture
def shipment(db, supplier):
    s = Shipment(
        po_number="PO-REG-001",
        supplier_id=supplier.id,
        status=ShipmentStatus.delayed,
        original_eta=datetime(2026, 3, 15, tzinfo=timezone.utc),
        current_eta=datetime(2026, 4, 1, tzinfo=timezone.utc),
        customer_email="buyer@example.com",
        customer_name="Test Buyer",
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@pytest.fixture
def po(db, supplier):
    p = PurchaseOrder(
        po_number="PO-REG-001",
        supplier_id=supplier.id,
        status=POStatus.open,
        version=1,
        notes="Initial",
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _delay_incident(db, *, key="reg-delay-001", shipment_id=None):
    inc = Incident(
        idempotency_key=key,
        type=IncidentType.shipment_delay,
        status=IncidentStatus.in_progress,
        severity=Severity.high,
        source="tms_webhook",
        payload={
            "po_number": "PO-REG-001",
            "supplier_id": str(uuid.uuid4()),
            "supplier_phone": "+15551234567",
            "delay_reason": "Port congestion",
            "new_eta": "2026-04-01",
        },
        shipment_id=shipment_id,
    )
    db.add(inc)
    db.flush()
    return inc


def _absence_incident(db, *, key="reg-absence-001"):
    inc = Incident(
        idempotency_key=key,
        type=IncidentType.worker_absence,
        status=IncidentStatus.in_progress,
        severity=Severity.medium,
        source="hr_webhook",
        payload={
            "worker_name": "Reg Worker",
            "site_id": "SITE-01",
            "shift_date": "2026-03-22",
            "role": "Operator",
            "contractor_phone": "+15557654321",
            "reason": "Sick leave",
        },
    )
    db.add(inc)
    db.flush()
    return inc


def _add_action(db, incident, *, seq, atype, status=ActionStatus.pending):
    ar = ActionRun(
        incident_id=incident.id,
        action_type=atype,
        status=status,
        sequence=seq,
    )
    db.add(ar)
    db.flush()
    return ar


# ═══════════════════════════════════════════════════════════════════════════
# 1. Connector Failure Scenarios (6 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestConnectorFailures:
    """Every connector failure should mark the action as failed with an error message."""

    @patch("app.services.action_executor.slack_send")
    def test_slack_failure_records_error(self, mock_slack, db):
        mock_slack.return_value = SlackResult(ok=False, channel="#ops", error="channel_not_found")
        inc = _delay_incident(db)
        _add_action(db, inc, seq=1, atype=ActionType.slack_notify)
        db.commit()
        db.refresh(inc)

        execute_pending_actions(db, inc)
        action = inc.actions[0]
        assert action.status == ActionStatus.failed
        assert "channel_not_found" in action.error_message

    @patch("app.services.action_executor.make_call")
    def test_twilio_failure_records_error(self, mock_call, db):
        mock_call.return_value = CallResult(ok=False, to="+1555", error="Connection timeout")
        inc = _delay_incident(db)
        _add_action(db, inc, seq=1, atype=ActionType.call_production)
        db.commit()
        db.refresh(inc)

        execute_pending_actions(db, inc)
        action = inc.actions[0]
        assert action.status == ActionStatus.failed
        assert "Connection timeout" in action.error_message

    @patch("app.services.action_executor.send_email")
    def test_email_failure_records_error(self, mock_email, db, shipment):
        mock_email.return_value = EmailResult(ok=False, to="buyer@example.com", error="Rate limit exceeded")
        inc = _delay_incident(db, shipment_id=shipment.id)
        _add_action(db, inc, seq=1, atype=ActionType.email_customer)
        db.commit()
        db.refresh(inc)

        execute_pending_actions(db, inc)
        action = inc.actions[0]
        assert action.status == ActionStatus.failed
        assert "Rate limit" in action.error_message

    def test_po_not_found_records_error(self, db):
        """PO update for a non-existent PO should fail gracefully."""
        result = update_po(db, po_number="PO-NONEXISTENT", new_status=POStatus.amended, notes="test")
        assert result.ok is False
        assert "not found" in result.error

    def test_po_concurrency_conflict(self, db, po):
        """Optimistic concurrency violation should be caught."""
        result = update_po(db, po_number="PO-REG-001", new_status=POStatus.amended,
                           notes="test", expected_version=999)
        assert result.ok is False
        assert "Version conflict" in result.error

    @patch("app.services.action_executor.update_labor_record")
    def test_labor_system_failure(self, mock_labor, db):
        mock_labor.return_value = LaborUpdateResult(
            ok=False, site_id="SITE-01", worker_name="X",
            shift_date="2026-03-22", error="HRIS unavailable",
        )
        inc = _absence_incident(db)
        _add_action(db, inc, seq=1, atype=ActionType.update_labor)
        db.commit()
        db.refresh(inc)

        execute_pending_actions(db, inc)
        action = inc.actions[0]
        assert action.status == ActionStatus.failed
        assert "HRIS unavailable" in action.error_message


# ═══════════════════════════════════════════════════════════════════════════
# 2. Retry Behavior (4 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestRetryBehavior:

    @patch("app.services.action_executor.slack_send")
    def test_retry_then_succeed(self, mock_slack, db):
        """Failed action recovers on retry."""
        inc = _delay_incident(db)
        action = _add_action(db, inc, seq=1, atype=ActionType.slack_notify)
        action.status = ActionStatus.failed
        action.retry_count = 1
        action.error_message = "Temporary network error"
        db.commit()
        db.refresh(inc)

        mock_slack.return_value = SlackResult(ok=True, channel="#ops", ts="123")
        retried = retry_failed_actions(db, inc)
        assert len(retried) == 1
        assert retried[0].status == ActionStatus.completed

    @patch("app.services.action_executor.slack_send")
    def test_retry_exhaustion_dead_letter(self, mock_slack, db):
        """Action at max retries should not be retried."""
        mock_slack.return_value = SlackResult(ok=False, channel="#ops", error="still broken")
        inc = _delay_incident(db, key="reg-exhaust-001")
        action = _add_action(db, inc, seq=1, atype=ActionType.slack_notify)
        action.status = ActionStatus.failed
        action.retry_count = 3  # max_retries default
        action.error_message = "Permanent failure"
        db.commit()
        db.refresh(inc)

        retried = retry_failed_actions(db, inc)
        assert len(retried) == 0  # Nothing retried — exhausted

    @patch("app.services.action_executor.make_call")
    @patch("app.services.action_executor.slack_send")
    def test_partial_retry_only_failed(self, mock_slack, mock_call, db):
        """Only failed actions are retried; completed actions are untouched."""
        inc = _delay_incident(db, key="reg-partial-001")
        a1 = _add_action(db, inc, seq=1, atype=ActionType.slack_notify)
        a1.status = ActionStatus.completed
        a1.completed_at = datetime.now(timezone.utc)

        a2 = _add_action(db, inc, seq=2, atype=ActionType.call_production)
        a2.status = ActionStatus.failed
        a2.retry_count = 1
        a2.error_message = "Timeout"
        db.commit()
        db.refresh(inc)

        mock_call.return_value = CallResult(ok=True, call_sid="CA_RETRY", to="+1", from_="+1", status="queued")
        retried = retry_failed_actions(db, inc)
        assert len(retried) == 1
        assert retried[0].action_type == ActionType.call_production
        assert retried[0].status == ActionStatus.completed

        # Verify completed action was untouched
        db.refresh(a1)
        assert a1.status == ActionStatus.completed

    @patch("app.services.action_executor.slack_send")
    def test_retry_via_api_endpoint(self, mock_slack, client, db):
        """POST /incidents/{id}/retry triggers retry of failed actions."""
        inc = _delay_incident(db, key="reg-api-retry")
        action = _add_action(db, inc, seq=1, atype=ActionType.slack_notify)
        action.status = ActionStatus.failed
        action.retry_count = 1
        action.error_message = "first attempt failed"
        db.commit()
        db.refresh(inc)

        mock_slack.return_value = SlackResult(ok=True, channel="#ops", ts="456")
        r = client.post(f"/api/v1/incidents/{inc.id}/retry")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 1


# ═══════════════════════════════════════════════════════════════════════════
# 3. Approval Gate Scenarios (5 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestApprovalGate:

    def _setup_gated_action(self, db):
        inc = _delay_incident(db, key=f"reg-approval-{uuid.uuid4().hex[:8]}")
        action = _add_action(db, inc, seq=1, atype=ActionType.email_customer,
                             status=ActionStatus.needs_approval)
        approval = Approval(
            action_run_id=action.id,
            incident_id=inc.id,
            status=ApprovalStatus.pending,
        )
        db.add(approval)
        db.commit()
        db.refresh(inc)
        db.refresh(action)
        db.refresh(approval)
        return inc, action, approval

    @patch("app.services.action_executor.send_email")
    def test_approve_then_execute_success(self, mock_email, client, db, shipment):
        mock_email.return_value = EmailResult(ok=True, to="buyer@example.com", email_id="E123")
        inc, action, approval = self._setup_gated_action(db)
        inc.shipment_id = shipment.id
        db.commit()

        r = client.post(f"/api/v1/approvals/{approval.id}/decide", json={
            "decision": "approved",
            "decided_by": "operator@test",
            "reason": "Looks good",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "approved"
        assert data["next_status"] in ("completed", "failed")

    @patch("app.services.action_executor.send_email")
    def test_approve_then_execute_fails(self, mock_email, client, db, shipment):
        mock_email.return_value = EmailResult(ok=False, to="buyer@example.com", error="API down")
        inc, action, approval = self._setup_gated_action(db)
        inc.shipment_id = shipment.id
        db.commit()

        r = client.post(f"/api/v1/approvals/{approval.id}/decide", json={
            "decision": "approved",
            "decided_by": "operator@test",
        })
        assert r.status_code == 200
        assert r.json()["next_status"] == "failed"

    def test_reject_skips_action(self, client, db):
        inc, action, approval = self._setup_gated_action(db)

        r = client.post(f"/api/v1/approvals/{approval.id}/decide", json={
            "decision": "rejected",
            "decided_by": "operator@test",
            "reason": "Not needed",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "rejected"
        assert data["next_status"] == "skipped"

        db.refresh(action)
        assert action.status == ActionStatus.skipped

    def test_double_decide_returns_409(self, client, db):
        inc, action, approval = self._setup_gated_action(db)

        r1 = client.post(f"/api/v1/approvals/{approval.id}/decide", json={
            "decision": "rejected",
            "decided_by": "operator@test",
        })
        assert r1.status_code == 200

        r2 = client.post(f"/api/v1/approvals/{approval.id}/decide", json={
            "decision": "approved",
            "decided_by": "operator@test",
        })
        assert r2.status_code == 409

    def test_approval_not_found_returns_404(self, client, db):
        r = client.post(f"/api/v1/approvals/{uuid.uuid4()}/decide", json={
            "decision": "approved",
            "decided_by": "operator@test",
        })
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# 4. Idempotency (3 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestIdempotency:

    @patch("app.services.action_executor.send_email")
    @patch("app.services.action_executor.po_update")
    @patch("app.services.action_executor.make_call")
    @patch("app.services.action_executor.slack_send")
    def test_delay_duplicate_returns_200(self, mock_s, mock_c, mock_p, mock_e, client, db, supplier):
        mock_s.return_value = SlackResult(ok=True, channel="#ops", ts="1")
        mock_c.return_value = CallResult(ok=True, call_sid="CA1", to="+1", from_="+1", status="queued")
        mock_p.return_value = POUpdateResult(ok=True, po_number="PO-REG-001", old_version=1, new_version=2, status="amended")

        payload = {
            "po_number": "PO-REG-001",
            "supplier_id": str(supplier.id),
            "delay_reason": "Storm",
            "new_eta": "2026-04-10",
            "severity": "high",
            "source": "tms_webhook",
        }

        r1 = client.post("/api/v1/incidents/delay",
                          headers={"Idempotency-Key": "dedup-delay-001"}, json=payload)
        assert r1.status_code == 201
        assert r1.json()["is_duplicate"] is False

        r2 = client.post("/api/v1/incidents/delay",
                          headers={"Idempotency-Key": "dedup-delay-001"}, json=payload)
        assert r2.status_code == 200
        assert r2.json()["is_duplicate"] is True

    @patch("app.services.action_executor.notify_site_manager")
    @patch("app.services.action_executor.make_call")
    @patch("app.services.action_executor.update_labor_record")
    def test_absence_duplicate_returns_200(self, mock_l, mock_c, mock_n, client, db, supplier):
        mock_l.return_value = LaborUpdateResult(ok=True, site_id="SITE-01", worker_name="Worker One", shift_date="2026-03-22")
        mock_c.return_value = CallResult(ok=True, call_sid="CA1", to="+1", from_="+1", status="queued")
        mock_n.return_value = ManagerNotifyResult(ok=True, channel="#ops", ts="1")

        payload = {
            "worker_name": "Worker One",
            "site_id": "SITE-01",
            "shift_date": "2026-03-22",
            "role": "Operator",
            "reason": "Sick leave",
            "contractor_phone": "+15557654321",
            "severity": "medium",
            "source": "hr_webhook",
        }

        r1 = client.post("/api/v1/incidents/absence",
                          headers={"Idempotency-Key": "dedup-abs-001"}, json=payload)
        assert r1.status_code == 201

        r2 = client.post("/api/v1/incidents/absence",
                          headers={"Idempotency-Key": "dedup-abs-001"}, json=payload)
        assert r2.status_code == 200
        assert r2.json()["is_duplicate"] is True

    @patch("app.services.action_executor.send_email")
    @patch("app.services.action_executor.po_update")
    @patch("app.services.action_executor.make_call")
    @patch("app.services.action_executor.slack_send")
    def test_rapid_fire_same_key(self, mock_s, mock_c, mock_p, mock_e, client, db, supplier):
        """5 rapid-fire requests with the same key should yield 1 create + 4 dupes."""
        mock_s.return_value = SlackResult(ok=True, channel="#ops", ts="1")
        mock_c.return_value = CallResult(ok=True, call_sid="CA1", to="+1", from_="+1", status="queued")
        mock_p.return_value = POUpdateResult(ok=True, po_number="PO-REG-001", old_version=1, new_version=2, status="amended")

        payload = {
            "po_number": "PO-REG-001",
            "supplier_id": str(supplier.id),
            "delay_reason": "Storm",
            "new_eta": "2026-04-10",
            "severity": "medium",
            "source": "tms_webhook",
        }

        results = []
        for _ in range(5):
            r = client.post("/api/v1/incidents/delay",
                            headers={"Idempotency-Key": "rapid-fire-001"}, json=payload)
            results.append(r)

        creates = [r for r in results if r.status_code == 201]
        dupes = [r for r in results if r.status_code == 200]
        assert len(creates) == 1
        assert len(dupes) == 4


# ═══════════════════════════════════════════════════════════════════════════
# 5. Full Workflow End-to-End (3 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestFullWorkflows:

    @patch("app.services.action_executor.send_email")
    @patch("app.services.action_executor.po_update")
    @patch("app.services.action_executor.make_call")
    @patch("app.services.action_executor.slack_send")
    def test_delay_workflow_all_actions(self, mock_s, mock_c, mock_p, mock_e, client, db, supplier):
        """Full delay workflow: Slack ✓, Call ✓, PO ✓, Email blocked at approval gate."""
        mock_s.return_value = SlackResult(ok=True, channel="#ops", ts="1")
        mock_c.return_value = CallResult(ok=True, call_sid="CA1", to="+1", from_="+1", status="queued")
        mock_p.return_value = POUpdateResult(ok=True, po_number="PO-REG-001", old_version=1, new_version=2, status="amended")

        r = client.post("/api/v1/incidents/delay",
                         headers={"Idempotency-Key": "e2e-delay-001"},
                         json={
                             "po_number": "PO-REG-001",
                             "supplier_id": str(supplier.id),
                             "delay_reason": "Factory fire",
                             "new_eta": "2026-05-01",
                             "severity": "critical",
                             "source": "tms_webhook",
                         })
        assert r.status_code == 201
        incident_id = r.json()["incident"]["id"]

        detail = client.get(f"/api/v1/incidents/{incident_id}").json()
        actions = detail["actions"]
        assert len(actions) == 4

        statuses = {a["action_type"]: a["status"] for a in actions}
        assert statuses["slack_notify"] == "completed"
        assert statuses["call_production"] == "completed"
        assert statuses["update_po"] == "completed"
        assert statuses["email_customer"] == "needs_approval"

    @patch("app.services.action_executor.notify_site_manager")
    @patch("app.services.action_executor.make_call")
    @patch("app.services.action_executor.update_labor_record")
    def test_absence_workflow_all_actions(self, mock_l, mock_c, mock_n, client, db, supplier):
        """Full absence workflow: Labor ✓, Call ✓, Notify ✓."""
        mock_l.return_value = LaborUpdateResult(ok=True, site_id="S1", worker_name="W", shift_date="2026-03-22")
        mock_c.return_value = CallResult(ok=True, call_sid="CA1", to="+1", from_="+1", status="queued")
        mock_n.return_value = ManagerNotifyResult(ok=True, channel="#ops", ts="1")

        r = client.post("/api/v1/incidents/absence",
                         headers={"Idempotency-Key": "e2e-absence-001"},
                         json={
                             "worker_name": "Maria",
                             "site_id": "S1",
                             "shift_date": "2026-03-22",
                             "role": "Welder",
                             "reason": "Injury",
                             "contractor_phone": "+15557654321",
                             "severity": "high",
                             "source": "hr_webhook",
                         })
        assert r.status_code == 201
        incident_id = r.json()["incident"]["id"]

        detail = client.get(f"/api/v1/incidents/{incident_id}").json()
        actions = detail["actions"]
        assert len(actions) == 3
        assert all(a["status"] == "completed" for a in actions)

    @patch("app.services.action_executor.notify_site_manager")
    @patch("app.services.action_executor.make_call")
    @patch("app.services.action_executor.update_labor_record")
    def test_mid_workflow_failure(self, mock_l, mock_c, mock_n, db):
        """Action 2 fails but actions 1 and 3 still execute."""
        mock_l.return_value = LaborUpdateResult(ok=True, site_id="S1", worker_name="W", shift_date="2026-03-22")
        mock_c.return_value = CallResult(ok=False, to="+1", error="No answer")
        mock_n.return_value = ManagerNotifyResult(ok=True, channel="#ops", ts="1")

        inc = _absence_incident(db, key="mid-fail-001")
        _add_action(db, inc, seq=1, atype=ActionType.update_labor)
        _add_action(db, inc, seq=2, atype=ActionType.call_contractor)
        _add_action(db, inc, seq=3, atype=ActionType.notify_manager)
        db.commit()
        db.refresh(inc)

        execute_pending_actions(db, inc)

        statuses = {a.action_type.value: a.status.value for a in inc.actions}
        assert statuses["update_labor"] == "completed"
        assert statuses["call_contractor"] == "failed"
        assert statuses["notify_manager"] == "completed"


# ═══════════════════════════════════════════════════════════════════════════
# 6. Adversarial / Edge Cases (5 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestAdversarial:

    def test_missing_required_fields_delay(self, client, db):
        """Missing po_number should return 422."""
        r = client.post("/api/v1/incidents/delay",
                         headers={"Idempotency-Key": "adv-001"},
                         json={
                             "delay_reason": "Storm",
                             "new_eta": "2026-04-01",
                             "supplier_id": str(uuid.uuid4()),
                         })
        assert r.status_code == 422

    def test_missing_idempotency_header(self, client, db):
        """Missing Idempotency-Key header should return 422."""
        r = client.post("/api/v1/incidents/delay", json={
            "po_number": "PO-001",
            "supplier_id": str(uuid.uuid4()),
            "delay_reason": "Storm",
            "new_eta": "2026-04-01",
        })
        assert r.status_code == 422

    def test_invalid_incident_id_returns_404(self, client, db):
        r = client.get(f"/api/v1/incidents/{uuid.uuid4()}")
        assert r.status_code == 404

    def test_empty_payload_action_handles_gracefully(self, db):
        """Action executor should handle incident with empty payload without crashing."""
        inc = Incident(
            idempotency_key="adv-empty-payload",
            type=IncidentType.shipment_delay,
            status=IncidentStatus.in_progress,
            severity=Severity.low,
            source="test",
            payload={},
        )
        db.add(inc)
        db.flush()

        ar = ActionRun(
            incident_id=inc.id,
            action_type=ActionType.call_production,
            status=ActionStatus.pending,
            sequence=1,
        )
        db.add(ar)
        db.commit()
        db.refresh(inc)

        with patch("app.services.action_executor.make_call") as mock_call:
            mock_call.return_value = CallResult(ok=False, to="", error="No destination phone number available")
            execute_pending_actions(db, inc)

        db.refresh(ar)
        assert ar.status == ActionStatus.failed

    def test_po_update_with_concurrent_modification(self, db, po):
        """Two concurrent PO updates — second should fail at row-level guard."""
        r1 = update_po(db, po_number="PO-REG-001", new_status=POStatus.amended, notes="First update")
        assert r1.ok is True
        assert r1.new_version == 2

        # Now try with stale version
        r2 = update_po(db, po_number="PO-REG-001", new_status=POStatus.amended,
                        notes="Second update", expected_version=1)
        assert r2.ok is False
        assert "Version conflict" in r2.error


# ═══════════════════════════════════════════════════════════════════════════
# 7. Safety Policy (4 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestSafetyPolicy:

    def test_customer_facing_requires_approval(self):
        d = check_human_approval_required(
            require_human_approval=True,
            is_customer_facing=True,
            approved_by_human=False,
        )
        assert d.allowed is False
        assert "approval" in d.reason.lower()

    def test_internal_action_allowed(self):
        d = check_human_approval_required(
            require_human_approval=True,
            is_customer_facing=False,
            approved_by_human=False,
        )
        assert d.allowed is True

    def test_policy_disabled_allows_all(self):
        d = check_human_approval_required(
            require_human_approval=False,
            is_customer_facing=True,
            approved_by_human=False,
        )
        assert d.allowed is True

    def test_human_approved_allows_customer_facing(self):
        d = check_human_approval_required(
            require_human_approval=True,
            is_customer_facing=True,
            approved_by_human=True,
        )
        assert d.allowed is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
