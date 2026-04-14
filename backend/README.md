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
- `POST /api/v1/voice/commands/{call_sid}`

Queue status includes runtime worker fields for rollout health:
- `worker_running`
- `worker_last_cycle_at`
- `VOICE_COMMAND_POLL_INTERVAL_S` (default `1.0`)
- `VOICE_COMMAND_QUEUE_TTL_S` (default `1800`)
- `VOICE_OPENING_PROMPT_DELAY_S` (default `0.03`)
- `VOICE_AUDIO_BATCH_MS` (default `40`)
- `VOICE_VAD_PREFIX_PADDING_MS` (default `60`)
- `VOICE_VAD_SILENCE_DURATION_MS` (default `260`)
- `VOICE_STATE_TTL_S` (default `3600`)
- `VOICE_STATE_TRANSCRIPT_MAX_ENTRIES` (default `200`)
- `ACTION_EXECUTION_MODE` (default `inline`; set `queued` to enable background worker)
- `ACTION_WORKER_POLL_INTERVAL_S` (default `1.0`)

Probe scripts for iterative latency testing:

- Synthetic pipeline + event-loop lag probe:
	- `/path/to/backend/.venv/bin/python backend/scripts/voice_latency_probe.py`
- Live Gemini first-audio latency probe:
	- `/path/to/backend/.venv/bin/python backend/scripts/gemini_live_latency_probe.py`
