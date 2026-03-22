from __future__ import annotations

import logging
from dataclasses import dataclass

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from app.config import get_settings
from app.resilience import ConnectorTimeout, get_circuit_breaker, with_timeout, CircuitOpenError

logger = logging.getLogger("backend.connectors.slack")

# Reuse client across calls (connection pooling)
_client: WebClient | None = None


def _get_client() -> WebClient:
    global _client
    settings = get_settings()
    if _client is None:
        _client = WebClient(token=settings.slack_bot_token, timeout=int(settings.timeout_slack_s))
    return _client


@dataclass(frozen=True)
class SlackResult:
    ok: bool
    channel: str
    ts: str | None = None
    error: str | None = None


def send_message(channel: str | None, message: str) -> SlackResult:
    settings = get_settings()
    target = channel or settings.slack_default_channel

    if not settings.slack_bot_token:
        logger.warning("SLACK_BOT_TOKEN not set — skipping real send")
        return SlackResult(ok=False, channel=target, error="No bot token configured")

    cb = get_circuit_breaker("slack")
    if not cb.allow_request():
        logger.warning("Slack circuit breaker OPEN — skipping send")
        return SlackResult(ok=False, channel=target, error="Circuit breaker open: Slack temporarily unavailable")

    client = _get_client()

    try:
        resp = with_timeout(
            client.chat_postMessage, settings.timeout_slack_s, "slack",
            channel=target, text=message,
        )
        logger.info("Slack message sent to %s (ts=%s)", target, resp["ts"])
        cb.record_success()
        return SlackResult(ok=True, channel=target, ts=resp["ts"])
    except ConnectorTimeout:
        cb.record_failure()
        logger.error("Slack send timed out after %ss", settings.timeout_slack_s)
        return SlackResult(ok=False, channel=target, error=f"Timeout after {settings.timeout_slack_s}s")
    except SlackApiError as exc:
        cb.record_failure()
        error_msg = exc.response.get("error", str(exc))
        logger.error("Slack API error: %s", error_msg)
        return SlackResult(ok=False, channel=target, error=error_msg)
