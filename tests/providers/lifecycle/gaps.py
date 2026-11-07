"""Behaviour a scenario needs that is not in the code under test yet.

A scenario carrying one of these reasons is skipped, not weakened. Removing the
reason from the scenario is the whole change needed once the behaviour lands.
"""

from __future__ import annotations

import pytest

DEADLINE_RELEASE = (
    "a deadline outcome does not yet release the machines it leaves pending "
    "(no deadline release service)"
)
ABANDONED_PENDING = (
    "a pending request whose create never recorded resources is not yet failed after "
    "the abandon window"
)
CANCEL_ABORTS = (
    "cancel does not yet abort in-flight provisioning (no provider abort outcome per resource)"
)
DEADLINE_COUNTS_LAUNCHED = (
    "a deadline outcome is decided from the launched or recorded machine count instead of the "
    "machines running at the fresh provider read"
)
ZERO_ID_RELEASE = (
    "a deadline outcome with a resource id and no machine ids is not marked for release; "
    "machines the resource launches later are not released"
)
DEPLOYMENT_LISTS_ITSELF = (
    "a completed Deployment request lists the Deployment itself as a machine with unknown "
    "status, no address and no image"
)
TERMINAL_ROWS_STALE = (
    "a settled request is served from stored machine rows without a provider read, so a machine "
    "lost later still shows as running"
)
INSTANT_FLEET_PARTIAL_RETURN = (
    "returning some machines of an instant fleet deletes the fleet with termination, which also "
    "kills the members that were not returned"
)
STALE_READ_GUARD = "a failed or empty provider read still overwrites stored machine rows and status"
SPOT_RUN_INSTANCES = (
    "RunInstances does not yet accept spot templates, and the default spec ignores the spot "
    "price type"
)

skip_deadline_release = pytest.mark.skip(reason=DEADLINE_RELEASE)
skip_abandoned_pending = pytest.mark.skip(reason=ABANDONED_PENDING)
skip_deadline_counts_launched = pytest.mark.skip(reason=DEADLINE_COUNTS_LAUNCHED)
skip_cancel_aborts = pytest.mark.skip(reason=CANCEL_ABORTS)
skip_terminal_rows_stale = pytest.mark.skip(reason=TERMINAL_ROWS_STALE)
