## Backend Initial Setup

Basic FastAPI scaffold for the supply-chain coordinator backend.

### Included
- Pinned dependencies in `pyproject.toml`.
- App entrypoint at `app/main.py`.
- Health endpoints.
- Placeholder orchestration and connector routes (stub responses only).

### Run
```bash
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Quick checks
- `GET /`
- `GET /health/live`
- `GET /health/ready`
- `POST /api/v1/orchestration/delay`
- `POST /api/v1/connectors/slack/notify`
- `GET /api/v1/voice/active-sessions`
- `GET /api/v1/voice/checkpoints/{call_sid}`
- `POST /api/v1/voice/commands/{call_sid}`
- `GET /api/v1/incidents/queue/status`
- `GET /api/v1/incidents/actions/dead-letter`
- `POST /api/v1/incidents/actions/{action_id}/requeue`

### Voice and Queue Tuning
- `VOICE_VAD_PREFIX_PADDING_MS` (default `60`)
- `VOICE_VAD_SILENCE_DURATION_MS` (default `260`)
- `VOICE_STATE_REDIS_URL` (default empty; enables Redis-backed active-call checkpoints and command routing when set)
- `VOICE_STATE_CHECKPOINT_INTERVAL_S` (default `5.0`)
- `VOICE_STATE_TTL_S` (default `3600`)
- `VOICE_STATE_TRANSCRIPT_MAX_ENTRIES` (default `200`)
- `VOICE_COMMAND_POLL_INTERVAL_S` (default `1.0`)
- `VOICE_COMMAND_QUEUE_TTL_S` (default `1800`)
- `VOICE_OWNER_STALE_AFTER_S` (default `20.0`; remote command routing rejects stale owner checkpoints)
- `ACTION_EXECUTION_MODE` (default `inline`; set `queued` to enable background worker)
- `ACTION_WORKER_POLL_INTERVAL_S` (default `1.0`)
- `WORKFLOW_ENGINE_MODE` (default `queue`; `durable` is reserved and fail-closed until implemented)

### Idempotency Notes
- Action executions carry deterministic idempotency keys (`action:{action_id}`).
- Email connector enforces provider-side idempotency by forwarding this key to Resend.
- Timeout on non-idempotent connectors (Slack/Twilio/manager notify paths) uses fail-closed dead-letter behavior to avoid duplicate side effects.

### Voice Command Routing Notes
- If the call is active on the local pod, the command executes immediately.
- If not local, command routing requires Redis and an active call checkpoint; otherwise command is rejected.
- If the remote owner checkpoint is stale, commands are rejected with fail-closed semantics.

### Workflow Engine Notes
- Legacy shipment-delay orchestration now runs through a workflow engine abstraction.
- Current default is queue-backed behavior for short/simple workflows.
- Durable mode is intentionally fail-closed until a concrete durable engine integration is added.

### Probe Scripts
- Synthetic pipeline + event-loop lag probe:
  - `/path/to/backend/.venv/bin/python backend/scripts/voice_latency_probe.py`
- Live Gemini first-audio latency probe:
  - `/path/to/backend/.venv/bin/python backend/scripts/gemini_live_latency_probe.py`
