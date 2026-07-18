"""Unit tests for resilience/strategy/circuit_breaker.py."""

import time
import uuid

import pytest

from orb.domain.base.exceptions import QuotaError
from orb.infrastructure.resilience.exceptions import CircuitBreakerOpenError
from orb.infrastructure.resilience.strategy.circuit_breaker import (
    CircuitBreakerStrategy,
    CircuitState,
)


def _unique_service() -> str:
    """Return a unique service name so tests don't share circuit state."""
    return f"test-svc-{uuid.uuid4().hex}"


@pytest.mark.unit
class TestCircuitBreakerInitialState:
    """Tests for initial state after construction."""

    def test_starts_in_closed_state(self) -> None:
        svc = _unique_service()
        cb = CircuitBreakerStrategy(service_name=svc, failure_threshold=3)
        info = cb.get_circuit_info()
        assert info["state"] == CircuitState.CLOSED.value

    def test_failure_count_starts_at_zero(self) -> None:
        svc = _unique_service()
        cb = CircuitBreakerStrategy(service_name=svc, failure_threshold=3)
        assert cb.get_circuit_info()["failure_count"] == 0

    def test_has_state_returns_true_after_init(self) -> None:
        svc = _unique_service()
        CircuitBreakerStrategy(service_name=svc, failure_threshold=3)
        assert CircuitBreakerStrategy.has_state(svc) is True

    def test_has_state_returns_false_for_unknown_service(self) -> None:
        assert CircuitBreakerStrategy.has_state("completely-unknown-svc-xyz") is False


@pytest.mark.unit
class TestCircuitBreakerShouldRetry:
    """Tests for should_retry logic across CLOSED/OPEN/HALF_OPEN states."""

    def test_closed_state_allows_retries_below_max_attempts(self) -> None:
        svc = _unique_service()
        cb = CircuitBreakerStrategy(service_name=svc, failure_threshold=10, max_attempts=3)
        # attempt 0 with a generic exception — circuit still CLOSED, under threshold
        result = cb.should_retry(0, IOError("transient"))
        assert result is True

    def test_closed_state_rejects_retry_at_max_attempts(self) -> None:
        svc = _unique_service()
        cb = CircuitBreakerStrategy(service_name=svc, failure_threshold=10, max_attempts=2)
        assert cb.should_retry(2, IOError("too many")) is False

    def test_circuit_opens_after_threshold(self) -> None:
        svc = _unique_service()
        # threshold=2: first call succeeds, second call opens and raises
        cb = CircuitBreakerStrategy(service_name=svc, failure_threshold=2, max_attempts=10)
        cb.should_retry(0, IOError("f1"))  # failure_count=1, still CLOSED
        try:
            cb.should_retry(1, IOError("f2"))  # failure_count=2 >= threshold -> OPEN -> raises
        except CircuitBreakerOpenError:
            pass
        assert cb.get_circuit_info()["state"] == CircuitState.OPEN.value

    def test_open_circuit_raises_circuit_breaker_error(self) -> None:
        svc = _unique_service()
        # threshold=1: first call to should_retry opens circuit and raises immediately
        cb = CircuitBreakerStrategy(service_name=svc, failure_threshold=1, max_attempts=10)
        with pytest.raises(CircuitBreakerOpenError):
            cb.should_retry(0, IOError("first"))

    def test_quota_error_forces_open_immediately(self) -> None:
        svc = _unique_service()
        cb = CircuitBreakerStrategy(service_name=svc, failure_threshold=100, max_attempts=10)
        result = cb.should_retry(0, QuotaError("quota exceeded"))
        assert result is False
        assert cb.get_circuit_info()["state"] == CircuitState.OPEN.value

    def test_non_retryable_exception_returns_false_without_opening(self) -> None:
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

        svc = _unique_service()
        cb = CircuitBreakerStrategy(service_name=svc, failure_threshold=100, max_attempts=10)

        try:
            result = cb.should_retry(0, _Permanent("bad request"))
            assert result is False
            # circuit must remain CLOSED — non-retryable errors are caller errors
            assert cb.get_circuit_info()["state"] == CircuitState.CLOSED.value
        finally:
            clear_classifiers()


@pytest.mark.unit
class TestCircuitBreakerTransitions:
    """Tests for OPEN → HALF_OPEN → CLOSED transitions."""

    def test_open_transitions_to_half_open_after_reset_timeout(self) -> None:
        svc = _unique_service()
        cb = CircuitBreakerStrategy(
            service_name=svc, failure_threshold=1, max_attempts=10, reset_timeout=1
        )
        # Open the circuit directly via _force_open to avoid the raises path
        cb._force_open(svc)
        assert cb.get_circuit_info()["state"] == CircuitState.OPEN.value

        # Simulate time passing beyond reset_timeout
        future = time.time() + 2
        state = cb._get_current_state(future)
        assert state == CircuitState.HALF_OPEN

    def test_half_open_allows_first_attempt(self) -> None:
        svc = _unique_service()
        cb = CircuitBreakerStrategy(
            service_name=svc, failure_threshold=1, max_attempts=10, reset_timeout=0
        )
        # Force OPEN directly so we don't hit the raises path
        cb._force_open(svc)
        # Advance time so circuit transitions to HALF_OPEN (reset_timeout=0)
        future_time = time.time() + 1
        cb._get_current_state(future_time)
        assert CircuitBreakerStrategy._circuit_states[svc]["state"] == CircuitState.HALF_OPEN
        # In HALF_OPEN, attempt 0 should be allowed (returns True)
        result = cb.should_retry(0, IOError("test"))
        assert result is True

    def test_record_success_resets_failure_count(self) -> None:
        svc = _unique_service()
        cb = CircuitBreakerStrategy(service_name=svc, failure_threshold=10, max_attempts=10)
        cb.record_failure(time.time())
        cb.record_failure(time.time())
        cb.record_success()
        assert cb.get_circuit_info()["failure_count"] == 0

    def test_record_success_closes_half_open_circuit(self) -> None:
        svc = _unique_service()
        cb = CircuitBreakerStrategy(
            service_name=svc, failure_threshold=1, max_attempts=10, reset_timeout=0
        )
        # Force OPEN then set state to HALF_OPEN directly
        cb._force_open(svc)
        CircuitBreakerStrategy._circuit_states[svc]["state"] = CircuitState.HALF_OPEN
        cb.record_success()
        assert cb.get_circuit_info()["state"] == CircuitState.CLOSED.value

    def test_half_open_timeout_returns_to_open(self) -> None:
        svc = _unique_service()
        cb = CircuitBreakerStrategy(
            service_name=svc,
            failure_threshold=1,
            max_attempts=10,
            reset_timeout=0,
            half_open_timeout=1,
        )
        # Force HALF_OPEN
        CircuitBreakerStrategy._circuit_states[svc] = {
            "state": CircuitState.HALF_OPEN,
            "failure_count": 1,
            "last_failure_time": time.time() - 2,
            "last_success_time": None,
            "half_open_start_time": time.time() - 2,
        }
        state = cb._get_current_state(time.time())
        assert state == CircuitState.OPEN


@pytest.mark.unit
class TestCircuitBreakerDelayAndInfo:
    """Tests for get_delay, calculate_delay, and get_circuit_info."""

    def test_closed_state_gives_positive_delay(self) -> None:
        svc = _unique_service()
        cb = CircuitBreakerStrategy(service_name=svc, base_delay=1.0, jitter=False)
        delay = cb.get_delay(0)
        assert delay >= 0.0

    def test_open_state_gives_zero_delay(self) -> None:
        svc = _unique_service()
        cb = CircuitBreakerStrategy(service_name=svc, failure_threshold=1, base_delay=1.0)
        # Force OPEN
        CircuitBreakerStrategy._circuit_states[svc] = {
            "state": CircuitState.OPEN,
            "failure_count": 5,
            "last_failure_time": time.time(),
            "last_success_time": None,
            "half_open_start_time": None,
        }
        delay = cb.get_delay(0)
        assert delay == 0.0

    def test_calculate_delay_exponential_grows(self) -> None:
        svc = _unique_service()
        cb = CircuitBreakerStrategy(service_name=svc, base_delay=1.0, max_delay=60.0, jitter=False)
        d0 = cb.calculate_delay(0)
        d1 = cb.calculate_delay(1)
        d2 = cb.calculate_delay(2)
        assert d0 < d1 < d2

    def test_calculate_delay_caps_at_max_delay(self) -> None:
        svc = _unique_service()
        cb = CircuitBreakerStrategy(service_name=svc, base_delay=1.0, max_delay=5.0, jitter=False)
        delay = cb.calculate_delay(100)
        assert delay <= 5.0

    def test_calculate_delay_with_jitter_stays_non_negative(self) -> None:
        svc = _unique_service()
        cb = CircuitBreakerStrategy(service_name=svc, base_delay=1.0, max_delay=10.0, jitter=True)
        for attempt in range(5):
            assert cb.calculate_delay(attempt) >= 0.0

    def test_get_circuit_info_contains_expected_keys(self) -> None:
        svc = _unique_service()
        cb = CircuitBreakerStrategy(service_name=svc, failure_threshold=5)
        info = cb.get_circuit_info()
        expected_keys = {
            "service_name",
            "state",
            "failure_count",
            "failure_threshold",
            "last_failure_time",
            "last_success_time",
            "reset_timeout",
            "half_open_timeout",
        }
        assert expected_keys.issubset(info.keys())
        assert info["service_name"] == svc
        assert info["failure_threshold"] == 5

    def test_on_retry_does_not_raise(self) -> None:
        svc = _unique_service()
        cb = CircuitBreakerStrategy(service_name=svc)
        cb.on_retry(0, IOError("retry"))

    def test_force_open_sets_state_to_open(self) -> None:
        svc = _unique_service()
        cb = CircuitBreakerStrategy(service_name=svc, failure_threshold=100)
        cb._force_open(svc)
        assert CircuitBreakerStrategy._circuit_states[svc]["state"] == CircuitState.OPEN
