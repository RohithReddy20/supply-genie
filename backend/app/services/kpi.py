"""KPI computation service — queries DB to produce dashboard metrics."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import (
    ActionRun,
    ActionStatus,
    ActionType,
    Incident,
    IncidentStatus,
    IncidentType,
    VoiceSession,
)


def compute_kpis(db: Session) -> dict[str, Any]:
    """Return all KPIs in a single call for the dashboard endpoint."""
    return {
        "incidents": _incident_kpis(db),
        "actions": _action_kpis(db),
        "action_breakdown": _action_type_breakdown(db),
        "voice": _voice_kpis(db),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Incident-level KPIs ──────────────────────────────────────────────────

def _incident_kpis(db: Session) -> dict[str, Any]:
    total = db.scalar(select(func.count(Incident.id))) or 0
    if total == 0:
        return {
            "total": 0,
            "by_status": {},
            "by_type": {},
            "auto_resolution_rate": 0.0,
            "escalation_rate": 0.0,
            "mean_time_to_resolution_s": None,
        }

    # Counts by status
    status_rows = db.execute(
        select(Incident.status, func.count(Incident.id)).group_by(Incident.status)
    ).all()
    by_status = {row[0].value: row[1] for row in status_rows}

    # Counts by type
    type_rows = db.execute(
        select(Incident.type, func.count(Incident.id)).group_by(Incident.type)
    ).all()
    by_type = {row[0].value: row[1] for row in type_rows}

    resolved = by_status.get("resolved", 0)
    escalated = by_status.get("escalated", 0)

    # MTTR — average seconds from created_at to resolved_at for resolved incidents
    mttr_result = db.scalar(
        select(
            func.avg(
                func.extract("epoch", Incident.resolved_at)
                - func.extract("epoch", Incident.created_at)
            )
        ).where(
            Incident.status == IncidentStatus.resolved,
            Incident.resolved_at.isnot(None),
        )
    )

    return {
        "total": total,
        "by_status": by_status,
        "by_type": by_type,
        "auto_resolution_rate": round(resolved / total, 4) if total else 0.0,
        "escalation_rate": round(escalated / total, 4) if total else 0.0,
        "mean_time_to_resolution_s": round(float(mttr_result), 2) if mttr_result else None,
    }


# ── Action-level KPIs ────────────────────────────────────────────────────

def _action_kpis(db: Session) -> dict[str, Any]:
    total = db.scalar(select(func.count(ActionRun.id))) or 0
    if total == 0:
        return {
            "total": 0,
            "completed": 0,
            "failed": 0,
            "pending": 0,
            "needs_approval": 0,
            "success_rate": 0.0,
            "failure_rate": 0.0,
            "avg_duration_ms": None,
        }

    status_rows = db.execute(
        select(ActionRun.status, func.count(ActionRun.id)).group_by(ActionRun.status)
    ).all()
    by_status = {row[0].value: row[1] for row in status_rows}

    completed = by_status.get("completed", 0)
    failed = by_status.get("failed", 0)
    terminal = completed + failed + by_status.get("skipped", 0)

    # Average duration for completed actions (completed_at - started_at)
    avg_dur = db.scalar(
        select(
            func.avg(
                func.extract("epoch", ActionRun.completed_at)
                - func.extract("epoch", ActionRun.started_at)
            )
            * 1000  # convert to ms
        ).where(
            ActionRun.status == ActionStatus.completed,
            ActionRun.started_at.isnot(None),
            ActionRun.completed_at.isnot(None),
        )
    )

    return {
        "total": total,
        "completed": completed,
        "failed": failed,
        "pending": by_status.get("pending", 0),
        "needs_approval": by_status.get("needs_approval", 0),
        "success_rate": round(completed / terminal, 4) if terminal else 0.0,
        "failure_rate": round(failed / terminal, 4) if terminal else 0.0,
        "avg_duration_ms": round(float(avg_dur), 1) if avg_dur else None,
    }


# ── Per-action-type breakdown ────────────────────────────────────────────

def _action_type_breakdown(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        select(
            ActionRun.action_type,
            func.count(ActionRun.id).label("total"),
            func.sum(
                case((ActionRun.status == ActionStatus.completed, 1), else_=0)
            ).label("completed"),
            func.sum(
                case((ActionRun.status == ActionStatus.failed, 1), else_=0)
            ).label("failed"),
            func.avg(
                case(
                    (
                        ActionRun.status == ActionStatus.completed,
                        (
                            func.extract("epoch", ActionRun.completed_at)
                            - func.extract("epoch", ActionRun.started_at)
                        )
                        * 1000,
                    ),
                    else_=None,
                )
            ).label("avg_duration_ms"),
        ).group_by(ActionRun.action_type)
    ).all()

    result = []
    for row in rows:
        completed = int(row.completed or 0)
        failed = int(row.failed or 0)
        terminal = completed + failed
        result.append({
            "action_type": row.action_type.value,
            "total": row.total,
            "completed": completed,
            "failed": failed,
            "success_rate": round(completed / terminal, 4) if terminal else 0.0,
            "avg_duration_ms": round(float(row.avg_duration_ms), 1) if row.avg_duration_ms else None,
        })

    return result


# ── Voice KPIs ───────────────────────────────────────────────────────────

def _voice_kpis(db: Session) -> dict[str, Any]:
    total_sessions = db.scalar(select(func.count(VoiceSession.id))) or 0
    if total_sessions == 0:
        return {
            "total_sessions": 0,
            "completed_sessions": 0,
            "answer_rate": 0.0,
            "avg_duration_s": None,
            "total_duration_s": 0,
        }

    completed = db.scalar(
        select(func.count(VoiceSession.id)).where(
            VoiceSession.status.in_(["completed", "mock"])
        )
    ) or 0

    avg_dur = db.scalar(
        select(func.avg(VoiceSession.duration_seconds)).where(
            VoiceSession.duration_seconds.isnot(None)
        )
    )

    total_dur = db.scalar(
        select(func.sum(VoiceSession.duration_seconds)).where(
            VoiceSession.duration_seconds.isnot(None)
        )
    ) or 0

    return {
        "total_sessions": total_sessions,
        "completed_sessions": completed,
        "answer_rate": round(completed / total_sessions, 4) if total_sessions else 0.0,
        "avg_duration_s": round(float(avg_dur), 1) if avg_dur else None,
        "total_duration_s": int(total_dur),
    }
