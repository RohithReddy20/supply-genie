# SLO and Alert Baseline

Purpose: define initial service objectives and actionable alerts for distributed rollout.

## SLO 1 - Incident ingestion availability
- Objective: 99.9% successful responses for incident ingestion endpoints.
- Scope: POST /api/v1/incidents/delay and POST /api/v1/incidents/absence.
- Alert:
  - Trigger: 5xx rate > 2% for 5 minutes.
  - Severity: high.

## SLO 2 - Action execution reliability
- Objective: >= 98% action terminal success rate for non-approval actions.
- Data source: action status ratios from KPI dashboard + queue/dead-letter endpoints.
- Alert:
  - Trigger: dead_lettered count increases by > 10 within 15 minutes.
  - Severity: high.

## SLO 3 - Voice command routing safety
- Objective: 100% of unsupported/stale/missing-owner commands must be rejected (no false acceptance).
- Data source: voice.command.events counter labels and API responses.
- Alert:
  - Trigger: any mismatch between rejected command condition and accepted response in audit checks.
  - Severity: critical.

## SLO 4 - Queue worker health
- Objective: worker_running=true and worker_last_cycle_at freshness < 2 * ACTION_WORKER_POLL_INTERVAL_S.
- Data source: GET /api/v1/incidents/queue/status.
- Alert:
  - Trigger: worker_running=false in queued mode, or worker_last_error non-null repeatedly for > 5 minutes.
  - Severity: high.

## SLO 5 - Voice ownership freshness
- Objective: stale active session ratio < 1% during normal operations.
- Data source: GET /api/v1/voice/active-sessions.
- Alert:
  - Trigger: stale session count >= 1 for > 3 consecutive checks.
  - Severity: medium.

## Review cadence
- Weekly: review SLO breaches and alert noise.
- Monthly: tighten thresholds based on observed stability.
- Post-incident: update SLOs and runbooks with concrete corrective actions.
