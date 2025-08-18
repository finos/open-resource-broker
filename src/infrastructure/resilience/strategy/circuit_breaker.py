"""Circuit breaker retry strategy."""

import secrets
import time
from enum import Enum
from typing import Any, Dict

from infrastructure.logging.logger import get_logger
from infrastructure.resilience.exceptions import CircuitBreakerOpenError
from infrastructure.resilience.strategy.base import RetryStrategy

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
    - HALF_OPEN: Allows limited requests to test if service recovered
    """

    # Class-level storage for circuit states (shared across instances)
    _circuit_states: Dict[str, Dict[str, Any]] = {}

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
        **kwargs,
    ):
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

        # Initialize circuit state if not exists
        if service_name not in self._circuit_states:
            self._circuit_states[service_name] = {
                "state": CircuitState.CLOSED,
                "failure_count": 0,
                "last_failure_time": None,
                "last_success_time": None,
                "half_open_start_time": None,
            }

        logger.debug(
            "Initialized circuit breaker for %s", service_name,
            extra={
                "service_name": service_name,
                "failure_threshold": failure_threshold,
                "reset_timeout": reset_timeout,
                "half_open_timeout": half_open_timeout,
            },
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
        current_time = time.time()
        circuit_state = self._circuit_states[self.service_name]

        # Update failure count and state
        self._record_failure(current_time)

        # Check circuit state
        state = self._get_current_state(current_time)

        if state == CircuitState.OPEN:
            logger.warning(
                "Circuit breaker OPEN for %s - failing fast", self.service_name,
                extra={
                    "service_name": self.service_name,
                    "state": state.value,
                    "failure_count": circuit_state["failure_count"],
                    "last_failure_time": circuit_state["last_failure_time"],
                },
            )
            # Raise circuit breaker exception instead of returning False
            raise CircuitBreakerOpenError(
                service_name=self.service_name,
                failure_count=circuit_state["failure_count"],
                last_failure_time=circuit_state["last_failure_time"],
            )

        elif state == CircuitState.HALF_OPEN:
            # Allow limited retries in half-open state
            if attempt < 1:  # Only allow one retry attempt in half-open
                logger.info(
                    "Circuit breaker HALF_OPEN for %s - allowing test request",  self.service_name,
                    extra={
                        "service_name": self.service_name,
                        "state": state.value,
                        "attempt": attempt + 1,
                    },
                )
                return True
            else:
                logger.warning(
                    "Circuit breaker HALF_OPEN for %s - test failed, opening circuit",  self.service_name,
                    extra={
                        "service_name": self.service_name,
                        "state": state.value,
                        "attempt": attempt + 1,
                    },
                )
                return False

        else:  # CLOSED state
            # Normal retry logic
            if attempt >= self.max_attempts:
                return False

            logger.info(
                "Circuit breaker CLOSED for %s - normal retry", self.service_name,
                extra={
                    "service_name": self.service_name,
                    "state": state.value,
                    "attempt": attempt + 1,
                    "max_attempts": self.max_attempts,
                },
            )
            return True

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
            "Retrying %s operation (attempt %s/%s) ", self.service_name, attempt + 1, self.max_attempts
            f"with circuit breaker in {state.value} state",
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
        current_time = time.time()
        circuit_state = self._circuit_states[self.service_name]

        circuit_state["last_success_time"] = current_time
        circuit_state["failure_count"] = 0  # Reset failure count on success

        # If we were in half-open state, close the circuit
        if circuit_state["state"] == CircuitState.HALF_OPEN:
            circuit_state["state"] = CircuitState.CLOSED
            circuit_state["half_open_start_time"] = None

            logger.info(
                "Circuit breaker CLOSED for %s after successful recovery",  self.service_name,
                extra={
                    "service_name": self.service_name,
                    "state": CircuitState.CLOSED.value,
                },
            )

    def _record_failure(self, current_time: float) -> None:
        """Record a failure and update circuit state."""
        circuit_state = self._circuit_states[self.service_name]

        circuit_state["failure_count"] += 1
        circuit_state["last_failure_time"] = current_time

        # Check if we should open the circuit
        if (
            circuit_state["state"] == CircuitState.CLOSED
            and circuit_state["failure_count"] >= self.failure_threshold
        ):

            circuit_state["state"] = CircuitState.OPEN

            logger.error(
                "Circuit breaker OPENED for %s after %s failures",  self.service_name,  circuit_state['failure_count'],
                extra={
                    "service_name": self.service_name,
                    "state": CircuitState.OPEN.value,
                    "failure_count": circuit_state["failure_count"],
                    "failure_threshold": self.failure_threshold,
                },
            )

    def _get_current_state(self, current_time: float) -> CircuitState:
        """Get the current circuit state, handling state transitions."""
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

                logger.info(
                    "Circuit breaker transitioning to HALF_OPEN for %s",  self.service_name,
                    extra={
                        "service_name": self.service_name,
                        "state": CircuitState.HALF_OPEN.value,
                        "reset_timeout": self.reset_timeout,
                    },
                )

                return CircuitState.HALF_OPEN

        elif (
            current_state == CircuitState.HALF_OPEN
        ):  # noqa: SIM102 (false positive - if-elif structure)
            # Check if we should timeout back to open
            if (
                circuit_state["half_open_start_time"]
                and current_time - circuit_state["half_open_start_time"] >= self.half_open_timeout
            ):

                circuit_state["state"] = CircuitState.OPEN
                circuit_state["half_open_start_time"] = None

                logger.warning(
                    "Circuit breaker timeout in HALF_OPEN, returning to OPEN for %s",  self.service_name,
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

    def get_circuit_info(self) -> Dict[str, Any]:
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
