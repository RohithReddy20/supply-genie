from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ActionRun, ActionStatus, ActionType, Incident, POStatus
from app.services.connectors.po_system import update_po as po_update
from app.services.connectors.slack import send_message as slack_send
from app.services.connectors.twilio_voice import make_call

logger = logging.getLogger("backend.action_executor")


def execute_pending_actions(db: Session, incident: Incident) -> list[ActionRun]:
    executed: list[ActionRun] = []

    for action in incident.actions:
        if action.status != ActionStatus.pending:
            continue

        success = _dispatch(db, action, incident)

        if success:
            action.status = ActionStatus.completed
            action.completed_at = datetime.now(timezone.utc)
        else:
            _handle_failure(action)

        executed.append(action)

    db.commit()
    return executed


def retry_failed_actions(db: Session, incident: Incident) -> list[ActionRun]:
    settings = get_settings()
    retried: list[ActionRun] = []

    for action in incident.actions:
        if action.status != ActionStatus.failed:
            continue
        if action.retry_count >= settings.max_retries:
            continue

        action.retry_count += 1
        action.status = ActionStatus.pending
        action.error_message = None
        retried.append(action)

    db.commit()

    if retried:
        return execute_pending_actions(db, incident)
    return []


def _dispatch(db: Session, action: ActionRun, incident: Incident) -> bool:
    action.started_at = datetime.now(timezone.utc)
    payload = incident.payload or {}

    if action.action_type == ActionType.slack_notify:
        return _execute_slack(action, payload)

    if action.action_type == ActionType.call_production:
        return _execute_call(action, payload, call_type="production")

    if action.action_type == ActionType.call_contractor:
        return _execute_call(action, payload, call_type="contractor")

    if action.action_type == ActionType.update_po:
        return _execute_po_update(db, action, payload)

    # Other action types will be implemented in subsequent days
    logger.info("Action %s not yet implemented — marking completed (stub)", action.action_type.value)
    action.response_payload = {"stub": True}
    return True


def _execute_slack(action: ActionRun, payload: dict) -> bool:
    po_number = payload.get("po_number", "N/A")
    delay_reason = payload.get("delay_reason", "Unknown")
    new_eta = payload.get("new_eta", "TBD")
    worker_name = payload.get("worker_name", "")
    site_id = payload.get("site_id", "")

    if worker_name:
        message = (
            f"⚠️ *Worker Absence Alert*\n"
            f"• Worker: {worker_name}\n"
            f"• Site: {site_id}\n"
            f"• Reason: {payload.get('reason', 'N/A')}\n"
            f"• Shift: {payload.get('shift_date', 'N/A')}\n"
            f"Replacement workflow initiated."
        )
    else:
        message = (
            f"🚨 *Shipment Delay Alert*\n"
            f"• PO: {po_number}\n"
            f"• Reason: {delay_reason}\n"
            f"• Revised ETA: {new_eta}\n"
            f"Coordination workflow in progress."
        )

    action.request_payload = {"channel": get_settings().slack_default_channel, "message": message}

    result = slack_send(channel=None, message=message)
    action.response_payload = asdict(result)

    if not result.ok:
        action.error_message = result.error
        return False
    return True


def _execute_call(action: ActionRun, payload: dict, call_type: str) -> bool:
    settings = get_settings()

    if call_type == "production":
        po_number = payload.get("po_number", "N/A")
        delay_reason = payload.get("delay_reason", "Unknown")
        new_eta = payload.get("new_eta", "TBD")
        to = payload.get("supplier_phone") or settings.twilio_default_to
        message = (
            f"This is an automated message from the Supply Chain Coordinator. "
            f"Purchase order {po_number} has experienced a delay due to {delay_reason}. "
            f"The revised estimated arrival is {new_eta}. "
            f"Please confirm this update at your earliest convenience. Thank you."
        )
    else:
        worker_name = payload.get("worker_name", "a team member")
        site_id = payload.get("site_id", "N/A")
        role = payload.get("role", "general")
        shift_date = payload.get("shift_date", "TBD")
        to = payload.get("contractor_phone") or settings.twilio_default_to
        message = (
            f"This is an automated message from the Supply Chain Coordinator. "
            f"We have an urgent staffing need at site {site_id} for a {role} position "
            f"on {shift_date} due to the absence of {worker_name}. "
            f"Please confirm your availability. Thank you."
        )

    if not to:
        action.error_message = "No destination phone number available"
        return False

    action.request_payload = {"to": to, "message": message, "call_type": call_type}

    result = make_call(to=to, message=message)
    action.response_payload = asdict(result)

    if not result.ok:
        action.error_message = result.error
        return False
    return True


def _execute_po_update(db: Session, action: ActionRun, payload: dict) -> bool:
    po_number = payload.get("po_number")
    if not po_number:
        action.error_message = "No po_number in incident payload"
        return False

    delay_reason = payload.get("delay_reason", "Unknown")
    new_eta = payload.get("new_eta", "TBD")
    notes = f"[Auto] ETA revised to {new_eta} — reason: {delay_reason}"

    action.request_payload = {
        "po_number": po_number,
        "new_status": POStatus.amended.value,
        "notes": notes,
    }

    result = po_update(db, po_number=po_number, new_status=POStatus.amended, notes=notes)
    action.response_payload = asdict(result)

    if not result.ok:
        action.error_message = result.error
        return False
    return True


def _handle_failure(action: ActionRun) -> None:
    settings = get_settings()
    action.retry_count += 1

    if action.retry_count >= settings.max_retries:
        action.status = ActionStatus.failed
        logger.warning(
            "Action %s exhausted retries (%d/%d) — dead-lettered",
            action.id, action.retry_count, settings.max_retries,
        )
    else:
        action.status = ActionStatus.failed
        logger.info(
            "Action %s failed (attempt %d/%d)",
            action.id, action.retry_count, settings.max_retries,
        )
