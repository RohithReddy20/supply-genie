# Distributed Migration Plan (Todo Style)

Status legend:
- [ ] Not started
- [~] In progress
- [x] Done

## Phase 0 - Baseline and safety rails
- [x] Add voice/action structured metrics (latency, error rate, completion rate).
- [x] Add failure-drill checklist (pod kill, connector timeout, DB lock contention).
- [x] Define SLO thresholds and alert conditions.

## Phase 1 - Voice routing stability and ownership
- [x] Add sticky-routing deployment templates for Twilio media-stream traffic at ingress/service layer.
- [x] Add active session ownership metadata (pod_id, call_sid, stream_sid, correlation_id, incident_id).
- [x] Add active session heartbeat updates and stale-session detection.
- [x] Expose active session ownership API endpoint for operational visibility.
- [x] Fix health endpoint to report active and stale voice sessions from the real pipeline registry.
- [x] Add deployment-level sticky-session config templates in infrastructure manifests (Kubernetes/Nginx).
- [ ] Apply templates to target environment and verify with multi-pod smoke test.

## Phase 2 - Durable in-call checkpoints
- [x] Introduce Redis-backed call state store with TTL.
- [x] Checkpoint transcript chunks during the call (not only post-call).
- [x] Checkpoint progress flags on each update_call_progress tool invocation.
- [x] Add recovery read-path for interrupted sessions.

## Phase 3 - Async action execution
- [x] Add job queue abstraction for connector actions.
- [~] Move execute_pending_actions out of request thread into workers (enabled via ACTION_EXECUTION_MODE=queued rollout flag).
- [x] Add retry with backoff and dead-letter handling (worker auto-requeue plus dead-letter visibility and manual requeue endpoints).
- [x] Add per-action idempotency guard for external side effects (deterministic propagation complete; provider-side email enforcement plus fail-closed timeout policy for non-idempotent connectors).

## Phase 4 - Cross-pod active-call control plane
- [x] Add owner-pod command dispatch channel (Redis queue per call_sid).
- [x] Route non-owner control requests to owner pod via command bus with checkpoint presence validation.
- [x] Add stale-owner detection and fail-closed policy (reject stale owner checkpoint routes).

## Phase 5 - Optional workflow orchestration
- [x] Evaluate Temporal/Durable-style workflows against current queue model.
- [ ] Migrate one high-value long-running workflow first.
- [ ] Keep short/simple workflows on lightweight queue path.

## Notes
- Phase 1 application changes are complete.
- Deployment templates and smoke-test runbook are now in-repo.
- Remaining Phase 1 work is environment rollout and multi-pod validation execution.
- Phase 2 checkpoint write/read paths are in place.
- Phase 3 dispatcher + background worker scaffolding is in place behind a rollout flag.
- Phase 3 includes operational queue/dead-letter control endpoints for safe rollout.
- Queue status now exposes worker runtime health fields (running/cycle/error/processed count) for safer queued-mode rollout.
- Phase 4 command routing is implemented with strict rejection when remote owner evidence is missing.
- Phase 4 stale-owner detection is enforced to prevent ambiguous command delivery.
- Phase 0 baseline work is documented in operations runbooks and instrumented with queue/voice-command events.
- Phase 5 evaluation is documented in docs/operations/workflow-orchestration-evaluation.md with explicit adoption gates.
