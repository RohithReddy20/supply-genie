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
- `GET /api/v1/incidents/queue/status`
- `GET /api/v1/incidents/actions/dead-letter`
- `POST /api/v1/incidents/actions/{action_id}/requeue`

Queue status includes runtime worker fields for rollout health:
- `worker_running`
- `worker_last_cycle_at`
- `worker_processed_total`
- `worker_last_error`

Idempotency notes:
- Action executions carry deterministic idempotency keys (`action:{action_id}`).
- Email connector enforces provider-side idempotency by forwarding this key to Resend.

This is setup-only code. No real external side effects are executed yet.

### Voice latency tuning
Use these environment variables to tune live voice responsiveness:

- `VOICE_THINKING_BUDGET` (default `0`; set `-1` for model automatic thinking)
- `VOICE_OPENING_PROMPT_DELAY_S` (default `0.03`)
- `VOICE_AUDIO_BATCH_MS` (default `40`)
- `VOICE_INBOUND_AUDIO_QUEUE_MAX` (default `24`)
- `VOICE_OUTBOUND_AUDIO_QUEUE_MAX` (default `24`)
- `VOICE_VAD_PREFIX_PADDING_MS` (default `60`)
- `VOICE_VAD_SILENCE_DURATION_MS` (default `260`)
- `VOICE_STATE_REDIS_URL` (default empty; enables Redis-backed active-call checkpoints when set)
- `VOICE_STATE_CHECKPOINT_INTERVAL_S` (default `5.0`)
- `VOICE_STATE_TTL_S` (default `3600`)
- `VOICE_STATE_TRANSCRIPT_MAX_ENTRIES` (default `200`)
- `ACTION_EXECUTION_MODE` (default `inline`; set `queued` to enable background worker)
- `ACTION_WORKER_POLL_INTERVAL_S` (default `1.0`)

Probe scripts for iterative latency testing:

- Synthetic pipeline + event-loop lag probe:
	- `/path/to/backend/.venv/bin/python backend/scripts/voice_latency_probe.py`
- Live Gemini first-audio latency probe:
	- `/path/to/backend/.venv/bin/python backend/scripts/gemini_live_latency_probe.py`
