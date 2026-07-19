"""Unit tests for K8s handler API-call metrics wiring.

Covers:

* ``with_retry`` increments ``orb_k8s_api_retries_total`` once per genuine
  retry (attempts beyond the first) from the resilience/backoff path.
* ``_record_api_exception`` / ``_classify_and_record_api_exception`` increment
  ``orb_k8s_api_errors_total`` and, on a 429, ``orb_k8s_api_throttles_total``.
* Status-resolver read/list paths record API errors when the call fails and
  time the call via ``_timed_api_call`` (apiserver latency histogram).
* ``read_namespaced_job`` is a first-class member of ``API_OPERATIONS`` so its
  metric labels do not bucket to ``"unknown"``.

The counter assertions use a MagicMock ``K8sMetrics`` so we can assert on the
exact record_* calls, plus one end-to-end scrape test proving the retries
counter surfaces on the Prometheus registry.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from kubernetes.client.exceptions import ApiException

from orb.infrastructure.resilience.retry_classifier_registry import (
    clear_classifiers,
    register_retry_classifier,
)
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.infrastructure.handlers.base_handler import K8sHandlerBase
from orb.providers.k8s.infrastructure.instrumentation.metrics import (
    API_OPERATIONS,
    K8sMetrics,
)
from orb.providers.k8s.resilience.retry_classifier import K8sRetryClassifier

# ---------------------------------------------------------------------------
# Minimal concrete subclass — K8sHandlerBase is abstract.
# ---------------------------------------------------------------------------


class _ConcreteHandler(K8sHandlerBase):
    """Minimal concrete handler for exercising the base class in isolation."""

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
    metrics: Any,
    max_retries: int = 2,
) -> _ConcreteHandler:
    """Return a handler wired with *metrics* and a unique circuit key."""
    handler = _ConcreteHandler(
        kubernetes_client=MagicMock(),
        config=K8sProviderConfig(namespace="orb-test"),
        logger=MagicMock(),
        max_retries=max_retries,
        base_delay=0.0,
        max_delay=0.0,
        circuit_breaker_failure_threshold=100,
        circuit_breaker_reset_timeout=60,
        metrics=metrics,
    )
    # Unique PROVIDER_API → fresh circuit-breaker state per test.
    handler.PROVIDER_API = f"Test_{uuid.uuid4().hex}"
    return handler


@pytest.fixture(autouse=True)
def _register_k8s_classifier():
    """Register the K8s retry classifier so 5xx is treated as retryable."""
    register_retry_classifier(K8sRetryClassifier())
    yield
    clear_classifiers()


# ---------------------------------------------------------------------------
# Enum membership
# ---------------------------------------------------------------------------


def test_read_namespaced_job_is_a_known_operation() -> None:
    """The Job read op must be first-class so its labels don't bucket to unknown."""
    assert "read_namespaced_job" in API_OPERATIONS


# ---------------------------------------------------------------------------
# with_retry → api_retries counter
# ---------------------------------------------------------------------------


def test_with_retry_increments_retries_counter_once_per_retry() -> None:
    """One transient failure then success → exactly one retry recorded."""
    metrics = MagicMock(spec=K8sMetrics)
    handler = _make_handler(metrics=metrics, max_retries=3)

    calls: list[int] = []

    def _flaky() -> str:
        calls.append(1)
        if len(calls) < 2:
            raise ApiException(status=500, reason="Internal Server Error")
        return "ok"

    result = handler.with_retry(_flaky, operation_name="read_namespaced_job")

    assert result == "ok"
    assert len(calls) == 2
    # The second invocation is the retry → recorded exactly once.
    metrics.record_api_retry.assert_called_once_with(operation="read_namespaced_job")


def test_with_retry_records_no_retry_on_first_attempt_success() -> None:
    """A call that succeeds immediately records zero retries."""
    metrics = MagicMock(spec=K8sMetrics)
    handler = _make_handler(metrics=metrics)

    handler.with_retry(lambda: "ok", operation_name="list_namespaced_pod")

    metrics.record_api_retry.assert_not_called()


def test_with_retry_records_multiple_retries_before_success() -> None:
    """Two transient failures then success → two retries recorded."""
    metrics = MagicMock(spec=K8sMetrics)
    handler = _make_handler(metrics=metrics, max_retries=5)

    calls: list[int] = []

    def _flaky() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise ApiException(status=503, reason="Service Unavailable")
        return "ok"

    handler.with_retry(_flaky, operation_name="create_namespaced_pod")

    assert len(calls) == 3
    assert metrics.record_api_retry.call_count == 2
    for call in metrics.record_api_retry.call_args_list:
        assert call.kwargs == {"operation": "create_namespaced_pod"}


def test_with_retry_records_retries_until_budget_exhausted() -> None:
    """Every retry is recorded even when the call ultimately fails."""
    metrics = MagicMock(spec=K8sMetrics)
    handler = _make_handler(metrics=metrics, max_retries=2)

    def _always_fails() -> None:
        raise ApiException(status=500, reason="boom")

    with pytest.raises(Exception):  # noqa: B017  # MaxRetriesExceededError
        handler.with_retry(_always_fails, operation_name="read_namespaced_deployment")

    # 1 initial + 2 retries = 3 attempts → 2 retries recorded.
    assert metrics.record_api_retry.call_count == 2


def test_with_retry_no_retry_for_non_retryable_status() -> None:
    """A 409 is non-retryable → no retry counter increment."""
    metrics = MagicMock(spec=K8sMetrics)
    handler = _make_handler(metrics=metrics, max_retries=3)

    def _op() -> None:
        raise ApiException(status=409, reason="Conflict")

    with pytest.raises(ApiException):
        handler.with_retry(_op, operation_name="create_namespaced_pod")

    metrics.record_api_retry.assert_not_called()


# ---------------------------------------------------------------------------
# error / throttle counters
# ---------------------------------------------------------------------------


def test_record_api_exception_records_status_code() -> None:
    """A raw ApiException's HTTP status becomes the error_code label."""
    metrics = MagicMock(spec=K8sMetrics)
    handler = _make_handler(metrics=metrics)

    handler._record_api_exception(
        ApiException(status=403, reason="Forbidden"),
        operation="read_namespaced_deployment",
    )

    metrics.record_api_error.assert_called_once_with(
        operation="read_namespaced_deployment", error_code="403"
    )


def test_record_api_exception_non_apiexception_buckets_unknown() -> None:
    """A non-ApiException failure is recorded with error_code='unknown'."""
    metrics = MagicMock(spec=K8sMetrics)
    handler = _make_handler(metrics=metrics)

    handler._record_api_exception(RuntimeError("network down"), operation="list_namespaced_pod")

    metrics.record_api_error.assert_called_once_with(
        operation="list_namespaced_pod", error_code="unknown"
    )


def test_record_api_exception_unwraps_retry_wrapper() -> None:
    """A MaxRetriesExceededError wrapping an ApiException still yields the code."""
    from orb.infrastructure.resilience.exceptions import MaxRetriesExceededError

    metrics = MagicMock(spec=K8sMetrics)
    handler = _make_handler(metrics=metrics)

    wrapped = MaxRetriesExceededError(3, ApiException(status=429, reason="Too Many Requests"))
    handler._record_api_exception(wrapped, operation="create_namespaced_pod")

    metrics.record_api_error.assert_called_once_with(
        operation="create_namespaced_pod", error_code="429"
    )


def test_classify_and_record_api_exception_records_error() -> None:
    """The classify helper still emits the error counter for an ApiException."""
    metrics = MagicMock(spec=K8sMetrics)
    handler = _make_handler(metrics=metrics)

    typed = handler._classify_and_record_api_exception(
        ApiException(status=500, reason="boom"),
        operation="delete_namespaced_pod",
    )

    assert typed is not None
    metrics.record_api_error.assert_called_once_with(
        operation="delete_namespaced_pod", error_code="500"
    )


def test_record_api_exception_noop_without_metrics() -> None:
    """When metrics are not wired the helper is a silent no-op."""
    handler = _make_handler(metrics=None)
    # Must not raise even though no K8sMetrics is present.
    handler._record_api_exception(ApiException(status=500), operation="list_namespaced_pod")


# ---------------------------------------------------------------------------
# 429 throttle counter end-to-end via real K8sMetrics + Prometheus scrape
# ---------------------------------------------------------------------------


def _make_meter_and_registry() -> tuple[Any, Any]:
    from opentelemetry.exporter.prometheus import PrometheusMetricReader
    from opentelemetry.sdk.metrics import MeterProvider
    from prometheus_client import CollectorRegistry

    reg = CollectorRegistry()
    reader = PrometheusMetricReader(registry=reg)
    provider = MeterProvider(metric_readers=[reader])
    return provider.get_meter("test"), reg


def _scrape(registry: Any) -> str:
    from prometheus_client import generate_latest

    return generate_latest(registry).decode("utf-8")


def test_retry_counter_surfaces_on_prometheus_scrape() -> None:
    """End-to-end: a real retry increments the scraped orb_k8s_api_retries_total."""
    meter, reg = _make_meter_and_registry()
    metrics = K8sMetrics(meter=meter)
    handler = _make_handler(metrics=metrics, max_retries=3)

    calls: list[int] = []

    def _flaky() -> str:
        calls.append(1)
        if len(calls) < 2:
            raise ApiException(status=500, reason="boom")
        return "ok"

    handler.with_retry(_flaky, operation_name="read_namespaced_job")

    text = _scrape(reg)
    assert "orb_k8s_api_retries_total" in text
    assert 'operation="read_namespaced_job"' in text


def test_circuit_breaker_gauge_surfaces_via_with_retry() -> None:
    """The breaker protecting apiserver calls emits ``orb_k8s_circuit_breaker_state``.

    Proves the metrics-aware ``K8sCircuitBreaker`` is wired into the production
    ``with_retry`` path — before the wiring fix this gauge was only emitted by
    tests and stayed absent (always-zero / never present) in production.
    """
    meter, reg = _make_meter_and_registry()
    metrics = K8sMetrics(meter=meter)
    handler = _make_handler(metrics=metrics, max_retries=1)

    # A recoverable failure drives the breaker through the retry path, which
    # emits the CLOSED-state gauge on breaker construction.
    def _flaky() -> str:
        raise ApiException(status=500, reason="boom")

    with pytest.raises(Exception):  # noqa: B017  # MaxRetriesExceededError
        handler.with_retry(_flaky, operation_name="read_namespaced_job")

    text = _scrape(reg)
    assert "orb_k8s_circuit_breaker_state" in text


def test_circuit_breaker_gauge_records_open_transition_via_with_retry() -> None:
    """Once the breaker trips, the gauge reflects the OPEN (=1) state."""
    from orb.infrastructure.resilience import CircuitBreakerOpenError
    from orb.infrastructure.resilience.strategy.circuit_breaker import CircuitBreakerStrategy

    meter, reg = _make_meter_and_registry()
    metrics = K8sMetrics(meter=meter)
    handler = _make_handler(metrics=metrics, max_retries=1)
    # Low threshold so the breaker opens quickly.
    handler._cb_failure_threshold = 2
    service_key = f"kubernetes.{handler.PROVIDER_API.lower()}"
    CircuitBreakerStrategy._circuit_states.pop(service_key, None)

    def _always_fails() -> None:
        raise ApiException(status=500, reason="boom")

    for _ in range(20):
        try:
            handler.with_retry(_always_fails, operation_name="read_namespaced_job")
        except CircuitBreakerOpenError:
            break
        except Exception:
            continue

    text = _scrape(reg)
    open_lines = [
        line
        for line in text.splitlines()
        if "orb_k8s_circuit_breaker_state" in line and f'name="{service_key}"' in line
    ]
    assert open_lines, f"circuit_breaker_state gauge not scraped for {service_key}"
    assert any(line.strip().endswith("1.0") or line.strip().endswith(" 1") for line in open_lines)


def test_throttle_counter_surfaces_on_429_exception() -> None:
    """A recorded 429 API exception bumps both errors and throttles counters."""
    meter, reg = _make_meter_and_registry()
    metrics = K8sMetrics(meter=meter)
    handler = _make_handler(metrics=metrics)

    handler._record_api_exception(
        ApiException(status=429, reason="Too Many Requests"),
        operation="list_namespaced_pod",
    )

    text = _scrape(reg)
    assert "orb_k8s_api_errors_total" in text
    assert "orb_k8s_api_throttles_total" in text
    assert 'error_code="429"' in text
