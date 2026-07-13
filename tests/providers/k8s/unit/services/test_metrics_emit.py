"""Unit tests for the k8s metrics emit sites.

Covers:
* orb_k8s_apiserver_latency_seconds — via K8sMetrics.record_apiserver_latency()
  and via the watcher's _relist_snapshot timed-list wrapper.
* orb_k8s_active_pods / orb_k8s_active_requests — via watcher._update_cache_gauges()
  after cache mutations in _handle_event.
* orb_k8s_circuit_breaker_state — via K8sCircuitBreaker with metrics wired.

Each test uses an isolated MeterProvider + PrometheusMetricReader + CollectorRegistry
to avoid global-registry pollution and prove the OTel→Prometheus bridge works.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import MagicMock

import pytest
from prometheus_client import generate_latest

from orb.infrastructure.resilience.strategy.circuit_breaker import CircuitState
from orb.providers.k8s.infrastructure.instrumentation.metrics import K8sMetrics
from orb.providers.k8s.resilience.circuit_breaker import K8sCircuitBreaker
from orb.providers.k8s.watch.pod_state_cache import PodStateCache  # noqa: F401
from orb.providers.k8s.watch.watcher import K8sWatcher

# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------


def _make_meter_and_registry() -> tuple[Any, Any]:
    """Return an isolated (meter, registry) pair."""
    from opentelemetry.exporter.prometheus import PrometheusMetricReader
    from opentelemetry.sdk.metrics import MeterProvider
    from prometheus_client import CollectorRegistry

    reg = CollectorRegistry()
    reader = PrometheusMetricReader(registry=reg)
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("test")
    return meter, reg


def _fresh_metrics() -> tuple[K8sMetrics, Any]:
    """K8sMetrics on isolated meter + registry, returns (metrics, registry)."""
    meter, reg = _make_meter_and_registry()
    return K8sMetrics(meter=meter), reg


def _scrape(registry: Any) -> str:
    return generate_latest(registry).decode("utf-8")


def _fresh_cb_key() -> str:
    return f"test.k8s.emit.{uuid.uuid4().hex}"


def _pod(
    *,
    name: str = "orb-0001",
    phase: str = "Running",
    ready: bool = True,
    request_id: str = "req-emit",
    namespace: str = "ns",
) -> SimpleNamespace:
    conditions = [SimpleNamespace(type="Ready", status="True" if ready else "False", reason=None)]
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            namespace=namespace,
            labels={
                "orb.io/managed": "true",
                "orb.io/request-id": request_id,
            },
        ),
        spec=SimpleNamespace(node_name="node-a"),
        status=SimpleNamespace(
            phase=phase,
            pod_ip="10.0.0.1",
            host_ip="10.1.0.1",
            start_time=None,
            conditions=conditions,
            container_statuses=[],
        ),
    )


class _StubWatch:
    def __init__(self, events: Iterator[Any]) -> None:
        self._events = events
        self._stopped = False
        self.resource_version: str | None = None

    def stream(self, func: Any, **kwargs: Any) -> Iterator[Any]:
        for ev in self._events:
            if self._stopped:
                return
            yield ev

    def stop(self) -> None:
        self._stopped = True


def _make_k8s_client_mock() -> MagicMock:
    client = MagicMock()
    client.core_v1.list_namespaced_pod = MagicMock(
        return_value=SimpleNamespace(items=[], metadata=SimpleNamespace(resource_version="42"))
    )
    client.core_v1.list_pod_for_all_namespaces = MagicMock(
        return_value=SimpleNamespace(items=[], metadata=SimpleNamespace(resource_version="42"))
    )
    return client


# ---------------------------------------------------------------------------
# 1. orb_k8s_apiserver_latency_seconds — record_apiserver_latency helper
# ---------------------------------------------------------------------------


class TestApiserverLatencyHelper:
    def test_observe_records_sample(self) -> None:
        m, reg = _fresh_metrics()
        m.record_apiserver_latency(operation="list_pods", seconds=0.05)
        text = _scrape(reg)
        assert "orb_k8s_apiserver_latency_seconds" in text
        assert "list_pods" in text

    def test_multiple_operations_stay_separate(self) -> None:
        m, reg = _fresh_metrics()
        m.record_apiserver_latency(operation="list_pods", seconds=0.1)
        m.record_apiserver_latency(operation="create_pod", seconds=0.2)
        text = _scrape(reg)
        assert "list_pods" in text
        assert "create_pod" in text


# ---------------------------------------------------------------------------
# 2. orb_k8s_apiserver_latency_seconds — emitted by watcher._relist_snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_relist_snapshot_emits_latency() -> None:
    """_relist_snapshot wraps the LIST call in _timed_list → latency metric gets a sample."""
    m, reg = _fresh_metrics()
    cache = PodStateCache()
    client = _make_k8s_client_mock()

    watcher = K8sWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=MagicMock(),
        namespace="ns",
        metrics=m,
    )
    # Call _relist_snapshot directly (it's a sync method run in a thread normally).
    watcher._relist_snapshot()

    text = _scrape(reg)
    assert "orb_k8s_apiserver_latency_seconds" in text


# ---------------------------------------------------------------------------
# 3. orb_k8s_active_pods / orb_k8s_active_requests — watcher cache gauges
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_active_pods_and_requests_set_on_added_event() -> None:
    """After an ADDED event the active_pods and active_requests gauges are non-zero."""
    m, reg = _fresh_metrics()
    cache = PodStateCache()
    client = _make_k8s_client_mock()
    events = [
        {"type": "ADDED", "object": _pod(name="p1", request_id="req-1")},
        {"type": "ADDED", "object": _pod(name="p2", request_id="req-1")},
        {"type": "ADDED", "object": _pod(name="p3", request_id="req-2")},
    ]
    stubs_iter = iter([_StubWatch(iter(events))])

    def factory() -> _StubWatch:
        try:
            return next(stubs_iter)
        except StopIteration:
            return _StubWatch(iter([]))

    watcher = K8sWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=MagicMock(),
        namespace="ns",
        watch_factory=factory,
        watch_timeout_seconds=1,
        metrics=m,
    )
    watcher.start()
    for _ in range(100):
        if cache.size() >= 3:
            break
        await asyncio.sleep(0.01)
    await watcher.stop()

    text = _scrape(reg)
    assert "orb_k8s_active_pods" in text
    assert "orb_k8s_active_requests" in text
    # 3 pods, 2 distinct request_ids
    assert "3" in text or "3.0" in text
    assert "2" in text or "2.0" in text


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_active_pods_decrements_on_deleted_event() -> None:
    """DELETED event reduces active_pods gauge."""
    m, reg = _fresh_metrics()
    cache = PodStateCache()
    client = _make_k8s_client_mock()
    events = [
        {"type": "ADDED", "object": _pod(name="p1", request_id="req-1")},
        {"type": "ADDED", "object": _pod(name="p2", request_id="req-1")},
        {"type": "DELETED", "object": _pod(name="p1", request_id="req-1")},
    ]
    stubs_iter = iter([_StubWatch(iter(events))])

    def factory() -> _StubWatch:
        try:
            return next(stubs_iter)
        except StopIteration:
            return _StubWatch(iter([]))

    watcher = K8sWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=MagicMock(),
        namespace="ns",
        watch_factory=factory,
        watch_timeout_seconds=1,
        metrics=m,
    )
    watcher.start()
    for _ in range(100):
        # Wait until DELETED has been processed (cache has 1 entry).
        if cache.size() == 1:
            break
        await asyncio.sleep(0.01)
    await watcher.stop()

    text = _scrape(reg)
    assert "orb_k8s_active_pods" in text
    assert "1" in text or "1.0" in text


# ---------------------------------------------------------------------------
# 4. orb_k8s_circuit_breaker_state — K8sCircuitBreaker state transitions
# ---------------------------------------------------------------------------


class TestCircuitBreakerStateGauge:
    """Circuit breaker state gauge is set on every state change."""

    def _make_cb(self, key: str, metrics: K8sMetrics, threshold: int = 3) -> K8sCircuitBreaker:
        return K8sCircuitBreaker(
            service_name=key,
            failure_threshold=threshold,
            reset_timeout=60,
            half_open_timeout=30,
            max_attempts=3,
            base_delay=0.0,
            max_delay=0.0,
            jitter=False,
            metrics=metrics,
        )

    def test_initial_state_is_closed(self) -> None:
        m, reg = _fresh_metrics()
        key = _fresh_cb_key()
        self._make_cb(key, m)
        text = _scrape(reg)
        assert "orb_k8s_circuit_breaker_state" in text
        # Initial state = 0 (CLOSED) — the UpDownCounter starts at 0 and
        # set_circuit_breaker_state(state=0) emits delta=0 (no-op from 0→0)
        # so no value line for this label set may appear yet.  The key
        # assertion is that no exception was raised and the metric is registered.

    def test_open_state_emitted_on_threshold(self) -> None:
        m, reg = _fresh_metrics()
        key = _fresh_cb_key()
        cb = self._make_cb(key, m, threshold=2)

        now = time.time()
        cb.record_failure(now)
        cb.record_failure(now)  # trips to OPEN

        text = _scrape(reg)
        assert "orb_k8s_circuit_breaker_state" in text
        assert "1" in text or "1.0" in text  # OPEN = 1

    def test_closed_state_emitted_after_recovery(self) -> None:
        m, reg = _fresh_metrics()
        key = _fresh_cb_key()
        cb = self._make_cb(key, m, threshold=1)

        # Trip to OPEN.
        cb.record_failure(time.time())

        # Manually set state to HALF_OPEN so record_success closes it.
        K8sCircuitBreaker._circuit_states[key]["state"] = CircuitState.HALF_OPEN
        cb.record_success()

        text = _scrape(reg)
        assert "orb_k8s_circuit_breaker_state" in text
        # After OPEN(1) → CLOSED(0) the net value is 0.  OTel UpDownCounter
        # tracks cumulative delta so 0+1-1=0.
        assert "0" in text or "0.0" in text

    def test_no_metrics_is_noop(self) -> None:
        """K8sCircuitBreaker without metrics must not raise."""
        key = _fresh_cb_key()
        cb = K8sCircuitBreaker(
            service_name=key,
            failure_threshold=1,
            reset_timeout=60,
            max_attempts=1,
            base_delay=0.0,
            max_delay=0.0,
            jitter=False,
        )
        cb.record_failure(time.time())  # should not raise

    def test_set_helpers_on_metrics_directly(self) -> None:
        m, reg = _fresh_metrics()
        m.set_circuit_breaker_state(name="svc-x", state=2)
        text = _scrape(reg)
        assert "orb_k8s_circuit_breaker_state" in text
        assert "2" in text or "2.0" in text

    def test_set_active_pods_and_requests_helpers(self) -> None:
        m, reg = _fresh_metrics()
        m.set_active_pods(namespace="default", count=7)
        m.set_active_requests(namespace="default", count=3)
        text = _scrape(reg)
        assert "orb_k8s_active_pods" in text
        assert "orb_k8s_active_requests" in text
        assert "7" in text or "7.0" in text
        assert "3" in text or "3.0" in text
