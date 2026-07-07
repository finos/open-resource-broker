"""Unit tests for :class:`MultiNamespaceWatcher`.

Validates the namespace-mode dispatch:

* ``namespaces=None``      -> one watcher for ``config.namespace``
* explicit list            -> one watcher per entry
* ``namespaces=["*"]``     -> single cluster-scoped watcher (namespace=None)

Uses a stub :class:`K8sWatcher` factory by passing a
``watch_factory`` that returns inert stubs so no real watch task is
started.  We assert on the shape of the watcher fleet, not on the
event-stream behaviour (which has dedicated tests in
``test_watcher.py``).
"""

from __future__ import annotations

from typing import Iterator
from unittest.mock import MagicMock

import pytest

from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.watch.multi_namespace import MultiNamespaceWatcher
from orb.providers.k8s.watch.pod_state_cache import PodStateCache


class _NoopWatch:
    """Stub Watch that yields nothing — keeps the watch loop idle."""

    def __init__(self) -> None:
        self.resource_version: str | None = None

    def stream(self, _func, **_kwargs) -> Iterator[dict]:  # type: ignore[no-untyped-def]
        if False:  # pragma: no cover — generator with no yields
            yield {}
        return

    def stop(self) -> None:
        return None


def _build_manager(config: K8sProviderConfig) -> MultiNamespaceWatcher:
    client = MagicMock()
    return MultiNamespaceWatcher(
        kubernetes_client=client,
        config=config,
        logger=MagicMock(),
        cache=PodStateCache(),
        watch_factory=_NoopWatch,
    )


@pytest.mark.asyncio
async def test_single_namespace_mode_spawns_one_watcher() -> None:
    manager = _build_manager(K8sProviderConfig(namespace="orb"))
    manager.start()
    try:
        assert len(manager.watchers) == 1
        assert manager.watchers[0].namespace == "orb"
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_explicit_namespace_list_spawns_one_per_entry() -> None:
    manager = _build_manager(
        K8sProviderConfig(namespace="orb", namespaces=["alpha", "beta", "gamma"])
    )
    manager.start()
    try:
        namespaces = [w.namespace for w in manager.watchers]
        assert namespaces == ["alpha", "beta", "gamma"]
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_wildcard_namespaces_spawns_one_cluster_watcher() -> None:
    manager = _build_manager(K8sProviderConfig(namespace="orb", namespaces=["*"]))
    manager.start()
    try:
        assert len(manager.watchers) == 1
        # Cluster-scoped: namespace=None.
        assert manager.watchers[0].namespace is None
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_is_healthy_requires_every_watcher_alive() -> None:
    manager = _build_manager(K8sProviderConfig(namespace="orb", namespaces=["alpha", "beta"]))
    manager.start()
    try:
        manager.mark_first_sync_complete()
        # All watchers were just started — every one must report alive.
        assert manager.is_healthy() is True
        # Stop one watcher manually; aggregate health flips to False.
        await manager.watchers[0].stop()
        assert manager.is_healthy() is False
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_start_is_idempotent() -> None:
    manager = _build_manager(K8sProviderConfig(namespace="orb"))
    manager.start()
    try:
        first = list(manager.watchers)
        manager.start()  # No-op
        # The underlying watcher list must not have been replaced.
        assert [id(w) for w in manager.watchers] == [id(w) for w in first]
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_stop_clears_watchers_and_resets_state() -> None:
    manager = _build_manager(K8sProviderConfig(namespace="orb"))
    manager.start()
    await manager.stop()
    assert manager.is_started() is False
    assert manager.watchers == ()


# ---------------------------------------------------------------------------
# T08 — MultiNamespaceWatcher.stop() exception handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_continues_after_watcher_raises() -> None:
    """A watcher whose stop() raises must not prevent remaining watchers stopping.

    Injects a two-namespace manager where the first watcher's stop()
    raises; the second watcher must still be stopped.
    """

    MagicMock()
    manager = _build_manager(K8sProviderConfig(namespace="orb", namespaces=["ns1", "ns2"]))
    manager.start()

    # Replace the two child watchers with mocks so we control stop() behaviour.
    stopped: list[str] = []

    async def stop_raises() -> None:
        raise RuntimeError("simulated stop failure")

    async def stop_ok() -> None:
        stopped.append("ns2")

    watcher1 = manager.watchers[0]
    watcher2 = manager.watchers[1]
    watcher1.stop = stop_raises  # type: ignore[method-assign]
    watcher2.stop = stop_ok  # type: ignore[method-assign]

    # stop() must not propagate the exception from watcher1.
    await manager.stop()

    # watcher2 must have been stopped despite watcher1 raising.
    assert "ns2" in stopped, "stop() must continue to remaining watchers after one raises"
    # manager must still reach the cleaned-up state.
    assert manager.is_started() is False
    assert manager.watchers == ()


# ---------------------------------------------------------------------------
# T09 — is_healthy cold-start gap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_healthy_false_before_first_sync() -> None:
    """is_healthy() must return False until mark_first_sync_complete() is called."""
    manager = _build_manager(K8sProviderConfig(namespace="orb"))
    manager.start()
    try:
        # Watchers are running but the first sync has not been signalled yet.
        assert manager.is_healthy() is False, (
            "is_healthy() must be False before mark_first_sync_complete()"
        )
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_is_healthy_true_after_first_sync() -> None:
    """is_healthy() must return True once mark_first_sync_complete() is called and watchers run."""
    manager = _build_manager(K8sProviderConfig(namespace="orb"))
    manager.start()
    try:
        # Signal first sync complete.
        manager.mark_first_sync_complete()
        assert manager.is_healthy() is True, (
            "is_healthy() must be True after mark_first_sync_complete() with watchers running"
        )
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_is_healthy_false_if_watcher_stops_after_sync() -> None:
    """is_healthy() must return False when a watcher dies even after first sync."""
    manager = _build_manager(K8sProviderConfig(namespace="orb", namespaces=["a", "b"]))
    manager.start()
    try:
        manager.mark_first_sync_complete()
        assert manager.is_healthy() is True
        # Manually stop one watcher to simulate it dying.
        await manager.watchers[0].stop()
        assert manager.is_healthy() is False, (
            "is_healthy() must be False when a child watcher is no longer running"
        )
    finally:
        await manager.stop()
