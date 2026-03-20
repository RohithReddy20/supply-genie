from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    ActionRun,
    ActionStatus,
    ActionType,
    Approval,
    ApprovalStatus,
    Incident,
)
from app.services.action_executor import execute_pending_actions
from app.services.chat import (
    APPROVAL_REQUIRED_ACTIONS,
    COMMAND_TO_ACTION,
    ChatResponse,
    process_message,
)

logger = logging.getLogger("backend.chat_router")

router = APIRouter(prefix="/chat", tags=["chat"])


# ── Request / Response schemas ───────────────────────────────────────────


class ChatMessageIn(BaseModel):
    incident_id: UUID
    message: str = Field(min_length=1)
    history: list[dict] | None = None


class ProposedActionOut(BaseModel):
    action_type: str
    label: str
    description: str
    requires_approval: bool = False


class ChatMessageOut(BaseModel):
    reply: str
    proposed_actions: list[ProposedActionOut] = []


class ChatCommandIn(BaseModel):
    incident_id: UUID
    command: str = Field(min_length=1)
    reason: str = ""


class ChatCommandOut(BaseModel):
    status: str
    action_run_id: str | None = None
    message: str


# ── Endpoints ────────────────────────────────────────────────────────────


@router.post("/message", response_model=ChatMessageOut)
def chat_message(body: ChatMessageIn, db: Session = Depends(get_db)):
    incident = db.query(Incident).filter(Incident.id == body.incident_id).first()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    result: ChatResponse = process_message(
        db=db,
        incident=incident,
        user_message=body.message,
        history=body.history,
    )

    return ChatMessageOut(
        reply=result.reply,
        proposed_actions=[
            ProposedActionOut(
                action_type=pa.action_type,
                label=pa.label,
                description=pa.description,
                requires_approval=pa.requires_approval,
            )
            for pa in result.proposed_actions
        ],
    )


@router.post("/command", response_model=ChatCommandOut)
def chat_command(body: ChatCommandIn, db: Session = Depends(get_db)):
    incident = db.query(Incident).filter(Incident.id == body.incident_id).first()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    action_type = COMMAND_TO_ACTION.get(body.command)
    if not action_type:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown command: {body.command}. Available: {', '.join(COMMAND_TO_ACTION.keys())}",
        )

    # Check if there's an existing action_run for this type we can re-trigger
    existing = None
    for a in incident.actions:
        if a.action_type == action_type and a.status in (
            ActionStatus.pending,
            ActionStatus.failed,
            ActionStatus.needs_approval,
        ):
            existing = a
            break

    if existing:
        if existing.status == ActionStatus.needs_approval:
            return ChatCommandOut(
                status="needs_approval",
                action_run_id=str(existing.id),
                message=f"Action '{body.command}' requires operator approval. Please approve it from the action timeline.",
            )

        # Re-trigger: reset to pending and execute
        if existing.status == ActionStatus.failed:
            existing.retry_count += 1
        existing.status = ActionStatus.pending
        existing.error_message = None
        db.commit()

        executed = execute_pending_actions(db, incident)
        run = next((a for a in executed if a.action_type == action_type), None)

        return ChatCommandOut(
            status="executed" if run and run.status == ActionStatus.completed else "failed",
            action_run_id=str(existing.id),
            message=f"Action '{body.command}' has been executed."
            if run and run.status == ActionStatus.completed
            else f"Action '{body.command}' failed: {existing.error_message or 'unknown error'}",
        )

    # No existing action — create a new one
    max_seq = max((a.sequence for a in incident.actions), default=0)
    requires_approval = action_type in APPROVAL_REQUIRED_ACTIONS

    new_action = ActionRun(
        incident_id=incident.id,
        action_type=action_type,
        status=ActionStatus.needs_approval if requires_approval else ActionStatus.pending,
        sequence=max_seq + 1,
    )
    db.add(new_action)
    db.flush()  # Assign new_action.id before referencing it

    if requires_approval:
        approval = Approval(
            action_run_id=new_action.id,
            incident_id=incident.id,
            status=ApprovalStatus.pending,
        )
        db.add(approval)
        db.commit()
        return ChatCommandOut(
            status="needs_approval",
            action_run_id=str(new_action.id),
            message=f"Action '{body.command}' requires approval. It has been added to the timeline.",
        )

    db.commit()
    db.refresh(incident)

    executed = execute_pending_actions(db, incident)
    run = next((a for a in executed if a.id == new_action.id), None)

    return ChatCommandOut(
        status="executed" if run and run.status == ActionStatus.completed else "failed",
        action_run_id=str(new_action.id),
        message=f"Action '{body.command}' executed successfully."
        if run and run.status == ActionStatus.completed
        else f"Action '{body.command}' failed: {new_action.error_message or 'unknown error'}",
    )
