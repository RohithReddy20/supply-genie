# Supply Chain Coordinator AI Worker

> **Architectural Inspiration: The HappyRobot Paradigm**
> 
> This project's core design directly mirrors the operational capabilities of the [HappyRobot AI Platform](https://www.happyrobot.ai/), a leading generative AI system for supply chain logistics. Based on a deep technical review of HappyRobot's architecture, this project implements several of its flagship features:
> *   **AI-Driven Voice Automation**: Just as HappyRobot handles multi-turn logistics calls autonomously to resolve exceptions, this project implements a low-latency voice pipeline (`voice_pipeline.py`) connecting Twilio to Gemini Realtime. It is capable of highly dynamic, interruptible conversations with production facilities and contractors.
> *   **Seamless Enterprise Integration**: HappyRobot synchronizes directly with legacy TMS, CRMs, and load boards. This project replicates that pattern via the Action Executor layer, executing discrete tasks across external APIs (Slack, Resend, Mock ERP/WMS) using idempotent webhook ingestion and deterministic state machines.
> *   **Multi-Channel Orchestration**: Supply chain exceptions rarely live in a single silo. Like HappyRobot, this system coordinates context seamlessly across Voice (Phone), Messaging (Slack), and Email (Resend), ensuring operators never do redundant work.
> *   **Reliability & Auditing**: HappyRobot utilizes an "AI Auditor" and orchestration systems for reliability. This architecture mirrors that need through strict Human-In-The-Loop (HITL) Approval Gates to review customer-facing actions, alongside comprehensive OpenTelemetry (OTel) dashboards for real-time KPIs (MTTR, auto-resolution rate).

The Supply Chain Coordinator AI Worker is built as a modern, decoupled monorepo. It orchestrates complex, multi-modal workflows (Chat + Voice + API integrations) while enforcing strict human-in-the-loop (HITL) safety controls and robust telemetry.

## System Architecture

### 1. Frontend Layer (Operator Workspace)
**Tech Stack**: Next.js App Router, TypeScript, Tailwind CSS, shadcn/ui.
**Responsibilities**:
*   **Mixed-Initiative UI**: Provides a 3-column layout (Incident List, AI Chat Panel, Action Timeline). Operators can view live events, ask the AI for context, and issue natural language commands.
*   **Human-In-The-Loop (HITL) Console**: Surfaces "Approval Gates." The AI can propose customer-facing actions, but the operator must explicitly click "Approve" or "Reject" to proceed.
*   **Real-time Observability**: Uses React Query to poll backend status, action life-cycles, and drive a top-level KPI Dashboard (MTTR, auto-resolution rate, escalation risk).

### 2. API & Transport Layer
**Tech Stack**: FastAPI (Python 3.12+), Pydantic.
**Responsibilities**:
*   **Contract Management**: Exposes strictly typed REST endpoints (`/api/v1/incidents`, `/api/v1/chat`, `/api/v1/approvals`).
*   **Idempotent Ingestion**: Webhook ingress strictly enforces `Idempotency-Key` headers—preventing duplicate incidents or redundant orchestration pipelines during upstream retry storms.
*   **Middleware**: Handles CORS and request tracing (Correlation IDs), alongside strict timeout boundaries.

### 3. Orchestration & Services Layer
**Tech Stack**: `google-genai` (Gemini 2.5 Flash & Realtime API).
**Responsibilities**:
*   **AI Chat Planner (`chat.py`)**: Interacts with the operator, converting natural language intents into structured tool execution and proposing next best actions based on standard playbooks.
*   **Voice Pipeline (`voice_pipeline.py`)**: Manages non-blocking, bidirectional audio streams between Gemini Live and Twilio. Features bounded audio queues, backpressure/drop handling, and Voice Activity Detection (VAD) to ensure ultra-low latency. 
*   **Action Executor (`action_executor.py`)**: A deterministic engine that processes the `action_runs` state machine (Pending → Needs Approval → Running → Completed/Failed). Features exponential backoff, jitter, and dead-letter queues on exhaustion.
*   **Approval Gates (`approvals.py`)**: Centralized policy enforcement that pauses workflow execution for designated destructive/customer-facing actions until operator consent is recorded. 

### 4. Data Layer
**Tech Stack**: PostgreSQL, SQLAlchemy ORM, Alembic.
**Responsibilities**:
*   **Domain Persistence**: Stores all entities (`supplier`, `shipment`, `po`, `incident`, `action_run`, `approval`) with full audit timestamps.
*   **Optimistic Concurrency**: Uses row-level versioning checks (e.g., `WHERE version = ?`) during critical operations like Purchase Order (PO) document amendments to completely avoid race conditions.
*   **Auditability**: Records full JSON request/response payloads and error messages on every action run to assist in debugging and trace recovery.

### 5. Infrastructure & Connectors Layer
**Tech Stack**: External APIs (Twilio, Slack, Resend), OpenTelemetry.
**Responsibilities**:
*   **Integrations**: 
    *   **Twilio REST & SIP**: Initiates and manages live phone calls to production facilities.
    *   **Slack SDK**: Dispatches situational alerts to internal operation channels.
    *   **Resend**: Dispatches formatted dynamic emails to customers.
    *   **Mock ERP/WMS**: Adapters simulating legacy enterprise connections.
*   **Resilience**: Built-in `CircuitBreaker` and `ConnectorTimeout` classes wrap all external network calls, preventing degraded third-party systems from causing cascading application failures.
*   **Observability**: Integrated OpenTelemetry (OTel) traces span action runs, logging metrics across standard histograms (e.g., action duration, outcome status) and custom counters (voice channel audio drops).

---

## Data Flow Example: Incident to Resolution
1. **Trigger**: An external logistics system fires a webhook reporting a Shipment Delay. The API Layer deduplicates it using the `Idempotency-Key`.
2. **Orchestrate**: The backend commits an `incident` record and spins up a playbook, spawning pending `action_runs`.
3. **Execute (Internal)**: The Action Executor instantly fires a Slack alert and makes an automated Twilio call to the production site.
4. **Gate (External)**: The playbook reaches an "Email Customer" step. The action transitions to `needs_approval`.
5. **Operator Intervention**: The operator sees the exception on the Next.js UI, converses with the AI to summarize the Twilio call transcript, and clicks "Approve".
6. **Resolution**: The executor sends the Resend email. OpenTelemetry updates the metrics, dynamically reflecting the success on the UI's KPI dashboard.

---

## Key Design Principles
*   **Modularity over Magic**: External API integration logic is strictly decoupled from LLM inference. The AI decides *what* needs to be done, but deterministic Python code executes *how* it's done.
*   **Trust but Verify**: Internal operations can run autonomously, but actions with external side-effects (Emailing Customers) are statically intercepted and gated by human approval.
*   **Production Fault Tolerance**: The system degrades gracefully. If external APIs fail, transient issues are caught by exponential backoff; hard outages trip a circuit breaker, halting execution safely. High-load latency is absorbed via bounded streaming channels.

---

## Setup Instructions

### 1. Prerequisites
- **Node.js**: v18+ (uses `npm` or `pnpm`).
- **Python**: 3.12+ managed via `uv` (`pip install uv`).
- **PostgreSQL**: A running local or cloud Postgres instance.
- **Redis**: A running Redis instance.

### 2. Environment Variables
Create a `.env` file in the `backend/` directory with the following keys. You can toggle `TWILIO_MOCK_MODE=True` if you do not want to configure Twilio physical phone calls.

```env
# Database
DATABASE_URL=postgresql+psycopg://localhost:5432/happy_robot  # Adjust to your database URI
VOICE_STATE_REDIS_URL=redis://localhost:6379/0

# LLM / Gemini Models
VERTEX_AI_KEY=your_vertex_ai_key

# Twilio Voice Config
TWILIO_ACCOUNT_SID=your_twilio_sid
TWILIO_AUTH_TOKEN=your_twilio_auth_token
TWILIO_FROM_NUMBER=+18885551234
TWILIO_DEFAULT_TO=+18885559876
TWILIO_MOCK_MODE=false  # Set to true to bypass physical phone calls

# Slack
SLACK_BOT_TOKEN=xoxb-your-slack-bot-token
SLACK_DEFAULT_CHANNEL=#ops-alerts

# Resend (Email capability)
RESEND_API_KEY=re_your_api_key
```

### 3. Install Dependencies
Run this from the repository root to install both the Next.js UI and FastAPI backend dependencies:
```bash
npm run setup
```

### 4. Running the Application
To run both the backend and frontend concurrently:
```bash
npm run dev
```

To run the services separately:
```bash
npm run dev:backend  # Runs FastAPI on http://localhost:8000
npm run dev:ui       # Runs Next.js on http://localhost:3000
```
