from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel, Field

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
async def slack_notify(payload: SlackNotifyRequest) -> dict[str, str]:
    return {
        "job_id": str(uuid4()),
        "status": "queued",
        "provider": "slack",
        "channel": payload.channel,
    }


@router.post("/twilio/outbound-call")
async def twilio_outbound_call(payload: TwilioCallRequest) -> dict[str, str]:
    return {
        "job_id": str(uuid4()),
        "status": "queued",
        "provider": "twilio",
        "to": payload.to,
    }


@router.post("/po/update")
async def po_update(payload: PoUpdateRequest) -> dict[str, str]:
    return {
        "job_id": str(uuid4()),
        "status": "queued",
        "provider": "po-system",
        "po_number": payload.po_number,
    }


@router.post("/email/send")
async def send_email(payload: EmailRequest) -> dict[str, str]:
    return {
        "job_id": str(uuid4()),
        "status": "queued",
        "provider": "resend",
        "to": payload.to,
    }
