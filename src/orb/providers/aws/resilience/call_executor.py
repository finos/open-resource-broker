"""Single owner of retry, circuit breaking and error shaping for AWS calls.

Every AWS call that goes through this executor is retried by ORB's resilience
layer only: the boto clients it drives are built with botocore retries turned
off, so the attempt budget is exactly the configured number of retries plus
the first call.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional, TypeVar

from botocore.exceptions import ClientError

from orb.domain.base.ports import LoggingPort
from orb.infrastructure.resilience import CircuitBreakerOpenError, MaxRetriesExceededError, retry
from orb.providers.aws.resilience.botocore_retry_gate import executor_call
from orb.providers.aws.resilience.retry_classifier import (
    call_overrides,
    register_aws_retry_classifier,
)

T = TypeVar("T")

# Retry budgets (retries after the first call) when no configuration is available.
DEFAULT_REQUEST_RETRY_ATTEMPTS = 3
DEFAULT_DESCRIBE_RETRY_ATTEMPTS = 2

_DEFAULT_CIRCUIT_BREAKER = {
    "failure_threshold": 5,
    "reset_timeout": 60,
    "half_open_timeout": 30,
}

# Calls that provision, change or tear down capacity are guarded by a circuit
# breaker even when the caller did not mark them ``critical``.
CRITICAL_OPERATIONS = frozenset(
    {
        "create_fleet",
        "request_spot_fleet",
        "create_auto_scaling_group",
        "run_instances",
        "modify_fleet",
        "delete_fleets",
        "cancel_spot_fleet_requests",
        "update_auto_scaling_group",
        "delete_auto_scaling_group",
    }
)

# Instance-id keyed describes right after creation can see EC2's eventual
# consistency; these are retried for those calls only.
EVENTUAL_CONSISTENCY_ERRORS = frozenset(
    {"InvalidInstanceID.NotFound", "InvalidReservationID.NotFound"}
)

_UNNAMED = ("<lambda>", "aws_operation")


def usable_operation_name(func: Any) -> Optional[str]:
    """Return ``func.__name__`` when it identifies the operation, else None."""
    name = getattr(func, "__name__", None)
    if isinstance(name, str) and name and name not in _UNNAMED and not name.startswith("<"):
        return name
    return None


class AWSCallExecutor:
    """Run AWS calls with classified retries, per-operation breakers and typed errors."""

    def __init__(
        self,
        logger: LoggingPort,
        *,
        handler_type: str = "default",
        provider_name: Optional[str] = None,
        config_port: Any = None,
        attempts_provider: Optional[Callable[[], tuple[int, int]]] = None,
        error_converter: Optional[Callable[[ClientError, str], Exception]] = None,
    ) -> None:
        """
        Args:
            logger: Logging port.
            handler_type: Handler or adapter name that scopes circuit breakers.
            provider_name: Provider instance name that scopes circuit breakers.
            config_port: Source of the circuit breaker settings.
            attempts_provider: Returns ``(request_retries, describe_retries)``.
            error_converter: Maps a ``ClientError`` to a typed domain error. When
                absent, or when a call sets ``preserve_client_error``, the original
                ``ClientError`` is raised.
        """
        self._logger = logger
        self._handler_type = handler_type
        self._provider_name = provider_name
        self._config_port = config_port
        self._attempts_provider = attempts_provider
        self._error_converter = error_converter
        register_aws_retry_classifier()

    @classmethod
    def for_client(cls, aws_client: Any, service: str, logger: LoggingPort) -> AWSCallExecutor:
        """Build an executor for a collaborator of an ``AWSClient``.

        Tolerates partial clients: anything that cannot be read from
        *aws_client* falls back to the defaults. Callers use
        ``preserve_client_error=True`` so they keep interpreting AWS error codes.
        """
        provider_name = getattr(aws_client, "_provider_name", None)
        config_port = getattr(aws_client, "_config_manager", None)
        return cls(
            logger,
            handler_type=service,
            provider_name=provider_name if isinstance(provider_name, str) else None,
            config_port=config_port,
            attempts_provider=getattr(aws_client, "retry_attempts", None),
        )

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def retry_attempts(self) -> tuple[int, int]:
        """Return ``(request_retries, describe_retries)``."""
        if self._attempts_provider is not None:
            try:
                request_retries, describe_retries = self._attempts_provider()
                if (
                    isinstance(request_retries, int)
                    and isinstance(describe_retries, int)
                    and request_retries >= 0
                    and describe_retries >= 0
                ):
                    return request_retries, describe_retries
            except Exception as exc:
                self._logger.debug("Could not resolve AWS retry attempts: %s", exc)
        return DEFAULT_REQUEST_RETRY_ATTEMPTS, DEFAULT_DESCRIBE_RETRY_ATTEMPTS

    def circuit_breaker_config(self) -> dict[str, int]:
        """Circuit breaker settings from configuration, defaulting when absent."""
        config = dict(_DEFAULT_CIRCUIT_BREAKER)
        if self._config_port is None:
            return config
        try:
            cb_config = self._config_port.app_config.circuit_breaker
        except AttributeError:
            return config
        threshold = getattr(cb_config, "failure_threshold", None)
        recovery = getattr(cb_config, "recovery_timeout", None)
        half_open = getattr(cb_config, "half_open_timeout", None)
        if isinstance(threshold, int):
            config["failure_threshold"] = threshold
        if isinstance(recovery, int):
            config["reset_timeout"] = recovery
        if isinstance(half_open, int) and half_open > 0:
            config["half_open_timeout"] = half_open
        return config

    def circuit_breaker_key(self, operation_name: str) -> str:
        """Breaker identity: provider instance, handler type and operation."""
        return f"aws:{self._provider_name or 'default'}:{self._handler_type}:{operation_name}"

    def strategy_config(self, operation_type: str, operation_name: Optional[str]) -> dict[str, Any]:
        """Build the ``retry`` decorator arguments for an operation type."""
        if operation_type == "standard" and operation_name in CRITICAL_OPERATIONS:
            operation_type = "critical"

        request_retries, describe_retries = self.retry_attempts()

        if operation_type == "critical":
            if not operation_name:
                raise ValueError(
                    "operation_name is required for critical AWS calls "
                    "(it keys the circuit breaker)"
                )
            return {
                "strategy": "circuit_breaker",
                "service": self.circuit_breaker_key(operation_name),
                "max_attempts": request_retries,
                "base_delay": 1.0,
                "max_delay": 30.0,
                "jitter": True,
                "count_per_call": True,
                **self.circuit_breaker_config(),
            }
        if operation_type == "read_only":
            return {
                "strategy": "exponential",
                "service": self._handler_type,
                "max_attempts": describe_retries,
                "base_delay": 0.5,
                "max_delay": 10.0,
            }
        return {
            "strategy": "exponential",
            "service": self._handler_type,
            "max_attempts": request_retries,
            "base_delay": 1.0,
            "max_delay": 30.0,
        }

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        operation_type: str = "standard",
        operation_name: Optional[str] = None,
        non_retryable_errors: Optional[list[str]] = None,
        retryable_errors: Optional[frozenset[str]] = None,
        preserve_client_error: bool = False,
        log_payload: bool = True,
        log_response: bool = True,
        **kwargs: Any,
    ) -> T:
        """Execute *func* with the retry policy for *operation_type*.

        Args:
            func: Callable performing the AWS call.
            operation_type: ``critical`` (circuit breaker), ``standard`` or ``read_only``.
            operation_name: Name of the operation; defaults to ``func.__name__``.
                Required for ``critical`` calls when ``func`` has no usable name.
            non_retryable_errors: Error codes attempted once and never counted
                against the circuit breaker.
            retryable_errors: Extra error codes retried for this call only.
            preserve_client_error: Raise the original ``ClientError`` instead of a
                typed domain error.

        Raises:
            CircuitBreakerOpenError: The operation's circuit is open.
            Exception: The typed domain error (or the original ``ClientError``)
                once retries are exhausted; never ``MaxRetriesExceededError``.
        """
        name = operation_name or usable_operation_name(func)
        config = self.strategy_config(operation_type, name)
        display_name = name or "aws_operation"

        def _format_debug_data(data: Any) -> str:
            try:
                return json.dumps(data, default=str, indent=2, sort_keys=True)
            except Exception:
                return str(data)

        @retry(**config)
        def wrapped_operation() -> T:
            if log_payload:
                self._logger.debug(
                    "Calling AWS operation %s with payload:\n%s",
                    display_name,
                    _format_debug_data({"args": args, "kwargs": kwargs}),
                )
            return func(*args, **kwargs)

        try:
            with (
                executor_call(),
                call_overrides(
                    retryable=retryable_errors,
                    non_retryable=frozenset(non_retryable_errors or ()),
                ),
            ):
                result = wrapped_operation()
            if log_response:
                self._logger.debug(
                    "AWS operation %s response:\n%s",
                    display_name,
                    _format_debug_data(result),
                )
            return result
        except CircuitBreakerOpenError:
            self._logger.error(
                "Circuit breaker OPEN for %s.%s",
                self._handler_type,
                display_name,
                extra={
                    "service": self._handler_type,
                    "operation": display_name,
                    "operation_type": operation_type,
                },
            )
            raise
        except Exception as exc:
            error = exc.last_exception if isinstance(exc, MaxRetriesExceededError) else exc
            if isinstance(error, ClientError) and not preserve_client_error:
                if self._error_converter is not None:
                    converted = self._error_converter(error, display_name)
                    raise converted from error
            if error is exc:
                raise
            # Surface the original failure, not the retry bookkeeping around it.
            error.__suppress_context__ = True
            raise error
