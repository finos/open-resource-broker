"""Integration test for startup reconciliation + orphan garbage collection.

Covers the two operator-visible behaviours documented in
``docs/providers/k8s/rbac.yaml`` and the orphan-GC module
docstring:

* on provider start, the :class:`StartupReconciler` lists every
  managed pod and partitions it into ``adopted`` (request id in ORB
  storage) versus ``orphan`` (no matching request id);
* the :class:`OrphanGarbageCollector` runs periodically and either
  *logs only* (``auto_cleanup_orphans=False`` — default) or *deletes*
  (``auto_cleanup_orphans=True``) the orphan pods.

The tests drive these subsystems via a strategy whose
``initialize`` warms the cache and whose ``orphan_gc`` runs an
explicit one-shot sweep so we do not need to wait for a real interval.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orb.providers.k8s.reconciliation.orphan_gc import OrphanGarbageCollector
from tests.integration.providers.k8s.conftest import (
    make_kubernetes_client_mock,
    make_namespaced_config,
    make_pod_object,
    make_strategy,
)


def _managed_pod(*, name: str, request_id: str, namespace: str = "orb-it") -> SimpleNamespace:
    return make_pod_object(
        name=name,
        namespace=namespace,
        request_id=request_id,
        phase="Running",
        ready=True,
        creation_timestamp="2026-06-19T10:00:00Z",
    )


def _orphan_pod(*, name: str, namespace: str = "orb-it") -> SimpleNamespace:
    """Build a pod with the managed label but a request-id ORB does not know."""
    return make_pod_object(
        name=name,
        namespace=namespace,
        request_id="req-deadbeef-dead-beef-dead-beefdeadbeef",
        phase="Running",
        ready=True,
        creation_timestamp="2026-06-19T09:00:00Z",
    )


def test_startup_reconciler_partitions_adopted_and_orphans() -> None:
    """Adopted pods land in the cache; orphans are reported but not deleted."""
    known_request_id = "req-11111111-1111-1111-1111-111111111111"

    pods = [
        _managed_pod(name="orb-known-0001", request_id=known_request_id),
        _managed_pod(name="orb-known-0002", request_id=known_request_id),
        _orphan_pod(name="orb-orphan-0001"),
        _orphan_pod(name="orb-orphan-0002"),
    ]

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(items=pods)
    client = make_kubernetes_client_mock(core_v1=core_v1)
    strategy = make_strategy(
        client=client,
        known_request_ids=lambda: [known_request_id],
    )

    # The reconciler runs in start_daemon_services; trigger it directly for
    # this synchronous test so the report is populated before we assert on it.
    strategy._run_startup_reconciler()  # type: ignore[attr-defined]

    report = strategy.last_reconciliation_report
    assert report is not None
    assert report.completed is True
    assert report.pods_seen == 4
    assert report.pods_adopted == 2
    assert report.requests_warmed == 1
    assert report.orphan_count == 2
    orphan_names = {orphan.pod_name for orphan in report.orphans}
    assert orphan_names == {"orb-orphan-0001", "orb-orphan-0002"}
    # The reconciler does NOT delete orphans — deletion is the GC's job
    # and is gated on ``auto_cleanup_orphans``.
    assert core_v1.delete_namespaced_pod.call_count == 0

    # The cache was warmed for the known request id so cache-first reads
    # short-circuit the apiserver hop.
    cache = strategy._ensure_watch_manager().cache  # type: ignore[attr-defined]
    states = cache.get(known_request_id)
    assert states is not None
    assert {s.pod_name for s in states} == {"orb-known-0001", "orb-known-0002"}


@pytest.mark.asyncio
async def test_orphan_gc_logs_only_when_auto_cleanup_disabled() -> None:
    """``auto_cleanup_orphans=False`` (default) logs orphans and never deletes."""
    namespace = "orb-it"
    known_id = "req-22222222-2222-2222-2222-222222222222"

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            _managed_pod(name="orb-keeper", request_id=known_id),
            _orphan_pod(name="orb-orphan-x"),
            _orphan_pod(name="orb-orphan-y"),
        ]
    )
    client = make_kubernetes_client_mock(core_v1=core_v1)
    config = make_namespaced_config(
        namespace=namespace,
        orphan_gc_enabled=False,
        auto_cleanup_orphans=False,
    )
    gc = OrphanGarbageCollector(
        kubernetes_client=client,
        config=config,
        logger=MagicMock(),
        known_request_ids=lambda: [known_id],
        interval_seconds=0.5,
    )

    orphans = await gc.run_once()
    assert {o.pod_name for o in orphans} == {"orb-orphan-x", "orb-orphan-y"}
    assert gc.stats.total_orphans_found == 2
    assert gc.stats.total_orphans_deleted == 0
    # The GC must NOT have called delete_namespaced_pod.
    assert core_v1.delete_namespaced_pod.call_count == 0


@pytest.mark.asyncio
async def test_orphan_gc_deletes_when_auto_cleanup_enabled() -> None:
    """``auto_cleanup_orphans=True`` deletes orphan pods (best-effort on 404)."""
    namespace = "orb-it"
    known_id = "req-33333333-3333-3333-3333-333333333333"

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            _orphan_pod(name="orb-orphan-1"),
            _orphan_pod(name="orb-orphan-2"),
            _orphan_pod(name="orb-orphan-3"),
            _managed_pod(name="orb-known-1", request_id=known_id),
        ]
    )
    core_v1.delete_namespaced_pod.return_value = SimpleNamespace()
    client = make_kubernetes_client_mock(core_v1=core_v1)
    config = make_namespaced_config(
        namespace=namespace,
        orphan_gc_enabled=True,
        auto_cleanup_orphans=True,
    )
    gc = OrphanGarbageCollector(
        kubernetes_client=client,
        config=config,
        logger=MagicMock(),
        known_request_ids=lambda: [known_id],
        interval_seconds=0.5,
    )

    orphans = await gc.run_once()
    assert len(orphans) == 3
    # Exactly the three orphans are deleted; the known pod is left alone.
    assert core_v1.delete_namespaced_pod.call_count == 3
    deleted_names = {call.kwargs["name"] for call in core_v1.delete_namespaced_pod.call_args_list}
    assert deleted_names == {"orb-orphan-1", "orb-orphan-2", "orb-orphan-3"}
    assert gc.stats.total_orphans_deleted == 3


@pytest.mark.asyncio
async def test_orphan_gc_swallows_404_during_delete() -> None:
    """A pod that has already evaporated is counted as a successful delete."""
    from kubernetes.client.exceptions import ApiException

    namespace = "orb-it"
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(
        items=[_orphan_pod(name="orb-vanish")]
    )

    def _delete(*, name: str, namespace: str) -> None:
        raise ApiException(status=404, reason="Not Found")

    core_v1.delete_namespaced_pod.side_effect = _delete
    client = make_kubernetes_client_mock(core_v1=core_v1)
    config = make_namespaced_config(
        namespace=namespace,
        orphan_gc_enabled=True,
        auto_cleanup_orphans=True,
    )
    gc = OrphanGarbageCollector(
        kubernetes_client=client,
        config=config,
        logger=MagicMock(),
        known_request_ids=lambda: [],
        interval_seconds=0.5,
    )
    orphans = await gc.run_once()
    assert len(orphans) == 1
    # Counted as deleted even though the apiserver returned 404.
    assert gc.stats.total_orphans_deleted == 1
    assert gc.stats.delete_failures == 0
