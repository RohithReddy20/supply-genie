from __future__ import annotations

import logging
from dataclasses import dataclass

import resend

from app.config import get_settings

logger = logging.getLogger("backend.connectors.email")


@dataclass(frozen=True)
class EmailResult:
    ok: bool
    to: str
    email_id: str | None = None
    error: str | None = None


def send_email(to: str, subject: str, body: str) -> EmailResult:
    settings = get_settings()

    if not settings.resend_api_key:
        logger.warning("RESEND_API_KEY not set — skipping real send")
        return EmailResult(ok=False, to=to, error="Resend API key not configured")

    resend.api_key = settings.resend_api_key

    try:
        result = resend.Emails.send(
            {
                "from": settings.email_from,
                "to": [to],
                "subject": subject,
                "html": body,
            }
        )
        email_id = result.get("id") if isinstance(result, dict) else getattr(result, "id", str(result))
        logger.info("Email sent to %s (id=%s)", to, email_id)
        return EmailResult(ok=True, to=to, email_id=str(email_id))
    except Exception as exc:
        error_msg = str(exc)
        logger.error("Resend API error: %s", error_msg)
        return EmailResult(ok=False, to=to, error=error_msg)
