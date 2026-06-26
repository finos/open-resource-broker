"""Unit tests for :class:`OrphanGarbageCollector`.

Covers:

* one-shot sweep semantics — ``run_once`` returns the orphan list and
  populates :class:`OrphanGCStats`;
* the ``auto_cleanup_orphans`` flag — False (default) logs only, True
  deletes via ``delete_namespaced_pod`` with 404 swallowing;
* the periodic loop — wakes on the configured interval and stops
  cleanly on :meth:`stop`;
* error tolerance — list failures and known-id failures do not raise.
"""

from __future__ import annotations

import asyncio
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
) -> SimpleNamespace:
    labels: dict[str, str] = {"orb.io/managed": "true"}
    if request_id is not None:
        labels["orb.io/request-id"] = request_id
    metadata = SimpleNamespace(
        name=name,
        namespace=namespace,
        labels=labels,
        creation_timestamp="2026-06-19T11:00:00Z",
    )
    return SimpleNamespace(metadata=metadata)


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
        first = gc._task  # noqa: SLF001 — testing idempotency
        gc.start()  # Should not replace the task.
        assert gc._task is first  # noqa: SLF001
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
