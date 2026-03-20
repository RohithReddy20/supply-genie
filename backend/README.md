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

This is setup-only code. No real external side effects are executed yet.
