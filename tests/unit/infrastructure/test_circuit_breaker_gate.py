"""Pre-call gating, single-probe and per-call counting of the circuit breaker."""

import threading
import time
import uuid
from unittest.mock import MagicMock, patch

import pytest

from orb.infrastructure.resilience.exceptions import (
    CircuitBreakerOpenError,
    MaxRetriesExceededError,
)
from orb.infrastructure.resilience.retry_decorator import retry
from orb.infrastructure.resilience.strategy.circuit_breaker import (
    CircuitBreakerStrategy,
    CircuitState,
)


def _svc() -> str:
    return f"gate-svc-{uuid.uuid4().hex}"


def _state(svc: str) -> CircuitState:
    return CircuitBreakerStrategy._circuit_states[svc]["state"]


@pytest.mark.unit
class TestOpenCircuitShortCircuits:
    def test_open_circuit_does_not_invoke_the_function(self) -> None:
        svc = _svc()
        calls = MagicMock(side_effect=IOError("down"))

        @retry(
            strategy="circuit_breaker",
            service=svc,
            max_attempts=0,
            failure_threshold=2,
            reset_timeout=60,
            count_per_call=True,
        )
        def op() -> None:
            calls()

        for _ in range(2):
            with pytest.raises((MaxRetriesExceededError, IOError)):
                op()
        assert _state(svc) == CircuitState.OPEN
        invoked = calls.call_count

        with pytest.raises(CircuitBreakerOpenError):
            op()
        assert calls.call_count == invoked

    def test_gate_runs_on_every_attempt(self) -> None:
        """A circuit opened by another caller stops the next attempt, chained to the real error."""
        svc = _svc()
        opener = CircuitBreakerStrategy(service_name=svc, failure_threshold=1, reset_timeout=60)
        attempts: list[int] = []

        @retry(
            strategy="circuit_breaker",
            service=svc,
            max_attempts=5,
            base_delay=0,
            jitter=False,
            count_per_call=True,
        )
        def op() -> None:
            attempts.append(1)
            # Another caller trips the breaker while this call is mid-retry.
            opener._force_open(svc)
            raise IOError("throttled")

        with patch("orb.infrastructure.resilience.retry_decorator.time.sleep"):
            with pytest.raises(CircuitBreakerOpenError) as exc_info:
                op()
        assert len(attempts) == 1
        assert isinstance(exc_info.value.__cause__, IOError)


@pytest.mark.unit
class TestHalfOpenProbe:
    def _open_and_expire(self, svc: str, **kwargs):
        cb = CircuitBreakerStrategy(
            service_name=svc, failure_threshold=1, reset_timeout=1, **kwargs
        )
        cb._force_open(svc)
        CircuitBreakerStrategy._circuit_states[svc]["last_failure_time"] = time.time() - 5
        return cb

    @pytest.mark.parametrize("count_per_call", [True, False])
    def test_failed_probe_reopens_with_a_single_attempt(self, count_per_call: bool) -> None:
        svc = _svc()
        self._open_and_expire(svc)
        calls = MagicMock(side_effect=IOError("still down"))

        @retry(
            strategy="circuit_breaker",
            service=svc,
            max_attempts=5,
            base_delay=0,
            failure_threshold=1,
            reset_timeout=1,
            count_per_call=count_per_call,
        )
        def op() -> None:
            calls()

        with pytest.raises(IOError):
            op()
        assert calls.call_count == 1
        assert _state(svc) == CircuitState.OPEN
        assert CircuitBreakerStrategy._circuit_states[svc]["probe_in_flight"] is False
        with pytest.raises(CircuitBreakerOpenError):
            op()
        assert calls.call_count == 1

    def test_successful_probe_closes_the_circuit(self) -> None:
        svc = _svc()
        self._open_and_expire(svc)

        @retry(strategy="circuit_breaker", service=svc, failure_threshold=1, reset_timeout=1)
        def op() -> str:
            return "ok"

        assert op() == "ok"
        assert _state(svc) == CircuitState.CLOSED
        assert CircuitBreakerStrategy._circuit_states[svc]["probe_in_flight"] is False

    def test_only_one_probe_is_admitted_under_concurrency(self) -> None:
        svc = _svc()
        self._open_and_expire(svc)
        gate = CircuitBreakerStrategy(service_name=svc, failure_threshold=1, reset_timeout=1)
        admitted: list[int] = []
        rejected: list[int] = []
        barrier = threading.Barrier(8)

        def worker() -> None:
            barrier.wait()
            try:
                gate.before_call()
                admitted.append(1)
            except CircuitBreakerOpenError:
                rejected.append(1)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(admitted) == 1
        assert len(rejected) == 7

    def test_stuck_probe_is_released_after_half_open_timeout(self) -> None:
        svc = _svc()
        cb = self._open_and_expire(svc, half_open_timeout=1)
        cb.before_call()  # becomes the probe
        CircuitBreakerStrategy._circuit_states[svc]["half_open_start_time"] = time.time() - 5
        with pytest.raises(CircuitBreakerOpenError):
            cb.before_call()
        assert _state(svc) == CircuitState.OPEN
        assert CircuitBreakerStrategy._circuit_states[svc]["probe_in_flight"] is False

    def test_first_state_creation_is_race_free(self) -> None:
        svc = _svc()
        barrier = threading.Barrier(8)
        seen: list[int] = []

        def worker() -> None:
            barrier.wait()
            cb = CircuitBreakerStrategy(service_name=svc)
            seen.append(id(CircuitBreakerStrategy._circuit_states[svc]))
            cb.record_failure(time.time())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(set(seen)) == 1
        assert CircuitBreakerStrategy._circuit_states[svc]["failure_count"] == 8


@pytest.mark.unit
class TestCountPerCall:
    def test_failures_are_counted_once_per_exhausted_call(self) -> None:
        svc = _svc()

        @retry(
            strategy="circuit_breaker",
            service=svc,
            max_attempts=3,
            base_delay=0,
            jitter=False,
            failure_threshold=5,
            count_per_call=True,
        )
        def op() -> None:
            raise IOError("throttled")

        with patch("orb.infrastructure.resilience.retry_decorator.time.sleep"):
            with pytest.raises(MaxRetriesExceededError):
                op()
        # Four attempts, one counted failure.
        assert CircuitBreakerStrategy._circuit_states[svc]["failure_count"] == 1
        assert _state(svc) == CircuitState.CLOSED

    def test_per_attempt_counting_remains_the_default(self) -> None:
        svc = _svc()
        cb = CircuitBreakerStrategy(service_name=svc, failure_threshold=10, max_attempts=10)
        cb.should_retry(0, IOError("a"))
        cb.should_retry(1, IOError("b"))
        assert CircuitBreakerStrategy._circuit_states[svc]["failure_count"] == 2

    def test_permanent_errors_are_not_counted(self) -> None:
        from orb.infrastructure.resilience.retry_classifier_registry import (
            clear_classifiers,
            register_retry_classifier,
        )

        class _Permanent:
            def is_non_retryable(self, exception: Exception) -> bool:
                return isinstance(exception, ValueError)

        classifier = _Permanent()
        register_retry_classifier(classifier)  # type: ignore[arg-type]
        try:
            svc = _svc()

            @retry(
                strategy="circuit_breaker",
                service=svc,
                max_attempts=3,
                failure_threshold=1,
                count_per_call=True,
            )
            def op() -> None:
                raise ValueError("bad request")

            for _ in range(3):
                with pytest.raises(ValueError):
                    op()
            assert CircuitBreakerStrategy._circuit_states[svc]["failure_count"] == 0
            assert _state(svc) == CircuitState.CLOSED
        finally:
            clear_classifiers()
