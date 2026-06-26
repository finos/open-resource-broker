"""Unit tests for :class:`K8sNodeStateCache`.

Covers:

* basic upsert / get / delete semantics
* ``all()`` snapshot method
* ``clear()``
* concurrent upsert / delete safety under high contention
* the ``last_updated`` field is stamped by the cache (not the caller)
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest

from orb.providers.k8s.watch.node_state_cache import K8sNodeState, K8sNodeStateCache


def _state(
    *,
    name: str = "node-a",
    instance_type: str | None = "m5.xlarge",
    zone: str | None = "us-east-1a",
    capacity_type: str | None = "on-demand",
    cpu_capacity: str | None = "4",
    memory_capacity: str | None = "16Gi",
    cpu_allocatable: str | None = "3800m",
    memory_allocatable: str | None = "14Gi",
    ready: bool = True,
) -> K8sNodeState:
    return K8sNodeState(
        name=name,
        instance_type=instance_type,
        zone=zone,
        capacity_type=capacity_type,
        cpu_capacity=cpu_capacity,
        memory_capacity=memory_capacity,
        cpu_allocatable=cpu_allocatable,
        memory_allocatable=memory_allocatable,
        conditions=[{"type": "Ready", "status": "True", "reason": None, "lastTransitionTime": ""}],
        ready=ready,
    )


def test_upsert_then_get_returns_matching_state() -> None:
    cache = K8sNodeStateCache()
    cache.upsert(_state(name="node-a"))

    result = cache.get("node-a")
    assert result is not None
    assert result.name == "node-a"
    assert result.instance_type == "m5.xlarge"
    assert result.zone == "us-east-1a"
    assert result.capacity_type == "on-demand"
    assert result.ready is True


def test_get_returns_none_for_unknown_node() -> None:
    cache = K8sNodeStateCache()
    assert cache.get("does-not-exist") is None


def test_upsert_overwrites_existing_entry() -> None:
    cache = K8sNodeStateCache()
    cache.upsert(_state(name="node-a", instance_type="m5.xlarge"))
    cache.upsert(_state(name="node-a", instance_type="c5.2xlarge"))

    result = cache.get("node-a")
    assert result is not None
    assert result.instance_type == "c5.2xlarge"
    assert cache.size() == 1


def test_delete_removes_entry() -> None:
    cache = K8sNodeStateCache()
    cache.upsert(_state(name="node-a"))
    cache.delete("node-a")

    assert cache.get("node-a") is None
    assert cache.size() == 0


def test_delete_missing_entry_is_noop() -> None:
    cache = K8sNodeStateCache()
    # Should not raise.
    cache.delete("not-there")
    assert cache.size() == 0


def test_all_returns_snapshot_of_all_entries() -> None:
    cache = K8sNodeStateCache()
    cache.upsert(_state(name="node-a"))
    cache.upsert(_state(name="node-b"))
    cache.upsert(_state(name="node-c"))

    all_states = cache.all()
    assert {s.name for s in all_states} == {"node-a", "node-b", "node-c"}
    assert len(all_states) == 3


def test_clear_empties_cache() -> None:
    cache = K8sNodeStateCache()
    cache.upsert(_state(name="node-a"))
    cache.upsert(_state(name="node-b"))
    cache.clear()

    assert cache.size() == 0
    assert cache.get("node-a") is None
    assert cache.get("node-b") is None


def test_upsert_stamps_last_updated() -> None:
    """The cache overwrites last_updated with the current UTC clock."""
    # Pass a very old last_updated; the cache must replace it.
    old_time = datetime(2000, 1, 1, tzinfo=timezone.utc)
    state = K8sNodeState(name="node-x", last_updated=old_time)
    cache = K8sNodeStateCache()
    cache.upsert(state)

    result = cache.get("node-x")
    assert result is not None
    # The stamped time must be significantly newer than 2000.
    assert result.last_updated.year >= 2024


def test_conditions_are_copied_on_upsert() -> None:
    """Mutations to the original conditions list must not affect the cache."""
    conditions = [{"type": "Ready", "status": "True", "reason": None, "lastTransitionTime": ""}]
    state = K8sNodeState(name="node-y", conditions=conditions, ready=True)
    cache = K8sNodeStateCache()
    cache.upsert(state)

    # Mutate original
    conditions.append(
        {"type": "MemoryPressure", "status": "True", "reason": None, "lastTransitionTime": ""}
    )

    result = cache.get("node-y")
    assert result is not None
    # Cache entry should have only 1 condition (snapshot at upsert time)
    assert len(result.conditions) == 1


@pytest.mark.timeout(10)
def test_concurrent_upsert_does_not_corrupt_cache() -> None:
    """Many threads upserting different node names must not lose entries.

    The cache uses a single RLock; this test ensures the lock is held
    for both the dict write and the size counter update.
    """
    cache = K8sNodeStateCache()
    n_threads = 8
    n_per_thread = 50

    def worker(tid: int) -> None:
        for i in range(n_per_thread):
            cache.upsert(_state(name=f"node-{tid}-{i}"))

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert cache.size() == n_threads * n_per_thread
    for tid in range(n_threads):
        for i in range(n_per_thread):
            assert cache.get(f"node-{tid}-{i}") is not None


@pytest.mark.timeout(10)
def test_concurrent_upsert_and_delete_keep_cache_consistent() -> None:
    """Mixed write workload must leave the cache self-consistent.

    Half the threads insert nodes and half delete them for the same
    set of names; after joining, ``size()`` must equal ``len(all())``.
    """
    cache = K8sNodeStateCache()
    n_writers = 4
    n_deleters = 4
    n_per_thread = 50

    def writer(tid: int) -> None:
        for i in range(n_per_thread):
            cache.upsert(_state(name=f"node-{i}"))

    def deleter(tid: int) -> None:
        for i in range(n_per_thread):
            cache.delete(f"node-{i}")

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(n_writers)] + [
        threading.Thread(target=deleter, args=(t,)) for t in range(n_deleters)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Whatever survived, size() must equal len(all()).
    assert cache.size() == len(cache.all())
