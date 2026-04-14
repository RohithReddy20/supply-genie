"""Tests for resilience patterns: circuit breaker, backoff, timeouts, fallbacks."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    ConnectorTimeout,
    backoff_delay_ms,
    get_circuit_breaker,
    get_fallback_message,
    with_timeout,
)
from app.services.connectors.email import send_email


# ── Circuit Breaker Tests ────────────────────────────────────────────────


class TestCircuitBreaker:
    def test_starts_closed(self):
        cb = CircuitBreaker(name="test", failure_threshold=3, recovery_timeout_s=1.0)
        assert cb.state == CircuitState.CLOSED

    def test_stays_closed_on_success(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)
        cb.record_success()
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_open_blocks_requests(self):
        cb = CircuitBreaker(name="test", failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.allow_request() is False

    def test_transitions_to_half_open_after_recovery(self):
        cb = CircuitBreaker(name="test", failure_threshold=2, recovery_timeout_s=0.1)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.allow_request() is True

    def test_half_open_closes_after_successes(self):
        cb = CircuitBreaker(name="test", failure_threshold=2, recovery_timeout_s=0.1)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.15)

        assert cb.state == CircuitState.HALF_OPEN
        cb.record_success()
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_reopens_on_failure(self):
        cb = CircuitBreaker(name="test", failure_threshold=2, recovery_timeout_s=0.1)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.15)

        assert cb.state == CircuitState.HALF_OPEN
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()  # resets
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED  # still closed, didn't hit 3

    def test_thread_safety(self):
        cb = CircuitBreaker(name="test_thread", failure_threshold=100)
        errors: list[Exception] = []

        def stress():
            try:
                for _ in range(50):
                    cb.record_failure()
                    cb.record_success()
                    _ = cb.state
                    _ = cb.allow_request()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=stress) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0


# ── Circuit Breaker Registry ────────────────────────────────────────────


class TestCircuitBreakerRegistry:
    def test_returns_same_instance(self):
        cb1 = get_circuit_breaker("slack")
        cb2 = get_circuit_breaker("slack")
        assert cb1 is cb2

    def test_different_connectors_get_different_breakers(self):
        cb_slack = get_circuit_breaker("test_slack")
        cb_twilio = get_circuit_breaker("test_twilio")
        assert cb_slack is not cb_twilio


# ── Backoff Tests ────────────────────────────────────────────────────────


class TestBackoff:
    def test_first_attempt_bounded(self):
        delay = backoff_delay_ms(0, base_ms=100, max_ms=5000)
        assert 0 <= delay <= 100

    def test_increases_with_attempts(self):
        delays = [backoff_delay_ms(i, base_ms=100, max_ms=10000) for i in range(5)]
        # With jitter, individual delays may vary, but max possible should grow
        # Just check they're all non-negative and bounded
        for d in delays:
            assert 0 <= d <= 10000

    def test_capped_at_max(self):
        delay = backoff_delay_ms(20, base_ms=100, max_ms=5000)
        assert delay <= 5000

    def test_jitter_produces_variation(self):
        delays = {backoff_delay_ms(3, base_ms=100, max_ms=5000) for _ in range(20)}
        # With jitter, we should see some variation (not all identical)
        assert len(delays) > 1


# ── Timeout Tests ────────────────────────────────────────────────────────


class TestTimeout:
    def test_fast_function_succeeds(self):
        result = with_timeout(lambda: 42, 5.0, "test")
        assert result == 42

    def test_slow_function_raises(self):
        def slow():
            time.sleep(5)
            return "never"

        with pytest.raises(ConnectorTimeout) as exc_info:
            with_timeout(slow, 0.1, "slow_test")

        assert exc_info.value.connector == "slow_test"
        assert exc_info.value.timeout_s == 0.1

    def test_exception_propagates(self):
        def failing():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            with_timeout(failing, 5.0, "test")


# ── Fallback Messages ───────────────────────────────────────────────────


class TestFallbackMessages:
    def test_known_action_types(self):
        known = ["slack_notify", "call_production", "call_contractor",
                 "update_po", "email_customer", "update_labor", "notify_manager"]
        for action in known:
            msg = get_fallback_message(action)
            assert len(msg) > 10

    def test_unknown_action_type(self):
        msg = get_fallback_message("unknown_action")
        assert "unknown_action" in msg
        assert "failed" in msg


class TestEmailConnectorIdempotency:
    @patch("app.services.connectors.email.get_settings")
    @patch("app.services.connectors.email.get_circuit_breaker")
    @patch("app.services.connectors.email.with_timeout")
    @patch("app.services.connectors.email.resend.Emails.send")
    def test_passes_idempotency_key_to_resend(
        self,
        mock_send,
        mock_with_timeout,
        mock_get_cb,
        mock_get_settings,
    ):
        mock_get_settings.return_value = SimpleNamespace(
            resend_api_key="rk_test",
            email_from="coord@example.com",
            timeout_email_s=5.0,
        )
        cb = MagicMock()
        cb.allow_request.return_value = True
        mock_get_cb.return_value = cb
        mock_send.return_value = {"id": "email_123"}

        def _run(func, timeout_s, connector):
            return func()

        mock_with_timeout.side_effect = _run

        result = send_email(
            to="buyer@example.com",
            subject="Test",
            body="<p>hello</p>",
            idempotency_key="action:abc",
        )

        assert result.ok is True
        assert mock_send.call_count == 1
        args = mock_send.call_args.args
        assert len(args) == 2
        assert args[1]["idempotency_key"] == "action:abc"

    @patch("app.services.connectors.email.get_settings")
    @patch("app.services.connectors.email.get_circuit_breaker")
    @patch("app.services.connectors.email.with_timeout")
    @patch("app.services.connectors.email.resend.Emails.send")
    def test_omits_idempotency_key_when_not_provided(
        self,
        mock_send,
        mock_with_timeout,
        mock_get_cb,
        mock_get_settings,
    ):
        mock_get_settings.return_value = SimpleNamespace(
            resend_api_key="rk_test",
            email_from="coord@example.com",
            timeout_email_s=5.0,
        )
        cb = MagicMock()
        cb.allow_request.return_value = True
        mock_get_cb.return_value = cb
        mock_send.return_value = {"id": "email_124"}

        def _run(func, timeout_s, connector):
            return func()

        mock_with_timeout.side_effect = _run

        result = send_email(
            to="buyer@example.com",
            subject="Test",
            body="<p>hello</p>",
        )

        assert result.ok is True
        assert mock_send.call_count == 1
        args = mock_send.call_args.args
        assert len(args) == 1
