from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.orchestrator import run_delay_workflow
from app.services.workflows.engine import DelayWorkflowRequest, get_workflow_engine


class _Event:
    def __init__(self, po_number: str, supplier_name: str, eta_days: int) -> None:
        self.po_number = po_number
        self.supplier_name = supplier_name
        self.eta_days = eta_days


class TestWorkflowEngineMode:
    def test_queue_mode_runs_shipment_delay(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.workflows.engine.get_settings",
            lambda: SimpleNamespace(workflow_engine_mode="queue"),
        )

        event = _Event("PO-1001", "Acme", 2)
        result = run_delay_workflow(
            event=event,
            correlation_id="corr-1",
            require_human_approval=True,
            approved_by_human=False,
        )

        assert result.correlation_id == "corr-1"
        statuses = {a.action: a.status for a in result.actions}
        assert statuses["slack"] == "queued"
        assert statuses["call_production"] == "queued"
        assert statuses["update_po"] == "queued"
        assert statuses["email_customer"] == "needs_approval"

    def test_queue_mode_allows_email_when_approved(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.workflows.engine.get_settings",
            lambda: SimpleNamespace(workflow_engine_mode="queue"),
        )

        event = _Event("PO-1002", "Acme", 1)
        result = run_delay_workflow(
            event=event,
            correlation_id="corr-2",
            require_human_approval=True,
            approved_by_human=True,
        )

        statuses = {a.action: a.status for a in result.actions}
        assert statuses["email_customer"] == "queued"

    def test_durable_mode_not_implemented(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.workflows.engine.get_settings",
            lambda: SimpleNamespace(workflow_engine_mode="durable"),
        )

        with pytest.raises(NotImplementedError):
            get_workflow_engine()

    def test_invalid_mode_rejected(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.workflows.engine.get_settings",
            lambda: SimpleNamespace(workflow_engine_mode="invalid-mode"),
        )

        with pytest.raises(ValueError):
            get_workflow_engine()
