"""Unit tests for K8sCircuitBreaker live-threshold rebind on config reload.

Covers:
* Without threshold_provider: behaves identically to the base class
  (uses the static failure_threshold integer).
* With threshold_provider: ``record_failure`` uses the value returned by
  the callable — mutating the underlying config object causes the CB to
  honour the new threshold on the very next failure event.
* Threshold increase after construction: previously-accumulated failures
  below the new threshold no longer trip the circuit.
* Threshold decrease after construction: a lower threshold trips the
  circuit immediately if the failure count already meets it.
"""

from __future__ import annotations

import time
import uuid

from orb.infrastructure.resilience.strategy.circuit_breaker import CircuitState
from orb.providers.k8s.resilience.circuit_breaker import K8sCircuitBreaker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_key() -> str:
    """Return a unique service key so tests never share circuit state."""
    return f"test.k8s.cb.{uuid.uuid4().hex}"


def _make_cb(service_name: str, failure_threshold: int = 5, **kwargs) -> K8sCircuitBreaker:
    return K8sCircuitBreaker(
        service_name=service_name,
        failure_threshold=failure_threshold,
        reset_timeout=60,
        max_attempts=3,
        base_delay=0.0,
        max_delay=0.0,
        jitter=False,
        **kwargs,
    )


def _drive_failures(cb: K8sCircuitBreaker, count: int) -> None:
    now = time.time()
    for _ in range(count):
        cb.record_failure(now)


def _state(cb: K8sCircuitBreaker) -> CircuitState:
    return K8sCircuitBreaker._circuit_states[cb.service_name]["state"]


def _failure_count(cb: K8sCircuitBreaker) -> int:
    return K8sCircuitBreaker._circuit_states[cb.service_name]["failure_count"]


# ---------------------------------------------------------------------------
# Tests: static (no threshold_provider) — identical to base class
# ---------------------------------------------------------------------------


def test_static_threshold_trips_circuit() -> None:
    """Without threshold_provider, failure_threshold is used as a constant."""
    key = _fresh_key()
    cb = _make_cb(key, failure_threshold=3)

    _drive_failures(cb, 2)
    assert _state(cb) == CircuitState.CLOSED, "circuit must stay CLOSED before threshold"

    _drive_failures(cb, 1)  # 3rd failure
    assert _state(cb) == CircuitState.OPEN, "circuit must OPEN at threshold"


def test_static_no_provider_uses_constructor_value() -> None:
    """Verify _get_failure_threshold() returns constructor value when no provider."""
    key = _fresh_key()
    cb = _make_cb(key, failure_threshold=7)
    assert cb._get_failure_threshold() == 7


# ---------------------------------------------------------------------------
# Tests: dynamic threshold_provider
# ---------------------------------------------------------------------------


class _MutableConfig:
    """Simple mutable config stand-in for the live-reload scenario."""

    def __init__(self, threshold: int) -> None:
        self.circuit_breaker_failure_threshold = threshold


def test_dynamic_threshold_honoured_on_record_failure() -> None:
    """threshold_provider() is read on every record_failure; mutate → immediate effect."""
    key = _fresh_key()
    cfg = _MutableConfig(threshold=10)

    cb = _make_cb(
        key,
        failure_threshold=10,  # initial static value (irrelevant with provider)
        threshold_provider=lambda: cfg.circuit_breaker_failure_threshold,
    )

    # Drive 4 failures — well below the current threshold of 10.
    _drive_failures(cb, 4)
    assert _state(cb) == CircuitState.CLOSED

    # Config reload: lower threshold to 5.
    cfg.circuit_breaker_failure_threshold = 5

    # One more failure (5th) should now trip the circuit because threshold=5.
    _drive_failures(cb, 1)
    assert _state(cb) == CircuitState.OPEN, (
        "circuit must OPEN immediately after config reload lowers threshold"
    )


def test_threshold_increase_keeps_circuit_closed() -> None:
    """Raising the threshold after failures prevents premature circuit open."""
    key = _fresh_key()
    cfg = _MutableConfig(threshold=3)

    cb = _make_cb(
        key,
        failure_threshold=3,
        threshold_provider=lambda: cfg.circuit_breaker_failure_threshold,
    )

    # 2 failures — just under the original threshold of 3.
    _drive_failures(cb, 2)
    assert _state(cb) == CircuitState.CLOSED

    # Config reload: raise threshold to 10.
    cfg.circuit_breaker_failure_threshold = 10

    # One more failure (3rd) must NOT trip the circuit (new threshold=10).
    _drive_failures(cb, 1)
    assert _state(cb) == CircuitState.CLOSED, (
        "circuit must stay CLOSED after threshold was raised above current failure count"
    )
    assert _failure_count(cb) == 3


def test_get_failure_threshold_reads_live_value() -> None:
    """_get_failure_threshold() reflects mutations to the config object."""
    key = _fresh_key()
    cfg = _MutableConfig(threshold=5)
    cb = _make_cb(key, threshold_provider=lambda: cfg.circuit_breaker_failure_threshold)

    assert cb._get_failure_threshold() == 5

    cfg.circuit_breaker_failure_threshold = 99
    assert cb._get_failure_threshold() == 99


def test_provider_takes_precedence_over_constructor_int() -> None:
    """When threshold_provider is given, the constructor integer is ignored."""
    key = _fresh_key()
    cfg = _MutableConfig(threshold=2)
    cb = _make_cb(
        key,
        failure_threshold=100,  # large static value — must be ignored
        threshold_provider=lambda: cfg.circuit_breaker_failure_threshold,
    )

    # 2 failures should trip the circuit (dynamic threshold=2), not 100.
    _drive_failures(cb, 2)
    assert _state(cb) == CircuitState.OPEN, (
        "threshold_provider must override the static failure_threshold integer"
    )
