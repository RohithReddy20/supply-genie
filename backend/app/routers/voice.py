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
from app.services.voice_session import VoicePipeline

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
    if incident_id:
        ws_url += f"?incident_id={incident_id}"

    connect = Connect()
    connect.stream(url=ws_url)
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
    params = []
    if body.incident_id:
        params.append(f"incident_id={body.incident_id}")
    if body.greeting:
        params.append(f"greeting={body.greeting}")
    if params:
        ws_url += "?" + "&".join(params)

    twiml = VoiceResponse()
    twiml.say(
        "Please hold while we connect you to the coordinator.",
        voice="Google.en-US-Chirp3-HD-Aoede",
    )
    connect = Connect()
    connect.stream(url=ws_url)
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
    """
    await websocket.accept()
    logger.info("Media stream WebSocket accepted (incident_id=%s)", incident_id)

    pipeline = VoicePipeline(
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


def _persist_session(pipeline: VoicePipeline) -> None:
    """Save voice session and transcript events to the database."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
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
            "Persisted voice session %s with %d transcript events",
            vs.id,
            len(pipeline.transcript),
        )
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
