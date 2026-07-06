"""Unit tests for DisruptionTarget preemption and restart_count surfacing.

Covers:

* :class:`PodState` carries ``disrupted_reason``, ``disrupted_message``,
  and ``restart_count`` fields, defaulting to ``None`` / ``0``.
* :meth:`K8sWatcher._pod_to_state` extracts the ``DisruptionTarget``
  condition when present and sums ``restart_count`` across containers.
* :meth:`K8sHandlerBase._instance_dict_for_pod` surfaces the same fields
  via the live-list path.
* :meth:`K8sHandlerBase._instance_dict_for_state` surfaces them via the
  cache path.
* The watcher integrates end-to-end: a pod event with both a
  DisruptionTarget condition and a restarting container produces the
  expected cache entry.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import MagicMock

import pytest

from orb.providers.k8s.watch.pod_state_cache import PodState, PodStateCache
from orb.providers.k8s.watch.watcher import K8sWatcher

# ---------------------------------------------------------------------------
# Pod builder helpers
# ---------------------------------------------------------------------------


def _container_status(restart_count: int = 0) -> SimpleNamespace:
    return SimpleNamespace(restart_count=restart_count)


def _pod(
    *,
    name: str = "pod-a",
    phase: str = "Running",
    ready: bool = True,
    request_id: str = "req-1",
    namespace: str = "ns",
    label_prefix: str = "orb.io",
    disruption_target: bool = False,
    disruption_reason: str = "TerminatingNode",
    disruption_message: str = "Node is being terminated",
    container_restart_counts: list[int] | None = None,
) -> SimpleNamespace:
    conditions: list[SimpleNamespace] = []
    if ready:
        conditions.append(SimpleNamespace(type="Ready", status="True", reason=None, message=None))
    else:
        conditions.append(SimpleNamespace(type="Ready", status="False", reason=None, message=None))
    if disruption_target:
        conditions.append(
            SimpleNamespace(
                type="DisruptionTarget",
                status="True",
                reason=disruption_reason,
                message=disruption_message,
            )
        )
    container_statuses: list[SimpleNamespace] = [
        _container_status(rc) for rc in (container_restart_counts or [])
    ]
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            namespace=namespace,
            labels={
                f"{label_prefix}/managed": "true",
                f"{label_prefix}/request-id": request_id,
                f"{label_prefix}/machine-id": name,
            },
        ),
        spec=SimpleNamespace(node_name="node-a"),
        status=SimpleNamespace(
            phase=phase,
            pod_ip="10.0.0.1",
            host_ip="10.1.0.1",
            start_time=None,
            conditions=conditions,
            container_statuses=container_statuses,
        ),
    )


class _StubWatch:
    def __init__(self, events: Iterator[Any]) -> None:
        self._events = events
        self.resource_version: str | None = None

    def stream(self, func: Any, **kwargs: Any) -> Iterator[Any]:
        yield from self._events

    def stop(self) -> None:
        pass


def _make_kubernetes_client_mock() -> MagicMock:
    client = MagicMock()
    client.core_v1.list_namespaced_pod = MagicMock()
    return client


# ---------------------------------------------------------------------------
# PodState dataclass defaults
# ---------------------------------------------------------------------------


def test_pod_state_defaults_for_new_fields() -> None:
    """New fields default to sentinel values when not supplied."""
    state = PodState(request_id="r", pod_name="p", namespace="ns", status="running")
    assert state.disrupted_reason is None
    assert state.disrupted_message is None
    assert state.restart_count == 0


def test_pod_state_accepts_disruption_fields() -> None:
    state = PodState(
        request_id="r",
        pod_name="p",
        namespace="ns",
        status="running",
        disrupted_reason="TerminatingNode",
        disrupted_message="Node evicted by Karpenter",
        restart_count=3,
    )
    assert state.disrupted_reason == "TerminatingNode"
    assert state.disrupted_message == "Node evicted by Karpenter"
    assert state.restart_count == 3


# ---------------------------------------------------------------------------
# PodStateCache.upsert preserves new fields
# ---------------------------------------------------------------------------


def test_cache_upsert_preserves_disruption_and_restart() -> None:
    """upsert must carry disruption and restart_count through to the stamped entry."""
    cache = PodStateCache()
    incoming = PodState(
        request_id="req-1",
        pod_name="pod-x",
        namespace="ns",
        status="running",
        disrupted_reason="Consolidating",
        disrupted_message="Consolidation in progress",
        restart_count=5,
    )
    cache.upsert(incoming)
    states = cache.get("req-1")
    assert states is not None and len(states) == 1
    s = states[0]
    assert s.disrupted_reason == "Consolidating"
    assert s.disrupted_message == "Consolidation in progress"
    assert s.restart_count == 5


# ---------------------------------------------------------------------------
# K8sWatcher._pod_to_state — unit-level
# ---------------------------------------------------------------------------


def _make_watcher() -> K8sWatcher:
    return K8sWatcher(
        kubernetes_client=_make_kubernetes_client_mock(),
        cache=PodStateCache(),
        logger=MagicMock(),
        namespace="ns",
    )


def test_pod_to_state_no_disruption_no_restarts() -> None:
    watcher = _make_watcher()
    pod = _pod(name="pod-a")
    state = watcher._pod_to_state(pod, "req-1", "ns", deleted=False)
    assert state.disrupted_reason is None
    assert state.disrupted_message is None
    assert state.restart_count == 0


def test_pod_to_state_with_disruption_target() -> None:
    watcher = _make_watcher()
    pod = _pod(
        name="pod-b",
        disruption_target=True,
        disruption_reason="TerminatingNode",
        disruption_message="Karpenter is terminating the node",
    )
    state = watcher._pod_to_state(pod, "req-1", "ns", deleted=False)
    assert state.disrupted_reason == "TerminatingNode"
    assert state.disrupted_message == "Karpenter is terminating the node"


def test_pod_to_state_disruption_target_false_status_is_ignored() -> None:
    """A DisruptionTarget condition with status=False must not set the fields."""
    watcher = _make_watcher()
    pod = _pod(name="pod-c")
    # Manually inject a False-status DisruptionTarget.
    pod.status.conditions.append(
        SimpleNamespace(type="DisruptionTarget", status="False", reason="Gone", message="Gone")
    )
    state = watcher._pod_to_state(pod, "req-1", "ns", deleted=False)
    assert state.disrupted_reason is None
    assert state.disrupted_message is None


def test_pod_to_state_restart_count_sums_across_containers() -> None:
    watcher = _make_watcher()
    pod = _pod(name="pod-d", container_restart_counts=[3, 2, 0])
    state = watcher._pod_to_state(pod, "req-1", "ns", deleted=False)
    assert state.restart_count == 5


def test_pod_to_state_restart_count_single_container() -> None:
    watcher = _make_watcher()
    pod = _pod(name="pod-e", container_restart_counts=[7])
    state = watcher._pod_to_state(pod, "req-1", "ns", deleted=False)
    assert state.restart_count == 7


def test_pod_to_state_restart_count_no_containers() -> None:
    watcher = _make_watcher()
    pod = _pod(name="pod-f", container_restart_counts=[])
    state = watcher._pod_to_state(pod, "req-1", "ns", deleted=False)
    assert state.restart_count == 0


# ---------------------------------------------------------------------------
# K8sHandlerBase._instance_dict_for_pod — live-list path
# ---------------------------------------------------------------------------


def _make_handler() -> Any:
    """Return a concrete K8sPodHandler with stub dependencies."""
    from orb.providers.k8s.handlers.pod_handler import K8sPodHandler

    config = MagicMock()
    config.label_prefix = "orb.io"
    config.namespace = "ns"
    config.namespaces = []
    config.stale_cache_timeout_seconds = 60
    config.pod_timeout_seconds = 300
    config.delete_timed_out_pods = False
    return K8sPodHandler(
        kubernetes_client=MagicMock(),
        config=config,
        logger=MagicMock(),
    )


def test_instance_dict_for_pod_no_disruption() -> None:
    handler = _make_handler()
    pod = _pod(name="pod-g")
    d = handler._instance_dict_for_pod(pod, namespace="ns")
    pd = d["provider_data"]
    assert pd["disrupted_reason"] is None
    assert pd["disrupted_message"] is None
    assert pd["restart_count"] == 0


def test_instance_dict_for_pod_with_disruption() -> None:
    handler = _make_handler()
    pod = _pod(
        name="pod-h",
        disruption_target=True,
        disruption_reason="Consolidating",
        disruption_message="Node selected for consolidation",
        container_restart_counts=[1, 2],
    )
    d = handler._instance_dict_for_pod(pod, namespace="ns")
    pd = d["provider_data"]
    assert pd["disrupted_reason"] == "Consolidating"
    assert pd["disrupted_message"] == "Node selected for consolidation"
    assert pd["restart_count"] == 3


# ---------------------------------------------------------------------------
# K8sHandlerBase._instance_dict_for_state — cache path
# ---------------------------------------------------------------------------


def test_instance_dict_for_state_no_disruption() -> None:
    handler = _make_handler()
    state = PodState(
        request_id="req-1",
        pod_name="pod-i",
        namespace="ns",
        status="running",
    )
    d = handler._instance_dict_for_state(state)
    pd = d["provider_data"]
    assert pd["disrupted_reason"] is None
    assert pd["disrupted_message"] is None
    assert pd["restart_count"] == 0


def test_instance_dict_for_state_with_disruption_and_restarts() -> None:
    handler = _make_handler()
    state = PodState(
        request_id="req-1",
        pod_name="pod-j",
        namespace="ns",
        status="running",
        disrupted_reason="TerminatingNode",
        disrupted_message="Karpenter eviction",
        restart_count=4,
    )
    d = handler._instance_dict_for_state(state)
    pd = d["provider_data"]
    assert pd["disrupted_reason"] == "TerminatingNode"
    assert pd["disrupted_message"] == "Karpenter eviction"
    assert pd["restart_count"] == 4


# ---------------------------------------------------------------------------
# Integration: watcher end-to-end with DisruptionTarget + restart_count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_watcher_surfaces_disruption_and_restart_in_cache() -> None:
    """End-to-end: ADDED event with DisruptionTarget + restarts lands correctly."""
    cache = PodStateCache()
    client = _make_kubernetes_client_mock()
    pod = _pod(
        name="pod-k",
        disruption_target=True,
        disruption_reason="TerminatingNode",
        disruption_message="Preempted by Karpenter",
        container_restart_counts=[2, 3],
    )
    events = [{"type": "ADDED", "object": pod}]
    stubs = iter([_StubWatch(iter(events))])

    def factory() -> _StubWatch:
        try:
            return next(stubs)
        except StopIteration:
            return _StubWatch(iter([]))

    watcher = K8sWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=MagicMock(),
        namespace="ns",
        watch_factory=factory,
        watch_timeout_seconds=1,
    )
    watcher.start()
    for _ in range(50):
        if cache.size() > 0:
            break
        await asyncio.sleep(0.01)
    await watcher.stop()

    states = cache.get("req-1")
    assert states is not None and len(states) == 1
    state = states[0]
    assert state.disrupted_reason == "TerminatingNode"
    assert state.disrupted_message == "Preempted by Karpenter"
    assert state.restart_count == 5
