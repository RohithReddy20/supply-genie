"""Post-call summarization: extracts structured findings from voice transcripts."""
from __future__ import annotations

import json
import logging

from google import genai
from google.genai import types
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import VoiceSession
from app.services.connectors.slack import send_message as slack_send

logger = logging.getLogger("backend.call_summary")

SUMMARY_PROMPT = """\
You are analyzing a transcript from a supply chain coordination phone call. \
Extract the following structured information. Return ONLY valid JSON, no markdown.

{
  "call_outcome": "brief one-line summary of what happened on the call",
  "confirmed_root_cause": "the root cause confirmed by the other party, or null",
  "updated_eta": "any new ETA provided by the other party, or null",
  "action_items": ["list of follow-up actions discussed"],
  "escalation_needed": true/false,
  "escalation_reason": "why escalation is needed, or null",
  "cooperation_level": "cooperative / uncooperative / neutral",
  "key_findings": ["list of important facts learned during the call"]
}

Transcript:
"""


def summarize_and_notify(
    db: Session,
    voice_session_id,
    transcript: list[dict[str, str]],
    incident_id: str | None = None,
) -> dict | None:
    """Summarize a call transcript and send a Slack notification.

    Returns the structured summary dict, or None if summarization failed.
    """
    if not transcript:
        logger.info("No transcript to summarize for session %s", voice_session_id)
        return None

    # Build transcript text
    lines = []
    for entry in transcript:
        role = entry.get("role", "unknown")
        content = entry.get("content", "")
        if content.strip():
            lines.append(f"{role}: {content}")

    if not lines:
        return None

    transcript_text = "\n".join(lines)

    settings = get_settings()
    if not settings.vertex_ai_key:
        logger.warning("No Gemini API key — skipping call summarization")
        return None

    try:
        client = genai.Client(api_key=settings.vertex_ai_key)
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=SUMMARY_PROMPT + transcript_text,
            config=types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=1024,
            ),
        )

        raw_text = response.text.strip()
        # Strip markdown code fences if present
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[-1]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3].strip()

        summary = json.loads(raw_text)
    except (json.JSONDecodeError, Exception) as exc:
        logger.error("Failed to parse call summary: %s", exc)
        summary = {
            "call_outcome": "Summarization failed — review transcript manually",
            "error": str(exc),
        }

    # Store summary on the voice session
    vs = db.get(VoiceSession, voice_session_id)
    if vs:
        vs.summary = summary
        db.commit()
        logger.info("Stored call summary for session %s", voice_session_id)

    # Send Slack notification
    _send_summary_notification(summary, incident_id)

    return summary


def _send_summary_notification(summary: dict, incident_id: str | None) -> None:
    """Send a Slack message with the call summary."""
    outcome = summary.get("call_outcome", "No summary available")
    root_cause = summary.get("confirmed_root_cause")
    updated_eta = summary.get("updated_eta")
    escalation = summary.get("escalation_needed", False)
    cooperation = summary.get("cooperation_level", "unknown")
    action_items = summary.get("action_items", [])
    key_findings = summary.get("key_findings", [])

    lines = ["📞 *Post-Call Summary*"]
    if incident_id:
        lines.append(f"• Incident: `{incident_id}`")
    lines.append(f"• Outcome: {outcome}")
    if root_cause:
        lines.append(f"• Root Cause: {root_cause}")
    if updated_eta:
        lines.append(f"• Updated ETA: {updated_eta}")
    lines.append(f"• Cooperation: {cooperation}")

    if key_findings:
        lines.append("\n*Key Findings:*")
        for finding in key_findings:
            lines.append(f"  • {finding}")

    if action_items:
        lines.append("\n*Action Items:*")
        for item in action_items:
            lines.append(f"  • {item}")

    if escalation:
        reason = summary.get("escalation_reason", "")
        lines.append(f"\n🚨 *Escalation Needed*: {reason}")

    message = "\n".join(lines)
    result = slack_send(channel=None, message=message)
    if result.ok:
        logger.info("Call summary notification sent to Slack")
    else:
        logger.warning("Failed to send call summary to Slack: %s", result.error)
