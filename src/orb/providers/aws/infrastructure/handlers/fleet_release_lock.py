"""Per-fleet mutual exclusion for fleet release operations.

A return reads a maintain fleet's target capacity, subtracts the capacity being
returned and writes the result. The EC2 API has no compare-and-set, so two returns
on the same fleet that interleave lose one of the reductions. Serialising the
release of one fleet makes every read-modify-write run against the value the
previous return left behind.

The guarantee holds within one process only. The lock is an in-memory object: a
second server worker, or an ``orb return`` run beside a server, has its own copy
and is not serialised against this one.

Entries are reference counted and removed once nobody holds or waits for them, so
the map does not grow with the number of fleets ever released.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from orb.providers.aws.exceptions.aws_exceptions import AWSInfrastructureError

# How long a release waits for another release of the same fleet. This is
# the order of the time one release may spend in its retried AWS calls, so a
# waiter outlasts a normal predecessor and gives up, retryably, behind a stuck one.
DEFAULT_ACQUIRE_TIMEOUT_SECONDS = 120.0


class FleetReleaseBusyError(AWSInfrastructureError):
    """Another release of the same fleet did not finish in time; the return can be retried."""


@dataclass
class _Entry:
    lock: threading.Lock = field(default_factory=threading.Lock)
    holders: int = 0


_guard = threading.Lock()
_entries: dict[str, _Entry] = {}


def active_entries() -> int:
    """Number of fleets currently held or awaited (for tests and diagnostics)."""
    with _guard:
        return len(_entries)


@contextmanager
def fleet_release_lock(
    fleet_id: str, timeout: float = DEFAULT_ACQUIRE_TIMEOUT_SECONDS
) -> Iterator[None]:
    """Hold the release lock of *fleet_id*, waiting at most *timeout* seconds.

    Raises:
        FleetReleaseBusyError: The lock was not obtained in time. The entry's
            reference is dropped on this path as well.
    """
    with _guard:
        entry = _entries.setdefault(fleet_id, _Entry())
        entry.holders += 1
    acquired = False
    try:
        acquired = entry.lock.acquire(timeout=timeout)
        if not acquired:
            raise FleetReleaseBusyError(
                f"Another release of fleet {fleet_id} did not finish within {timeout:g}s; "
                "retry the return"
            )
        yield
    finally:
        if acquired:
            entry.lock.release()
        with _guard:
            entry.holders -= 1
            if entry.holders == 0:
                _entries.pop(fleet_id, None)
