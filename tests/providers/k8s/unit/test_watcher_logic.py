"""Unit tests for K8sWatcher pure-logic methods (no async watcher loop).

Covers uncovered ranges from watcher.py:
  72, 90, 92, 221, 273-276, 287-290, 344, 388, 399, 426, 508, 513, 621, 631, 634, 638,
  661, 675, 692, 723, 726, 729, 809, 822, 831-833

Only non-async, non-blocking methods are tested here per the isolation rules
(skip async watchers).  The tested surface includes:
  - _classify_reconnect_reason
  - _is_resource_version_too_old
  - _backoff_for_attempt
  - _record_reconnect / _record_event (metrics wiring)
  - _build_list_call
  - _handle_event (DELETE / non-DELETE paths)
  - _pod_to_state (status string computation, escalation logic)
  - is_running (before start)
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.providers.k8s.watch.pod_state_cache import PodState, PodStateCache
from orb.providers.k8s.watch.watcher import K8sWatcher, _classify_reconnect_reason

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_logger() -> Any:
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


def _make_mock_client() -> Any:
    client = MagicMock()
    client.core_v1 = MagicMock()
    return client


def _make_watcher(
    *,
    namespace: str | None = "test-ns",
    metrics: Any = None,
    periodic_resync_interval_seconds: int = 0,
) -> K8sWatcher:
    return K8sWatcher(
        kubernetes_client=_make_mock_client(),  # type: ignore[arg-type]
        cache=PodStateCache(),
        logger=_make_logger(),
        namespace=namespace,
        metrics=metrics,
        periodic_resync_interval_seconds=periodic_resync_interval_seconds,
        base_backoff_seconds=0.001,  # fast for tests
        max_backoff_seconds=0.01,
    )


def _make_fake_api_exception(status: int) -> Exception:
    try:
        from kubernetes.client.exceptions import ApiException

        exc = ApiException(status=status)
        return exc
    except ImportError:

        class _Fake(Exception):
            def __init__(self, s: int) -> None:
                self.status = s

        return _Fake(status)


def _make_pod(
    *,
    name: str = "test-pod",
    namespace: str = "test-ns",
    labels: dict[str, str] | None = None,
    phase: str | None = "Running",
    ready: bool = True,
    node_name: str | None = "node-1",
    provider_api: str | None = None,
) -> Any:
    if labels is None:
        labels = {"orb.io/request-id": "req-123"}
    if provider_api:
        labels["orb.io/provider-api"] = provider_api

    meta = SimpleNamespace(
        name=name,
        namespace=namespace,
        labels=labels,
    )

    condition_type = "Ready"
    condition_status = "True" if ready else "False"
    conditions = [
        SimpleNamespace(type=condition_type, status=condition_status, reason=None, message=None)
    ]

    container_statuses: list[Any] = []
    status = SimpleNamespace(
        phase=phase,
        pod_ip="10.0.0.1",
        host_ip="192.168.1.1",
        start_time=None,
        conditions=conditions,
        container_statuses=container_statuses,
    )
    spec = SimpleNamespace(
        node_name=node_name,
        containers=[SimpleNamespace(image="my-image:latest")],
        restart_policy="Always",
    )
    return SimpleNamespace(metadata=meta, status=status, spec=spec)


# ---------------------------------------------------------------------------
# _classify_reconnect_reason
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClassifyReconnectReason:
    def test_timeout_exception(self) -> None:
        exc = TimeoutError("timed out")
        assert _classify_reconnect_reason(exc) == "timeout"

    def test_connection_error(self) -> None:
        exc = ConnectionError("reset by peer")
        assert _classify_reconnect_reason(exc) == "network"

    def test_os_error(self) -> None:
        exc = OSError("broken pipe")
        assert _classify_reconnect_reason(exc) == "network"

    def test_unknown_exception(self) -> None:
        exc = ValueError("something else")
        assert _classify_reconnect_reason(exc) == "unknown"

    def test_custom_timeout_class(self) -> None:
        class MyTimeoutError(Exception):
            pass

        exc = MyTimeoutError("deadline exceeded")
        # The class name contains "timeout" → should be classified as timeout
        assert _classify_reconnect_reason(exc) == "timeout"


# ---------------------------------------------------------------------------
# K8sWatcher._is_resource_version_too_old
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsResourceVersionTooOld:
    def test_true_for_410_api_exception(self) -> None:
        exc = _make_fake_api_exception(410)
        assert K8sWatcher._is_resource_version_too_old(exc) is True

    def test_false_for_404_api_exception(self) -> None:
        exc = _make_fake_api_exception(404)
        assert K8sWatcher._is_resource_version_too_old(exc) is False

    def test_false_for_generic_exception(self) -> None:
        assert K8sWatcher._is_resource_version_too_old(RuntimeError("nope")) is False


# ---------------------------------------------------------------------------
# K8sWatcher._backoff_for_attempt
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBackoffForAttempt:
    def test_first_attempt_is_base(self) -> None:
        w = _make_watcher()
        # base is 0.001 in our helper
        delay = w._backoff_for_attempt(1)
        assert delay == pytest.approx(0.001)

    def test_doubles_each_attempt(self) -> None:
        w = _make_watcher()
        d1 = w._backoff_for_attempt(1)
        d2 = w._backoff_for_attempt(2)
        assert d2 == pytest.approx(d1 * 2)

    def test_capped_at_max(self) -> None:
        w = _make_watcher()
        # After enough attempts the cap should kick in
        d_large = w._backoff_for_attempt(100)
        assert d_large == pytest.approx(w._max_backoff_seconds)

    def test_zero_attempt_treated_as_one(self) -> None:
        w = _make_watcher()
        d0 = w._backoff_for_attempt(0)
        d1 = w._backoff_for_attempt(1)
        assert d0 == d1


# ---------------------------------------------------------------------------
# K8sWatcher._record_reconnect / _record_event
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMetricsRecording:
    def test_record_reconnect_calls_metrics(self) -> None:
        mock_metrics = MagicMock()
        w = _make_watcher(metrics=mock_metrics)
        w._record_reconnect("timeout")
        mock_metrics.record_watch_reconnect.assert_called_once_with(
            namespace="test-ns", reason="timeout"
        )

    def test_record_reconnect_no_metrics_is_noop(self) -> None:
        w = _make_watcher(metrics=None)
        # Must not raise
        w._record_reconnect("network")

    def test_record_event_calls_metrics(self) -> None:
        mock_metrics = MagicMock()
        w = _make_watcher(metrics=mock_metrics)
        w._record_event("ADDED")
        mock_metrics.record_watch_event.assert_called_once_with(
            namespace="test-ns", event_type="ADDED"
        )

    def test_record_reconnect_cluster_scoped_uses_star(self) -> None:
        mock_metrics = MagicMock()
        w = _make_watcher(namespace=None, metrics=mock_metrics)
        w._record_reconnect("unknown")
        mock_metrics.record_watch_reconnect.assert_called_once_with(namespace="*", reason="unknown")


# ---------------------------------------------------------------------------
# K8sWatcher.is_running
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsRunning:
    def test_false_before_start(self) -> None:
        w = _make_watcher()
        assert w.is_running() is False

    def test_last_error_is_none_initially(self) -> None:
        w = _make_watcher()
        assert w.last_error is None


# ---------------------------------------------------------------------------
# K8sWatcher._build_list_call
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildListCall:
    def test_namespace_scoped(self) -> None:
        w = _make_watcher(namespace="my-ns")
        list_func, call_kwargs = w._build_list_call(None)
        assert list_func is w._client.core_v1.list_namespaced_pod
        assert call_kwargs.get("namespace") == "my-ns"

    def test_cluster_scoped_when_namespace_none(self) -> None:
        w = _make_watcher(namespace=None)
        list_func, _call_kwargs = w._build_list_call(None)
        assert list_func is w._client.core_v1.list_pod_for_all_namespaces

    def test_resource_version_in_kwargs_when_provided(self) -> None:
        w = _make_watcher(namespace="ns")
        _list_func, call_kwargs = w._build_list_call("v123")
        assert call_kwargs.get("resource_version") == "v123"

    def test_no_resource_version_when_none(self) -> None:
        w = _make_watcher(namespace="ns")
        _list_func, call_kwargs = w._build_list_call(None)
        assert "resource_version" not in call_kwargs


# ---------------------------------------------------------------------------
# K8sWatcher._handle_event
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleEvent:
    def test_add_event_upserts_to_cache(self) -> None:
        w = _make_watcher()
        pod = _make_pod(name="pod-1", labels={"orb.io/request-id": "req-1"})
        event = {"type": "ADDED", "object": pod}
        w._handle_event(event)
        states = w._cache.get("req-1")
        assert states is not None
        assert len(states) == 1
        assert states[0].pod_name == "pod-1"
        assert not states[0].deleted

    def test_modified_event_updates_cache(self) -> None:
        w = _make_watcher()
        pod = _make_pod(name="pod-1", labels={"orb.io/request-id": "req-1"})
        w._handle_event({"type": "ADDED", "object": pod})
        w._handle_event({"type": "MODIFIED", "object": pod})
        states = w._cache.get("req-1")
        assert states is not None

    def test_deleted_event_marks_deleted_then_removes(self) -> None:
        w = _make_watcher()
        pod = _make_pod(name="pod-del", labels={"orb.io/request-id": "req-del"})
        # Step 1: ADD populates the cache with a live (not-deleted) entry.
        w._handle_event({"type": "ADDED", "object": pod})
        after_add = w._cache.get("req-del")
        assert after_add is not None
        assert len(after_add) == 1
        assert after_add[0].pod_name == "pod-del"
        assert after_add[0].deleted is False
        # Step 2: DELETE evicts the pod entirely — the request's bucket is
        # removed, so get() returns None (uncached), not an empty list.
        w._handle_event({"type": "DELETED", "object": pod})
        assert w._cache.get("req-del") is None

    def test_missing_request_id_label_skips_event(self) -> None:
        w = _make_watcher()
        pod = _make_pod(name="pod-nolabel", labels={})  # no request-id label
        w._handle_event({"type": "ADDED", "object": pod})
        # Cache should be empty — pod was skipped
        assert len(w._cache.all_states()) == 0

    def test_none_object_in_event_is_skipped(self) -> None:
        w = _make_watcher()
        w._handle_event({"type": "ADDED", "object": None})
        assert len(w._cache.all_states()) == 0

    def test_event_without_metadata_is_skipped(self) -> None:
        w = _make_watcher()
        no_meta_pod = SimpleNamespace(metadata=None)
        w._handle_event({"type": "ADDED", "object": no_meta_pod})
        assert len(w._cache.all_states()) == 0


# ---------------------------------------------------------------------------
# K8sWatcher._pod_to_state — status escalation logic
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPodToState:
    def _get_state(self, w: K8sWatcher, pod: Any, **kwargs: Any) -> PodState:
        return w._pod_to_state(
            pod,
            request_id=kwargs.get("request_id", "req-1"),
            namespace=kwargs.get("namespace", "test-ns"),
            deleted=kwargs.get("deleted", False),
        )

    def test_running_phase_ready_pod_is_running(self) -> None:
        w = _make_watcher()
        pod = _make_pod(phase="Running", ready=True)
        state = self._get_state(w, pod)
        assert state.status == "running"

    def test_pending_phase_is_pending(self) -> None:
        w = _make_watcher()
        pod = _make_pod(phase="Pending", ready=False)
        state = self._get_state(w, pod)
        # pod_status_string(phase="Pending", ...) is deterministic → "pending".
        assert state.status == "pending"

    def test_succeeded_bare_pod_is_terminated(self) -> None:
        w = _make_watcher()
        pod = _make_pod(phase="Succeeded", ready=False, provider_api="Pod")
        state = self._get_state(w, pod)
        assert state.status == "terminated"

    def test_succeeded_deployment_pod_is_running(self) -> None:
        w = _make_watcher()
        pod = _make_pod(phase="Succeeded", ready=False, provider_api="Deployment")
        state = self._get_state(w, pod)
        # Controller pods that Succeed are kept as "running" until replaced
        assert state.status == "running"

    def test_fatal_image_pull_error_escalated_to_failed(self) -> None:
        w = _make_watcher()
        # Build a pod with ImagePullBackOff waiting reason
        waiting = SimpleNamespace(reason="ImagePullBackOff", message="pull failed")
        cs = SimpleNamespace(
            state=SimpleNamespace(waiting=waiting, running=None, terminated=None),
            ready=False,
            restart_count=0,
        )
        pod = _make_pod(phase="Pending", ready=False)
        pod.status.container_statuses = [cs]
        state = self._get_state(w, pod)
        assert state.status == "failed"

    def test_crash_loop_escalated_to_failed(self) -> None:
        w = _make_watcher()
        # Build a pod with CrashLoopBackOff waiting reason
        waiting = SimpleNamespace(reason="CrashLoopBackOff", message="crash")
        cs = SimpleNamespace(
            state=SimpleNamespace(waiting=waiting, running=None, terminated=None),
            ready=False,
            restart_count=6,
        )
        pod = _make_pod(phase="Running", ready=False)
        pod.status.container_statuses = [cs]
        state = self._get_state(w, pod)
        assert state.status == "failed"

    def test_deleted_state_is_marked(self) -> None:
        w = _make_watcher()
        pod = _make_pod(phase="Running", ready=True)
        state = self._get_state(w, pod, deleted=True)
        assert state.deleted is True

    def test_pod_name_captured_in_state(self) -> None:
        w = _make_watcher()
        pod = _make_pod(name="my-pod")
        state = self._get_state(w, pod)
        assert state.pod_name == "my-pod"

    def test_image_id_captured(self) -> None:
        w = _make_watcher()
        pod = _make_pod()
        state = self._get_state(w, pod)
        assert state.image_id == "my-image:latest"

    def test_no_containers_no_image_id(self) -> None:
        w = _make_watcher()
        pod = _make_pod()
        pod.spec.containers = []
        state = self._get_state(w, pod)
        assert state.image_id is None


# ---------------------------------------------------------------------------
# K8sWatcher._update_cache_gauges
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateCacheGauges:
    def test_no_metrics_is_noop(self) -> None:
        w = _make_watcher(metrics=None)
        # Should not raise even when cache is empty
        w._update_cache_gauges()

    def test_metrics_set_called_when_wired(self) -> None:
        mock_metrics = MagicMock()
        w = _make_watcher(metrics=mock_metrics)
        pod = _make_pod(labels={"orb.io/request-id": "req-1"})
        w._handle_event({"type": "ADDED", "object": pod})
        # _update_cache_gauges is called by _handle_event
        mock_metrics.set_active_pods.assert_called()
        mock_metrics.set_active_requests.assert_called()
