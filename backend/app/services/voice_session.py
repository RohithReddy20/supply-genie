"""Real-time voice session manager bridging Twilio Media Streams ↔ Gemini Live API.

Architecture:
  Caller ←→ Twilio ←→ [Media Stream WS] ←→ Backend ←→ [Gemini Live WS]

Audio format conversion:
  Twilio → mulaw/8kHz/base64  →  PCM16/16kHz  → Gemini
  Gemini → PCM16/24kHz        →  mulaw/8kHz/base64  → Twilio
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from google import genai
from google.genai import types
from starlette.websockets import WebSocket

from app.config import get_settings
from app.services.connectors.audio_utils import (
    gemini_pcm_to_twilio_mulaw,
    twilio_mulaw_to_gemini_pcm,
)

logger = logging.getLogger("backend.voice_session")


# ── Gemini Live API config ───────────────────────────────────────────────

VOICE_SYSTEM_PROMPT = """\
You are a Supply Chain Coordinator AI worker for HappyRobot, speaking on a \
phone call. You take actions to resolve supply chain incidents — shipment \
delays and worker absences.

How you work:
- When the caller asks you to do something (call production, update a PO, \
  send a Slack notification, etc.), you DO IT immediately using the \
  execute_command tool. Say "On it" and then report the result briefly.
- After completing an action, proactively suggest the next best step.
- Customer-facing actions (like emailing a customer) require human approval. \
  Explain that the action needs operator approval on the dashboard.
- You can look up incident status and shipment data when asked.
- If you are unsure or the request is risky, say you will escalate to a \
  human operator.

Tone: Brief, direct, conversational. Speak naturally for a phone call — \
short sentences, no markdown or bullet points. Be warm but efficient.\
"""

LIVE_TOOL_DECLARATIONS: list[dict] = [
    {
        "function_declarations": [
            {
                "name": "get_incident_status",
                "description": "Get the current status of the active incident including all action runs.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "incident_id": {
                            "type": "STRING",
                            "description": "The UUID of the incident to look up.",
                        },
                    },
                    "required": ["incident_id"],
                },
            },
            {
                "name": "list_active_shipments",
                "description": "List active shipments, optionally filtered by PO number.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "po_number": {
                            "type": "STRING",
                            "description": "Optional PO number to filter by.",
                        },
                    },
                },
            },
            {
                "name": "execute_command",
                "description": (
                    "Execute an action for the current incident. Available commands: "
                    "call_production, call_contractor, update_po, email_customer, "
                    "slack_notify, notify_manager, escalate_ticket, update_labor. "
                    "email_customer requires human approval and will be queued."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "command": {
                            "type": "STRING",
                            "description": "The action command to execute.",
                        },
                        "reason": {
                            "type": "STRING",
                            "description": "Brief reason or context for the action.",
                        },
                    },
                    "required": ["command"],
                },
            },
        ],
    },
]

GEMINI_LIVE_MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"


# ── Active session registry ─────────────────────────────────────────────

_active_sessions: dict[str, VoicePipeline] = {}


def get_active_session(stream_sid: str) -> VoicePipeline | None:
    return _active_sessions.get(stream_sid)


# ── Voice pipeline ──────────────────────────────────────────────────────


class VoicePipeline:
    """Manages a single real-time voice session between Twilio and Gemini."""

    def __init__(
        self,
        twilio_ws: WebSocket,
        *,
        call_sid: str,
        incident_id: str | None = None,
        greeting: str = "",
    ) -> None:
        self.twilio_ws = twilio_ws
        self.call_sid = call_sid
        self.stream_sid: str | None = None
        self.incident_id = incident_id
        self.greeting = greeting
        self.correlation_id = str(uuid4())

        # Transcript accumulator
        self.transcript: list[dict] = []

        # Gemini session (set in run())
        self._gemini_session = None
        self._stopped = False
        self._stream_ready = asyncio.Event()  # set when Twilio stream starts

        # Tool execution imports (lazy to avoid circular)
        self._db_session = None

    async def run(self) -> None:
        """Main loop: bridge Twilio WS ↔ Gemini Live API session."""
        settings = get_settings()
        client = genai.Client(api_key=settings.vertex_ai_key)

        config: dict = {
            "response_modalities": ["AUDIO"],
            "system_instruction": self._build_system_instruction(),
            "tools": LIVE_TOOL_DECLARATIONS,
            "speech_config": {
                "voice_config": {
                    "prebuilt_voice_config": {
                        "voice_name": "Kore",
                    },
                },
            },
            "input_audio_transcription": {},
            "output_audio_transcription": {},
        }

        try:
            async with client.aio.live.connect(
                model=GEMINI_LIVE_MODEL, config=config
            ) as session:
                self._gemini_session = session
                logger.info(
                    "Gemini Live session started (call_sid=%s, correlation=%s)",
                    self.call_sid,
                    self.correlation_id,
                )

                await asyncio.gather(
                    self._receive_from_twilio(session),
                    self._receive_from_gemini(session),
                    self._send_opening_prompt(session),
                )
        except Exception:
            logger.exception("Voice pipeline error (call_sid=%s)", self.call_sid)
        finally:
            self._stopped = True
            if self.stream_sid and self.stream_sid in _active_sessions:
                del _active_sessions[self.stream_sid]
            logger.info("Voice pipeline ended (call_sid=%s)", self.call_sid)

    def _build_system_instruction(self) -> str:
        parts = [VOICE_SYSTEM_PROMPT]
        if self.incident_id:
            parts.append(f"\n\nActive incident ID: {self.incident_id}")
        return "\n".join(parts)

    # ── Opening prompt ────────────────────────────────────────────────────

    async def _send_opening_prompt(self, session) -> None:
        """Wait for Twilio stream to be ready, then prompt Gemini to speak first."""
        await self._stream_ready.wait()
        # Small delay to ensure audio pipe is fully established
        await asyncio.sleep(0.5)

        if self.greeting:
            prompt = f"[System: Greet the caller. Context: {self.greeting}]"
        else:
            prompt = (
                "[System: You just answered a phone call. Introduce yourself briefly as "
                "the Supply Chain Coordinator AI assistant and ask how you can help.]"
            )

        logger.info("Sending opening prompt to Gemini")
        await session.send_client_content(
            turns=types.Content(
                role="user",
                parts=[types.Part(text=prompt)],
            ),
            turn_complete=True,
        )

    # ── Twilio → Gemini ──────────────────────────────────────────────────

    async def _receive_from_twilio(self, session) -> None:
        """Read Twilio Media Stream messages and forward audio to Gemini."""
        try:
            while not self._stopped:
                raw = await self.twilio_ws.receive_text()
                msg = json.loads(raw)
                event = msg.get("event")

                if event == "start":
                    self.stream_sid = msg["start"]["streamSid"]
                    _active_sessions[self.stream_sid] = self
                    self._stream_ready.set()
                    logger.info("Twilio stream started: %s", self.stream_sid)

                elif event == "media":
                    payload_b64 = msg["media"]["payload"]
                    pcm_16k = twilio_mulaw_to_gemini_pcm(payload_b64)
                    await session.send_realtime_input(
                        audio=types.Blob(
                            data=pcm_16k,
                            mime_type="audio/pcm;rate=16000",
                        )
                    )

                elif event == "stop":
                    logger.info("Twilio stream stopped: %s", self.stream_sid)
                    self._stopped = True
                    break

        except Exception:
            if not self._stopped:
                logger.exception("Error receiving from Twilio")
            self._stopped = True

    # ── Gemini → Twilio ──────────────────────────────────────────────────

    async def _receive_from_gemini(self, session) -> None:
        """Read Gemini Live responses and forward audio back to Twilio."""
        try:
            while not self._stopped:
                async for response in session.receive():
                    if self._stopped:
                        break

                    # Handle audio output
                    if response.server_content:
                        sc = response.server_content

                        # Barge-in: caller interrupted
                        if getattr(sc, "interrupted", False):
                            logger.info("Barge-in detected — clearing Twilio buffer")
                            await self._send_clear_to_twilio()

                        # Audio chunks from model
                        if sc.model_turn:
                            for part in sc.model_turn.parts:
                                if part.inline_data and part.inline_data.data:
                                    await self._send_audio_to_twilio(
                                        part.inline_data.data
                                    )

                        # Transcription capture
                        if sc.input_transcription and sc.input_transcription.text:
                            self.transcript.append({
                                "role": "caller",
                                "content": sc.input_transcription.text,
                                "ts": datetime.now(timezone.utc).isoformat(),
                            })

                        if sc.output_transcription and sc.output_transcription.text:
                            self.transcript.append({
                                "role": "assistant",
                                "content": sc.output_transcription.text,
                                "ts": datetime.now(timezone.utc).isoformat(),
                            })

                    # Handle tool calls
                    if response.tool_call:
                        await self._handle_tool_calls(session, response.tool_call)

        except Exception:
            if not self._stopped:
                logger.exception("Error receiving from Gemini")
            self._stopped = True

    async def _handle_tool_calls(self, session, tool_call) -> None:
        """Execute tool calls from Gemini and send responses back."""
        function_responses = []

        for fc in tool_call.function_calls:
            fn_name = fc.name
            fn_args = dict(fc.args) if fc.args else {}
            logger.info("Voice tool call: %s(%s)", fn_name, fn_args)

            result = self._execute_tool(fn_name, fn_args)

            self.transcript.append({
                "role": "system",
                "content": f"Tool call: {fn_name}({fn_args}) → {result[:200]}",
                "ts": datetime.now(timezone.utc).isoformat(),
            })

            function_responses.append(
                types.FunctionResponse(
                    name=fn_name,
                    id=fc.id,
                    response={"result": result},
                )
            )

        await session.send_tool_response(function_responses=function_responses)

    def _execute_tool(self, name: str, args: dict) -> str:
        """Execute a tool synchronously (DB operations via thread-local session)."""
        from app.database import SessionLocal
        from app.models import (
            ActionRun,
            ActionStatus,
            ActionType,
            Approval,
            ApprovalStatus,
            Incident,
            Shipment,
        )
        from app.services.action_executor import execute_pending_actions
        from app.services.chat import (
            APPROVAL_REQUIRED_ACTIONS,
            COMMAND_TO_ACTION,
            ACTION_LABELS,
        )

        db = SessionLocal()
        try:
            if name == "get_incident_status":
                incident_id = args.get("incident_id") or self.incident_id
                if not incident_id:
                    return "No incident ID available."
                incident = db.query(Incident).filter(Incident.id == incident_id).first()
                if not incident:
                    return f"Incident {incident_id} not found."
                lines = [
                    f"Incident {incident.id}: {incident.type.value}, status={incident.status.value}",
                ]
                for a in incident.actions:
                    line = f"  {a.action_type.value}: {a.status.value}"
                    if a.completed_at:
                        line += f" (done {a.completed_at.strftime('%H:%M')})"
                    if a.error_message:
                        line += f" [error: {a.error_message}]"
                    lines.append(line)
                return "\n".join(lines)

            elif name == "list_active_shipments":
                po = args.get("po_number")
                q = db.query(Shipment)
                if po:
                    q = q.filter(Shipment.po_number == po)
                ships = q.limit(10).all()
                if not ships:
                    return "No shipments found."
                return "\n".join(
                    f"PO:{s.po_number} status={s.status.value} eta={s.current_eta.strftime('%Y-%m-%d')}"
                    for s in ships
                )

            elif name == "execute_command":
                command = args.get("command", "")
                action_type = COMMAND_TO_ACTION.get(command)
                if not action_type:
                    return f"Unknown command: {command}"

                incident_id = self.incident_id
                if not incident_id:
                    return "No active incident for this call."

                incident = db.query(Incident).filter(Incident.id == incident_id).first()
                if not incident:
                    return f"Incident {incident_id} not found."

                requires_approval = action_type in APPROVAL_REQUIRED_ACTIONS
                label = ACTION_LABELS.get(command, command)

                if requires_approval:
                    # Create approval-gated action
                    max_seq = max((a.sequence for a in incident.actions), default=0)
                    new_action = ActionRun(
                        incident_id=incident.id,
                        action_type=action_type,
                        status=ActionStatus.needs_approval,
                        sequence=max_seq + 1,
                    )
                    db.add(new_action)
                    db.flush()
                    approval = Approval(
                        action_run_id=new_action.id,
                        incident_id=incident.id,
                        status=ApprovalStatus.pending,
                    )
                    db.add(approval)
                    db.commit()
                    return f"'{label}' requires human approval. Added to action timeline for review."

                # Check existing
                existing = next(
                    (a for a in incident.actions if a.action_type == action_type),
                    None,
                )
                if existing and existing.status == ActionStatus.completed:
                    return f"'{label}' already completed."

                if existing and existing.status in (ActionStatus.pending, ActionStatus.failed):
                    if existing.status == ActionStatus.failed:
                        existing.retry_count += 1
                    existing.status = ActionStatus.pending
                    existing.error_message = None
                    db.commit()
                elif not existing:
                    max_seq = max((a.sequence for a in incident.actions), default=0)
                    new_action = ActionRun(
                        incident_id=incident.id,
                        action_type=action_type,
                        status=ActionStatus.pending,
                        sequence=max_seq + 1,
                    )
                    db.add(new_action)
                    db.commit()

                db.refresh(incident)
                executed = execute_pending_actions(db, incident)
                run = next((a for a in executed if a.action_type == action_type), None)

                if run and run.status == ActionStatus.completed:
                    return f"Done. '{label}' completed successfully."
                elif run:
                    return f"'{label}' failed: {run.error_message or 'unknown error'}"
                return f"'{label}' could not be executed right now."

            return f"Unknown tool: {name}"
        finally:
            db.close()

    # ── Twilio outbound helpers ──────────────────────────────────────────

    async def _send_audio_to_twilio(self, pcm_24k: bytes) -> None:
        """Convert Gemini PCM24k audio to mulaw and send to Twilio."""
        if not self.stream_sid or self._stopped:
            return
        payload_b64 = gemini_pcm_to_twilio_mulaw(pcm_24k)
        msg = {
            "event": "media",
            "streamSid": self.stream_sid,
            "media": {"payload": payload_b64},
        }
        await self.twilio_ws.send_json(msg)

    async def _send_clear_to_twilio(self) -> None:
        """Send clear event to interrupt Twilio's audio buffer (barge-in)."""
        if not self.stream_sid or self._stopped:
            return
        msg = {"event": "clear", "streamSid": self.stream_sid}
        await self.twilio_ws.send_json(msg)
