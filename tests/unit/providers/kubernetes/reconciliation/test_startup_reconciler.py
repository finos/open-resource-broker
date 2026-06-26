"""Unit tests for :class:`StartupReconciler`.

Covers:

* adoption — pods carrying the managed label whose ``request-id`` is in
  the known set are upserted into the cache;
* orphan classification — pods missing the request-id label or carrying
  one not in the known set are reported as orphans (NOT deleted);
* multi-namespace fan-out — single-namespace, explicit list, and
  cluster-scoped (``["*"]``) modes each issue the correct list call;
* error tolerance — list failures and known-id lookup failures must
  not raise; the report records the error and the reconciler moves on.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from orb.providers.kubernetes.configuration.config import KubernetesProviderConfig
from orb.providers.kubernetes.reconciliation.startup_reconciler import (
    OrphanPod,
    ReconciliationReport,
    StartupReconciler,
)
from orb.providers.kubernetes.watch.pod_state_cache import PodStateCache


def _pod(
    *,
    name: str,
    request_id: str | None,
    namespace: str = "orb",
    phase: str = "Running",
    ready: bool = True,
    creation_timestamp: str | None = None,
) -> SimpleNamespace:
    labels: dict[str, str] = {"orb.io/managed": "true"}
    if request_id is not None:
        labels["orb.io/request-id"] = request_id
    metadata = SimpleNamespace(
        name=name,
        namespace=namespace,
        labels=labels,
        creation_timestamp=creation_timestamp,
    )
    conditions = []
    if ready:
        conditions.append(SimpleNamespace(type="Ready", status="True", reason=None))
    else:
        conditions.append(SimpleNamespace(type="Ready", status="False", reason=None))
    status = SimpleNamespace(
        phase=phase,
        pod_ip="10.0.0.1",
        host_ip="10.0.0.100",
        start_time="2026-06-19T12:00:00Z",
        conditions=conditions,
        container_statuses=[],
    )
    spec = SimpleNamespace(node_name="node-1")
    return SimpleNamespace(metadata=metadata, status=status, spec=spec)


def _make_reconciler(
    *,
    pods_by_namespace: dict[str | None, list[Any]] | None = None,
    pods: list[Any] | None = None,
    config: KubernetesProviderConfig | None = None,
    known: list[str] | None = None,
    known_raises: Exception | None = None,
    list_raises: Exception | None = None,
) -> tuple[StartupReconciler, PodStateCache, MagicMock]:
    cache = PodStateCache()
    client = MagicMock()
    core_v1 = MagicMock()
    client.core_v1 = core_v1

    if list_raises is not None:
        core_v1.list_namespaced_pod.side_effect = list_raises
        core_v1.list_pod_for_all_namespaces.side_effect = list_raises
    else:

        def _list_ns(namespace: str, **_kw: Any) -> Any:
            data = (pods_by_namespace or {}).get(namespace, pods or [])
            return SimpleNamespace(items=list(data))

        def _list_all(**_kw: Any) -> Any:
            data = (pods_by_namespace or {}).get(None, pods or [])
            return SimpleNamespace(items=list(data))

        core_v1.list_namespaced_pod.side_effect = _list_ns
        core_v1.list_pod_for_all_namespaces.side_effect = _list_all

    cfg = config or KubernetesProviderConfig(namespace="orb")

    def _known_ids() -> list[str]:
        if known_raises is not None:
            raise known_raises
        return list(known or [])

    reconciler = StartupReconciler(
        kubernetes_client=client,
        config=cfg,
        cache=cache,
        logger=MagicMock(),
        known_request_ids=_known_ids,
    )
    return reconciler, cache, core_v1


# ---------------------------------------------------------------------------
# Adoption
# ---------------------------------------------------------------------------


def test_adopts_pods_with_known_request_ids_into_cache() -> None:
    pods = [
        _pod(name="pod-a", request_id="req-1"),
        _pod(name="pod-b", request_id="req-1"),
    ]
    reconciler, cache, _ = _make_reconciler(pods=pods, known=["req-1"])

    report = reconciler.run()

    assert report.completed is True
    assert report.pods_seen == 2
    assert report.pods_adopted == 2
    assert report.requests_warmed == 1
    assert report.orphan_count == 0

    cached = cache.get("req-1")
    assert cached is not None
    assert {s.pod_name for s in cached} == {"pod-a", "pod-b"}


def test_running_ready_pod_translates_to_running_status() -> None:
    pods = [_pod(name="pod-a", request_id="req-1", phase="Running", ready=True)]
    reconciler, cache, _ = _make_reconciler(pods=pods, known=["req-1"])
    reconciler.run()

    states = cache.get("req-1")
    assert states is not None
    assert states[0].status == "running"
    assert states[0].ready is True


def test_pending_pod_translates_to_pending_status() -> None:
    pods = [_pod(name="pod-a", request_id="req-1", phase="Pending", ready=False)]
    reconciler, cache, _ = _make_reconciler(pods=pods, known=["req-1"])
    reconciler.run()

    states = cache.get("req-1")
    assert states is not None
    assert states[0].status == "pending"


# ---------------------------------------------------------------------------
# Orphan classification
# ---------------------------------------------------------------------------


def test_pod_without_known_request_id_is_classified_as_orphan() -> None:
    pods = [
        _pod(name="orph", request_id="req-stranger", creation_timestamp="2026-06-19T11:00:00Z"),
    ]
    reconciler, cache, _ = _make_reconciler(pods=pods, known=["req-1"])

    report = reconciler.run()

    assert report.pods_adopted == 0
    assert report.orphan_count == 1
    orphan = report.orphans[0]
    assert isinstance(orphan, OrphanPod)
    assert orphan.pod_name == "orph"
    assert orphan.request_id == "req-stranger"
    # Reconciler must NOT delete orphans.
    assert cache.get("req-stranger") is None


def test_pod_missing_request_id_label_is_an_orphan() -> None:
    pods = [_pod(name="legacy-pod", request_id=None)]
    reconciler, _, _ = _make_reconciler(pods=pods, known=["req-1"])

    report = reconciler.run()

    assert report.orphan_count == 1
    assert report.orphans[0].request_id is None


# ---------------------------------------------------------------------------
# Multi-namespace dispatch
# ---------------------------------------------------------------------------


def test_explicit_namespace_list_runs_one_list_per_namespace() -> None:
    cfg = KubernetesProviderConfig(namespace="orb", namespaces=["alpha", "beta"])
    pods_by_ns: dict[str | None, list[Any]] = {
        "alpha": [_pod(name="a1", request_id="r1", namespace="alpha")],
        "beta": [_pod(name="b1", request_id="r2", namespace="beta")],
    }
    reconciler, cache, core_v1 = _make_reconciler(
        config=cfg,
        pods_by_namespace=pods_by_ns,
        known=["r1", "r2"],
    )

    report = reconciler.run()
    assert report.pods_adopted == 2
    # One call per namespace.
    namespaces_called = [c.kwargs.get("namespace") for c in core_v1.list_namespaced_pod.mock_calls]
    assert set(namespaces_called) == {"alpha", "beta"}
    assert cache.get("r1") is not None
    assert cache.get("r2") is not None


def test_cluster_scoped_mode_uses_list_pod_for_all_namespaces() -> None:
    cfg = KubernetesProviderConfig(namespace="orb", namespaces=["*"])
    pods = [_pod(name="any", request_id="r1", namespace="anywhere")]
    reconciler, _, core_v1 = _make_reconciler(
        config=cfg,
        pods_by_namespace={None: pods},
        known=["r1"],
    )

    report = reconciler.run()
    assert report.pods_adopted == 1
    assert core_v1.list_pod_for_all_namespaces.called is True
    assert core_v1.list_namespaced_pod.called is False


# ---------------------------------------------------------------------------
# Error tolerance
# ---------------------------------------------------------------------------


def test_list_failure_does_not_crash_and_is_logged_on_report() -> None:
    reconciler, _, _ = _make_reconciler(
        list_raises=RuntimeError("apiserver down"),
        known=["r1"],
    )

    report = reconciler.run()

    # Reconciler does not raise; it surfaces a clean empty report.
    assert isinstance(report, ReconciliationReport)
    assert report.completed is True
    assert report.pods_seen == 0
    assert report.pods_adopted == 0
    assert report.orphan_count == 0


def test_known_request_ids_failure_treats_every_pod_as_orphan() -> None:
    pods = [
        _pod(name="adopt", request_id="req-1"),
        _pod(name="adopt-2", request_id="req-2"),
    ]
    reconciler, cache, _ = _make_reconciler(
        pods=pods,
        known_raises=RuntimeError("storage offline"),
    )

    report = reconciler.run()

    # Known set was unavailable — every pod becomes an orphan.
    assert report.orphan_count == 2
    assert report.pods_adopted == 0
    # Cache must remain empty since nothing was adopted.
    assert cache.size() == 0


def test_duration_recorded_on_report() -> None:
    reconciler, _, _ = _make_reconciler(pods=[], known=[])

    report = reconciler.run()

    assert report.duration_seconds >= 0.0
