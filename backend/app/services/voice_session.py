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
from collections import deque
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, cast
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

LIVE_TOOL_DECLARATIONS: list[dict[str, Any]] = [
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

_DEFAULT_START_SENSITIVITY = "START_SENSITIVITY_HIGH"
_DEFAULT_END_SENSITIVITY = "END_SENSITIVITY_HIGH"
_VALID_START_SENSITIVITY = {
    "START_SENSITIVITY_UNSPECIFIED",
    "START_SENSITIVITY_HIGH",
    "START_SENSITIVITY_LOW",
}
_VALID_END_SENSITIVITY = {
    "END_SENSITIVITY_UNSPECIFIED",
    "END_SENSITIVITY_HIGH",
    "END_SENSITIVITY_LOW",
}


class _LatencyWindow:
    """Keeps a rolling window of latency samples for quick p95 diagnostics."""

    def __init__(self, max_samples: int = 240) -> None:
        self.samples: deque[float] = deque(maxlen=max_samples)

    def add(self, duration_ms: float) -> None:
        self.samples.append(duration_ms)

    def summary(self) -> tuple[float, float, float, int]:
        count = len(self.samples)
        if count == 0:
            return (0.0, 0.0, 0.0, 0)
        ordered = sorted(self.samples)
        p95_index = min(count - 1, max(0, int(count * 0.95) - 1))
        avg = sum(self.samples) / count
        return (avg, ordered[p95_index], ordered[-1], count)


def _normalize_sensitivity(value: str, *, is_start: bool) -> str:
    normalized = (value or "").strip().upper()
    if is_start:
        return (
            normalized
            if normalized in _VALID_START_SENSITIVITY
            else _DEFAULT_START_SENSITIVITY
        )
    return (
        normalized if normalized in _VALID_END_SENSITIVITY else _DEFAULT_END_SENSITIVITY
    )


# ── Active session registry ─────────────────────────────────────────────

_active_sessions: dict[str, VoicePipeline] = {}


def get_active_session(stream_sid: str) -> VoicePipeline | None:
    return _active_sessions.get(stream_sid)


# ── Voice pipeline ──────────────────────────────────────────────────────


class VoicePipeline:
    """Manages a single real-time voice session between Twilio and Gemini."""

    LATENCY_SAMPLE_WINDOW = 240

    def __init__(
        self,
        twilio_ws: WebSocket,
        *,
        call_sid: str,
        incident_id: str | None = None,
        greeting: str = "",
    ) -> None:
        settings = get_settings()

        self.twilio_ws = twilio_ws
        self.call_sid = call_sid
        self.stream_sid: str | None = None
        self.incident_id = incident_id
        self.greeting = greeting
        self.correlation_id = str(uuid4())

        self._opening_prompt_delay_s = settings.voice_opening_prompt_delay_s
        self._audio_batch_bytes = 16000 * 2 * settings.voice_audio_batch_ms // 1000

        self._vad_start_sensitivity = _normalize_sensitivity(
            settings.voice_vad_start_sensitivity,
            is_start=True,
        )
        self._vad_end_sensitivity = _normalize_sensitivity(
            settings.voice_vad_end_sensitivity,
            is_start=False,
        )
        self._vad_prefix_padding_ms = settings.voice_vad_prefix_padding_ms
        self._vad_silence_duration_ms = settings.voice_vad_silence_duration_ms
        self._thinking_budget = settings.voice_thinking_budget

        # Transcript accumulator
        self.transcript: list[dict[str, str]] = []

        # Gemini session (set in run())
        self._gemini_session = None
        self._stopped = False
        self._stream_ready = asyncio.Event()  # set when Twilio stream starts
        self._last_caller_transcript_text: str | None = None
        self._pending_turn_started_at: float | None = None

        # Inbound audio queue (Twilio -> Gemini)
        self._audio_queue: asyncio.Queue[tuple[bytes, float]] = asyncio.Queue(
            maxsize=settings.voice_inbound_audio_queue_max
        )

        # Outbound audio queue (Gemini -> Twilio) for non-blocking sends
        self._twilio_out_queue: asyncio.Queue[tuple[bytes, float]] = asyncio.Queue(
            maxsize=settings.voice_outbound_audio_queue_max
        )

        self._dropped_inbound_audio = 0
        self._dropped_outbound_audio = 0
        self._stage_latency: dict[str, _LatencyWindow] = {}

    async def run(self) -> None:
        """Main loop: bridge Twilio WS ↔ Gemini Live API session.

        Supports reconnection on Gemini failure and enforces a session-level timeout.
        """
        settings = get_settings()
        self._session_started_at = perf_counter()
        reconnect_attempts = settings.voice_gemini_reconnect_attempts
        attempt = 0

        while not self._stopped and attempt <= reconnect_attempts:
            try:
                await self._run_session(settings, is_reconnect=(attempt > 0))
                break  # clean exit
            except Exception:
                attempt += 1
                if attempt > reconnect_attempts or self._stopped:
                    logger.exception(
                        "Voice pipeline error (call_sid=%s, attempt=%d/%d) — giving up",
                        self.call_sid, attempt, reconnect_attempts + 1,
                    )
                    break
                logger.warning(
                    "Gemini session failed (call_sid=%s, attempt=%d/%d) — reconnecting",
                    self.call_sid, attempt, reconnect_attempts + 1,
                )
                await asyncio.sleep(0.5)  # brief pause before reconnect

        self._stopped = True
        self._export_latency_summary()
        if self.stream_sid and self.stream_sid in _active_sessions:
            del _active_sessions[self.stream_sid]
        logger.info("Voice pipeline ended (call_sid=%s)", self.call_sid)

    async def _run_session(self, settings, *, is_reconnect: bool = False) -> None:
        client = genai.Client(api_key=settings.vertex_ai_key)

        config = cast(
            types.LiveConnectConfigOrDict,
            {
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
                "thinking_config": {
                    "thinking_budget": self._thinking_budget,
                },
                "realtime_input_config": {
                    "automatic_activity_detection": {
                        "start_of_speech_sensitivity": self._vad_start_sensitivity,
                        "end_of_speech_sensitivity": self._vad_end_sensitivity,
                        "prefix_padding_ms": self._vad_prefix_padding_ms,
                        "silence_duration_ms": self._vad_silence_duration_ms,
                    },
                    "activity_handling": "START_OF_ACTIVITY_INTERRUPTS",
                    "turn_coverage": "TURN_INCLUDES_ONLY_ACTIVITY",
                },
            },
        )

        async with client.aio.live.connect(
            model=settings.gemini_live_model, config=config
        ) as session:
            self._gemini_session = session
            logger.info(
                "Gemini Live session %s (call_sid=%s, correlation=%s)",
                "reconnected" if is_reconnect else "started",
                self.call_sid,
                self.correlation_id,
            )

            tasks = [
                self._receive_from_twilio(),
                self._send_queued_audio_to_gemini(session),
                self._receive_from_gemini(session),
                self._send_queued_audio_to_twilio(),
                self._session_timeout_watchdog(settings.voice_session_timeout_s),
            ]
            if not is_reconnect:
                tasks.append(self._send_opening_prompt(session))

            await asyncio.gather(*tasks)

    async def _session_timeout_watchdog(self, timeout_s: float) -> None:
        """Terminate session if it exceeds the configured timeout."""
        while not self._stopped:
            elapsed = perf_counter() - self._session_started_at
            if elapsed >= timeout_s:
                logger.warning(
                    "Voice session timed out after %.0fs (call_sid=%s)",
                    elapsed, self.call_sid,
                )
                self._stopped = True
                return
            await asyncio.sleep(5.0)

    def _export_latency_summary(self) -> None:
        """Log final latency stats for the session (useful for post-call diagnostics)."""
        if not self._stage_latency:
            return
        lines = [f"Voice session latency summary (call_sid={self.call_sid}):"]
        for stage, window in sorted(self._stage_latency.items()):
            avg, p95, mx, count = window.summary()
            if count > 0:
                lines.append(f"  {stage}: avg={avg:.1f}ms p95={p95:.1f}ms max={mx:.1f}ms n={count}")
        if self._dropped_inbound_audio > 0:
            lines.append(f"  inbound_audio_drops: {self._dropped_inbound_audio}")
        if self._dropped_outbound_audio > 0:
            lines.append(f"  outbound_audio_drops: {self._dropped_outbound_audio}")
        logger.info("\n".join(lines))

    def _build_system_instruction(self) -> str:
        parts = [VOICE_SYSTEM_PROMPT]
        if self.incident_id:
            parts.append(f"\n\nActive incident ID: {self.incident_id}")
        return "\n".join(parts)

    def _record_latency(self, stage: str, duration_ms: float) -> None:
        window = self._stage_latency.get(stage)
        if window is None:
            window = _LatencyWindow(max_samples=self.LATENCY_SAMPLE_WINDOW)
            self._stage_latency[stage] = window
        window.add(duration_ms)

    # ── Opening prompt ────────────────────────────────────────────────────

    async def _send_opening_prompt(self, session: Any) -> None:
        """Wait for Twilio stream to be ready, then prompt Gemini to speak first."""
        while not self._stopped and not self._stream_ready.is_set():
            await asyncio.sleep(0.05)
        if self._stopped:
            return

        # Small delay to ensure audio pipe is fully established
        await asyncio.sleep(self._opening_prompt_delay_s)

        if self.greeting:
            prompt = f"[System: Greet the caller. Context: {self.greeting}]"
        else:
            prompt = (
                "[System: You just answered a phone call. Introduce yourself briefly as "
                "the Supply Chain Coordinator AI assistant and ask how you can help.]"
            )

        started = perf_counter()
        await session.send_client_content(
            turns=types.Content(
                role="user",
                parts=[types.Part(text=prompt)],
            ),
            turn_complete=True,
        )
        elapsed_ms = (perf_counter() - started) * 1000.0
        self._record_latency("opening_prompt_send_ms", elapsed_ms)

    # ── Twilio → Gemini ──────────────────────────────────────────────────

    async def _receive_from_twilio(self) -> None:
        """Read Twilio Media Stream messages and forward audio to Gemini."""
        try:
            while not self._stopped:
                recv_started = perf_counter()
                raw = await self.twilio_ws.receive_text()
                self._record_latency(
                    "twilio_ws_receive_ms",
                    (perf_counter() - recv_started) * 1000.0,
                )

                msg = json.loads(raw)
                event = msg.get("event")

                if event == "start":
                    stream_sid = msg["start"]["streamSid"]
                    self.stream_sid = stream_sid
                    _active_sessions[stream_sid] = self
                    self._stream_ready.set()
                    logger.info("Twilio stream started: %s", self.stream_sid)

                elif event == "media":
                    payload_b64 = msg["media"]["payload"]
                    decode_started = perf_counter()
                    pcm_16k = twilio_mulaw_to_gemini_pcm(payload_b64)
                    self._record_latency(
                        "twilio_decode_ms",
                        (perf_counter() - decode_started) * 1000.0,
                    )
                    self._enqueue_inbound_audio(pcm_16k)

                elif event == "stop":
                    logger.info("Twilio stream stopped: %s", self.stream_sid)
                    self._stopped = True
                    break

        except Exception:
            if not self._stopped:
                logger.exception("Error receiving from Twilio")
            self._stopped = True

    async def _send_queued_audio_to_gemini(self, session: Any) -> None:
        """Drain queued inbound audio and send to Gemini without blocking Twilio reads."""
        try:
            while not self._stopped:
                try:
                    pcm_16k, enqueued_at = await asyncio.wait_for(
                        self._audio_queue.get(), timeout=0.1
                    )
                except asyncio.TimeoutError:
                    continue

                self._record_latency(
                    "inbound_queue_delay_ms",
                    (perf_counter() - enqueued_at) * 1000.0,
                )

                # Opportunistically batch already-queued chunks to lower WS overhead.
                batch = bytearray(pcm_16k)
                while len(batch) < self._audio_batch_bytes:
                    try:
                        queued_pcm_16k, _queued_at = self._audio_queue.get_nowait()
                        batch.extend(queued_pcm_16k)
                    except asyncio.QueueEmpty:
                        break

                send_started = perf_counter()
                await session.send_realtime_input(
                    audio=types.Blob(
                        data=bytes(batch),
                        mime_type="audio/pcm;rate=16000",
                    )
                )
                self._record_latency(
                    "gemini_send_realtime_input_ms",
                    (perf_counter() - send_started) * 1000.0,
                )
        except Exception:
            if not self._stopped:
                logger.exception("Error sending audio to Gemini")
            self._stopped = True

    def _enqueue_inbound_audio(self, pcm_16k: bytes) -> None:
        """Queue Twilio audio, dropping oldest frames on backpressure to stay realtime."""
        item = (pcm_16k, perf_counter())
        try:
            self._audio_queue.put_nowait(item)
        except asyncio.QueueFull:
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._audio_queue.put_nowait(item)
            except asyncio.QueueFull:
                return
            self._dropped_inbound_audio += 1
            if self._dropped_inbound_audio % 25 == 0:
                logger.warning(
                    "Dropped %d inbound audio chunks due to backpressure (call_sid=%s)",
                    self._dropped_inbound_audio,
                    self.call_sid,
                )

    # ── Gemini → Twilio ──────────────────────────────────────────────────

    async def _receive_from_gemini(self, session: Any) -> None:
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
                                    self._enqueue_outbound_audio(part.inline_data.data)

                        # Transcription capture
                        if sc.input_transcription and sc.input_transcription.text:
                            caller_text = sc.input_transcription.text.strip()
                            if caller_text and caller_text != self._last_caller_transcript_text:
                                self._last_caller_transcript_text = caller_text
                                self._pending_turn_started_at = perf_counter()

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

    async def _handle_tool_calls(self, session: Any, tool_call: Any) -> None:
        """Execute tool calls from Gemini and send responses back."""
        function_responses: list[types.FunctionResponse] = []

        for fc in tool_call.function_calls:
            fn_name = fc.name
            fn_args = dict(fc.args) if fc.args else {}
            logger.info("Voice tool call: %s(%s)", fn_name, fn_args)

            started = perf_counter()
            result = await asyncio.to_thread(self._execute_tool, fn_name, fn_args)
            self._record_latency("tool_exec_ms", (perf_counter() - started) * 1000.0)

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

    def _execute_tool(self, name: str, args: dict[str, Any]) -> str:
        """Execute a tool synchronously (DB operations via thread-local session)."""
        from app.database import SessionLocal
        from app.models import (
            ActionRun,
            ActionStatus,
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

    async def _send_queued_audio_to_twilio(self) -> None:
        """Drain queued model audio and send to Twilio without blocking Gemini reads."""
        try:
            while not self._stopped:
                try:
                    pcm_24k, enqueued_at = await asyncio.wait_for(
                        self._twilio_out_queue.get(), timeout=0.1
                    )
                except asyncio.TimeoutError:
                    continue

                self._record_latency(
                    "outbound_queue_delay_ms",
                    (perf_counter() - enqueued_at) * 1000.0,
                )

                await self._send_audio_to_twilio(pcm_24k)
        except Exception:
            if not self._stopped:
                logger.exception("Error sending audio to Twilio")
            self._stopped = True

    def _enqueue_outbound_audio(self, pcm_24k: bytes) -> None:
        """Queue Gemini audio, dropping oldest frames on backpressure to stay realtime."""
        item = (pcm_24k, perf_counter())
        try:
            self._twilio_out_queue.put_nowait(item)
        except asyncio.QueueFull:
            try:
                self._twilio_out_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._twilio_out_queue.put_nowait(item)
            except asyncio.QueueFull:
                return
            self._dropped_outbound_audio += 1
            if self._dropped_outbound_audio % 25 == 0:
                logger.warning(
                    "Dropped %d outbound audio chunks due to backpressure (call_sid=%s)",
                    self._dropped_outbound_audio,
                    self.call_sid,
                )

    async def _send_audio_to_twilio(self, pcm_24k: bytes) -> None:
        """Convert Gemini PCM24k audio to mulaw and send to Twilio."""
        if not self.stream_sid or self._stopped:
            return

        encode_started = perf_counter()
        payload_b64 = await asyncio.to_thread(gemini_pcm_to_twilio_mulaw, pcm_24k)
        self._record_latency("twilio_mulaw_encode_ms", (perf_counter() - encode_started) * 1000.0)

        msg: dict[str, Any] = {
            "event": "media",
            "streamSid": self.stream_sid,
            "media": {"payload": payload_b64},
        }

        send_started = perf_counter()
        await self.twilio_ws.send_json(msg)
        self._record_latency("twilio_ws_send_ms", (perf_counter() - send_started) * 1000.0)

        if self._pending_turn_started_at is not None:
            turn_latency_ms = (perf_counter() - self._pending_turn_started_at) * 1000.0
            self._record_latency("turn_input_to_first_audio_ms", turn_latency_ms)
            self._pending_turn_started_at = None

    async def _send_clear_to_twilio(self) -> None:
        """Send clear event to interrupt Twilio's audio buffer (barge-in)."""
        if not self.stream_sid or self._stopped:
            return
        msg = {"event": "clear", "streamSid": self.stream_sid}
        started = perf_counter()
        await self.twilio_ws.send_json(msg)
        self._record_latency("twilio_clear_send_ms", (perf_counter() - started) * 1000.0)
