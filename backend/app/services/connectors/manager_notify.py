from __future__ import annotations

import logging
from dataclasses import dataclass

from app.services.connectors.slack import send_message as slack_send

logger = logging.getLogger("backend.connectors.manager_notify")


@dataclass(frozen=True)
class ManagerNotifyResult:
    ok: bool
    channel: str
    ts: str | None = None
    error: str | None = None


def notify_site_manager(
    site_id: str,
    worker_name: str,
    shift_date: str,
    role: str,
    reason: str = "",
    channel: str | None = None,
    idempotency_key: str | None = None,
) -> ManagerNotifyResult:
    """Send a formatted Slack notification to the site manager about a staffing
    gap that needs attention.

    Reuses the existing Slack connector under the hood.
    """
    message = (
        f"👷 *Site Manager Alert — Staffing Gap*\n"
        f"• Site: {site_id}\n"
        f"• Absent worker: {worker_name}\n"
        f"• Role: {role}\n"
        f"• Shift date: {shift_date}\n"
        f"• Reason: {reason or 'Not specified'}\n\n"
        f"Contractor call has been initiated. "
        f"Please confirm site readiness and brief the replacement on arrival."
    )

    try:
        result = slack_send(
            channel=channel,
            message=message,
            idempotency_key=idempotency_key,
        )
        logger.info(
            "Manager notification sent for site %s (ok=%s)", site_id, result.ok,
        )
        return ManagerNotifyResult(
            ok=result.ok,
            channel=result.channel,
            ts=result.ts,
            error=result.error,
        )
    except Exception as exc:
        error_msg = str(exc)
        logger.error("Manager notification error: %s", error_msg)
        return ManagerNotifyResult(
            ok=False,
            channel=channel or "unknown",
            error=error_msg,
        )
