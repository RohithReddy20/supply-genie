from __future__ import annotations

import logging
from typing import Callable
from uuid import uuid4

from fastapi import Request, Response
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider

logger = logging.getLogger("backend")
_CORRELATION_HEADER = "X-Correlation-Id"


def configure_observability(service_name: str) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    resource = Resource.create({"service.name": service_name})
    trace.set_tracer_provider(TracerProvider(resource=resource))


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
    response = await call_next(request)
    response.headers[_CORRELATION_HEADER] = correlation_id
    return response
