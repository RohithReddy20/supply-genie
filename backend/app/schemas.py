from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class DelayEvent(BaseModel):
    po_number: str = Field(min_length=2)
    supplier_name: str = Field(min_length=2)
    customer_email: str = Field(min_length=5)
    delay_reason: str = Field(min_length=3)
    eta_days: int = Field(ge=1, le=60)
    reported_at: datetime = Field(default_factory=datetime.utcnow)


class ActionResult(BaseModel):
    action: Literal["slack", "call_production", "update_po", "email_customer"]
    status: Literal["skipped", "queued", "needs_approval"]
    detail: str


class DelayWorkflowResponse(BaseModel):
    workflow_id: str
    correlation_id: str
    actions: list[ActionResult]
