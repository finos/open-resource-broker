"""Unit tests for resilience/exceptions.py."""

import pytest

from orb.infrastructure.resilience.exceptions import (
    CircuitBreakerOpenError,
    InvalidRetryStrategyError,
    MaxRetriesExceededError,
    RetryConfigurationError,
    RetryError,
)


@pytest.mark.unit
class TestMaxRetriesExceededError:
    """Tests for MaxRetriesExceededError."""

    def test_is_retry_error_subclass(self) -> None:
        exc = MaxRetriesExceededError(3, ValueError("last"))
        assert isinstance(exc, RetryError)

    def test_stores_attempts(self) -> None:
        exc = MaxRetriesExceededError(5, IOError("io"))
        assert exc.attempts == 5

    def test_stores_last_exception(self) -> None:
        last = RuntimeError("kaboom")
        exc = MaxRetriesExceededError(2, last)
        assert exc.last_exception is last

    def test_message_contains_attempts_and_last_error(self) -> None:
        exc = MaxRetriesExceededError(4, ValueError("oops"))
        msg = str(exc)
        assert "4" in msg
        assert "oops" in msg


@pytest.mark.unit
class TestInvalidRetryStrategyError:
    """Tests for InvalidRetryStrategyError."""

    def test_is_retry_error_subclass(self) -> None:
        exc = InvalidRetryStrategyError("adaptive")
        assert isinstance(exc, RetryError)

    def test_stores_strategy(self) -> None:
        exc = InvalidRetryStrategyError("custom_strategy")
        assert exc.strategy == "custom_strategy"

    def test_message_contains_strategy(self) -> None:
        exc = InvalidRetryStrategyError("my_strategy")
        assert "my_strategy" in str(exc)


@pytest.mark.unit
class TestRetryConfigurationError:
    """Tests for RetryConfigurationError."""

    def test_is_retry_error_subclass(self) -> None:
        exc = RetryConfigurationError("bad config")
        assert isinstance(exc, RetryError)


@pytest.mark.unit
class TestCircuitBreakerOpenError:
    """Tests for CircuitBreakerOpenError."""

    def test_is_retry_error_subclass(self) -> None:
        exc = CircuitBreakerOpenError("svc", 5, 1234567890.0)
        assert isinstance(exc, RetryError)

    def test_stores_service_name(self) -> None:
        exc = CircuitBreakerOpenError("my-service", 3, 1000.0)
        assert exc.service_name == "my-service"

    def test_stores_failure_count(self) -> None:
        exc = CircuitBreakerOpenError("svc", 7, 999.0)
        assert exc.failure_count == 7

    def test_stores_last_failure_time(self) -> None:
        ts = 1700000000.5
        exc = CircuitBreakerOpenError("svc", 1, ts)
        assert exc.last_failure_time == ts

    def test_message_contains_service_name_and_failure_count(self) -> None:
        exc = CircuitBreakerOpenError("payment-svc", 10, 0.0)
        msg = str(exc)
        assert "payment-svc" in msg
        assert "10" in msg
