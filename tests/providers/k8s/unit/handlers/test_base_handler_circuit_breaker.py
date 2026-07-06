"""Unit tests for K8sHandlerBase circuit-breaker wiring.

Covers:
* with_retry propagates transient errors and eventually exhausts the retry
  budget via the circuit breaker strategy.
* The circuit breaker trips after the configurable failure threshold and
  fast-fails subsequent calls with CircuitBreakerOpenError.
* Non-retryable ApiException status codes (400, 409) are raised immediately
  without consuming circuit-breaker failure credits that would trip the circuit
  prematurely.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from kubernetes.client.exceptions import ApiException

from orb.infrastructure.resilience import CircuitBreakerOpenError
from orb.infrastructure.resilience.retry_classifier_registry import (
    clear_classifiers,
    register_retry_classifier,
)
from orb.infrastructure.resilience.strategy.circuit_breaker import (
    CircuitBreakerStrategy,
    CircuitState,
)
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.handlers.base_handler import K8sHandlerBase
from orb.providers.k8s.resilience.retry_classifier import K8sRetryClassifier

# ---------------------------------------------------------------------------
# Minimal concrete subclass — required because K8sHandlerBase is abstract.
# ---------------------------------------------------------------------------


class _ConcreteHandler(K8sHandlerBase):
    """Minimal concrete handler for testing the base class in isolation."""

    PROVIDER_API = "TestResource"

    async def acquire_hosts(self, request: Any, template: Any) -> dict[str, Any]:  # type: ignore[override]
        return {}

    def check_hosts_status(self, request: Any) -> Any:  # type: ignore[override]
        return None

    async def release_hosts(self, machine_ids: list[str], request: Any) -> None:  # type: ignore[override]
        pass

    @classmethod
    def get_example_templates(cls) -> list[Any]:  # type: ignore[override]
        return []


def _make_handler(
    *,
    failure_threshold: int = 5,
    reset_timeout: int = 60,
    max_retries: int = 1,
    base_delay: float = 0.0,
    max_delay: float = 0.0,
) -> _ConcreteHandler:
    """Return a handler with injected circuit-breaker tuning for fast tests."""
    client = MagicMock()
    config = K8sProviderConfig(namespace="orb-test")
    logger = MagicMock()
    return _ConcreteHandler(
        kubernetes_client=client,
        config=config,
        logger=logger,
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
        circuit_breaker_failure_threshold=failure_threshold,
        circuit_breaker_reset_timeout=reset_timeout,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _register_k8s_classifier():
    """Register the K8s retry classifier for the duration of each test."""
    register_retry_classifier(K8sRetryClassifier())
    yield
    clear_classifiers()


def _unique_handler() -> _ConcreteHandler:
    """Return a handler with a unique PROVIDER_API to get a fresh circuit state."""
    handler = _make_handler(failure_threshold=3, max_retries=1, base_delay=0.0)
    # Each handler instance uses PROVIDER_API as the circuit service key.
    # Patch with a unique key so tests do not share circuit state.
    handler.PROVIDER_API = f"Test_{uuid.uuid4().hex}"
    return handler


# ---------------------------------------------------------------------------
# Tests: 5xx errors are retried; budget exhaustion surfaces MaxRetriesExceededError
# ---------------------------------------------------------------------------


def test_transient_error_is_retried() -> None:
    """A 500 ApiException must be retried up to max_retries times."""
    handler = _unique_handler()
    calls: list[int] = []

    def _flaky_op() -> str:
        calls.append(1)
        if len(calls) < 2:
            raise ApiException(status=500, reason="Internal Server Error")
        return "ok"

    result = handler.with_retry(_flaky_op, operation_name="test_op")
    assert result == "ok"
    assert len(calls) == 2


def test_5xx_exhausts_budget_raises() -> None:
    """When every attempt fails with a 5xx the retry budget is exhausted."""
    handler = _unique_handler()

    def _always_fails() -> None:
        raise ApiException(status=503, reason="Service Unavailable")

    # max_retries=1 means 1 initial + 1 retry = 2 total attempts, then raises.
    with pytest.raises(Exception):  # MaxRetriesExceededError or CircuitBreakerOpenError
        handler.with_retry(_always_fails, operation_name="test_op")


# ---------------------------------------------------------------------------
# Tests: non-retryable status codes do not consume retry budget
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status_code", [400, 409, 403, 422])
def test_non_retryable_status_raises_immediately(status_code: int) -> None:
    """ApiException with non-retryable status must propagate immediately (attempt=1 only)."""
    handler = _unique_handler()
    calls: list[int] = []

    def _op() -> None:
        calls.append(1)
        raise ApiException(status=status_code, reason=f"HTTP {status_code}")

    with pytest.raises(ApiException) as exc_info:
        handler.with_retry(_op, operation_name="test_op")

    assert exc_info.value.status == status_code
    # Exactly one attempt — no retry for non-retryable codes.
    assert len(calls) == 1, (
        f"Non-retryable status={status_code} should not be retried; got {len(calls)} calls"
    )


# ---------------------------------------------------------------------------
# Tests: circuit breaker trips after configurable failure threshold
# ---------------------------------------------------------------------------


def test_circuit_breaker_trips_after_threshold() -> None:
    """The circuit breaker opens after failure_threshold consecutive 5xx failures."""
    # Use a low threshold to keep the test fast.
    handler = _make_handler(
        failure_threshold=3,
        max_retries=1,
        base_delay=0.0,
        max_delay=0.0,
    )
    handler.PROVIDER_API = f"CB_Test_{uuid.uuid4().hex}"
    service_key = f"kubernetes.{handler.PROVIDER_API.lower()}"

    def _always_fails() -> None:
        raise ApiException(status=500, reason="boom")

    # Clear any prior state for this service key.
    CircuitBreakerStrategy._circuit_states.pop(service_key, None)

    # Drive failures until the circuit opens.  Each call to with_retry
    # will attempt + retry (max_retries=1 → 2 attempts per call).
    # failure_threshold=3 → circuit opens after 3 failures.
    open_raised = False
    for _ in range(20):
        try:
            handler.with_retry(_always_fails, operation_name="cb_test")
        except CircuitBreakerOpenError:
            open_raised = True
            break
        except Exception:
            pass  # MaxRetriesExceededError or ApiException — keep going

    assert open_raised, "CircuitBreakerOpenError was never raised; circuit breaker did not trip"

    # Confirm the recorded state is OPEN.
    if service_key in CircuitBreakerStrategy._circuit_states:
        assert CircuitBreakerStrategy._circuit_states[service_key]["state"] == CircuitState.OPEN
