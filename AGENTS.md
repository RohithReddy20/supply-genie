# Project Guidelines

## Scope And Hierarchy
- This file defines workspace-wide defaults for the monorepo.
- More specific AGENTS.md files in subdirectories override this file for that scope.
- For UI work, also follow ui/AGENTS.md.

## Architecture
- Monorepo with two apps:
  - backend: FastAPI service that exposes API routes under /api/v1 by default.
  - ui: Next.js App Router frontend.
- Backend request flow:
  - app/main.py wires middleware and routers.
  - app/routers/* handle HTTP contracts.
  - app/services/* contain workflow logic.
  - app/schemas.py defines shared request/response models.
- Keep transport logic in routers and business logic in services.

## Build And Run
- From repo root:
  - npm run setup
  - npm run dev
  - npm run dev:backend
  - npm run dev:ui
- Backend only:
  - cd backend && uv sync
  - cd backend && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
- UI only:
  - cd ui && pnpm dev (fallback: npm run dev)
  - cd ui && pnpm build (fallback: npm run build)
  - cd ui && npm run lint

## Lint And Test
- UI lint: cd ui && npm run lint
- Backend: no dedicated lint/test scripts are configured in this repo yet.
- Root/backend/ui do not currently define automated test commands.

## Conventions
- Backend:
  - Use Python 3.12+ and keep type hints on public functions.
  - Preserve dataclass-based settings in app/config.py and read env vars there.
  - Keep correlation-id behavior intact when adding middleware or response handling.
  - Extend Pydantic models in app/schemas.py before changing route contracts.
- UI:
  - Use TypeScript and App Router patterns already in ui/app.
  - Respect the Next.js version-specific guidance in ui/AGENTS.md.

## Pitfalls
- Backend dependencies and commands use uv, not pip.
- UI package manager preference is pnpm when available; scripts fall back to npm.
- Next.js version is 16.x with potential breaking changes. Read ui/AGENTS.md before changing UI code.
- Incident ingestion routes require Idempotency-Key; duplicates return existing records (200) instead of creating new ones (201).
- REQUIRE_HUMAN_APPROVAL defaults to true; customer-facing actions can be gated until approved.
- Connector endpoints are currently stubs. Do not claim external side effects are implemented unless you add them.
- No dedicated automated test commands are configured yet at root/backend/ui.

## Read First For Major Changes
- backend/app/main.py
- backend/app/config.py
- backend/app/schemas.py
- backend/app/services/orchestrator.py
- backend/app/services/safety.py
- ui/app/layout.tsx
- ui/AGENTS.md
- docs/api-contracts.md
- docs/domain.md
- setup-plan.md
- execution-plan-14-days.md
