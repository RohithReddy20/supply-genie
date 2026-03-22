from __future__ import annotations

import logging
import time
from dataclasses import asdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ActionRun, ActionStatus, ActionType, Incident, POStatus
from app.observability import trace_action
from app.resilience import backoff_delay_ms, get_fallback_message
from app.services.connectors.email import send_email
from app.services.connectors.labor_system import update_labor_record
from app.services.connectors.manager_notify import notify_site_manager
from app.services.connectors.po_system import update_po as po_update
from app.services.connectors.slack import send_message as slack_send
from app.services.connectors.twilio_voice import make_call

logger = logging.getLogger("backend.action_executor")


def execute_pending_actions(db: Session, incident: Incident) -> list[ActionRun]:
    executed: list[ActionRun] = []

    for action in incident.actions:
        if action.status != ActionStatus.pending:
            continue

        with trace_action(
            action_type=action.action_type.value,
            incident_id=str(incident.id),
            attributes={
                "action.id": str(action.id),
                "action.sequence": action.sequence,
                "action.retry_count": action.retry_count,
            },
        ) as trace_result:
            success = _dispatch(db, action, incident)
            trace_result["success"] = success

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

        # Exponential backoff with jitter before retry
        delay_ms = backoff_delay_ms(action.retry_count)
        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)

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

    if action.action_type == ActionType.email_customer:
        return _execute_email(db, action, payload, incident)

    if action.action_type == ActionType.update_labor:
        return _execute_labor_update(action, payload)

    if action.action_type == ActionType.notify_manager:
        return _execute_notify_manager(action, payload)

    # Remaining action types (escalate_ticket, etc.) — stub for now
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


def _execute_email(db: Session, action: ActionRun, payload: dict, incident: Incident) -> bool:
    from app.models import Shipment

    po_number = payload.get("po_number", "N/A")
    delay_reason = payload.get("delay_reason", "Unknown")
    new_eta = payload.get("new_eta", "TBD")

    shipment = incident.shipment
    if not shipment and po_number != "N/A":
        shipment = db.query(Shipment).filter(Shipment.po_number == po_number).first()

    customer_email = shipment.customer_email if shipment else payload.get("customer_email", "")
    customer_name = shipment.customer_name if shipment else payload.get("customer_name", "Valued Customer")

    if not customer_email:
        action.error_message = "No customer email available"
        return False

    subject = f"Update on your order {po_number}"
    body = (
        f"<p>Dear {customer_name},</p>"
        f"<p>We are writing to inform you about a delay affecting your order "
        f"<strong>{po_number}</strong>.</p>"
        f"<p><strong>Reason:</strong> {delay_reason}<br>"
        f"<strong>Revised ETA:</strong> {new_eta}</p>"
        f"<p>We sincerely apologize for the inconvenience and are actively "
        f"working to minimize the impact. Our team has already contacted the "
        f"supplier and updated the purchase order accordingly.</p>"
        f"<p>If you have any questions, please don't hesitate to reach out.</p>"
        f"<p>Best regards,<br>Supply Chain Coordination Team</p>"
    )

    action.request_payload = {"to": customer_email, "subject": subject}

    result = send_email(to=customer_email, subject=subject, body=body)
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


def _execute_labor_update(action: ActionRun, payload: dict) -> bool:
    worker_name = payload.get("worker_name", "Unknown")
    site_id = payload.get("site_id", "Unknown")
    shift_date = payload.get("shift_date", "TBD")
    role = payload.get("role", "general")
    reason = payload.get("reason", "")

    action.request_payload = {
        "site_id": site_id,
        "worker_name": worker_name,
        "shift_date": shift_date,
        "role": role,
        "status": "absent",
    }

    result = update_labor_record(
        site_id=site_id,
        worker_name=worker_name,
        shift_date=shift_date,
        role=role,
        reason=reason,
    )
    action.response_payload = {
        "ok": result.ok,
        "site_id": result.site_id,
        "worker_name": result.worker_name,
        "shift_date": result.shift_date,
        "status": result.status,
        "coverage_needed": result.coverage_needed,
        "error": result.error,
    }

    if not result.ok:
        action.error_message = result.error
        return False
    return True


def _execute_notify_manager(action: ActionRun, payload: dict) -> bool:
    site_id = payload.get("site_id", "Unknown")
    worker_name = payload.get("worker_name", "Unknown")
    shift_date = payload.get("shift_date", "TBD")
    role = payload.get("role", "general")
    reason = payload.get("reason", "")

    action.request_payload = {
        "site_id": site_id,
        "worker_name": worker_name,
        "shift_date": shift_date,
        "role": role,
    }

    result = notify_site_manager(
        site_id=site_id,
        worker_name=worker_name,
        shift_date=shift_date,
        role=role,
        reason=reason,
    )
    action.response_payload = {
        "ok": result.ok,
        "channel": result.channel,
        "ts": result.ts,
        "error": result.error,
    }

    if not result.ok:
        action.error_message = result.error
        return False
    return True


def _handle_failure(action: ActionRun) -> None:
    settings = get_settings()
    action.retry_count += 1

    if action.retry_count >= settings.max_retries:
        action.status = ActionStatus.failed
        fallback = get_fallback_message(action.action_type.value)
        logger.warning(
            "Action %s exhausted retries (%d/%d) — dead-lettered. Fallback: %s",
            action.id, action.retry_count, settings.max_retries, fallback,
        )
        # Store fallback in response_payload for UI display
        if action.response_payload is None:
            action.response_payload = {}
        action.response_payload["fallback_message"] = fallback
        action.response_payload["dead_lettered"] = True
    else:
        action.status = ActionStatus.failed
        logger.info(
            "Action %s failed (attempt %d/%d), next backoff ~%dms",
            action.id, action.retry_count, settings.max_retries,
            backoff_delay_ms(action.retry_count),
        )
