"""Behaviour a scenario needs that is not in the code under test yet.

A scenario carrying one of these reasons is skipped or pinned as a strict expected
failure, never weakened. Removing the reason from the scenario is the whole change
needed once the behaviour lands.
"""

from __future__ import annotations

import pytest

DEPLOYMENT_LISTS_ITSELF = (
    "a completed Deployment request lists the Deployment itself as a machine with unknown "
    "status, no address and no image"
)
TERMINAL_ROWS_STALE = (
    "a settled request is served from stored machine rows without a provider read, so a machine "
    "lost later still shows as running"
)
STALE_READ_GUARD = (
    "a pod list that fails is reported as an in-progress verdict with no machines, and stored "
    "machine rows and status are then overwritten from that empty answer"
)

skip_terminal_rows_stale = pytest.mark.skip(reason=TERMINAL_ROWS_STALE)
DEPLOYMENT_PODS_NOT_TRACKED = (
    "a Deployment request records the Deployment's own name as its machine id instead of its "
    "pods, so the pods it launched are neither counted at a deadline nor released"
)
DEPLOYMENT_LATE_PODS_NOT_RELEASED = (
    "pods a Deployment's controller creates after the request's deadline are left running and "
    "the Deployment's replicas are not lowered"
)
