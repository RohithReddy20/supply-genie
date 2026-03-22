"""Voice pipeline tool declarations and execution logic.

Separates Gemini function-call schemas and their DB-backed implementations
from pipeline plumbing so they can be maintained and tested independently.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("backend.voice_tools")


# ── Gemini tool declarations ────────────────────────────────────────────

TOOL_DECLARATIONS: list[dict[str, Any]] = [
    {
        "function_declarations": [
            {
                "name": "get_incident_status",
                "description": (
                    "Get the current status of the active incident "
                    "including all action runs."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "incident_id": {
                            "type": "STRING",
                            "description": "The UUID of the incident to look up.",
                        },
                    },
                    "required": ["incident_id"],
                },
            },
            {
                "name": "list_active_shipments",
                "description": (
                    "List active shipments, optionally filtered by PO number."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "po_number": {
                            "type": "STRING",
                            "description": "Optional PO number to filter by.",
                        },
                    },
                },
            },
            {
                "name": "execute_command",
                "description": (
                    "Execute an action for the current incident. Available commands: "
                    "call_production, call_contractor, update_po, email_customer, "
                    "slack_notify, notify_manager, escalate_ticket, update_labor. "
                    "email_customer requires human approval and will be queued."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "command": {
                            "type": "STRING",
                            "description": "The action command to execute.",
                        },
                        "reason": {
                            "type": "STRING",
                            "description": "Brief reason or context for the action.",
                        },
                    },
                    "required": ["command"],
                },
            },
            {
                "name": "end_call",
                "description": (
                    "End the active Twilio call gracefully. Use this when the "
                    "caller asks to stop or once the conversation is complete."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {},
                },
            },
            {
                "name": "update_call_progress",
                "description": (
                    "Update structured call objective progress. Mark objective flags "
                    "as true as they become confirmed. Set ready_to_close=true only "
                    "when all objectives are complete and the call can end."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "cause_confirmed": {
                            "type": "BOOLEAN",
                            "description": "Root cause has been confirmed by supplier.",
                        },
                        "eta_obtained": {
                            "type": "BOOLEAN",
                            "description": "Updated ETA has been obtained.",
                        },
                        "mitigation_obtained": {
                            "type": "BOOLEAN",
                            "description": "Mitigation steps have been gathered.",
                        },
                        "risk_assessed": {
                            "type": "BOOLEAN",
                            "description": "Risk of further delays has been assessed.",
                        },
                        "ready_to_close": {
                            "type": "BOOLEAN",
                            "description": "Call is complete and can be ended now.",
                        },
                    },
                },
            },
        ],
    },
]


# ── Tool execution ──────────────────────────────────────────────────────


def execute_tool(
    name: str,
    args: dict[str, Any],
    incident_id: str | None,
    call_sid: str | None,
) -> str:
    """Execute a voice tool synchronously.

    Runs in a thread via ``asyncio.to_thread`` from the pipeline.
    DB operations use a thread-local session.
    """
    from app.config import get_settings
    from app.database import SessionLocal
    from app.models import (
        ActionRun,
        ActionStatus,
        Approval,
        ApprovalStatus,
        Incident,
        Shipment,
    )
    from app.services.action_executor import execute_pending_actions
    from app.services.chat import (
        ACTION_LABELS,
        APPROVAL_REQUIRED_ACTIONS,
        COMMAND_TO_ACTION,
    )

    db = SessionLocal()
    try:
        if name == "end_call":
            if not call_sid:
                return "No active call SID available."
            if not call_sid.startswith("CA"):
                return f"Cannot end non-Twilio call SID: {call_sid}"
            settings = get_settings()
            if settings.twilio_mock_mode:
                return "Call ended (mock mode)."
            if not settings.twilio_account_sid or not settings.twilio_auth_token:
                return "Twilio credentials are not configured."
            from twilio.rest import Client as TwilioClient

            client = TwilioClient(
                settings.twilio_account_sid, settings.twilio_auth_token
            )
            client.calls(call_sid).update(status="completed")
            return "Call ended. Hanging up now."

        if name == "get_incident_status":
            iid = args.get("incident_id") or incident_id
            if not iid:
                return "No incident ID available."
            incident = db.query(Incident).filter(Incident.id == iid).first()
            if not incident:
                return f"Incident {iid} not found."
            lines = [
                f"Incident {incident.id}: {incident.type.value}, "
                f"status={incident.status.value}",
            ]
            for a in incident.actions:
                line = f"  {a.action_type.value}: {a.status.value}"
                if a.completed_at:
                    line += f" (done {a.completed_at.strftime('%H:%M')})"
                if a.error_message:
                    line += f" [error: {a.error_message}]"
                lines.append(line)
            return "\n".join(lines)

        elif name == "list_active_shipments":
            po = args.get("po_number")
            q = db.query(Shipment)
            if po:
                q = q.filter(Shipment.po_number == po)
            ships = q.limit(10).all()
            if not ships:
                return "No shipments found."
            return "\n".join(
                f"PO:{s.po_number} status={s.status.value} "
                f"eta={s.current_eta.strftime('%Y-%m-%d')}"
                for s in ships
            )

        elif name == "execute_command":
            command = args.get("command", "")
            action_type = COMMAND_TO_ACTION.get(command)
            if not action_type:
                return f"Unknown command: {command}"

            if not incident_id:
                return "No active incident for this call."

            incident = (
                db.query(Incident).filter(Incident.id == incident_id).first()
            )
            if not incident:
                return f"Incident {incident_id} not found."

            requires_approval = action_type in APPROVAL_REQUIRED_ACTIONS
            label = ACTION_LABELS.get(command, command)

            if requires_approval:
                max_seq = max(
                    (a.sequence for a in incident.actions), default=0
                )
                new_action = ActionRun(
                    incident_id=incident.id,
                    action_type=action_type,
                    status=ActionStatus.needs_approval,
                    sequence=max_seq + 1,
                )
                db.add(new_action)
                db.flush()
                approval = Approval(
                    action_run_id=new_action.id,
                    incident_id=incident.id,
                    status=ApprovalStatus.pending,
                )
                db.add(approval)
                db.commit()
                return (
                    f"'{label}' requires human approval. "
                    "Added to action timeline for review."
                )

            existing = next(
                (a for a in incident.actions if a.action_type == action_type),
                None,
            )
            if existing and existing.status == ActionStatus.completed:
                return f"'{label}' already completed."

            if existing and existing.status in (
                ActionStatus.pending,
                ActionStatus.failed,
            ):
                if existing.status == ActionStatus.failed:
                    existing.retry_count += 1
                existing.status = ActionStatus.pending
                existing.error_message = None
                db.commit()
            elif not existing:
                max_seq = max(
                    (a.sequence for a in incident.actions), default=0
                )
                new_action = ActionRun(
                    incident_id=incident.id,
                    action_type=action_type,
                    status=ActionStatus.pending,
                    sequence=max_seq + 1,
                )
                db.add(new_action)
                db.commit()

            db.refresh(incident)
            executed = execute_pending_actions(db, incident)
            run = next(
                (a for a in executed if a.action_type == action_type), None
            )

            if run and run.status == ActionStatus.completed:
                return f"Done. '{label}' completed successfully."
            elif run:
                return f"'{label}' failed: {run.error_message or 'unknown error'}"
            return f"'{label}' could not be executed right now."

        return f"Unknown tool: {name}"
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
