from __future__ import annotations

import logging
from dataclasses import dataclass
from html import escape
from uuid import uuid4

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from app.config import get_settings

logger = logging.getLogger("backend.connectors.twilio_voice")


@dataclass(frozen=True)
class CallResult:
    ok: bool
    call_sid: str | None = None
    to: str | None = None
    from_: str | None = None
    status: str | None = None
    error: str | None = None


def make_call(to: str, message: str, from_number: str | None = None) -> CallResult:
    settings = get_settings()

    if settings.twilio_mock_mode:
        return _mock_call(to, message, from_number or settings.twilio_from_number)

    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        logger.warning("Twilio credentials not configured — skipping real call")
        return CallResult(ok=False, to=to, error="Twilio credentials not configured")

    from_num = from_number or settings.twilio_from_number
    if not from_num:
        return CallResult(ok=False, to=to, error="No from number configured")

    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    twiml = f'<Response><Say voice="alice">{escape(message)}</Say></Response>'

    try:
        call = client.calls.create(twiml=twiml, to=to, from_=from_num)
        logger.info("Twilio call initiated to %s (sid=%s, status=%s)", to, call.sid, call.status)
        return CallResult(
            ok=True,
            call_sid=call.sid,
            to=to,
            from_=from_num,
            status=call.status,
        )
    except TwilioRestException as exc:
        logger.error("Twilio API error: %s", exc.msg)
        return CallResult(ok=False, to=to, from_=from_num, error=exc.msg)


def get_call_status(call_sid: str) -> CallResult:
    settings = get_settings()

    if settings.twilio_mock_mode:
        return CallResult(ok=True, call_sid=call_sid, status="completed")

    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    try:
        call = client.calls(call_sid).fetch()
        return CallResult(
            ok=True,
            call_sid=call.sid,
            to=call.to,
            from_=call.from_formatted,
            status=call.status,
        )
    except TwilioRestException as exc:
        return CallResult(ok=False, call_sid=call_sid, error=exc.msg)


def _mock_call(to: str, message: str, from_number: str) -> CallResult:
    mock_sid = f"CA{uuid4().hex[:32]}"
    logger.info("MOCK call to %s (sid=%s): %s", to, mock_sid, message[:80])
    return CallResult(
        ok=True,
        call_sid=mock_sid,
        to=to,
        from_=from_number,
        status="queued",
    )
