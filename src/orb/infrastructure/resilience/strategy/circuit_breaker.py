"""Circuit breaker retry strategy."""

import secrets
import threading
import time
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

from orb.domain.base.exceptions import QuotaError
from orb.infrastructure.logging.logger import get_logger
from orb.infrastructure.resilience.exceptions import CircuitBreakerOpenError
from orb.infrastructure.resilience.retry_classifier_registry import (
    is_non_retryable as _registry_is_non_retryable,
)
from orb.infrastructure.resilience.strategy.base import RetryStrategy

logger = get_logger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing fast
    HALF_OPEN = "half_open"  # Testing recovery


class CircuitBreakerStrategy(RetryStrategy):
    """
    Circuit breaker strategy that prevents cascading failures.

    The circuit breaker monitors failures and transitions between states:
    - CLOSED: Normal operation, allows all requests
    - OPEN: Fails fast, blocks all requests for a timeout period
    - HALF_OPEN: Admits exactly one probe call to test if the service recovered

    When driven through :meth:`before_call` (as the retry decorator does) the
    breaker also gates every attempt: an OPEN circuit raises
    ``CircuitBreakerOpenError`` without invoking the protected function, and
    only one caller is admitted as the half-open probe at a time.
    """

    # Class-level storage for circuit states (shared across instances)
    _circuit_states: dict[str, dict[str, Any]] = {}

    # Guards creation of, and every mutation to, the shared circuit states.
    _lock = threading.RLock()

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
        count_per_call: bool = False,
        **kwargs,
    ) -> None:
        """
        Initialize circuit breaker strategy.

        Args:
            service_name: Name of the service being protected
            failure_threshold: Number of failures before opening circuit
            reset_timeout: Seconds to wait before transitioning to half-open
            half_open_timeout: Seconds to wait in half-open before closing
            max_attempts: Maximum retry attempts
            base_delay: Base delay in seconds
            max_delay: Maximum delay in seconds
            jitter: Whether to add jitter to delays
            count_per_call: Count one failure per exhausted logical call instead
                of one per attempt.  Only counting changes; the half-open probe
                handling is identical in both modes.
            **kwargs: Additional retry strategy parameters
        """
        # Initialize base attributes since we don't have a concrete base class
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter

        self.service_name = service_name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.half_open_timeout = half_open_timeout
        self.count_per_call = count_per_call

        # Initialize circuit state if not exists
        with self._lock:
            if service_name not in self._circuit_states:
                self._circuit_states[service_name] = {
                    "state": CircuitState.CLOSED,
                    "failure_count": 0,
                    "last_failure_time": None,
                    "last_success_time": None,
                    "half_open_start_time": None,
                    "probe_in_flight": False,
                }

        logger.debug(
            "Initialized circuit breaker for %s",
            service_name,
            extra={
                "service_name": service_name,
                "failure_threshold": failure_threshold,
                "reset_timeout": reset_timeout,
                "half_open_timeout": half_open_timeout,
            },
        )

    @classmethod
    def has_state(cls, service_name: str) -> bool:
        """Return True if a circuit state entry exists for service_name."""
        with cls._lock:
            return service_name in cls._circuit_states

    def is_open(self) -> bool:
        """Return True while the circuit is open (half-open still admits a trial call)."""
        return self._get_current_state(time.time()) == CircuitState.OPEN

    def _force_open(self, service_name: str) -> None:
        """Force the circuit to OPEN immediately, bypassing the failure threshold."""
        with self._lock:
            circuit_state = self._circuit_states[service_name]
            circuit_state["state"] = CircuitState.OPEN
            circuit_state["last_failure_time"] = time.time()
            circuit_state["probe_in_flight"] = False
            circuit_state["half_open_start_time"] = None
        logger.error(
            "Circuit breaker force-opened for %s due to quota error",
            service_name,
            extra={"service_name": service_name, "state": CircuitState.OPEN.value},
        )

    def _is_permanent_failure(self, exception: Exception) -> bool:
        """True for caller errors that must not count as service degradation."""
        return isinstance(exception, QuotaError) or _registry_is_non_retryable(exception)

    def before_call(self) -> None:
        """Gate one attempt.

        Raises ``CircuitBreakerOpenError`` without the protected function being
        invoked when the circuit is OPEN, or when it is HALF_OPEN and another
        caller already holds the probe.  Otherwise admits the attempt; the
        caller that finds the circuit HALF_OPEN becomes the probe.
        """
        with self._lock:
            circuit_state = self._circuit_states[self.service_name]
            state = self._get_current_state(time.time())

            if state == CircuitState.OPEN:
                raise self._open_error(circuit_state)

            if state == CircuitState.HALF_OPEN:
                if circuit_state.get("probe_in_flight"):
                    raise self._open_error(circuit_state)
                circuit_state["probe_in_flight"] = True
                logger.info(
                    "Circuit breaker HALF_OPEN for %s - admitting the probe call",
                    self.service_name,
                    extra={"service_name": self.service_name, "state": state.value},
                )

    def _open_error(self, circuit_state: dict[str, Any]) -> CircuitBreakerOpenError:
        return CircuitBreakerOpenError(
            service_name=self.service_name,
            failure_count=circuit_state["failure_count"],
            last_failure_time=circuit_state["last_failure_time"],
        )

    def _release_probe(self) -> None:
        """Let the next caller probe (the probe ended without a service verdict)."""
        with self._lock:
            self._circuit_states[self.service_name]["probe_in_flight"] = False

    def _reopen_after_failed_probe(self, current_time: float) -> None:
        circuit_state = self._circuit_states[self.service_name]
        circuit_state["state"] = CircuitState.OPEN
        circuit_state["last_failure_time"] = current_time
        circuit_state["half_open_start_time"] = None
        circuit_state["probe_in_flight"] = False
        logger.warning(
            "Circuit breaker HALF_OPEN for %s - probe failed, reopening circuit",
            self.service_name,
            extra={"service_name": self.service_name, "state": CircuitState.OPEN.value},
        )

    def should_retry(self, attempt: int, exception: Exception) -> bool:
        """
        Determine if operation should be retried based on circuit state.

        Args:
            attempt: Current attempt number (0-based)
            exception: Exception that occurred

        Returns:
            True if should retry, False otherwise
        """
        # Quota errors are never retryable — force circuit open immediately
        if isinstance(exception, QuotaError):
            self._force_open(self.service_name)
            return False

        # Provider-registered classifiers flag permanent client errors (e.g.
        # 409 Conflict, 403 RBAC denied from a provider SDK) that must not be
        # retried and must not be counted as circuit failures — they are caller
        # errors, not service degradation signals.
        if _registry_is_non_retryable(exception):
            # A probe that ends in a caller error says nothing about the
            # service; free the probe slot so the next caller can test it.
            self._release_probe()
            return False

        with self._lock:
            current_time = time.time()
            circuit_state = self._circuit_states[self.service_name]

            # A failure while HALF_OPEN is the probe failing: reopen at once and
            # stop. The probe is exactly one attempt, so there is no retry.
            if self._get_current_state(current_time) == CircuitState.HALF_OPEN:
                self._reopen_after_failed_probe(current_time)
                return False

            if not self.count_per_call:
                # Update failure count and state
                self.record_failure(current_time)

            # Check circuit state
            state = self._get_current_state(current_time)

            if state == CircuitState.OPEN:
                logger.warning(
                    "Circuit breaker OPEN for %s - failing fast",
                    self.service_name,
                    extra={
                        "service_name": self.service_name,
                        "state": state.value,
                        "failure_count": circuit_state["failure_count"],
                        "last_failure_time": circuit_state["last_failure_time"],
                    },
                )
                # Raise circuit breaker exception instead of returning False
                raise self._open_error(circuit_state)

            # CLOSED state: normal retry logic
            if attempt >= self.max_attempts:
                return False

            logger.info(
                "Circuit breaker CLOSED for %s - normal retry",
                self.service_name,
                extra={
                    "service_name": self.service_name,
                    "state": state.value,
                    "attempt": attempt + 1,
                    "max_attempts": self.max_attempts,
                },
            )
            return True

    def on_give_up(self, attempt: int, exception: Exception) -> None:
        """Count a failed logical call once, when the retry loop gives up.

        Only used with ``count_per_call``. Permanent errors are never counted,
        and a failed half-open probe has already reopened the circuit.
        """
        if not self.count_per_call or self._is_permanent_failure(exception):
            return
        with self._lock:
            now = time.time()
            if self._get_current_state(now) == CircuitState.CLOSED:
                self.record_failure(now)

    def on_retry(self, attempt: int, exception: Exception) -> None:
        """
        Handle retry event and update circuit state.

        Args:
            attempt: Current attempt number (0-based)
            exception: Exception that occurred
        """
        current_time = time.time()
        state = self._get_current_state(current_time)

        logger.warning(
            "Retrying %s operation (attempt %s/%s) with circuit breaker in %s state",
            self.service_name,
            attempt + 1,
            self.max_attempts,
            state.value,
            extra={
                "service_name": self.service_name,
                "attempt": attempt + 1,
                "max_attempts": self.max_attempts,
                "circuit_state": state.value,
                "exception": str(exception),
            },
        )

    def record_success(self) -> None:
        """
        Record a successful operation to potentially close the circuit.
        """
        with self._lock:
            current_time = time.time()
            circuit_state = self._circuit_states[self.service_name]

            circuit_state["last_success_time"] = current_time
            circuit_state["failure_count"] = 0  # Reset failure count on success

            # If we were in half-open state, close the circuit
            if circuit_state["state"] == CircuitState.HALF_OPEN:
                circuit_state["state"] = CircuitState.CLOSED
                circuit_state["half_open_start_time"] = None
                circuit_state["probe_in_flight"] = False

                logger.info(
                    "Circuit breaker CLOSED for %s after successful recovery",
                    self.service_name,
                    extra={
                        "service_name": self.service_name,
                        "state": CircuitState.CLOSED.value,
                    },
                )

    def _get_failure_threshold(self) -> int:
        """Return the current failure threshold.

        Subclasses may override to source the threshold from a live
        configuration object (e.g. to honour config reloads without a
        process restart).  Default returns the value frozen at
        construction time.
        """
        return self.failure_threshold

    def record_failure(self, current_time: float) -> None:
        """Record a failure and update circuit state."""
        with self._lock:
            circuit_state = self._circuit_states[self.service_name]

            circuit_state["failure_count"] += 1
            circuit_state["last_failure_time"] = current_time

            current_threshold = self._get_failure_threshold()

            # Check if we should open the circuit
            if (
                circuit_state["state"] == CircuitState.CLOSED
                and circuit_state["failure_count"] >= current_threshold
            ):
                circuit_state["state"] = CircuitState.OPEN

                logger.error(
                    "Circuit breaker OPENED for %s after %s failures",
                    self.service_name,
                    circuit_state["failure_count"],
                    extra={
                        "service_name": self.service_name,
                        "state": CircuitState.OPEN.value,
                        "failure_count": circuit_state["failure_count"],
                        "failure_threshold": current_threshold,
                    },
                )

    def _get_current_state(self, current_time: float) -> CircuitState:
        """Get the current circuit state, handling state transitions."""
        with self._lock:
            circuit_state = self._circuit_states[self.service_name]
            current_state = circuit_state["state"]

            if current_state == CircuitState.OPEN:
                # Check if we should transition to half-open
                if (
                    circuit_state["last_failure_time"]
                    and current_time - circuit_state["last_failure_time"] >= self.reset_timeout
                ):
                    circuit_state["state"] = CircuitState.HALF_OPEN
                    circuit_state["half_open_start_time"] = current_time
                    circuit_state["probe_in_flight"] = False

                    logger.info(
                        "Circuit breaker transitioning to HALF_OPEN for %s",
                        self.service_name,
                        extra={
                            "service_name": self.service_name,
                            "state": CircuitState.HALF_OPEN.value,
                            "reset_timeout": self.reset_timeout,
                        },
                    )

                    return CircuitState.HALF_OPEN

            elif current_state == CircuitState.HALF_OPEN:
                # Check if we should timeout back to open
                if (
                    circuit_state["half_open_start_time"]
                    and current_time - circuit_state["half_open_start_time"]
                    >= self.half_open_timeout
                ):
                    circuit_state["state"] = CircuitState.OPEN
                    circuit_state["half_open_start_time"] = None
                    circuit_state["probe_in_flight"] = False

                    logger.warning(
                        "Circuit breaker timeout in HALF_OPEN, returning to OPEN for %s",
                        self.service_name,
                        extra={
                            "service_name": self.service_name,
                            "state": CircuitState.OPEN.value,
                            "half_open_timeout": self.half_open_timeout,
                        },
                    )

                    return CircuitState.OPEN

            return current_state

    def get_delay(self, attempt: int) -> float:
        """
        Calculate delay before next attempt.

        For circuit breaker, we use exponential backoff when in CLOSED state,
        and return 0 for OPEN/HALF_OPEN states (since we fail fast or allow immediate test).

        Args:
            attempt: Current attempt number (0-based)

        Returns:
            Delay in seconds before next retry
        """
        current_time = time.time()
        state = self._get_current_state(current_time)

        if state == CircuitState.OPEN:
            # No delay for open circuit - we fail fast
            return 0.0
        elif state == CircuitState.HALF_OPEN:
            # Minimal delay for half-open test
            return 0.1
        else:
            # CLOSED state - use exponential backoff
            return self.calculate_delay(attempt)

    def calculate_delay(self, attempt: int) -> float:
        """
        Calculate exponential backoff delay.

        Args:
            attempt: Current attempt number (0-based)

        Returns:
            Delay in seconds
        """

        # Exponential backoff: base_delay * (2 ^ attempt)
        delay = self.base_delay * (2**attempt)

        # Cap at max_delay
        delay = min(delay, self.max_delay)

        # Add jitter if enabled
        if self.jitter:
            jitter_range = delay * 0.1  # 10% jitter
            # Use secrets for cryptographically secure randomness
            random_float = secrets.SystemRandom().random() * 2 - 1  # Range from -1 to 1
            delay += jitter_range * random_float

        return max(0.0, delay)

    def get_circuit_info(self) -> dict[str, Any]:
        """
        Get current circuit breaker information.

        Returns:
            Dictionary with circuit state information
        """
        current_time = time.time()
        state = self._get_current_state(current_time)
        circuit_state = self._circuit_states[self.service_name]

        return {
            "service_name": self.service_name,
            "state": state.value,
            "failure_count": circuit_state["failure_count"],
            "failure_threshold": self.failure_threshold,
            "last_failure_time": circuit_state["last_failure_time"],
            "last_success_time": circuit_state["last_success_time"],
            "reset_timeout": self.reset_timeout,
            "half_open_timeout": self.half_open_timeout,
        }
