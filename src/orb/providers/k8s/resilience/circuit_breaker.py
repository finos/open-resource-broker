"""K8s-aware circuit breaker with live-reloadable failure threshold.

The base :class:`~orb.infrastructure.resilience.strategy.circuit_breaker.CircuitBreakerStrategy`
captures ``failure_threshold`` as a plain integer attribute at construction
time.  A config reload that mutates the provider configuration has no effect
until the process restarts — the old threshold lives forever inside the
already-constructed strategy object.

:class:`K8sCircuitBreaker` fixes this by accepting an optional
``threshold_provider`` callable.  The base class exposes a
``_get_failure_threshold()`` hook that this subclass overrides to
consult the callable on every ``record_failure`` invocation — a live
reload that writes a new value into the underlying config object is
respected immediately on the next failure event without any public-API
change.

Callers that omit ``threshold_provider`` get identical behaviour to the
base class (the integer passed as ``failure_threshold`` is used as a
constant fallback).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Optional

from orb.infrastructure.resilience.strategy.circuit_breaker import (
    CircuitBreakerStrategy,
    CircuitState,
)

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    pass

# Map CircuitState enum → integer gauge value (0=closed 1=open 2=half_open).
_STATE_TO_GAUGE: dict[CircuitState, int] = {
    CircuitState.CLOSED: 0,
    CircuitState.OPEN: 1,
    CircuitState.HALF_OPEN: 2,
}


class K8sCircuitBreaker(CircuitBreakerStrategy):
    """Circuit breaker whose failure threshold is read from a live callable.

    Parameters
    ----------
    threshold_provider:
        Optional zero-argument callable that returns the current failure
        threshold.  Called on every :meth:`record_failure` invocation so
        config reloads take effect without a process restart.  When
        ``None``, the integer ``failure_threshold`` passed to the
        constructor is used as a constant fallback — behaviour is
        identical to the base class.
    metrics:
        Optional :class:`~orb.providers.k8s.infrastructure.services.metrics.K8sMetrics`
        instance.  When supplied, the ``orb_k8s_circuit_breaker_state`` gauge
        is updated whenever the circuit state changes.  ``None`` disables
        metric emission (default) — no-op path.

    All other parameters are forwarded unchanged to
    :class:`~orb.infrastructure.resilience.strategy.circuit_breaker.CircuitBreakerStrategy`.
    """

    def __init__(
        self,
        service_name: str,
        failure_threshold: int = 5,
        reset_timeout: int = 60,
        half_open_timeout: int = 30,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        jitter: bool = True,
        *,
        threshold_provider: Optional[Callable[[], int]] = None,
        metrics: Any = None,
        **kwargs,
    ) -> None:
        super().__init__(
            service_name=service_name,
            failure_threshold=failure_threshold,
            reset_timeout=reset_timeout,
            half_open_timeout=half_open_timeout,
            max_attempts=max_attempts,
            base_delay=base_delay,
            max_delay=max_delay,
            jitter=jitter,
            **kwargs,
        )
        self._threshold_provider = threshold_provider
        self._metrics = metrics
        # Emit the initial CLOSED state so the gauge is always present.
        self._emit_circuit_state(CircuitState.CLOSED)

    # ------------------------------------------------------------------
    # Metrics helpers
    # ------------------------------------------------------------------

    def _emit_circuit_state(self, state: CircuitState) -> None:
        """Set the ``orb_k8s_circuit_breaker_state`` gauge when metrics are wired."""
        if self._metrics is None:
            return
        try:
            self._metrics.set_circuit_breaker_state(
                name=self.service_name,
                state=_STATE_TO_GAUGE.get(state, 0),
            )
        except Exception:  # pragma: no cover — defensive against misconfigured metrics
            pass

    # ------------------------------------------------------------------
    # State transition overrides — emit gauge on every state change
    # ------------------------------------------------------------------

    def record_failure(self, current_time: float) -> None:
        """Record a failure, update circuit state, and emit the state gauge."""
        state_before = self._circuit_states[self.service_name]["state"]
        super().record_failure(current_time)
        state_after = self._circuit_states[self.service_name]["state"]
        if state_after != state_before:
            self._emit_circuit_state(state_after)

    def record_success(self) -> None:
        """Record a success, update circuit state, and emit the state gauge."""
        state_before = self._circuit_states[self.service_name]["state"]
        super().record_success()
        state_after = self._circuit_states[self.service_name]["state"]
        if state_after != state_before:
            self._emit_circuit_state(state_after)

    # ------------------------------------------------------------------
    # Live-threshold hook
    # ------------------------------------------------------------------

    def _get_failure_threshold(self) -> int:
        """Return the live failure threshold.

        Overrides the base class hook so the parent ``record_failure``
        body picks up threshold changes on every invocation.  Falls back
        to the static constructor value if:

        * no ``threshold_provider`` was supplied,
        * the provider callable raises (e.g. config read fails
          mid-reload — a raise here would disable the circuit breaker
          entirely, so we prefer the stale-but-safe fallback),
        * the provider returns ``None`` or a non-int value.
        """
        if self._threshold_provider is None:
            return self.failure_threshold
        try:
            value = self._threshold_provider()
        except Exception:
            return self.failure_threshold
        if not isinstance(value, int) or value <= 0:
            return self.failure_threshold
        return value
