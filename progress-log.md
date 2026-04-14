# Progress Log (Append Only)

## 2026-04-14
- Created phased migration todo plan in distributed-migration-plan.md and kicked off Phase 1 execution.
- Implemented active voice-session ownership metadata in the voice pipeline with pod_id, timestamps, and call/session identifiers.
- Added heartbeat refresh loop plus stale-session detection for active voice sessions.
- Added new API endpoint GET /api/v1/voice/active-sessions for operational visibility of active ownership state.
- Updated readiness health checks to report active and stale voice-session counts from the current voice pipeline registry.
- Ran syntax validation on changed backend files; compile checks passed.
- Ran regression subset (tests/test_regression.py); observed 3 existing test failures unrelated to the new voice ownership metadata changes.

## 2026-04-14 (phase-1 infra templates)
- Added Kubernetes sticky-routing baseline manifest for voice websocket traffic with ClientIP affinity and ingress hash in deploy/k8s/voice-sticky-routing.yaml.
- Added NGINX sticky-routing template for voice websocket traffic in deploy/nginx/voice-sticky-routing.conf.
- Added multi-pod smoke-test runbook for voice session ownership/heartbeat validation in docs/operations/voice-sticky-routing-smoke-test.md.
- Updated distributed migration todo statuses to reflect completed template work and remaining environment rollout/verification task.

## 2026-04-14 (phase-2 checkpoint write-path)
- Added Redis-backed voice state store service in backend/app/services/voice_state_store.py with TTL checkpoint and cleanup helpers.
- Added new voice checkpoint configuration settings in backend/app/config.py (redis URL, checkpoint interval, TTL, transcript tail size).
- Wired periodic in-call checkpointing and tool-triggered progress checkpointing into backend/app/services/voice_pipeline.py.
- Added active-call checkpoint cleanup on graceful session completion.
- Validation: compile checks passed; workflow test subset still reports one pre-existing action-execution failure unrelated to voice checkpoint write-path.

## 2026-04-14 (phase-2 recovery read-path)
- Added Redis checkpoint read helper in backend/app/services/voice_state_store.py.
- Added GET /api/v1/voice/checkpoints/{call_sid} endpoint in backend/app/routers/voice.py for interrupted-call recovery reads.
- Re-ran compile validation for voice state store, voice router, and voice pipeline; all compiled successfully.

## 2026-04-14 (phase-3 async execution slice 1)
- Added queue-based action dispatcher service in backend/app/services/action_dispatcher.py with inline/queued execution mode support.
- Wired incident ingestion and approval decision flows to dispatch through the new dispatcher abstraction instead of directly calling inline execution.
- Added background action worker lifecycle hooks in backend/app/main.py startup/shutdown for queued mode.
- Added configuration flags in backend/app/config.py: ACTION_EXECUTION_MODE and ACTION_WORKER_POLL_INTERVAL_S.
- Validation: compile checks passed for changed files; existing workflow/regression test failures remain in action execution paths and predate queue rollout flag default behavior.

## 2026-04-14 (phase-3 async execution slice 2)
- Added failure retry scheduling metadata in backend/app/services/action_executor.py (next_retry_at, next_retry_in_ms).
- Added worker-side due-retry auto-requeue logic in backend/app/services/action_dispatcher.py for queued mode.
- Re-ran compile validation for action executor and dispatcher; compile succeeded.
- Re-ran focused retry test; existing retry-behavior test still failing with prior status-mismatch assertion.

## 2026-04-14 (phase-3 async execution slice 3)
- Added non-production Twilio destination fallback in backend/app/services/action_executor.py and backend/app/config.py to keep test/non-prod action execution deterministic when payload phone fields are missing.
- Re-ran previously failing workflow/regression action tests; all targeted failures cleared.
- Added deterministic per-action idempotency-key propagation through connector-facing action execution paths in backend/app/services/action_executor.py.
- Extended connector interfaces to accept optional idempotency_key and wired propagation through Slack/Twilio/Email/manager notification connectors.
- Validation: full suites tests/test_workflow_b.py and tests/test_regression.py now pass (40 passed).

## 2026-04-14 (strict-fail correction)
- Removed non-production Twilio fallback behavior from backend/app/services/action_executor.py and backend/app/config.py to enforce fail-fast semantics when no destination is provided.
- Added explicit contractor contact fields in absence ingestion schema/service route wiring (backend/app/schemas.py, backend/app/routers/incidents.py, backend/app/services/incidents.py) so valid API payloads can drive real call execution without synthetic fallbacks.
- Updated regression/workflow fixtures to provide explicit call destinations where success is expected (backend/tests/test_regression.py, backend/tests/test_workflow_b.py).
- Validation: strict-mode suites tests/test_workflow_b.py and tests/test_regression.py pass (40 passed).

## 2026-04-14 (phase-3 ops hardening)
- Added queue/dead-letter operational helpers in backend/app/services/action_dispatcher.py (queue status and manual failed-action requeue).
- Added new API endpoints in backend/app/routers/incidents.py:
	- GET /api/v1/incidents/queue/status
	- GET /api/v1/incidents/actions/dead-letter
	- POST /api/v1/incidents/actions/{action_id}/requeue
- Added regression coverage for dead-letter listing, queue status, and manual requeue flows in backend/tests/test_regression.py.
- Validation: targeted new tests passed and broader suites passed (tests/test_workflow_b.py + tests/test_regression.py).

## 2026-04-14 (phase-3 worker observability)
- Extended queue status reporting in backend/app/services/action_dispatcher.py with worker runtime state: running flag, last cycle timestamp, processed total, and last error.
- Kept queue/dead-letter endpoint contract stable while enriching operational visibility for queued-mode rollout.
- Extended regression coverage to assert presence of worker runtime fields in queue status response.
- Validation: compile checks passed and full suites tests/test_workflow_b.py + tests/test_regression.py passed (43 total).

## 2026-04-14 (phase-3 provider idempotency: email)
- Enabled real provider-side idempotency enforcement for email by passing idempotency_key to Resend send options in backend/app/services/connectors/email.py.
- Added focused unit coverage for email idempotency option behavior in backend/tests/test_resilience.py (with and without key).
- Validation: compile checks passed and combined suites (resilience idempotency + workflow + regression) passed (45 total).

## 2026-04-14 (phase-4 command bus routing)
- Added Redis-backed per-call voice command bus in backend/app/services/voice_command_bus.py.
- Wired active session command polling/dispatch into backend/app/services/voice_pipeline.py with strict command handling (currently end_call).
- Added command endpoint POST /api/v1/voice/commands/{call_sid} in backend/app/routers/voice.py with strict remote routing validation (requires active checkpoint evidence for non-local calls).
- Added regression coverage for local command dispatch, remote command bus routing, unsupported command rejection, and missing-checkpoint rejection in backend/tests/test_voice_commands.py.
- Validation: compile checks passed and combined suites (voice commands + workflow + regression) passed (47 total).

## 2026-04-14 (phase-4 stale-owner fail-closed)
- Added owner staleness threshold config in backend/app/config.py (`VOICE_OWNER_STALE_AFTER_S`).
- Enforced stale-owner rejection in backend/app/routers/voice.py for remote command routing when checkpoint recency is outside threshold.
- Added regression coverage for stale checkpoint rejection in backend/tests/test_voice_commands.py.
- Validation: combined suites (voice commands + workflow + regression) passed (48 total).

## 2026-04-14 (phase-0 baseline completion)
- Added structured observability events for queue lifecycle and voice command routing in backend/app/observability.py and wired emissions in action dispatcher and voice router paths.
- Added failure drill checklist runbook in docs/operations/failure-drill-checklist.md.
- Added SLO and alert baseline runbook in docs/operations/slo-and-alert-baseline.md.
- Validation: compile checks passed and combined suites (voice commands + workflow + regression) passed (48 total).

## 2026-04-14 (phase-3 fail-closed timeout policy)
- Added fail-closed timeout handling in backend/app/services/action_executor.py for non-idempotent connectors (Slack/Twilio/manager notify): timeout-like errors now dead-letter immediately to avoid duplicate side effects.
- Added regression tests in backend/tests/test_regression.py covering both non-idempotent timeout dead-letter behavior and idempotent email timeout retriable behavior.
- Validation: compile checks passed and combined suites (voice commands + workflow + regression) passed (50 total).
