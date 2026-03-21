from __future__ import annotations

import logging
from contextlib import contextmanager
from time import perf_counter
from typing import Any, Callable, Generator
from uuid import uuid4

from fastapi import Request, Response
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

logger = logging.getLogger("backend")
_CORRELATION_HEADER = "X-Correlation-Id"

# ── Tracer / Meter singletons ───────────────────────────────────────────
_tracer: trace.Tracer | None = None
_meter: metrics.Meter | None = None

# ── Metric instruments ──────────────────────────────────────────────────
action_counter: metrics.Counter | None = None
action_duration: metrics.Histogram | None = None
incident_counter: metrics.Counter | None = None


def configure_observability(service_name: str) -> None:
    global _tracer, _meter, action_counter, action_duration, incident_counter

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    resource = Resource.create({"service.name": service_name})

    # Tracing
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(service_name)

    # Metrics
    meter_provider = MeterProvider(resource=resource)
    metrics.set_meter_provider(meter_provider)
    _meter = metrics.get_meter(service_name)

    # Instruments
    action_counter = _meter.create_counter(
        "action.executions",
        description="Number of action executions by type and outcome",
    )
    action_duration = _meter.create_histogram(
        "action.duration_ms",
        description="Action execution duration in milliseconds",
        unit="ms",
    )
    incident_counter = _meter.create_counter(
        "incident.created",
        description="Number of incidents created by type",
    )


def get_tracer() -> trace.Tracer:
    if _tracer is None:
        return trace.get_tracer("happy-robot-backend")
    return _tracer


@contextmanager
def trace_action(
    action_type: str,
    incident_id: str,
    attributes: dict[str, Any] | None = None,
) -> Generator[dict[str, Any], None, None]:
    """Context manager that wraps an action dispatch in an OTel span and records metrics."""
    tracer = get_tracer()
    result: dict[str, Any] = {"success": False, "duration_ms": 0.0}

    with tracer.start_as_current_span(
        f"action.{action_type}",
        attributes={
            "action.type": action_type,
            "incident.id": incident_id,
            **(attributes or {}),
        },
    ) as span:
        started = perf_counter()
        try:
            yield result
        finally:
            duration_ms = (perf_counter() - started) * 1000.0
            result["duration_ms"] = duration_ms
            outcome = "success" if result["success"] else "failure"

            span.set_attribute("action.outcome", outcome)
            span.set_attribute("action.duration_ms", duration_ms)

            if action_counter:
                action_counter.add(1, {"action_type": action_type, "outcome": outcome})
            if action_duration:
                action_duration.record(duration_ms, {"action_type": action_type, "outcome": outcome})


def record_incident_created(incident_type: str) -> None:
    if incident_counter:
        incident_counter.add(1, {"incident_type": incident_type})


class CorrelationIdAdapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: dict) -> tuple[str, dict]:
        extra = kwargs.setdefault("extra", {})
        extra.setdefault("correlation_id", self.extra.get("correlation_id", "n/a"))
        return msg, kwargs


def get_logger(correlation_id: str | None = None) -> CorrelationIdAdapter:
    return CorrelationIdAdapter(logger, {"correlation_id": correlation_id or "n/a"})


async def correlation_id_middleware(
    request: Request,
    call_next: Callable[[Request], Response],
) -> Response:
    correlation_id = request.headers.get(_CORRELATION_HEADER, str(uuid4()))
    request.state.correlation_id = correlation_id

    tracer = get_tracer()
    with tracer.start_as_current_span(
        f"{request.method} {request.url.path}",
        attributes={
            "http.method": request.method,
            "http.url": str(request.url),
            "http.correlation_id": correlation_id,
        },
    ):
        response = await call_next(request)
        response.headers[_CORRELATION_HEADER] = correlation_id
        return response
