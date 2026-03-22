"""Resilience primitives: timeouts, exponential backoff, circuit breaker."""
from __future__ import annotations

import logging
import random
import threading
from dataclasses import dataclass, field
from enum import Enum
from time import monotonic, perf_counter
from typing import Any, Callable, TypeVar

from app.config import get_settings

logger = logging.getLogger("backend.resilience")
T = TypeVar("T")


# ── Timeout wrapper ──────────────────────────────────────────────────────

class ConnectorTimeout(Exception):
    """Raised when a connector call exceeds its timeout budget."""

    def __init__(self, connector: str, timeout_s: float) -> None:
        self.connector = connector
        self.timeout_s = timeout_s
        super().__init__(f"{connector} timed out after {timeout_s}s")


import concurrent.futures

_timeout_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)


def with_timeout(func: Callable[..., T], timeout_s: float, connector_name: str, *args: Any, **kwargs: Any) -> T:
    """Run a synchronous function with a thread-based timeout."""
    future = _timeout_pool.submit(func, *args, **kwargs)
    try:
        return future.result(timeout=timeout_s)
    except concurrent.futures.TimeoutError:
        raise ConnectorTimeout(connector_name, timeout_s)


# ── Exponential backoff with jitter ──────────────────────────────────────

def backoff_delay_ms(attempt: int, base_ms: int | None = None, max_ms: int | None = None) -> int:
    """Calculate backoff delay with full jitter (AWS-style).

    delay = random(0, min(max_ms, base_ms * 2^attempt))
    """
    settings = get_settings()
    base = base_ms or settings.backoff_base_ms
    cap = max_ms or settings.backoff_max_ms
    exp_delay = min(cap, base * (2 ** attempt))
    return random.randint(0, exp_delay)


# ── Circuit breaker ──────────────────────────────────────────────────────

class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Per-connector circuit breaker (thread-safe)."""

    name: str
    failure_threshold: int = 5
    recovery_timeout_s: float = 60.0
    _failure_count: int = field(default=0, repr=False)
    _last_failure_time: float = field(default=0.0, repr=False)
    _state: CircuitState = field(default=CircuitState.CLOSED, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _half_open_successes: int = field(default=0, repr=False)

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN:
                if monotonic() - self._last_failure_time >= self.recovery_timeout_s:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_successes = 0
                    logger.info("Circuit breaker %s: OPEN -> HALF_OPEN", self.name)
            return self._state

    def record_success(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._half_open_successes += 1
                if self._half_open_successes >= 2:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    logger.info("Circuit breaker %s: HALF_OPEN -> CLOSED", self.name)
            else:
                self._failure_count = 0

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = monotonic()
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                logger.warning("Circuit breaker %s: HALF_OPEN -> OPEN", self.name)
            elif self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                logger.warning(
                    "Circuit breaker %s: CLOSED -> OPEN (failures=%d)",
                    self.name, self._failure_count,
                )

    def allow_request(self) -> bool:
        current = self.state  # triggers OPEN->HALF_OPEN check
        if current == CircuitState.CLOSED:
            return True
        if current == CircuitState.HALF_OPEN:
            return True  # allow probe request
        return False


class CircuitOpenError(Exception):
    """Raised when a circuit breaker is open."""

    def __init__(self, connector: str) -> None:
        self.connector = connector
        super().__init__(f"Circuit breaker open for {connector}")


# ── Circuit breaker registry ─────────────────────────────────────────────

_breakers: dict[str, CircuitBreaker] = {}
_breakers_lock = threading.Lock()


def get_circuit_breaker(name: str) -> CircuitBreaker:
    """Get or create a circuit breaker for the named connector."""
    with _breakers_lock:
        if name not in _breakers:
            settings = get_settings()
            _breakers[name] = CircuitBreaker(
                name=name,
                failure_threshold=settings.cb_failure_threshold,
                recovery_timeout_s=settings.cb_recovery_timeout_s,
            )
        return _breakers[name]


def get_all_circuit_breakers() -> dict[str, CircuitBreaker]:
    with _breakers_lock:
        return dict(_breakers)


# ── Fallback messages ────────────────────────────────────────────────────

FALLBACK_MESSAGES: dict[str, str] = {
    "slack_notify": "Slack notification could not be delivered. The incident has been logged and will be retried automatically.",
    "call_production": "Production call could not be completed. A manual follow-up is required.",
    "call_contractor": "Contractor call could not be completed. Please reach out to the contractor directly.",
    "update_po": "PO update failed. The PO system may be temporarily unavailable. Update will be retried.",
    "email_customer": "Customer email could not be sent. The message has been queued for retry.",
    "update_labor": "Labor system update failed. Please update the shift record manually.",
    "notify_manager": "Manager notification could not be delivered. Please notify the site manager directly.",
}


def get_fallback_message(action_type: str) -> str:
    return FALLBACK_MESSAGES.get(action_type, f"Action '{action_type}' failed after all retries.")
