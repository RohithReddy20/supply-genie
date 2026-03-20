from __future__ import annotations

import logging
from dataclasses import dataclass

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from app.config import get_settings

logger = logging.getLogger("backend.connectors.slack")


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

    client = WebClient(token=settings.slack_bot_token)

    try:
        resp = client.chat_postMessage(channel=target, text=message)
        logger.info("Slack message sent to %s (ts=%s)", target, resp["ts"])
        return SlackResult(ok=True, channel=target, ts=resp["ts"])
    except SlackApiError as exc:
        error_msg = exc.response.get("error", str(exc))
        logger.error("Slack API error: %s", error_msg)
        return SlackResult(ok=False, channel=target, error=error_msg)
