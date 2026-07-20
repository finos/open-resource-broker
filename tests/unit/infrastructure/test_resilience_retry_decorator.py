"""Unit tests for resilience/retry_decorator.py."""

from unittest.mock import patch

import pytest

from orb.infrastructure.resilience.exceptions import (
    InvalidRetryStrategyError,
    MaxRetriesExceededError,
)
from orb.infrastructure.resilience.retry_decorator import retry


@pytest.mark.unit
class TestRetryDecoratorExponential:
    """Tests for retry() with exponential strategy."""

    def test_success_on_first_attempt_returns_value(self) -> None:
        @retry(strategy="exponential", max_attempts=3, base_delay=0.0, jitter=False)
        def func():
            return 42

        assert func() == 42

    def test_success_after_one_failure(self) -> None:
        calls = {"count": 0}

        @retry(strategy="exponential", max_attempts=3, base_delay=0.0, jitter=False)
        def func():
            calls["count"] += 1
            if calls["count"] == 1:
                raise ConnectionError("transient")
            return "ok"

        with patch("time.sleep"):
            result = func()
        assert result == "ok"
        assert calls["count"] == 2

    def test_raises_max_retries_exceeded_when_always_failing(self) -> None:
        @retry(strategy="exponential", max_attempts=2, base_delay=0.0, jitter=False)
        def func():
            raise ConnectionError("always")

        with patch("time.sleep"), pytest.raises(MaxRetriesExceededError) as exc_info:
            func()

        assert exc_info.value.attempts >= 1
        assert isinstance(exc_info.value.last_exception, ConnectionError)

    def test_non_retryable_exception_raised_immediately(self) -> None:
        """An exception flagged by the classifier is not retried."""
        from orb.infrastructure.resilience.retry_classifier_registry import (
            clear_classifiers,
            register_retry_classifier,
        )

        class _Permanent(Exception):
            pass

        class _Classifier:
            def is_non_retryable(self, exception: Exception) -> bool:
                return isinstance(exception, _Permanent)

        clear_classifiers()
        register_retry_classifier(_Classifier())

        calls = {"count": 0}

        try:

            @retry(strategy="exponential", max_attempts=3, base_delay=0.0, jitter=False)
            def func():
                calls["count"] += 1
                raise _Permanent("permanent failure")

            with pytest.raises(_Permanent):
                func()

            assert calls["count"] == 1
        finally:
            clear_classifiers()

    def test_invalid_strategy_raises_immediately(self) -> None:
        with pytest.raises(InvalidRetryStrategyError):

            @retry(strategy="nonexistent")
            def func():
                return 1  # pragma: no cover

    def test_sleep_is_called_between_retries(self) -> None:
        """time.sleep must be called between retry attempts."""
        calls: list[float] = []

        @retry(strategy="exponential", max_attempts=2, base_delay=0.001, jitter=False)
        def func():
            raise IOError("retry me")

        with patch("time.sleep", side_effect=lambda d: calls.append(d)):
            with pytest.raises(MaxRetriesExceededError):
                func()

        assert len(calls) >= 1

    def test_result_passed_through_on_success_after_retries(self) -> None:
        """Ensure the actual return value is forwarded, not swallowed."""
        attempt = {"n": 0}

        @retry(strategy="exponential", max_attempts=3, base_delay=0.0, jitter=False)
        def func():
            attempt["n"] += 1
            if attempt["n"] < 3:
                raise IOError("not yet")
            return {"payload": "data"}

        with patch("time.sleep"):
            result = func()
        assert result == {"payload": "data"}


@pytest.mark.unit
class TestRetryDecoratorCircuitBreaker:
    """Tests for retry() with circuit_breaker strategy."""

    def _unique_service(self, name: str) -> str:
        """Return a unique service name to avoid cross-test state pollution."""
        import uuid

        return f"{name}-{uuid.uuid4().hex}"

    def test_success_path_returns_value(self) -> None:
        svc = self._unique_service("cb-ok")

        @retry(
            strategy="circuit_breaker",
            max_attempts=3,
            base_delay=0.0,
            jitter=False,
            service=svc,
            failure_threshold=5,
            reset_timeout=60,
        )
        def func():
            return "done"

        assert func() == "done"

    def test_circuit_breaker_created_for_service(self) -> None:
        from orb.infrastructure.resilience.strategy.circuit_breaker import CircuitBreakerStrategy

        svc = self._unique_service("cb-create")

        @retry(
            strategy="circuit_breaker",
            max_attempts=1,
            base_delay=0.0,
            jitter=False,
            service=svc,
            failure_threshold=10,
        )
        def func():
            return 1

        func()
        assert CircuitBreakerStrategy.has_state(svc)
