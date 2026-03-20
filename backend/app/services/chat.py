from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from google import genai
from google.genai import types
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    ActionRun,
    ActionStatus,
    ActionType,
    Approval,
    ApprovalStatus,
    Incident,
    IncidentStatus,
    PurchaseOrder,
    Shipment,
)
from app.services.action_executor import execute_pending_actions

logger = logging.getLogger("backend.chat")

# ── Types ────────────────────────────────────────────────────────────────


@dataclass
class ProposedAction:
    """Only used for actions that need operator confirmation (approval-gated)."""
    action_type: str
    label: str
    description: str
    requires_approval: bool = False


@dataclass
class ChatResponse:
    reply: str
    proposed_actions: list[ProposedAction] = field(default_factory=list)


# ── System prompt ────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a Supply Chain Coordinator AI worker for HappyRobot. You take actions \
to resolve supply chain incidents — shipment delays and worker absences.

How you work:
- When an operator asks you to do something (call production, update a PO, send \
  a Slack notification, etc.), you DO IT immediately using the execute_command tool. \
  Respond briefly: "On it." or "Done." followed by the result.
- After completing an action, proactively suggest the next best step. Ask the \
  operator: "Shall I [next action]?" to keep the workflow moving.
- Customer-facing actions (like emailing a customer) require human approval. \
  When the operator asks for one, explain that it needs approval and it will be \
  added to the action timeline for review.
- You can look up incident status and shipment data when asked.

Tone: Brief, direct, action-oriented. Say "On it." when executing. Report results \
concisely. Always suggest what to do next.\
"""

# ── Tool / function definitions ──────────────────────────────────────────

_TOOL_DECLARATIONS = [
    types.FunctionDeclaration(
        name="get_incident_status",
        description="Get the current status of the active incident including all action runs and their statuses.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "incident_id": types.Schema(
                    type=types.Type.STRING,
                    description="The UUID of the incident to look up.",
                ),
            },
            required=["incident_id"],
        ),
    ),
    types.FunctionDeclaration(
        name="list_active_shipments",
        description="List active shipments, optionally filtered by PO number.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "po_number": types.Schema(
                    type=types.Type.STRING,
                    description="Optional PO number to filter by.",
                ),
            },
        ),
    ),
    types.FunctionDeclaration(
        name="execute_command",
        description=(
            "Execute an action for the current incident. This IMMEDIATELY performs "
            "the action (makes the call, sends the notification, updates the PO, etc.). "
            "Available commands: call_production, call_contractor, update_po, "
            "email_customer, slack_notify, notify_manager, escalate_ticket, update_labor. "
            "Use this whenever the operator asks you to perform an action. "
            "Note: email_customer requires human approval and will be queued for review."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "command": types.Schema(
                    type=types.Type.STRING,
                    description="The action command to execute.",
                ),
                "reason": types.Schema(
                    type=types.Type.STRING,
                    description="Brief reason or context for the action.",
                ),
            },
            required=["command"],
        ),
    ),
]

TOOLS = [types.Tool(function_declarations=_TOOL_DECLARATIONS)]

# ── Map command names to ActionType ──────────────────────────────────────

COMMAND_TO_ACTION: dict[str, ActionType] = {
    "call_production": ActionType.call_production,
    "call_contractor": ActionType.call_contractor,
    "update_po": ActionType.update_po,
    "email_customer": ActionType.email_customer,
    "slack_notify": ActionType.slack_notify,
    "notify_manager": ActionType.notify_manager,
    "escalate_ticket": ActionType.escalate_ticket,
    "update_labor": ActionType.update_labor,
}

APPROVAL_REQUIRED_ACTIONS = {ActionType.email_customer}

ACTION_LABELS: dict[str, str] = {
    "call_production": "Call production to confirm status",
    "call_contractor": "Call contractors for replacement",
    "update_po": "Update PO documents",
    "email_customer": "Email customer with update",
    "slack_notify": "Send Slack notification",
    "notify_manager": "Notify site manager",
    "escalate_ticket": "Escalate support ticket",
    "update_labor": "Update labor system",
}


# ── Tool execution (server-side) ─────────────────────────────────────────


def _handle_get_incident_status(db: Session, incident: Incident, args: dict) -> str:
    payload = incident.payload or {}
    actions_summary = []
    for a in incident.actions:
        entry = f"  - {a.action_type.value}: {a.status.value}"
        if a.completed_at:
            entry += f" (completed {a.completed_at.strftime('%H:%M')})"
        if a.error_message:
            entry += f" [error: {a.error_message}]"
        if a.status == ActionStatus.needs_approval:
            entry += " [REQUIRES APPROVAL]"
        actions_summary.append(entry)

    lines = [
        f"Incident ID: {incident.id}",
        f"Type: {incident.type.value}",
        f"Status: {incident.status.value}",
        f"Severity: {incident.severity.value}",
        f"Created: {incident.created_at.strftime('%Y-%m-%d %H:%M')}",
    ]

    if incident.type.value == "shipment_delay":
        lines += [
            f"PO Number: {payload.get('po_number', 'N/A')}",
            f"Delay Reason: {payload.get('delay_reason', 'N/A')}",
            f"New ETA: {payload.get('new_eta', 'N/A')}",
        ]
    elif incident.type.value == "worker_absence":
        lines += [
            f"Worker: {payload.get('worker_name', 'N/A')}",
            f"Site: {payload.get('site_id', 'N/A')}",
            f"Role: {payload.get('role', 'N/A')}",
            f"Shift Date: {payload.get('shift_date', 'N/A')}",
        ]

    lines.append("\nAction Timeline:")
    lines.extend(actions_summary)

    return "\n".join(lines)


def _handle_list_active_shipments(db: Session, args: dict) -> str:
    po_number = args.get("po_number")
    query = db.query(Shipment)
    if po_number:
        query = query.filter(Shipment.po_number == po_number)
    shipments = query.limit(20).all()

    if not shipments:
        return "No shipments found."

    lines = []
    for s in shipments:
        lines.append(
            f"- PO: {s.po_number} | Status: {s.status.value} | "
            f"ETA: {s.current_eta.strftime('%Y-%m-%d')} | "
            f"Customer: {s.customer_name}"
        )
    return "\n".join(lines)


def _handle_execute_command(
    db: Session, incident: Incident, args: dict,
) -> tuple[str, list[ProposedAction]]:
    """Execute an action immediately. Internal actions run now; customer-facing need approval."""
    command = args.get("command", "")
    reason = args.get("reason", "")
    action_type = COMMAND_TO_ACTION.get(command)

    if not action_type:
        return f"Unknown command: {command}. Available: {', '.join(COMMAND_TO_ACTION.keys())}", []

    requires_approval = action_type in APPROVAL_REQUIRED_ACTIONS
    label = ACTION_LABELS.get(command, command)

    # --- Check if there's an existing action_run for this type ---
    existing = None
    for a in incident.actions:
        if a.action_type == action_type:
            existing = a
            break

    if requires_approval:
        # Customer-facing: don't execute, propose for approval
        if existing and existing.status == ActionStatus.needs_approval:
            result_text = (
                f"'{label}' requires human approval before I can send it. "
                f"It's already in the action timeline waiting for review."
            )
        elif existing and existing.status == ActionStatus.completed:
            result_text = f"'{label}' has already been completed."
        else:
            # Create new action_run with approval gate
            max_seq = max((a.sequence for a in incident.actions), default=0)
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
            result_text = (
                f"'{label}' requires human approval. "
                f"I've added it to the action timeline for review."
            )

        proposed = ProposedAction(
            action_type=command,
            label=label,
            description=reason or "Requires approval before sending",
            requires_approval=True,
        )
        return result_text, [proposed]

    # --- Internal action: execute immediately ---

    if existing and existing.status == ActionStatus.completed:
        return f"'{label}' has already been completed.", []

    if existing and existing.status in (ActionStatus.pending, ActionStatus.failed):
        # Re-trigger existing action
        if existing.status == ActionStatus.failed:
            existing.retry_count += 1
        existing.status = ActionStatus.pending
        existing.error_message = None
        db.commit()
    elif not existing:
        # Create new action_run
        max_seq = max((a.sequence for a in incident.actions), default=0)
        new_action = ActionRun(
            incident_id=incident.id,
            action_type=action_type,
            status=ActionStatus.pending,
            sequence=max_seq + 1,
        )
        db.add(new_action)
        db.commit()

    # Refresh incident to pick up changes
    db.refresh(incident)

    # Execute now
    executed = execute_pending_actions(db, incident)
    run = next((a for a in executed if a.action_type == action_type), None)

    if run and run.status == ActionStatus.completed:
        return f"Done. '{label}' completed successfully.", []
    elif run:
        return f"'{label}' failed: {run.error_message or 'unknown error'}. I can retry if needed.", []
    else:
        return f"'{label}' could not be executed right now.", []


# ── Main chat function ───────────────────────────────────────────────────


def _get_client() -> genai.Client:
    settings = get_settings()
    return genai.Client(api_key=settings.vertex_ai_key)


def _build_incident_context(incident: Incident) -> str:
    payload = incident.payload or {}
    parts = [
        f"[Active Incident] ID: {incident.id}",
        f"Type: {incident.type.value} | Status: {incident.status.value} | Severity: {incident.severity.value}",
    ]
    if incident.type.value == "shipment_delay":
        parts.append(f"PO: {payload.get('po_number')} | Reason: {payload.get('delay_reason')} | New ETA: {payload.get('new_eta')}")
    elif incident.type.value == "worker_absence":
        parts.append(f"Worker: {payload.get('worker_name')} | Site: {payload.get('site_id')} | Role: {payload.get('role')}")

    action_statuses = [f"{a.action_type.value}={a.status.value}" for a in incident.actions]
    parts.append(f"Actions: {', '.join(action_statuses)}")
    return "\n".join(parts)


def process_message(
    db: Session,
    incident: Incident,
    user_message: str,
    history: list[dict] | None = None,
) -> ChatResponse:
    settings = get_settings()

    if not settings.vertex_ai_key:
        return ChatResponse(reply="Chat is unavailable — no API key configured.")

    client = _get_client()

    # Build conversation contents
    incident_context = _build_incident_context(incident)
    full_system = f"{SYSTEM_PROMPT}\n\nCurrent incident context:\n{incident_context}"

    contents: list[types.Content] = []

    # Add conversation history
    if history:
        for msg in history:
            role = "user" if msg["role"] == "user" else "model"
            contents.append(types.Content(role=role, parts=[types.Part.from_text(text=msg["content"])]))

    # Add current user message
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_message)]))

    proposed_actions: list[ProposedAction] = []

    try:
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=full_system,
                tools=TOOLS,
                temperature=0.3,
            ),
        )

        # Handle function calls
        if response.candidates and response.candidates[0].content:
            parts = response.candidates[0].content.parts or []

            for part in parts:
                if part.function_call:
                    fn_name = part.function_call.name
                    fn_args = dict(part.function_call.args) if part.function_call.args else {}

                    logger.info("Tool call: %s(%s)", fn_name, fn_args)

                    # Execute the tool
                    if fn_name == "execute_command":
                        tool_result, actions = _handle_execute_command(db, incident, fn_args)
                        proposed_actions.extend(actions)
                    elif fn_name == "get_incident_status":
                        tool_result = _handle_get_incident_status(db, incident, fn_args)
                    elif fn_name == "list_active_shipments":
                        tool_result = _handle_list_active_shipments(db, fn_args)
                    else:
                        tool_result = f"Unknown tool: {fn_name}"

                    # Send tool result back to model for final response
                    contents.append(response.candidates[0].content)
                    contents.append(
                        types.Content(
                            role="user",
                            parts=[
                                types.Part.from_function_response(
                                    name=fn_name,
                                    response={"result": tool_result},
                                )
                            ],
                        )
                    )

                    followup = client.models.generate_content(
                        model=settings.gemini_model,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            system_instruction=full_system,
                            temperature=0.3,
                        ),
                    )
                    reply_text = followup.text or "Done."

                    return ChatResponse(reply=reply_text, proposed_actions=proposed_actions)

        # No function calls — plain text response
        reply_text = response.text or "I'm not sure how to help with that. Could you rephrase?"

        return ChatResponse(reply=reply_text, proposed_actions=proposed_actions)

    except Exception as e:
        logger.exception("Gemini API error: %s", e)
        return ChatResponse(reply="I encountered an error processing your request. Please try again.")
