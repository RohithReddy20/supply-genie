from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import cast
from uuid import UUID

from sqlalchemy import select, update

from app.config import get_settings
from app.database import SessionLocal
from app.models import ActionRun, ActionStatus, Incident
from app.services.action_executor import execute_pending_actions

logger = logging.getLogger("backend.action_dispatcher")

_worker_task: asyncio.Task | None = None


def _is_queued_mode() -> bool:
    return get_settings().action_execution_mode.strip().lower() == "queued"


def dispatch_incident_actions(db, incident: Incident) -> int:
    """Dispatch incident actions according to configured execution mode.

    Returns number of actions enqueued (queued mode) or executed (inline mode).
    """
    if _is_queued_mode():
        return enqueue_pending_actions(db, incident.id)

    executed = execute_pending_actions(db, incident)
    return len(executed)


def enqueue_pending_actions(db, incident_id: UUID) -> int:
    """Mark pending actions as queued for worker pickup."""
    rows = db.execute(
        update(ActionRun)
        .where(
            ActionRun.incident_id == incident_id,
            ActionRun.status == ActionStatus.pending,
        )
        .values(status=ActionStatus.queued)
    ).rowcount
    db.commit()
    return cast(int, rows or 0)


async def start_action_worker() -> None:
    """Start background queue worker when queued mode is enabled."""
    global _worker_task
    if not _is_queued_mode() or _worker_task is not None:
        return
    _worker_task = asyncio.create_task(_worker_loop(), name="action-queue-worker")
    logger.info("Action queue worker started")


async def stop_action_worker() -> None:
    """Stop background queue worker if running."""
    global _worker_task
    if _worker_task is None:
        return
    _worker_task.cancel()
    try:
        await _worker_task
    except asyncio.CancelledError:
        pass
    _worker_task = None
    logger.info("Action queue worker stopped")


async def _worker_loop() -> None:
    poll_interval_s = get_settings().action_worker_poll_interval_s

    while True:
        try:
            _requeue_due_failed_actions()
            processed = _process_next_queued_incident()
            if not processed:
                await asyncio.sleep(poll_interval_s)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Action queue worker iteration failed")
            await asyncio.sleep(poll_interval_s)


def _process_next_queued_incident() -> bool:
    """Claim and process one incident with queued actions."""
    db = SessionLocal()
    try:
        incident_id = db.scalar(
            select(ActionRun.incident_id)
            .where(ActionRun.status == ActionStatus.queued)
            .order_by(ActionRun.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not incident_id:
            db.rollback()
            return False

        db.execute(
            update(ActionRun)
            .where(
                ActionRun.incident_id == incident_id,
                ActionRun.status == ActionStatus.queued,
            )
            .values(status=ActionStatus.pending)
        )
        db.commit()

        incident = db.get(Incident, incident_id)
        if not incident:
            return True

        execute_pending_actions(db, incident)
        return True
    finally:
        db.close()


def _requeue_due_failed_actions() -> int:
    """Requeue failed actions when their backoff window has elapsed."""
    settings = get_settings()
    db = SessionLocal()
    try:
        candidates = db.scalars(
            select(ActionRun)
            .where(
                ActionRun.status == ActionStatus.failed,
                ActionRun.retry_count < settings.max_retries,
            )
            .order_by(ActionRun.created_at.asc())
            .limit(100)
        ).all()

        now = datetime.now(timezone.utc)
        requeued = 0

        for action in candidates:
            payload = action.response_payload or {}
            raw_next_retry_at = payload.get("next_retry_at")
            if raw_next_retry_at:
                try:
                    next_retry_at = datetime.fromisoformat(str(raw_next_retry_at))
                except ValueError:
                    next_retry_at = now
            else:
                next_retry_at = now

            if next_retry_at > now:
                continue

            action.status = ActionStatus.queued
            requeued += 1

        if requeued:
            db.commit()
            logger.info("Requeued %d failed action(s) after backoff", requeued)
        else:
            db.rollback()

        return requeued
    finally:
        db.close()
