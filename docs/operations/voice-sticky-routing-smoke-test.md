# Voice Sticky Routing Smoke Test

Goal: verify voice websocket sessions remain stable and observable under multi-pod backend deployment.

## Prerequisites
- Backend deployed with at least 2 replicas.
- Sticky routing config applied from deploy templates.
- Endpoint available: GET /api/v1/voice/active-sessions.
- Health endpoint available: GET /health/ready.
- POD_ID env var set for each backend pod.

## Test 1: Baseline ownership visibility
1. Start one outbound voice call.
2. During the active call, query GET /api/v1/voice/active-sessions every 5 seconds.
3. Confirm exactly one active session with a non-empty pod_id.
4. Confirm last_heartbeat_at updates over time and stale remains false.

Pass criteria:
- Active call is visible.
- pod_id remains stable during the same call.
- No stale session while call is alive.

## Test 2: Concurrent calls distribution check
1. Start 5 outbound calls over 2-3 minutes.
2. Query GET /api/v1/voice/active-sessions periodically.
3. Record pod_id for each call_sid.

Pass criteria:
- Each call_sid sticks to one pod_id during its lifetime.
- Calls may distribute across pods, but ownership does not flip mid-call.

## Test 3: Pod restart behavior
1. Start one active call.
2. Restart the owning pod of that call.
3. Continue querying /api/v1/voice/active-sessions and /health/ready.

Pass criteria:
- Session cleanup occurs on pod restart.
- System remains ready after restart.
- No orphan stale sessions remain beyond heartbeat stale window.

## Test 4: Websocket timeout and long call
1. Run a call for at least 10 minutes.
2. Observe no proxy timeout disconnects.
3. Validate heartbeat freshness and final transcript persistence after call end.

Pass criteria:
- No unexpected disconnect due to proxy/read timeouts.
- Active session removed after call completion.

## Operational checklist
- If stale sessions appear repeatedly, inspect ingress/proxy affinity behavior.
- If pod ownership flips during active call, tighten sticky routing at ingress or service layer.
- If stale sessions remain after call end, inspect lifecycle cleanup path in voice pipeline.
