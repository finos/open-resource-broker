"""Unit tests for :class:`MultiNamespaceWatcher`.

Validates the namespace-mode dispatch:

* ``namespaces=None``      -> one watcher for ``config.namespace``
* explicit list            -> one watcher per entry
* ``namespaces=["*"]``     -> single cluster-scoped watcher (namespace=None)

Uses a stub :class:`KubernetesWatcher` factory by passing a
``watch_factory`` that returns inert stubs so no real watch task is
started.  We assert on the shape of the watcher fleet, not on the
event-stream behaviour (which has dedicated tests in
``test_watcher.py``).
"""

from __future__ import annotations

from typing import Iterator
from unittest.mock import MagicMock

import pytest

from orb.providers.kubernetes.configuration.config import KubernetesProviderConfig
from orb.providers.kubernetes.watch.multi_namespace import MultiNamespaceWatcher
from orb.providers.kubernetes.watch.pod_state_cache import PodStateCache


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


def _build_manager(config: KubernetesProviderConfig) -> MultiNamespaceWatcher:
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
    manager = _build_manager(KubernetesProviderConfig(namespace="orb"))
    manager.start()
    try:
        assert len(manager.watchers) == 1
        assert manager.watchers[0].namespace == "orb"
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_explicit_namespace_list_spawns_one_per_entry() -> None:
    manager = _build_manager(
        KubernetesProviderConfig(namespace="orb", namespaces=["alpha", "beta", "gamma"])
    )
    manager.start()
    try:
        namespaces = [w.namespace for w in manager.watchers]
        assert namespaces == ["alpha", "beta", "gamma"]
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_wildcard_namespaces_spawns_one_cluster_watcher() -> None:
    manager = _build_manager(KubernetesProviderConfig(namespace="orb", namespaces=["*"]))
    manager.start()
    try:
        assert len(manager.watchers) == 1
        # Cluster-scoped: namespace=None.
        assert manager.watchers[0].namespace is None
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_is_healthy_requires_every_watcher_alive() -> None:
    manager = _build_manager(
        KubernetesProviderConfig(namespace="orb", namespaces=["alpha", "beta"])
    )
    manager.start()
    try:
        # All watchers were just started — every one must report alive.
        assert manager.is_healthy() is True
        # Stop one watcher manually; aggregate health flips to False.
        await manager.watchers[0].stop()
        assert manager.is_healthy() is False
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_start_is_idempotent() -> None:
    manager = _build_manager(KubernetesProviderConfig(namespace="orb"))
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
    manager = _build_manager(KubernetesProviderConfig(namespace="orb"))
    manager.start()
    await manager.stop()
    assert manager.is_started() is False
    assert manager.watchers == ()
