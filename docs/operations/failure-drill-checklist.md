# Failure Drill Checklist

Purpose: run controlled failure drills to validate distributed behavior and fail-safe handling.

## Drill 1 - Voice owner pod kill
1. Start an active voice call.
2. Confirm owner in GET /api/v1/voice/active-sessions.
3. Kill the owner pod.
4. Check GET /health/ready and GET /api/v1/voice/active-sessions.
5. Confirm command routing to stale checkpoint is rejected by fail-closed policy.

Pass criteria:
- No crash loops.
- Stale owner is detected and remote commands are rejected.
- Operator can identify the failed call and retry path.

## Drill 2 - Connector timeout blast
1. Force timeout conditions for Slack/Twilio/Email connectors.
2. Trigger incident ingestion.
3. Observe retries and dead-letter transitions.
4. Verify GET /api/v1/incidents/actions/dead-letter contains exhausted actions.

Pass criteria:
- Timeout failures do not produce fake success.
- Retry/backoff behavior is visible in queue status and action payload metadata.
- Dead-letter records are queryable and requeue endpoint works.

## Drill 3 - Queue worker outage
1. Set ACTION_EXECUTION_MODE=queued.
2. Stop app process that runs worker loop.
3. Trigger incidents that enqueue actions.
4. Restart worker process.
5. Verify queued actions drain and status moves forward.

Pass criteria:
- Queue depth increases while worker is down.
- Queue drains after worker restarts.
- No duplicate side effects are observed for already-completed actions.

## Drill 4 - Redis command bus unavailable
1. Start active call on pod A.
2. Disable Redis or block bus connectivity on pod B.
3. Send POST /api/v1/voice/commands/{call_sid} from pod B route.

Pass criteria:
- Command is rejected with 503 (bus disabled/unavailable), not falsely accepted.
- Local commands on owner pod still work.

## Drill 5 - Stale owner routing
1. Create remote checkpoint older than VOICE_OWNER_STALE_AFTER_S.
2. Send remote command via POST /api/v1/voice/commands/{call_sid}.

Pass criteria:
- API responds 409 stale-owner rejection.
- No command is queued when staleness threshold is exceeded.

## Logging and reporting template
- Date/time:
- Drill name:
- Environment:
- Expected result:
- Observed result:
- Pass/fail:
- Follow-up action:
