from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.config import get_settings
from app.schemas import DelayWorkflowResponse
from app.services.workflows.queue_engine import QueueWorkflowEngine


@dataclass(frozen=True)
class DelayWorkflowRequest:
    po_number: str
    supplier_name: str
    eta_days: int
    correlation_id: str
    require_human_approval: bool
    approved_by_human: bool


class WorkflowEngine(Protocol):
    def run_shipment_delay(self, req: DelayWorkflowRequest) -> DelayWorkflowResponse:
        ...


def get_workflow_engine() -> WorkflowEngine:
    """Return workflow engine implementation based on config mode.

    Modes:
    - queue: queue-first lightweight orchestration (default)
    - durable: reserved for future durable workflow engine integration
    """
    settings = get_settings()
    mode = (settings.workflow_engine_mode or "").strip().lower()

    if mode in {"", "queue"}:
        return QueueWorkflowEngine()

    if mode == "durable":
        raise NotImplementedError(
            "WORKFLOW_ENGINE_MODE=durable is not implemented yet. "
            "Keep WORKFLOW_ENGINE_MODE=queue until durable engine integration is added."
        )

    raise ValueError(f"Unsupported WORKFLOW_ENGINE_MODE: {settings.workflow_engine_mode}")
