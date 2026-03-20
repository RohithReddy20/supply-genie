from __future__ import annotations

from dataclasses import asdict
from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from sqlalchemy.orm import Session

from app.database import get_db
from app.models import POStatus
from app.services.connectors.po_system import update_po
from app.services.connectors.slack import send_message as slack_send
from app.services.connectors.twilio_voice import get_call_status, make_call

router = APIRouter(prefix="/connectors", tags=["connectors"])


class SlackNotifyRequest(BaseModel):
    channel: str = Field(min_length=2)
    message: str = Field(min_length=1)


class TwilioCallRequest(BaseModel):
    to: str = Field(min_length=5)
    from_number: str = Field(min_length=5)
    context: str = Field(min_length=3)


class PoUpdateRequest(BaseModel):
    po_number: str = Field(min_length=2)
    status: str = Field(min_length=2)
    note: str = Field(min_length=3)


class EmailRequest(BaseModel):
    to: str = Field(min_length=5)
    subject: str = Field(min_length=3)
    body: str = Field(min_length=5)


@router.post("/slack/notify")
async def slack_notify(payload: SlackNotifyRequest) -> dict:
    result = slack_send(channel=payload.channel, message=payload.message)
    return {
        "job_id": str(uuid4()),
        "status": "sent" if result.ok else "failed",
        "provider": "slack",
        "channel": result.channel,
        **asdict(result),
    }


class CallStatusRequest(BaseModel):
    call_sid: str = Field(min_length=10)


@router.post("/twilio/outbound-call")
async def twilio_outbound_call(payload: TwilioCallRequest) -> dict:
    result = make_call(to=payload.to, message=payload.context, from_number=payload.from_number)
    return {
        "job_id": str(uuid4()),
        "status": result.status or ("sent" if result.ok else "failed"),
        "provider": "twilio",
        "to": payload.to,
        **asdict(result),
    }


@router.post("/twilio/call-status")
async def twilio_call_status(payload: CallStatusRequest) -> dict:
    result = get_call_status(payload.call_sid)
    return asdict(result)


@router.post("/po/update")
async def po_update_endpoint(payload: PoUpdateRequest, db: Session = Depends(get_db)) -> dict:
    try:
        new_status = POStatus(payload.status)
    except ValueError:
        return {"error": f"Invalid PO status: {payload.status}. Valid: {[s.value for s in POStatus]}"}

    result = update_po(db, po_number=payload.po_number, new_status=new_status, notes=payload.note)
    db.commit()
    return {
        "job_id": str(uuid4()),
        "provider": "po-system",
        **asdict(result),
    }


@router.post("/email/send")
async def send_email(payload: EmailRequest) -> dict[str, str]:
    return {
        "job_id": str(uuid4()),
        "status": "queued",
        "provider": "resend",
        "to": payload.to,
    }
