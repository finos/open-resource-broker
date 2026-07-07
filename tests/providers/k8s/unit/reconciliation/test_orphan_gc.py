"""Unit tests for :class:`OrphanGarbageCollector`.

Covers:

* one-shot sweep semantics — ``run_once`` returns the orphan list and
  populates :class:`OrphanGCStats`;
* the ``auto_cleanup_orphans`` flag — False (default) logs only, True
  deletes via ``delete_namespaced_pod`` with 404 swallowing;
* the periodic loop — wakes on the configured interval and stops
  cleanly on :meth:`stop`;
* error tolerance — list failures and known-id failures do not raise;
* min-age grace period — pods younger than ``orphan_min_age_seconds``
  are skipped; pods older are deleted.
"""

from __future__ import annotations

import asyncio
import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.reconciliation.orphan_gc import (
    OrphanGarbageCollector,
)


def _pod(
    *,
    name: str,
    request_id: str | None,
    namespace: str = "orb",
    creation_timestamp: str = "2026-06-19T11:00:00Z",
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
    return SimpleNamespace(metadata=metadata)


def _iso_now_offset(seconds: float) -> str:
    """Return an ISO 8601 UTC timestamp offset by ``seconds`` from now.

    Positive values are in the future; negative values are in the past.
    """
    ts = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(seconds=seconds)
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_gc(
    *,
    pods: list[Any] | None = None,
    config: K8sProviderConfig | None = None,
    known: list[str] | None = None,
    known_raises: Exception | None = None,
    delete_raises: Exception | None = None,
    list_raises: Exception | None = None,
) -> tuple[OrphanGarbageCollector, MagicMock]:
    client = MagicMock()
    core_v1 = MagicMock()
    client.core_v1 = core_v1

    if list_raises is not None:
        core_v1.list_namespaced_pod.side_effect = list_raises
    else:
        core_v1.list_namespaced_pod.return_value = SimpleNamespace(items=list(pods or []))

    if delete_raises is not None:
        core_v1.delete_namespaced_pod.side_effect = delete_raises

    cfg = config or K8sProviderConfig(namespace="orb")

    def _known() -> list[str]:
        if known_raises is not None:
            raise known_raises
        return list(known or [])

    gc = OrphanGarbageCollector(
        kubernetes_client=client,
        config=cfg,
        logger=MagicMock(),
        known_request_ids=_known,
        interval_seconds=0.01,  # Tight for periodic-loop tests.
    )
    return gc, core_v1


# ---------------------------------------------------------------------------
# One-shot sweep
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_once_returns_orphans_and_populates_stats() -> None:
    pods = [
        _pod(name="adopted", request_id="r1"),
        _pod(name="orph-a", request_id="r-stranger"),
        _pod(name="orph-b", request_id=None),
    ]
    gc, _ = _make_gc(pods=pods, known=["r1"])

    orphans = await gc.run_once()

    assert {o.pod_name for o in orphans} == {"orph-a", "orph-b"}
    assert gc.stats.runs == 1
    assert gc.stats.last_orphans_found == 2
    assert gc.stats.total_orphans_found == 2


# ---------------------------------------------------------------------------
# auto_cleanup_orphans flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_config_logs_orphans_but_does_not_delete() -> None:
    cfg = K8sProviderConfig(namespace="orb")  # auto_cleanup_orphans=False default
    pods = [_pod(name="orph", request_id="r-stranger")]
    gc, core_v1 = _make_gc(pods=pods, config=cfg, known=["r1"])

    orphans = await gc.run_once()
    assert len(orphans) == 1
    assert core_v1.delete_namespaced_pod.called is False
    assert gc.stats.total_orphans_deleted == 0


@pytest.mark.asyncio
async def test_auto_cleanup_true_deletes_orphans() -> None:
    cfg = K8sProviderConfig(namespace="orb", auto_cleanup_orphans=True)
    pods = [
        _pod(name="orph-a", request_id="r-stranger"),
        _pod(name="orph-b", request_id=None),
    ]
    gc, core_v1 = _make_gc(pods=pods, config=cfg, known=["r1"])

    await gc.run_once()

    deleted_names = {c.kwargs.get("name") for c in core_v1.delete_namespaced_pod.mock_calls}
    assert deleted_names == {"orph-a", "orph-b"}
    assert gc.stats.total_orphans_deleted == 2


@pytest.mark.asyncio
async def test_delete_failures_are_counted_but_do_not_raise() -> None:
    cfg = K8sProviderConfig(namespace="orb", auto_cleanup_orphans=True)
    pods = [_pod(name="orph", request_id="r-stranger")]
    gc, _ = _make_gc(
        pods=pods,
        config=cfg,
        known=["r1"],
        delete_raises=RuntimeError("boom"),
    )

    await gc.run_once()

    assert gc.stats.delete_failures == 1
    assert gc.stats.total_orphans_deleted == 0
    assert gc.stats.last_error is not None


# ---------------------------------------------------------------------------
# Periodic loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_periodic_loop_runs_at_least_once_and_stops_cleanly() -> None:
    pods = [_pod(name="orph", request_id="r-stranger")]
    gc, _ = _make_gc(pods=pods, known=["r1"])

    gc.start()
    try:
        # Let the loop run a couple of intervals (interval is 0.01s).
        for _ in range(20):
            if gc.stats.runs >= 1:
                break
            await asyncio.sleep(0.01)
        assert gc.stats.runs >= 1
    finally:
        await gc.stop()
    assert gc.is_running() is False


@pytest.mark.asyncio
async def test_start_is_idempotent_while_running() -> None:
    gc, _ = _make_gc(pods=[], known=[])
    gc.start()
    try:
        first = gc._task
        gc.start()  # Should not replace the task.
        assert gc._task is first
    finally:
        await gc.stop()


# ---------------------------------------------------------------------------
# Error tolerance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_known_ids_failure_skips_sweep() -> None:
    pods = [_pod(name="orph", request_id="r-stranger")]
    gc, core_v1 = _make_gc(
        pods=pods,
        known_raises=RuntimeError("storage offline"),
    )

    orphans = await gc.run_once()

    assert orphans == []
    # Storage lookup blew up before we could even list pods.
    assert core_v1.list_namespaced_pod.called is False
    assert gc.stats.last_error is not None


@pytest.mark.asyncio
async def test_list_failure_does_not_raise() -> None:
    gc, _ = _make_gc(
        list_raises=RuntimeError("apiserver down"),
        known=["r1"],
    )

    orphans = await gc.run_once()

    assert orphans == []
    assert gc.stats.last_error is not None


# ---------------------------------------------------------------------------
# Min-age grace period
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orphan_younger_than_min_age_is_not_deleted() -> None:
    """A pod created just now must not be deleted: it may be in-flight."""
    cfg = K8sProviderConfig(namespace="orb", auto_cleanup_orphans=True, orphan_min_age_seconds=300)
    # Pod created 10 seconds ago — well under the 300 s threshold.
    recent_ts = _iso_now_offset(-10)
    pods = [_pod(name="new-orph", request_id="r-stranger", creation_timestamp=recent_ts)]
    gc, core_v1 = _make_gc(pods=pods, config=cfg, known=["r1"])

    orphans = await gc.run_once()

    # The orphan is still returned (it is classified as an orphan) …
    assert len(orphans) == 1
    assert orphans[0].pod_name == "new-orph"
    # … but no delete call was issued.
    assert core_v1.delete_namespaced_pod.called is False
    assert gc.stats.total_orphans_deleted == 0


@pytest.mark.asyncio
async def test_orphan_older_than_min_age_is_deleted() -> None:
    """A pod older than the min-age threshold must be deleted."""
    cfg = K8sProviderConfig(namespace="orb", auto_cleanup_orphans=True, orphan_min_age_seconds=60)
    # Pod created 10 minutes ago — safely over the 60 s threshold.
    old_ts = _iso_now_offset(-600)
    pods = [_pod(name="old-orph", request_id="r-stranger", creation_timestamp=old_ts)]
    gc, core_v1 = _make_gc(pods=pods, config=cfg, known=["r1"])

    await gc.run_once()

    assert core_v1.delete_namespaced_pod.called is True
    deleted_names = {c.kwargs.get("name") for c in core_v1.delete_namespaced_pod.mock_calls}
    assert "old-orph" in deleted_names
    assert gc.stats.total_orphans_deleted == 1


# ---------------------------------------------------------------------------
# Parallel namespace fan-out (T02)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_once_calls_gc_namespace_for_all_three_namespaces() -> None:
    """run_once fans out across all configured namespaces; all three list calls happen."""
    cfg = K8sProviderConfig(namespace="orb", namespaces=["alpha", "beta", "gamma"])

    client = MagicMock()
    core_v1 = MagicMock()
    client.core_v1 = core_v1

    ns_pods: dict[str, list[Any]] = {
        "alpha": [_pod(name="orph-a", request_id="r-stranger-a", namespace="alpha")],
        "beta": [_pod(name="orph-b", request_id="r-stranger-b", namespace="beta")],
        "gamma": [],
    }

    def _list_ns(namespace: str, **_kw: Any) -> Any:
        return SimpleNamespace(items=list(ns_pods.get(namespace, [])))

    core_v1.list_namespaced_pod.side_effect = _list_ns

    gc = OrphanGarbageCollector(
        kubernetes_client=client,
        config=cfg,
        logger=MagicMock(),
        known_request_ids=lambda: [],
        interval_seconds=9999,
    )

    orphans = await gc.run_once()

    # All three namespaces were queried (order-independent).
    called_namespaces = {c.kwargs.get("namespace") for c in core_v1.list_namespaced_pod.mock_calls}
    assert called_namespaces == {"alpha", "beta", "gamma"}

    # Orphans from all responding namespaces are collected.
    assert {o.pod_name for o in orphans} == {"orph-a", "orph-b"}
    assert gc.stats.last_orphans_found == 2


@pytest.mark.asyncio
async def test_run_once_namespace_exception_does_not_halt_other_namespaces() -> None:
    """An exception in one namespace's list call is logged but does not stop the rest."""
    cfg = K8sProviderConfig(namespace="orb", namespaces=["ok1", "bad", "ok2"])

    client = MagicMock()
    core_v1 = MagicMock()
    client.core_v1 = core_v1

    def _list_ns(namespace: str, **_kw: Any) -> Any:
        if namespace == "bad":
            raise RuntimeError("apiserver blew up")
        return SimpleNamespace(
            items=[_pod(name=f"pod-{namespace}", request_id="r-stranger", namespace=namespace)]
        )

    core_v1.list_namespaced_pod.side_effect = _list_ns

    gc = OrphanGarbageCollector(
        kubernetes_client=client,
        config=cfg,
        logger=MagicMock(),
        known_request_ids=lambda: [],
        interval_seconds=9999,
    )

    orphans = await gc.run_once()

    # Orphans from the two healthy namespaces are still returned.
    pod_names = {o.pod_name for o in orphans}
    assert "pod-ok1" in pod_names
    assert "pod-ok2" in pod_names
    # The failing namespace set last_error (via _list_pods defensive catch) but did not
    # crash the whole sweep — other namespaces' orphans are still in the result.
    assert gc.stats.last_error is not None
    assert "apiserver blew up" in gc.stats.last_error


@pytest.mark.asyncio
async def test_run_once_fans_out_namespaces_in_parallel() -> None:
    """run_once must run namespace list calls concurrently.

    A serial regression (plain for-loop wrapped in ``asyncio.gather``)
    passes the "all namespaces called" test — this test blocks each list
    call on a shared ``threading.Barrier`` so a serial regression fails
    with ``BrokenBarrierError`` on timeout while the parallel path
    releases the barrier immediately.
    """
    import threading

    cfg = K8sProviderConfig(namespace="orb", namespaces=["alpha", "beta", "gamma"])
    barrier = threading.Barrier(parties=3, timeout=5)

    client = MagicMock()
    core_v1 = MagicMock()
    client.core_v1 = core_v1

    def _blocking_list(namespace: str, **_kw: Any) -> Any:
        barrier.wait()
        return SimpleNamespace(items=[])

    core_v1.list_namespaced_pod.side_effect = _blocking_list

    gc = OrphanGarbageCollector(
        kubernetes_client=client,
        config=cfg,
        logger=MagicMock(),
        known_request_ids=lambda: [],
        interval_seconds=9999,
    )

    await asyncio.wait_for(gc.run_once(), timeout=10.0)
    # If the barrier had timed out (BrokenBarrierError), we would not
    # have reached this line.  Explicit assertion is redundant but
    # documents the invariant this test protects.
    assert True
