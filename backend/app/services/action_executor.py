from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ActionRun, ActionStatus, ActionType, Incident
from app.services.connectors.slack import send_message as slack_send

logger = logging.getLogger("backend.action_executor")


def execute_pending_actions(db: Session, incident: Incident) -> list[ActionRun]:
    executed: list[ActionRun] = []

    for action in incident.actions:
        if action.status != ActionStatus.pending:
            continue

        success = _dispatch(action, incident)

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


def _dispatch(action: ActionRun, incident: Incident) -> bool:
    action.started_at = datetime.now(timezone.utc)
    payload = incident.payload or {}

    if action.action_type == ActionType.slack_notify:
        return _execute_slack(action, payload)

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
