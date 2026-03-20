"""Thorough tests for chat endpoints and service."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
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
    PurchaseOrder,
    POStatus,
    Severity,
    Shipment,
    ShipmentStatus,
    Supplier,
)
from app.services.chat import (
    COMMAND_TO_ACTION,
    ChatResponse,
    ProposedAction,
    _build_incident_context,
    _handle_execute_command,
    _handle_get_incident_status,
    _handle_list_active_shipments,
    process_message,
)


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    """In-memory SQLite database for tests."""
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
    """FastAPI test client with overridden DB dependency."""
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
def shipment(db, supplier):
    s = Shipment(
        po_number="PO-2026-0042",
        supplier_id=supplier.id,
        status=ShipmentStatus.delayed,
        original_eta=datetime(2026, 3, 15, tzinfo=timezone.utc),
        current_eta=datetime(2026, 4, 1, tzinfo=timezone.utc),
        customer_email="customer@example.com",
        customer_name="Jane Smith",
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@pytest.fixture
def delay_incident(db, shipment):
    incident = Incident(
        idempotency_key="test-delay-001",
        type=IncidentType.shipment_delay,
        status=IncidentStatus.in_progress,
        severity=Severity.high,
        source="tms_webhook",
        payload={
            "po_number": "PO-2026-0042",
            "supplier_id": str(shipment.supplier_id),
            "delay_reason": "Port congestion in Shanghai",
            "new_eta": "2026-04-01",
        },
        shipment_id=shipment.id,
    )
    db.add(incident)
    db.commit()
    db.refresh(incident)

    # Add playbook actions
    actions_data = [
        (ActionType.slack_notify, ActionStatus.completed, 1),
        (ActionType.call_production, ActionStatus.completed, 2),
        (ActionType.update_po, ActionStatus.completed, 3),
        (ActionType.email_customer, ActionStatus.needs_approval, 4),
    ]
    for atype, status, seq in actions_data:
        ar = ActionRun(
            incident_id=incident.id,
            action_type=atype,
            status=status,
            sequence=seq,
            started_at=datetime.now(timezone.utc) if status == ActionStatus.completed else None,
            completed_at=datetime.now(timezone.utc) if status == ActionStatus.completed else None,
        )
        db.add(ar)
        if status == ActionStatus.needs_approval:
            db.flush()
            approval = Approval(
                action_run_id=ar.id,
                incident_id=incident.id,
                status=ApprovalStatus.pending,
            )
            db.add(approval)

    db.commit()
    db.refresh(incident)
    return incident


@pytest.fixture
def absence_incident(db):
    incident = Incident(
        idempotency_key="test-absence-001",
        type=IncidentType.worker_absence,
        status=IncidentStatus.in_progress,
        severity=Severity.medium,
        source="hr_webhook",
        payload={
            "worker_name": "Mike Johnson",
            "site_id": "SITE-LA-01",
            "shift_date": "2026-03-22",
            "role": "Machine Operator",
            "reason": "Medical leave",
        },
    )
    db.add(incident)
    db.commit()

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


# ── Service unit tests ───────────────────────────────────────────────────

class TestHandleGetIncidentStatus:
    def test_delay_incident(self, db, delay_incident):
        result = _handle_get_incident_status(db, delay_incident, {"incident_id": str(delay_incident.id)})
        assert "PO-2026-0042" in result
        assert "Port congestion" in result
        assert "shipment_delay" in result
        assert "high" in result
        assert "slack_notify: completed" in result
        assert "email_customer: needs_approval" in result
        assert "REQUIRES APPROVAL" in result

    def test_absence_incident(self, db, absence_incident):
        result = _handle_get_incident_status(db, absence_incident, {"incident_id": str(absence_incident.id)})
        assert "Mike Johnson" in result
        assert "SITE-LA-01" in result
        assert "Machine Operator" in result
        assert "worker_absence" in result


class TestHandleListActiveShipments:
    def test_list_all(self, db, shipment):
        result = _handle_list_active_shipments(db, {})
        assert "PO-2026-0042" in result
        assert "Jane Smith" in result

    def test_filter_by_po(self, db, shipment):
        result = _handle_list_active_shipments(db, {"po_number": "PO-2026-0042"})
        assert "PO-2026-0042" in result

    def test_filter_no_match(self, db, shipment):
        result = _handle_list_active_shipments(db, {"po_number": "PO-9999"})
        assert "No shipments found" in result


class TestHandleExecuteCommand:
    def test_internal_action_executes_immediately(self, db, delay_incident):
        """Internal actions (call_production) should execute, not just propose."""
        # call_production is already completed in delay_incident fixture
        result_text, actions = _handle_execute_command(db, delay_incident, {"command": "call_production", "reason": "Confirm ETA"})
        assert len(actions) == 0  # No proposed actions — it's already done
        assert "already been completed" in result_text.lower()

    def test_approval_required_proposes_not_executes(self, db, delay_incident):
        """Customer-facing actions should be proposed, not executed."""
        result_text, actions = _handle_execute_command(db, delay_incident, {"command": "email_customer"})
        assert len(actions) == 1
        assert actions[0].requires_approval is True
        assert "approval" in result_text.lower()

    def test_unknown_command(self, db, delay_incident):
        result_text, actions = _handle_execute_command(db, delay_incident, {"command": "launch_rockets"})
        assert len(actions) == 0
        assert "Unknown command" in result_text

    @patch("app.services.chat.execute_pending_actions")
    def test_pending_action_executes(self, mock_exec, db, absence_incident):
        """Pending internal action should be executed immediately."""
        # Mock executor to simulate success
        def fake_execute(db, incident):
            for a in incident.actions:
                if a.status == ActionStatus.pending:
                    a.status = ActionStatus.completed
                    a.completed_at = datetime.now(timezone.utc)
            db.commit()
            return incident.actions
        mock_exec.side_effect = fake_execute

        result_text, actions = _handle_execute_command(db, absence_incident, {"command": "update_labor"})
        assert len(actions) == 0  # No proposals — executed directly
        assert "done" in result_text.lower() or "completed" in result_text.lower()
        mock_exec.assert_called_once()

    def test_new_approval_action_creates_records(self, db, absence_incident):
        """Creating a new email_customer action on absence incident creates ActionRun + Approval."""
        result_text, actions = _handle_execute_command(db, absence_incident, {"command": "email_customer"})
        assert len(actions) == 1
        assert actions[0].requires_approval is True

        # Verify DB records were created
        new_action = db.query(ActionRun).filter(
            ActionRun.incident_id == absence_incident.id,
            ActionRun.action_type == ActionType.email_customer,
        ).first()
        assert new_action is not None
        assert new_action.status == ActionStatus.needs_approval

        approval = db.query(Approval).filter(Approval.action_run_id == new_action.id).first()
        assert approval is not None
        assert approval.status == ApprovalStatus.pending


class TestBuildIncidentContext:
    def test_delay_context(self, delay_incident):
        ctx = _build_incident_context(delay_incident)
        assert "shipment_delay" in ctx
        assert "PO-2026-0042" in ctx
        assert "Port congestion" in ctx
        assert "slack_notify=completed" in ctx

    def test_absence_context(self, absence_incident):
        ctx = _build_incident_context(absence_incident)
        assert "worker_absence" in ctx
        assert "Mike Johnson" in ctx
        assert "SITE-LA-01" in ctx


class TestProcessMessage:
    @patch("app.services.chat.get_settings")
    def test_no_api_key(self, mock_settings, db, delay_incident):
        settings = MagicMock()
        settings.vertex_ai_key = ""
        mock_settings.return_value = settings

        result = process_message(db, delay_incident, "What's the status?")
        assert "unavailable" in result.reply.lower()

    @patch("app.services.chat._get_client")
    @patch("app.services.chat.get_settings")
    def test_plain_text_response(self, mock_settings, mock_client, db, delay_incident):
        settings = MagicMock()
        settings.vertex_ai_key = "test-key"
        settings.gemini_model = "gemini-2.0-flash"
        mock_settings.return_value = settings

        # Mock Gemini response — plain text, no function calls
        mock_response = MagicMock()
        mock_response.text = "The incident is currently being processed. All internal actions are complete."
        mock_candidate = MagicMock()
        mock_content = MagicMock()
        mock_part = MagicMock()
        mock_part.function_call = None
        mock_content.parts = [mock_part]
        mock_candidate.content = mock_content
        mock_response.candidates = [mock_candidate]

        client_instance = MagicMock()
        client_instance.models.generate_content.return_value = mock_response
        mock_client.return_value = client_instance

        result = process_message(db, delay_incident, "What's the status?")
        assert "processed" in result.reply.lower() or "complete" in result.reply.lower()

    @patch("app.services.chat._get_client")
    @patch("app.services.chat.get_settings")
    def test_function_call_response(self, mock_settings, mock_client, db, delay_incident):
        settings = MagicMock()
        settings.vertex_ai_key = "test-key"
        settings.gemini_model = "gemini-2.0-flash"
        mock_settings.return_value = settings

        # Mock Gemini response — function call to get_incident_status
        mock_fn_call = MagicMock()
        mock_fn_call.name = "get_incident_status"
        mock_fn_call.args = {"incident_id": str(delay_incident.id)}

        mock_part = MagicMock()
        mock_part.function_call = mock_fn_call

        mock_content = MagicMock()
        mock_content.parts = [mock_part]

        mock_candidate = MagicMock()
        mock_candidate.content = mock_content

        mock_response = MagicMock()
        mock_response.candidates = [mock_candidate]

        # Mock followup response
        mock_followup = MagicMock()
        mock_followup.text = "PO-2026-0042 is delayed due to port congestion. Three actions are complete."

        client_instance = MagicMock()
        client_instance.models.generate_content.side_effect = [mock_response, mock_followup]
        mock_client.return_value = client_instance

        result = process_message(db, delay_incident, "What's the status of PO-2026-0042?")
        assert "PO-2026-0042" in result.reply
        # Should have been called twice (initial + followup after tool result)
        assert client_instance.models.generate_content.call_count == 2

    @patch("app.services.chat._get_client")
    @patch("app.services.chat.get_settings")
    def test_execute_command_function_call(self, mock_settings, mock_client, db, delay_incident):
        settings = MagicMock()
        settings.vertex_ai_key = "test-key"
        settings.gemini_model = "gemini-2.0-flash"
        mock_settings.return_value = settings

        # Mock function call to execute_command
        mock_fn_call = MagicMock()
        mock_fn_call.name = "execute_command"
        mock_fn_call.args = {"command": "email_customer", "reason": "Update on delay"}

        mock_part = MagicMock()
        mock_part.function_call = mock_fn_call

        mock_content = MagicMock()
        mock_content.parts = [mock_part]

        mock_candidate = MagicMock()
        mock_candidate.content = mock_content

        mock_response = MagicMock()
        mock_response.candidates = [mock_candidate]

        mock_followup = MagicMock()
        mock_followup.text = "I'll queue the customer email. Note it requires approval first."

        client_instance = MagicMock()
        client_instance.models.generate_content.side_effect = [mock_response, mock_followup]
        mock_client.return_value = client_instance

        result = process_message(db, delay_incident, "Email the customer about the delay")
        assert len(result.proposed_actions) >= 1
        email_action = next((a for a in result.proposed_actions if a.action_type == "email_customer"), None)
        assert email_action is not None
        assert email_action.requires_approval is True

    @patch("app.services.chat._get_client")
    @patch("app.services.chat.get_settings")
    def test_api_error_handling(self, mock_settings, mock_client, db, delay_incident):
        settings = MagicMock()
        settings.vertex_ai_key = "test-key"
        settings.gemini_model = "gemini-2.0-flash"
        mock_settings.return_value = settings

        client_instance = MagicMock()
        client_instance.models.generate_content.side_effect = Exception("API rate limit")
        mock_client.return_value = client_instance

        result = process_message(db, delay_incident, "Hello")
        assert "error" in result.reply.lower()

    @patch("app.services.chat._get_client")
    @patch("app.services.chat.get_settings")
    def test_conversation_history(self, mock_settings, mock_client, db, delay_incident):
        settings = MagicMock()
        settings.vertex_ai_key = "test-key"
        settings.gemini_model = "gemini-2.0-flash"
        mock_settings.return_value = settings

        mock_response = MagicMock()
        mock_response.text = "Following up on our conversation."
        mock_candidate = MagicMock()
        mock_content = MagicMock()
        mock_part = MagicMock()
        mock_part.function_call = None
        mock_content.parts = [mock_part]
        mock_candidate.content = mock_content
        mock_response.candidates = [mock_candidate]

        client_instance = MagicMock()
        client_instance.models.generate_content.return_value = mock_response
        mock_client.return_value = client_instance

        history = [
            {"role": "user", "content": "What happened?"},
            {"role": "assistant", "content": "There was a shipment delay."},
        ]
        result = process_message(db, delay_incident, "Tell me more", history=history)

        # Verify history was passed in the contents
        call_args = client_instance.models.generate_content.call_args
        contents = call_args.kwargs.get("contents") or call_args[1].get("contents", [])
        # Should have history (2) + current message (1) = 3 content items
        assert len(contents) == 3


# ── Router integration tests ────────────────────────────────────────────

class TestChatMessageEndpoint:
    @patch("app.services.chat._get_client")
    @patch("app.services.chat.get_settings")
    def test_message_success(self, mock_settings, mock_client, client, db, delay_incident):
        settings = MagicMock()
        settings.vertex_ai_key = "test-key"
        settings.gemini_model = "gemini-2.0-flash"
        mock_settings.return_value = settings

        mock_response = MagicMock()
        mock_response.text = "The delay incident for PO-2026-0042 is in progress."
        mock_candidate = MagicMock()
        mock_content = MagicMock()
        mock_part = MagicMock()
        mock_part.function_call = None
        mock_content.parts = [mock_part]
        mock_candidate.content = mock_content
        mock_response.candidates = [mock_candidate]

        client_instance = MagicMock()
        client_instance.models.generate_content.return_value = mock_response
        mock_client.return_value = client_instance

        r = client.post("/api/v1/chat/message", json={
            "incident_id": str(delay_incident.id),
            "message": "What's happening with PO-2026-0042?",
        })
        assert r.status_code == 200
        data = r.json()
        assert "reply" in data
        assert "proposed_actions" in data
        assert isinstance(data["proposed_actions"], list)

    def test_message_incident_not_found(self, client, db):
        r = client.post("/api/v1/chat/message", json={
            "incident_id": str(uuid.uuid4()),
            "message": "Hello",
        })
        assert r.status_code == 404

    def test_message_empty_body(self, client, db):
        r = client.post("/api/v1/chat/message", json={
            "incident_id": str(uuid.uuid4()),
            "message": "",
        })
        assert r.status_code == 422

    @patch("app.services.chat._get_client")
    @patch("app.services.chat.get_settings")
    def test_message_with_history(self, mock_settings, mock_client, client, db, delay_incident):
        settings = MagicMock()
        settings.vertex_ai_key = "test-key"
        settings.gemini_model = "gemini-2.0-flash"
        mock_settings.return_value = settings

        mock_response = MagicMock()
        mock_response.text = "I can help with that follow-up."
        mock_candidate = MagicMock()
        mock_content = MagicMock()
        mock_part = MagicMock()
        mock_part.function_call = None
        mock_content.parts = [mock_part]
        mock_candidate.content = mock_content
        mock_response.candidates = [mock_candidate]

        client_instance = MagicMock()
        client_instance.models.generate_content.return_value = mock_response
        mock_client.return_value = client_instance

        r = client.post("/api/v1/chat/message", json={
            "incident_id": str(delay_incident.id),
            "message": "What should I do next?",
            "history": [
                {"role": "user", "content": "What's the status?"},
                {"role": "assistant", "content": "It's delayed."},
            ],
        })
        assert r.status_code == 200


class TestChatCommandEndpoint:
    def test_command_incident_not_found(self, client, db):
        r = client.post("/api/v1/chat/command", json={
            "incident_id": str(uuid.uuid4()),
            "command": "call_production",
        })
        assert r.status_code == 404

    def test_command_unknown(self, client, db, delay_incident):
        r = client.post("/api/v1/chat/command", json={
            "incident_id": str(delay_incident.id),
            "command": "launch_rockets",
        })
        assert r.status_code == 400
        assert "Unknown command" in r.json()["detail"]

    def test_command_needs_approval(self, client, db, delay_incident):
        """email_customer already exists as needs_approval — should return needs_approval."""
        r = client.post("/api/v1/chat/command", json={
            "incident_id": str(delay_incident.id),
            "command": "email_customer",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "needs_approval"
        assert "approval" in data["message"].lower()

    @patch("app.services.action_executor.slack_send")
    def test_command_creates_new_action(self, mock_slack, client, db, delay_incident):
        """escalate_ticket doesn't exist — should create and execute."""
        from app.services.connectors.slack import SlackResult
        mock_slack.return_value = SlackResult(ok=True, channel="#ops", ts="123", error=None)

        r = client.post("/api/v1/chat/command", json={
            "incident_id": str(delay_incident.id),
            "command": "escalate_ticket",
            "reason": "Need L2 support",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["action_run_id"] is not None
        # escalate_ticket is a stub, should complete
        assert data["status"] == "executed"

    def test_command_retrigger_failed(self, client, db, delay_incident):
        """A failed action can be retriggered via command."""
        # Set call_production to failed
        for a in delay_incident.actions:
            if a.action_type == ActionType.call_production:
                a.status = ActionStatus.failed
                a.error_message = "Connection timeout"
                a.retry_count = 1
                break
        db.commit()

        with patch("app.services.action_executor.make_call") as mock_call:
            from app.services.connectors.twilio_voice import CallResult
            mock_call.return_value = CallResult(ok=True, call_sid="CA123", to="+1555", from_="+1555", status="queued", error=None)

            r = client.post("/api/v1/chat/command", json={
                "incident_id": str(delay_incident.id),
                "command": "call_production",
            })
            assert r.status_code == 200


# ── Data integrity tests ────────────────────────────────────────────────

class TestChatDataIntegrity:
    def test_command_to_action_mapping_complete(self):
        """All mapped commands should resolve to valid ActionTypes."""
        for cmd, action_type in COMMAND_TO_ACTION.items():
            assert isinstance(action_type, ActionType), f"{cmd} maps to invalid type"

    def test_all_action_types_mapped(self):
        """All ActionTypes should have a command mapping."""
        for at in ActionType:
            assert at.value in COMMAND_TO_ACTION, f"ActionType {at.value} not in COMMAND_TO_ACTION"

    def test_proposed_action_structure(self, db, delay_incident):
        """ProposedActions from execute_command should have all required fields."""
        _, actions = _handle_execute_command(db, delay_incident, {"command": "call_production"})
        for a in actions:
            assert a.action_type
            assert a.label
            assert a.description
            assert isinstance(a.requires_approval, bool)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
