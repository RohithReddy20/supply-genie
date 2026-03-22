"""Voice router: Twilio webhook + WebSocket endpoints for real-time voice."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, WebSocket
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from twilio.rest import Client as TwilioClient
from twilio.twiml.voice_response import Connect, VoiceResponse

from app.config import get_settings
from app.database import get_db
from app.models import Incident, TranscriptEvent, VoiceSession
from app.schemas import OutboundCallRequest, TranscriptEventOut, VoiceSessionOut
from app.services.voice_pipeline import VoicePipelineSession

logger = logging.getLogger("backend.voice_router")

router = APIRouter(prefix="/voice", tags=["voice"])


# ── Inbound call webhook ─────────────────────────────────────────────────


@router.api_route("/incoming", methods=["GET", "POST"])
async def handle_incoming_call(
    request: Request,
    incident_id: str | None = Query(None),
) -> Response:
    """Twilio calls this when an inbound call arrives.

    Returns TwiML that connects the call to our bidirectional Media Stream.
    """
    response = VoiceResponse()
    response.say(
        "Connecting you to the Supply Chain Coordinator.",
        voice="Google.en-US-Chirp3-HD-Aoede",
    )
    response.pause(length=1)

    host = request.url.hostname
    ws_url = f"wss://{host}/api/v1/voice/media-stream"

    connect = Connect()
    stream = connect.stream(url=ws_url)
    if incident_id:
        stream.parameter(name="incident_id", value=incident_id)
    response.append(connect)

    return Response(content=str(response), media_type="application/xml")


# ── Outbound call ────────────────────────────────────────────────────────


@router.post("/outbound", response_model=VoiceSessionOut)
def initiate_outbound_call(
    body: OutboundCallRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> VoiceSessionOut:
    """Initiate an outbound call that connects to the AI voice agent."""
    settings = get_settings()

    if settings.twilio_mock_mode:
        # Create a mock voice session
        vs = VoiceSession(
            call_sid=f"mock_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            direction="outbound",
            from_number=settings.twilio_from_number,
            to_number=body.to,
            incident_id=body.incident_id,
            status="mock",
        )
        db.add(vs)
        db.commit()
        db.refresh(vs)
        return VoiceSessionOut.model_validate(vs)

    host = request.url.hostname
    ws_url = f"wss://{host}/api/v1/voice/media-stream"

    twiml = VoiceResponse()
    connect = Connect()
    stream = connect.stream(url=ws_url)
    # Twilio <Stream> strips query params — use <Parameter> instead.
    # These arrive in the WebSocket "start" message under start.customParameters.
    if body.incident_id:
        stream.parameter(name="incident_id", value=str(body.incident_id))
    if body.greeting:
        stream.parameter(name="greeting", value=body.greeting)
    twiml.append(connect)

    client = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)
    call = client.calls.create(
        twiml=str(twiml),
        to=body.to,
        from_=settings.twilio_from_number,
    )

    vs = VoiceSession(
        call_sid=call.sid,
        direction="outbound",
        from_number=settings.twilio_from_number,
        to_number=body.to,
        incident_id=body.incident_id,
        status=call.status,
    )
    db.add(vs)
    db.commit()
    db.refresh(vs)

    logger.info("Outbound call initiated: %s → %s (sid=%s)", settings.twilio_from_number, body.to, call.sid)
    return VoiceSessionOut.model_validate(vs)


# ── Bidirectional Media Stream WebSocket ─────────────────────────────────


@router.websocket("/media-stream")
async def media_stream_websocket(
    websocket: WebSocket,
    incident_id: str | None = Query(None),
    greeting: str | None = Query(None),
) -> None:
    """Bidirectional WebSocket endpoint for Twilio Media Streams.

    Bridges Twilio audio ↔ Gemini Live API for real-time voice interaction.
    Twilio <Stream> drops query params, so incident_id/greeting may arrive
    via customParameters in the "start" event instead. We create the pipeline
    with query-param values first, then patch from customParameters when the
    start event arrives (handled inside VoicePipeline._receive_from_twilio).
    """
    await websocket.accept()
    print(f"\n>>> WEBSOCKET CONNECTED: query incident_id={incident_id!r}, greeting={greeting!r}\n", flush=True)

    pipeline = VoicePipelineSession(
        websocket,
        call_sid=f"ws_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        incident_id=incident_id,
        greeting=greeting or "",
    )

    try:
        await pipeline.run()
    finally:
        # Persist session and transcript
        _persist_session(pipeline)


def _persist_session(pipeline: VoicePipelineSession) -> None:
    """Save voice session and transcript events to the database."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        # Check if an outbound session already exists for this call SID
        # (created by the /outbound endpoint before the WebSocket connected).
        vs = (
            db.query(VoiceSession)
            .filter(VoiceSession.call_sid == pipeline.call_sid)
            .first()
        )

        if vs:
            # Update the existing outbound session with stream details
            vs.stream_sid = pipeline.stream_sid
            vs.correlation_id = pipeline.correlation_id
            vs.status = "completed"
            vs.ended_at = datetime.now(timezone.utc)
        else:
            # No pre-existing session — this is an inbound call
            vs = VoiceSession(
                call_sid=pipeline.call_sid,
                stream_sid=pipeline.stream_sid,
                incident_id=pipeline.incident_id,
                correlation_id=pipeline.correlation_id,
                direction="inbound",
                status="completed",
                ended_at=datetime.now(timezone.utc),
            )
            db.add(vs)

        db.flush()

        for entry in pipeline.transcript:
            te = TranscriptEvent(
                voice_session_id=vs.id,
                role=entry["role"],
                content=entry["content"],
            )
            db.add(te)

        db.commit()
        logger.info(
            "Persisted voice session %s (%s) with %d transcript events",
            vs.id,
            vs.direction,
            len(pipeline.transcript),
        )

        # Post-call summarization and notification
        if pipeline.transcript:
            try:
                from app.services.call_summary import summarize_and_notify

                summarize_and_notify(
                    db,
                    voice_session_id=vs.id,
                    transcript=pipeline.transcript,
                    incident_id=pipeline.incident_id,
                )
            except Exception:
                logger.exception("Post-call summarization failed (non-fatal)")
    except Exception:
        logger.exception("Failed to persist voice session")
        db.rollback()
    finally:
        db.close()


# ── Session query endpoints ──────────────────────────────────────────────


@router.get("/sessions", response_model=list[VoiceSessionOut])
def list_voice_sessions(
    incident_id: UUID | None = None,
    limit: int = Query(default=20, le=100),
    db: Session = Depends(get_db),
) -> list[VoiceSessionOut]:
    """List voice sessions, optionally filtered by incident."""
    q = db.query(VoiceSession).order_by(VoiceSession.started_at.desc())
    if incident_id:
        q = q.filter(VoiceSession.incident_id == incident_id)
    sessions = q.limit(limit).all()
    return [VoiceSessionOut.model_validate(s) for s in sessions]


@router.get("/sessions/{session_id}/transcript", response_model=list[TranscriptEventOut])
def get_session_transcript(
    session_id: UUID,
    db: Session = Depends(get_db),
) -> list[TranscriptEventOut]:
    """Get transcript events for a voice session."""
    events = (
        db.query(TranscriptEvent)
        .filter(TranscriptEvent.voice_session_id == session_id)
        .order_by(TranscriptEvent.created_at)
        .all()
    )
    return [TranscriptEventOut.model_validate(e) for e in events]
