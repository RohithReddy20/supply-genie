# Workflow Orchestration Evaluation

Date: 2026-04-14
Scope: evaluate whether to introduce a durable workflow engine (Temporal/Durable Functions equivalent) beyond current queue-based action execution.

## Current State
- Queue model exists for action execution with retries, dead-letter listing, manual requeue, and worker observability.
- Voice call control and session checkpointing are Redis-backed with strict fail-closed command routing.
- Existing queue mode is controlled by ACTION_EXECUTION_MODE and can be rolled out incrementally.

## Evaluation Matrix

### Option A - Keep Queue Model (current path)
Pros:
- Already implemented and tested in this repo.
- Lower operational complexity and faster delivery.
- Fits short workflows with simple retry requirements.

Cons:
- Long-running, timer-heavy, and compensation-heavy flows become custom logic.
- Cross-step audit/replay semantics are manual.

Best fit:
- Incident playbooks with low branching depth and short execution windows.

### Option B - Introduce Durable Workflow Engine
Pros:
- Native history, replay, deterministic workflow state.
- Better support for long-running timers, human waits, and compensation.
- Stronger observability for multi-day workflow instances.

Cons:
- New infrastructure and operational overhead.
- Migration cost for existing playbook logic.
- Team learning curve and deterministic coding constraints.

Best fit:
- Multi-day workflows with repeated human-in-loop pauses and external dependencies.

## Recommendation
- Keep queue model as primary execution mechanism now.
- Add a durable workflow engine only when one or more adoption gates are met.

## Adoption Gates
Introduce durable orchestration when any of the following is consistently true:
1. Workflow duration regularly exceeds 30 minutes with timers/waits.
2. Compensation/reconciliation logic spans more than 3 external systems.
3. Queue worker restarts cause repeated manual intervention for in-flight business processes.
4. Audit/replay requirements demand deterministic event history beyond current action logs.

## Candidate First Migration (when gates are met)
- Workflow: Shipment delay customer communication flow.
- Reason: highest business visibility, includes approval gate, external calls, and customer-facing side effects.

## Proposed Rollout if Adopted
1. Shadow mode: run workflow engine read-only alongside queue path.
2. Canary by incident type and severity.
3. Promote a single workflow to primary path.
4. Keep queue fallback until SLOs hold for 2 release cycles.
