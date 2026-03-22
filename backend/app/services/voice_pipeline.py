"""Pipecat-based real-time voice pipeline bridging Twilio Media Streams ↔ Gemini Live API.

Architecture:
  Caller ←→ Twilio ←→ [Media Stream WS] ←→ Pipecat Pipeline ←→ Gemini Live S2S

Pipecat handles:
  - Audio format conversion (mu-law ↔ PCM via TwilioFrameSerializer)
  - VAD and turn management
  - Gemini Live session lifecycle
  - Barge-in / interruption handling
  - Graceful call termination via EndTaskFrame + auto_hang_up
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from time import monotonic
from typing import Any
from uuid import uuid4

from pipecat.frames.frames import (
    BotInterruptionFrame,
    BotStoppedSpeakingFrame,
    EndTaskFrame,
    LLMRunFrame,
    StartInterruptionFrame,
    TTSTextFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.google.gemini_live import GeminiLiveLLMService
from pipecat.services.google.gemini_live.llm import GeminiVADParams
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from starlette.websockets import WebSocket

from app.config import get_settings
from app.services.voice_prompts import build_system_instruction
from app.services.voice_tools import TOOL_DECLARATIONS, execute_tool

logger = logging.getLogger("backend.voice_pipeline")

# ── Active session registry ─────────────────────────────────────────────

_active_sessions: dict[str, "VoicePipelineSession"] = {}


def get_active_sessions() -> dict[str, "VoicePipelineSession"]:
    """Return the active session registry (used by shutdown handler)."""
    return _active_sessions


# ── VAD sensitivity helpers ─────────────────────────────────────────────

_VALID_START = {"START_SENSITIVITY_UNSPECIFIED", "START_SENSITIVITY_HIGH", "START_SENSITIVITY_LOW"}
_VALID_END = {"END_SENSITIVITY_UNSPECIFIED", "END_SENSITIVITY_HIGH", "END_SENSITIVITY_LOW"}


def _normalize_sensitivity(value: str, *, is_start: bool) -> str:
    normalized = (value or "").strip().upper()
    if is_start:
        return normalized if normalized in _VALID_START else "START_SENSITIVITY_HIGH"
    return normalized if normalized in _VALID_END else "END_SENSITIVITY_HIGH"


# ── Call lifecycle manager ──────────────────────────────────────────────


class CallLifecycleManager:
    """Tracks call-progress objectives and orchestrates graceful shutdown.

    Replaces the previous nested-closure approach with a proper class that
    owns all call-state transitions and can be tested independently.
    """

    def __init__(
        self,
        call_sid: str,
        incident_id: str | None,
    ) -> None:
        self.call_sid = call_sid
        self.incident_id = incident_id
        self._task: PipelineTask | None = None
        self._progress = {
            "cause_confirmed": False,
            "eta_obtained": False,
            "mitigation_obtained": False,
            "risk_assessed": False,
        }
        self._ready_to_close = False  # Set when objectives met + ready_to_close=true
        self._closing = False
        self._closed = False
        self._close_timeout_task: asyncio.Task | None = None
        self.transcript: list[dict[str, str]] = []

    def bind_task(self, task: PipelineTask) -> None:
        """Bind the pipeline task after construction (needed because the task
        requires the pipeline which requires tools that reference this manager)."""
        self._task = task

    @property
    def is_ready_to_close(self) -> bool:
        return all(self._progress.values())

    def update_progress(self, **kwargs: bool) -> str:
        """Update objective flags. Returns a human-readable progress string."""
        for key in self._progress:
            if key in kwargs and isinstance(kwargs[key], bool):
                self._progress[key] = self._progress[key] or kwargs[key]

        return (
            f"cause={self._progress['cause_confirmed']}, "
            f"eta={self._progress['eta_obtained']}, "
            f"mitigation={self._progress['mitigation_obtained']}, "
            f"risk={self._progress['risk_assessed']}"
        )

    async def begin_graceful_close(self) -> None:
        """Start the graceful close sequence.

        Pushes EndTaskFrame which tells Pipecat to finish current speech,
        then shut down the pipeline cleanly. TwilioFrameSerializer's
        auto_hang_up will terminate the Twilio call after EndFrame completes.
        """
        if self._closed or self._closing:
            return
        self._closing = True
        logger.info("Beginning graceful close (call_sid=%s)", self.call_sid)

        if self._task:
            # EndTaskFrame flows upstream → triggers EndFrame downstream →
            # pipeline drains current utterance → transport closes → auto hang-up
            await self._task.queue_frames([EndTaskFrame()])

        # Safety backstop: if pipeline doesn't shut down in time, force cancel
        settings = get_settings()
        self._close_timeout_task = asyncio.create_task(
            self._close_safety_timeout(settings.voice_graceful_close_timeout_s)
        )

    async def _close_safety_timeout(self, timeout_s: float) -> None:
        """Force-cancel the pipeline if graceful close takes too long."""
        await asyncio.sleep(timeout_s)
        if not self._closed and self._task:
            logger.warning(
                "Graceful close timed out after %.0fs, force-cancelling (call_sid=%s)",
                timeout_s,
                self.call_sid,
            )
            await self._task.cancel()

    def mark_closed(self) -> None:
        """Mark the call as fully closed. Cancel safety timeout if running."""
        self._closed = True
        if self._close_timeout_task and not self._close_timeout_task.done():
            self._close_timeout_task.cancel()

    def append_transcript(self, role: str, content: str) -> None:
        """Append an entry to the transcript log."""
        self.transcript.append({
            "role": role,
            "content": content,
            "ts": datetime.now(timezone.utc).isoformat(),
        })


# ── Barge-in processor ──────────────────────────────────────────────────


class TwilioBargeInProcessor(FrameProcessor):
    """Sends Twilio clear-buffer events on interruptions and detects bot stopped speaking."""

    def __init__(
        self,
        on_interrupt,
        on_bot_stopped_speaking=None,
    ) -> None:
        super().__init__()
        self._on_interrupt = on_interrupt
        self._on_bot_stopped_speaking = on_bot_stopped_speaking
        self._last_interrupt_ts = 0.0

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if direction is FrameDirection.DOWNSTREAM:
            if isinstance(frame, (UserStartedSpeakingFrame, StartInterruptionFrame, BotInterruptionFrame)):
                now = monotonic()
                if now - self._last_interrupt_ts >= 0.15:
                    self._last_interrupt_ts = now
                    asyncio.create_task(self._on_interrupt(frame.__class__.__name__))
            elif self._on_bot_stopped_speaking and isinstance(frame, BotStoppedSpeakingFrame):
                asyncio.create_task(self._on_bot_stopped_speaking())

        await self.push_frame(frame, direction)


class TranscriptCollector(FrameProcessor):
    """Captures speech transcriptions flowing through the pipeline.

    - TranscriptionFrame (user speech) flows upstream from Gemini Live.
    - TTSTextFrame (bot speech transcription) flows downstream.
    """

    def __init__(self, lifecycle: CallLifecycleManager) -> None:
        super().__init__()
        self._lifecycle = lifecycle

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            text = (frame.text or "").strip()
            if text:
                self._lifecycle.append_transcript("caller", text)
        elif isinstance(frame, TTSTextFrame):
            text = (frame.text or "").strip()
            if text:
                self._lifecycle.append_transcript("assistant", text)

        await self.push_frame(frame, direction)


# ── Voice pipeline session ──────────────────────────────────────────────


class VoicePipelineSession:
    """Manages a single Pipecat voice session between Twilio and Gemini Live.

    Lifecycle:
      1. Accept WebSocket, wait for Twilio stream start event
      2. Build system instruction from incident context
      3. Create Pipecat pipeline (transport → aggregator → barge-in → LLM → output)
      4. Run pipeline until completion or disconnection
      5. Caller (router) persists transcript and runs post-call summary
    """

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
        self._lifecycle: CallLifecycleManager | None = None
        self._last_clear_ts = 0.0

    @property
    def transcript(self) -> list[dict[str, str]]:
        """Proxy to lifecycle manager's transcript for backward compatibility."""
        if self._lifecycle:
            return self._lifecycle.transcript
        return []

    async def run(self) -> None:
        """Run the full Pipecat pipeline for this voice session."""
        settings = get_settings()

        # ── Step 1: Wait for Twilio stream start ────────────────────────
        stream_sid, call_sid_real, custom_params = await self._wait_for_stream_start()
        self.stream_sid = stream_sid
        if call_sid_real:
            self.call_sid = call_sid_real
        if custom_params.get("incident_id") and not self.incident_id:
            self.incident_id = custom_params["incident_id"]
            logger.info("Got incident_id from customParameters: %s", self.incident_id)
        if custom_params.get("greeting") and not self.greeting:
            self.greeting = custom_params["greeting"]

        # Register in active sessions
        _active_sessions[self.call_sid] = self

        # ── Step 2: Build system instruction ────────────────────────────
        system_instruction = build_system_instruction(self.incident_id, self.call_sid)
        logger.info(
            "Voice pipeline starting (call_sid=%s, incident_id=%s)",
            self.call_sid,
            self.incident_id,
        )

        # ── Step 3: Create lifecycle manager ────────────────────────────
        lifecycle = CallLifecycleManager(
            call_sid=self.call_sid,
            incident_id=self.incident_id,
        )
        self._lifecycle = lifecycle

        # ── Step 4: Set up Pipecat transport ────────────────────────────
        serializer = TwilioFrameSerializer(
            stream_sid=stream_sid,
            call_sid=self.call_sid,
            account_sid=settings.twilio_account_sid,
            auth_token=settings.twilio_auth_token,
        )

        transport = FastAPIWebsocketTransport(
            websocket=self.twilio_ws,
            params=FastAPIWebsocketParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                add_wav_header=False,
                serializer=serializer,
            ),
        )

        # ── Step 5: Set up Gemini Live LLM ──────────────────────────────
        vad_start = _normalize_sensitivity(settings.voice_vad_start_sensitivity, is_start=True)
        vad_end = _normalize_sensitivity(settings.voice_vad_end_sensitivity, is_start=False)

        llm = GeminiLiveLLMService(
            api_key=settings.vertex_ai_key,
            system_instruction=system_instruction,
            tools=TOOL_DECLARATIONS,
            settings=GeminiLiveLLMService.Settings(
                model=f"models/{settings.gemini_live_model}",
                voice="Kore",
                vad=GeminiVADParams(
                    start_sensitivity=vad_start,
                    end_sensitivity=vad_end,
                    prefix_padding_ms=settings.voice_vad_prefix_padding_ms,
                    silence_duration_ms=settings.voice_vad_silence_duration_ms,
                ),
            ),
        )

        # ── Step 6: Register tool handlers ──────────────────────────────
        self._register_tools(llm, lifecycle)

        # ── Step 6b: Set up transcript collector ────────────────────────
        # Captures TranscriptionFrame (user speech) and TTSTextFrame (bot
        # speech transcription) as they flow through the pipeline.
        transcript_collector = TranscriptCollector(lifecycle)

        # ── Step 7: Set up barge-in processor ───────────────────────────
        async def on_bot_stopped_speaking() -> None:
            # If the bot finished speaking after ready_to_close was set,
            # automatically begin graceful close — don't wait for LLM to call end_call
            if lifecycle._ready_to_close and not lifecycle._closing and not lifecycle._closed:
                logger.info(
                    "Bot finished speaking after ready_to_close — auto-initiating graceful close (call_sid=%s)",
                    self.call_sid,
                )
                await lifecycle.begin_graceful_close()
            elif lifecycle._closing and not lifecycle._closed:
                lifecycle.mark_closed()
                logger.info("Bot finished final utterance — call complete (call_sid=%s)", self.call_sid)

        barge_in = TwilioBargeInProcessor(
            on_interrupt=self._clear_twilio_buffer,
            on_bot_stopped_speaking=on_bot_stopped_speaking,
        )

        # ── Step 8: Assemble and run pipeline ───────────────────────────
        context = LLMContext()
        user_agg, assistant_agg = LLMContextAggregatorPair(context)

        pipeline = Pipeline([
            transport.input(),
            user_agg,
            barge_in,
            llm,
            transcript_collector,
            transport.output(),
            assistant_agg,
        ])

        task = PipelineTask(
            pipeline,
            params=PipelineParams(
                audio_in_sample_rate=8000,
                audio_out_sample_rate=8000,
                enable_metrics=True,
                enable_usage_metrics=True,
            ),
        )

        # Bind task to lifecycle manager (needed for EndTaskFrame)
        lifecycle.bind_task(task)

        @transport.event_handler("on_client_connected")
        async def on_client_connected(transport, client):
            logger.info("Twilio client connected (call_sid=%s)", self.call_sid)
            if self.incident_id:
                context.add_message({
                    "role": "user",
                    "content": (
                        "[System: You placed this outbound call. Introduce yourself, "
                        "state why you are calling, and begin working through your objectives. "
                        "Do NOT ask 'how can I help' — you initiated this call.]"
                    ),
                })
            else:
                context.add_message({
                    "role": "user",
                    "content": (
                        "[System: You just answered a phone call. Introduce yourself "
                        "briefly as the Supply Chain Coordinator AI assistant and ask "
                        "how you can help.]"
                    ),
                })
            await task.queue_frames([LLMRunFrame()])

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, client):
            logger.info("Twilio stream disconnected (call_sid=%s)", self.call_sid)
            lifecycle.mark_closed()
            await task.cancel()

        # ── Run pipeline ────────────────────────────────────────────────
        runner = PipelineRunner(handle_sigint=False)
        logger.info(
            "Pipecat pipeline started (call_sid=%s, correlation=%s)",
            self.call_sid,
            self.correlation_id,
        )

        try:
            await runner.run(task)
        finally:
            lifecycle.mark_closed()
            # Remove from active sessions
            _active_sessions.pop(self.call_sid, None)

        logger.info("Pipecat pipeline ended (call_sid=%s)", self.call_sid)

    # ── Tool registration ───────────────────────────────────────────────

    def _register_tools(
        self,
        llm: GeminiLiveLLMService,
        lifecycle: CallLifecycleManager,
    ) -> None:
        """Register all Gemini function-call handlers on the LLM service."""

        async def _handle_tool(params: FunctionCallParams) -> None:
            fn_name = params.function_name
            fn_args = dict(params.arguments)
            logger.info("Voice tool call: %s(%s)", fn_name, fn_args)

            # ── end_call ────────────────────────────────────────────────
            if fn_name == "end_call":
                await params.result_callback({"result": "Acknowledged. Wrapping up now."})
                lifecycle.append_transcript(
                    "system",
                    f"Tool call: end_call({{}}) → call closure initiated",
                )
                await lifecycle.begin_graceful_close()
                return

            # ── update_call_progress ────────────────────────────────────
            if fn_name == "update_call_progress":
                progress_text = lifecycle.update_progress(**fn_args)
                lifecycle.append_transcript(
                    "system",
                    f"Tool call: {fn_name}({fn_args}) → Progress: {progress_text}",
                )

                ready_to_close = bool(fn_args.get("ready_to_close", False))

                if ready_to_close and lifecycle.is_ready_to_close:
                    logger.info(
                        "All objectives met, ready_to_close=true — will auto-close after closing statement (call_sid=%s)",
                        lifecycle.call_sid,
                    )
                    lifecycle._ready_to_close = True
                    result_text = (
                        "All objectives complete. Give a brief closing summary "
                        "to the other party. The call will end automatically "
                        "after you finish speaking."
                    )
                elif lifecycle.is_ready_to_close and not ready_to_close:
                    # All flags are true but LLM didn't set ready_to_close
                    result_text = (
                        f"Progress: {progress_text}. "
                        "All four objectives are now confirmed! "
                        "Call update_call_progress with ready_to_close=true NOW."
                    )
                else:
                    # Tell the LLM exactly what's still missing
                    missing = [
                        k for k, v in lifecycle._progress.items() if not v
                    ]
                    missing_labels = {
                        "cause_confirmed": "root cause",
                        "eta_obtained": "updated ETA",
                        "mitigation_obtained": "mitigation steps",
                        "risk_assessed": "risk assessment",
                    }
                    missing_text = ", ".join(
                        missing_labels.get(m, m) for m in missing
                    )
                    result_text = (
                        f"Progress: {progress_text}. "
                        f"Still needed: {missing_text}. "
                        f"Ask about these in your NEXT question."
                    )

                await params.result_callback({"result": result_text})
                return

            # ── All other tools (DB-backed) ─────────────────────────────
            result = await asyncio.to_thread(
                execute_tool, fn_name, fn_args, lifecycle.incident_id, lifecycle.call_sid
            )
            lifecycle.append_transcript(
                "system",
                f"Tool call: {fn_name}({fn_args}) → {result[:200]}",
            )
            await params.result_callback({"result": result})

        # Register each tool name to the shared handler
        for tool_name in (
            "get_incident_status",
            "list_active_shipments",
            "execute_command",
            "update_call_progress",
            "end_call",
        ):
            llm.register_function(tool_name, _handle_tool)

    # ── Twilio helpers ──────────────────────────────────────────────────

    async def _wait_for_stream_start(self) -> tuple[str, str | None, dict]:
        """Read Twilio WS messages until the 'start' event arrives.

        Returns (stream_sid, call_sid, custom_parameters).
        """
        for _ in range(100):
            raw = await self.twilio_ws.receive_text()
            msg = json.loads(raw)
            if msg.get("event") == "start":
                start = msg["start"]
                return start["streamSid"], start.get("callSid"), start.get("customParameters", {})
            elif msg.get("event") == "connected":
                continue
        raise RuntimeError("Timed out waiting for Twilio stream start event")

    async def _clear_twilio_buffer(self, source: str) -> None:
        """Send a clear event to Twilio to flush queued audio on barge-in."""
        if not self.stream_sid:
            return
        now = monotonic()
        if now - self._last_clear_ts < 0.5:
            return
        self._last_clear_ts = now
        try:
            await self.twilio_ws.send_json({
                "event": "clear",
                "streamSid": self.stream_sid,
            })
            logger.debug("Sent Twilio clear on interruption (%s, call_sid=%s)", source, self.call_sid)
        except Exception:
            logger.exception("Failed sending Twilio clear on interruption")
