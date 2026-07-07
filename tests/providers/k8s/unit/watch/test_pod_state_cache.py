"""Unit tests for :class:`PodStateCache`.  (Includes T07 per-key lock tests.)

Covers:

* basic upsert / get / delete semantics
* the cold-start vs empty-list distinction in :meth:`get`
* :meth:`mark_stale` evicts entries past the staleness window
* concurrent upserts from many threads do not corrupt the secondary
  index (the watcher runs on a worker thread so this is the live
  contention pattern).
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from orb.providers.k8s.watch.pod_state_cache import PodState, PodStateCache


def _state(
    *,
    request_id: str = "req-1",
    pod_name: str = "pod-a",
    status: str = "running",
    namespace: str = "ns",
) -> PodState:
    return PodState(
        request_id=request_id,
        pod_name=pod_name,
        namespace=namespace,
        status=status,
    )


def test_upsert_then_get_returns_matching_state() -> None:
    cache = PodStateCache()
    cache.upsert(_state(pod_name="pod-a"))
    cache.upsert(_state(pod_name="pod-b"))

    states = cache.get("req-1")
    assert states is not None
    names = {s.pod_name for s in states}
    assert names == {"pod-a", "pod-b"}
    assert cache.size() == 2


def test_get_returns_none_for_unseen_request() -> None:
    """Cold-start: ``get`` must return ``None``, not an empty list."""
    cache = PodStateCache()
    assert cache.get("missing") is None


def test_get_returns_empty_list_after_all_deleted() -> None:
    """``delete`` empties the bucket and removes the request entry.

    A subsequent ``get`` should report cold-start (None) so callers
    fall back to a list rather than treating the request as a real
    zero-pod state.
    """
    cache = PodStateCache()
    cache.upsert(_state(pod_name="pod-a"))
    cache.delete("req-1", "pod-a")
    assert cache.get("req-1") is None


def test_upsert_overwrites_existing_entry() -> None:
    cache = PodStateCache()
    cache.upsert(_state(pod_name="pod-a", status="pending"))
    cache.upsert(_state(pod_name="pod-a", status="running"))
    states = cache.get("req-1")
    assert states is not None
    assert len(states) == 1
    assert states[0].status == "running"


def test_clear_empties_everything() -> None:
    cache = PodStateCache()
    cache.upsert(_state(pod_name="pod-a"))
    cache.upsert(_state(request_id="req-2", pod_name="pod-z"))
    cache.clear()
    assert cache.size() == 0
    assert cache.get("req-1") is None
    assert cache.get("req-2") is None


def test_mark_stale_drops_entries_older_than_threshold() -> None:
    cache = PodStateCache()
    cache.upsert(_state(pod_name="old"))
    # Wait long enough that the entry is unambiguously stale.
    time.sleep(0.02)
    dropped = cache.mark_stale("req-1", threshold=0.01)
    assert [s.pod_name for s in dropped] == ["old"]
    # And the entry is gone.
    assert cache.get("req-1") is None


def test_mark_stale_preserves_fresh_entries() -> None:
    cache = PodStateCache()
    cache.upsert(_state(pod_name="fresh"))
    dropped = cache.mark_stale("req-1", threshold=60.0)
    assert dropped == []
    states = cache.get("req-1")
    assert states is not None
    assert {s.pod_name for s in states} == {"fresh"}


def test_mark_stale_on_unknown_request_is_noop() -> None:
    cache = PodStateCache()
    assert cache.mark_stale("does-not-exist", threshold=0.0) == []


def test_all_states_returns_snapshot() -> None:
    cache = PodStateCache()
    cache.upsert(_state(pod_name="a"))
    cache.upsert(_state(request_id="req-2", pod_name="z"))
    all_states = cache.all_states()
    assert {s.pod_name for s in all_states} == {"a", "z"}


@pytest.mark.timeout(10)
def test_concurrent_upsert_does_not_corrupt_index() -> None:
    """Many threads upserting different (req, pod) tuples must not lose entries.

    The cache uses a single RLock; this test ensures the lock is held
    around both ``_states`` and the secondary ``_by_request`` index so
    they stay in sync under contention.
    """
    cache = PodStateCache()
    n_threads = 8
    n_per_thread = 50

    def worker(tid: int) -> None:
        for i in range(n_per_thread):
            cache.upsert(_state(request_id=f"req-{tid}", pod_name=f"pod-{i}"))

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert cache.size() == n_threads * n_per_thread
    for tid in range(n_threads):
        states = cache.get(f"req-{tid}")
        assert states is not None
        assert len(states) == n_per_thread


@pytest.mark.timeout(10)
def test_concurrent_upsert_and_delete_keep_index_consistent() -> None:
    """Mixed write workload must leave the secondary index consistent.

    Half the threads insert and half the threads delete the same
    request id; after they all join, the cache must agree with itself
    (``size()`` matches the union of ``get(req)``).
    """
    cache = PodStateCache()
    n_writers = 4
    n_deleters = 4
    n_per_thread = 50

    def writer(tid: int) -> None:
        for i in range(n_per_thread):
            cache.upsert(_state(request_id="shared", pod_name=f"w{tid}-{i}"))

    def deleter(tid: int) -> None:
        for i in range(n_per_thread):
            cache.delete("shared", f"w{tid}-{i}")

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(n_writers)] + [
        threading.Thread(target=deleter, args=(t,)) for t in range(n_deleters)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Whatever survived, ``get`` must return a list whose length
    # matches the cache size for the request.
    states = cache.get("shared")
    if states is None:
        assert cache.size() == 0
    else:
        assert len(states) == cache.size()


# ---------------------------------------------------------------------------
# T07 — per-key lock: 100 concurrent mark_stale on 100 different keys
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
def test_concurrent_mark_stale_on_distinct_keys_does_not_serialise() -> None:
    """100 concurrent mark_stale calls on 100 different request IDs must all
    complete without deadlock and without corrupting the cache.

    This test exercises the per-key locking path added in T07: calls for
    distinct keys acquire different per-key locks, so they can proceed in
    parallel without waiting for a single global lock to be released.
    """
    n_keys = 100
    cache = PodStateCache()

    # Seed each key with one pod entry that is old enough to be evicted.
    for i in range(n_keys):
        cache.upsert(_state(request_id=f"req-{i}", pod_name=f"pod-{i}", status="running"))

    # Wait briefly so the entries are older than the threshold we'll use.
    time.sleep(0.02)

    evicted: list[list[PodState]] = []
    errors: list[Exception] = []

    def evict(key_idx: int) -> list[PodState]:
        return cache.mark_stale(f"req-{key_idx}", threshold=0.01)

    with ThreadPoolExecutor(max_workers=n_keys) as pool:
        futures = {pool.submit(evict, i): i for i in range(n_keys)}
        for fut in as_completed(futures):
            try:
                evicted.append(fut.result())
            except Exception as exc:
                # Exception is the correct catch here: ThreadPoolExecutor
                # already surfaces worker failures via ``fut.result()`` as
                # ``Exception`` subclasses; catching ``BaseException`` would
                # additionally swallow ``KeyboardInterrupt`` and hang the
                # test on interactive Ctrl-C.
                errors.append(exc)

    assert errors == [], f"mark_stale raised on some keys: {errors}"
    # Every key had exactly one pod; each should have been evicted.
    total_evicted = sum(len(dropped) for dropped in evicted)
    assert total_evicted == n_keys, f"Expected {n_keys} evictions, got {total_evicted}"
    # Cache should be empty after evicting everything.
    assert cache.size() == 0


@pytest.mark.timeout(10)
def test_mark_stale_distinct_keys_use_distinct_locks() -> None:
    """Locks for distinct request IDs must be different instances.

    A regression to a single global lock would make this assertion fail
    even though the correctness test above would still pass — it is the
    parallelism proof missing from the throughput-only test.
    """
    cache = PodStateCache()
    cache.upsert(_state(request_id="req-a", pod_name="pod-a"))
    cache.upsert(_state(request_id="req-b", pod_name="pod-b"))
    # Trigger per-key lock creation via a no-op mark_stale for each key.
    cache.mark_stale("req-a", threshold=999.0)
    cache.mark_stale("req-b", threshold=999.0)
    with cache._stale_locks_mutex:  # type: ignore[attr-defined]
        lock_a = cache._stale_locks["req-a"]  # type: ignore[attr-defined]
        lock_b = cache._stale_locks["req-b"]  # type: ignore[attr-defined]
    assert lock_a is not lock_b, (
        "Per-key locks were collapsed to the same lock instance — a "
        "regression to a single global lock would silently pass the "
        "throughput test above.  Distinct keys must own distinct locks."
    )


@pytest.mark.timeout(10)
def test_concurrent_mark_stale_same_key_is_safe() -> None:
    """Multiple concurrent mark_stale calls for the *same* key must not
    double-evict or corrupt the secondary index."""
    cache = PodStateCache()
    for i in range(10):
        cache.upsert(_state(request_id="shared-req", pod_name=f"pod-{i}"))

    time.sleep(0.02)

    results: list[list[PodState]] = []

    def evict() -> list[PodState]:
        return cache.mark_stale("shared-req", threshold=0.01)

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(evict) for _ in range(20)]
        for fut in as_completed(futures):
            results.append(fut.result())

    # Total evicted across all calls must equal the original 10 (no double-eviction).
    total = sum(len(r) for r in results)
    assert total == 10, f"Expected 10 total evictions, got {total}"
    assert cache.size() == 0
