from __future__ import annotations

import logging
from dataclasses import dataclass

import resend

from app.config import get_settings
from app.resilience import ConnectorTimeout, get_circuit_breaker, with_timeout

logger = logging.getLogger("backend.connectors.email")


@dataclass(frozen=True)
class EmailResult:
    ok: bool
    to: str
    email_id: str | None = None
    error: str | None = None


def send_email(
    to: str,
    subject: str,
    body: str,
    *,
    idempotency_key: str | None = None,
) -> EmailResult:
    settings = get_settings()

    if not settings.resend_api_key:
        logger.warning("RESEND_API_KEY not set — skipping real send")
        return EmailResult(ok=False, to=to, error="Resend API key not configured")

    cb = get_circuit_breaker("email")
    if not cb.allow_request():
        logger.warning("Email circuit breaker OPEN — skipping send")
        return EmailResult(ok=False, to=to, error="Circuit breaker open: Email service temporarily unavailable")

    resend.api_key = settings.resend_api_key

    try:
        def _send():
            return resend.Emails.send(
                {
                    "from": settings.email_from,
                    "to": [to],
                    "subject": subject,
                    "html": body,
                }
            )

        result = with_timeout(_send, settings.timeout_email_s, "email")
        email_id = result.get("id") if isinstance(result, dict) else getattr(result, "id", str(result))
        if idempotency_key:
            logger.info("Email idempotency key: %s", idempotency_key)
        logger.info("Email sent to %s (id=%s)", to, email_id)
        cb.record_success()
        return EmailResult(ok=True, to=to, email_id=str(email_id))
    except ConnectorTimeout:
        cb.record_failure()
        logger.error("Email send timed out after %ss", settings.timeout_email_s)
        return EmailResult(ok=False, to=to, error=f"Timeout after {settings.timeout_email_s}s")
    except Exception as exc:
        cb.record_failure()
        error_msg = str(exc)
        logger.error("Resend API error: %s", error_msg)
        return EmailResult(ok=False, to=to, error=error_msg)
