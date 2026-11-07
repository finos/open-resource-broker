"""The invariant oracle.

``assert_consistent`` checks the global invariants from cloud ground truth and
scenario facts, never from the rows ORB stored. Scenarios only steer the cloud.
Each invariant records, per ID, how often its non-trivial branch was exercised,
so a scenario that silently lands on the happy path cannot keep the suite green.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from orb.domain.request.request_types import RequestStatus, RequestType
from tests.providers.lifecycle.cloud import PENDING, RUNNING, Cloud

TERMINAL = frozenset(
    {
        RequestStatus.COMPLETED,
        RequestStatus.PARTIAL,
        RequestStatus.TIMEOUT,
        RequestStatus.FAILED,
        RequestStatus.CANCELLED,
    }
)
NON_TERMINAL = frozenset(
    {
        RequestStatus.PENDING,
        RequestStatus.IN_PROGRESS,
        RequestStatus.ACQUIRING,
        RequestStatus.PARTIAL_PENDING,
    }
)

INVARIANT_IDS = (
    "L1",
    "L2",
    "L3",
    "L4",
    "L4b",
    "L5",
    "L6",
    "L7",
    "L8",
    "L9",
    "L10",
    "L11",
    "L12",
    "L13",
)

# Invariants whose only scenarios are skipped until the behaviour they check exists.
# The session check fails when one of them is exercised, so the entry gets removed.
UNEXERCISED_UNTIL_BEHAVIOUR: dict[str, str] = {
    "L4b": "abandoned pending requests are not yet failed after the abandon window",
}


class InvariantViolation(AssertionError):
    """A lifecycle invariant does not hold. Harness errors use other types."""

    def __init__(self, invariant: str, message: str) -> None:
        super().__init__(f"{invariant}: {message}")
        self.invariant = invariant


class PreconditionError(AssertionError):
    """A scenario did not reach the state it set out to test."""


def counter_dir() -> Path:
    """Directory every worker of one test run appends exercised invariants to.

    Workers are children of the controller, so the controller's pid names the run
    from both sides; without xdist the process is its own controller.
    """
    controller = os.getppid() if os.environ.get("PYTEST_XDIST_WORKER") else os.getpid()
    return Path(tempfile.gettempdir()) / f"orb-lifecycle-{controller}"


def exercise(invariant: str) -> None:
    """Record that an invariant's non-trivial branch ran."""
    directory = counter_dir()
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / f"{os.getpid()}.jsonl", "a") as handle:
        handle.write(json.dumps(invariant) + "\n")


def exercised_counts() -> Counter[str]:
    total: Counter[str] = Counter()
    for path in counter_dir().glob("*.jsonl"):
        for line in path.read_text().splitlines():
            total[json.loads(line)] += 1
    return total


def require(condition: bool, invariant: str, message: str) -> None:
    if not condition:
        raise InvariantViolation(invariant, message)


def precondition(condition: bool, message: str) -> None:
    if not condition:
        raise PreconditionError(message)


@dataclass
class Scenario:
    """Facts the oracle reasons from; set by the driver and the test."""

    cell: str
    requested: int
    dry_run: bool = False
    # Whether the deadline release of stragglers is part of this run's behaviour.
    release_expected: bool = False
    deadline_passed: bool = False
    request_ids: set[str] = field(default_factory=set)
    return_ids: set[str] = field(default_factory=set)
    cancelled_requests: set[str] = field(default_factory=set)
    released_requests: set[str] = field(default_factory=set)
    # Status each request had when its terminal transition was first checked.
    settled: dict[str, RequestStatus] = field(default_factory=dict)
    # Resource ids (not machines) a request may list among its rows; see the Deployment cell.
    tolerate_resource_rows: bool = False
    resource_ids: set[str] = field(default_factory=set)
    # Machines each acquire request owned, remembered for return requests.
    owned: dict[str, set[str]] = field(default_factory=dict)


@dataclass
class RequestView:
    """What ORB stored for one request, read without syncing."""

    request_id: str
    status: RequestStatus
    request_type: RequestType
    machine_ids: list[str]
    metadata: dict[str, Any]
    rows: dict[str, Any]
    dto: Any = None
    hf_entry: Optional[dict[str, Any]] = None


def _hf_complete(status: str) -> bool:
    return status in ("complete", "completed")


def assert_consistent(scn: Scenario, cloud: Cloud, view: RequestView, *, phase: str) -> None:
    """Check every applicable invariant against cloud truth."""
    _l12_blast_radius(scn, cloud)
    if view.request_type != RequestType.ACQUIRE:
        return
    if view.metadata.get("dry_run"):
        _l13_dry_run(scn, cloud, view)
        return
    status = view.status
    owned = cloud.machines(view.request_id)

    # L1, L2 and L7 describe the moment of the terminal transition. What happens to
    # machines afterwards (interruption, replacement) is L9.
    if status in TERMINAL and scn.settled.get(view.request_id) == status:
        return
    if status in TERMINAL:
        scn.settled[view.request_id] = status
    if status in TERMINAL:
        _l1_no_machine_in_flight(scn, view, owned)
    if status == RequestStatus.COMPLETED:
        _l2_complete_matches_cloud(scn, view, owned)
    if phase == "created" and status in TERMINAL:
        # L7: a final create result means every returned machine already runs.
        exercise("L7")
        for machine in owned:
            require(
                machine.state != PENDING,
                "L7",
                f"create returned a final {status.value} request but {machine.machine_id} "
                f"is {machine.state}",
            )


def _l1_no_machine_in_flight(scn: Scenario, view: RequestView, owned: list) -> None:
    if view.request_id in scn.cancelled_requests:
        return
    if scn.deadline_passed and not scn.release_expected:
        return
    exercise("L1")
    in_flight = [m.machine_id for m in owned if m.state == PENDING]
    require(
        not in_flight,
        "L1",
        f"request {view.request_id} is {view.status.value} while {in_flight} are still pending",
    )


def _l2_complete_matches_cloud(scn: Scenario, view: RequestView, owned: list) -> None:
    exercise("L2")
    running = [m for m in owned if m.state == RUNNING]
    require(
        len(running) == scn.requested,
        "L2",
        f"COMPLETED with {len(running)} running of {scn.requested} requested",
    )
    ignored = scn.resource_ids if scn.tolerate_resource_rows else set()
    reported = [i for i in view.machine_ids if i not in ignored]
    require(len(reported) == len(set(reported)), "L2", f"duplicate machine ids {reported}")
    cloud_ids = {m.machine_id for m in running}
    foreign = [i for i in reported if i not in cloud_ids]
    require(not foreign, "L2", f"ids {foreign} do not belong to this request")
    for machine in running:
        require(bool(machine.machine_id), "L2", "empty machine id")
    for machine_id, row in view.rows.items():
        if machine_id in ignored:
            continue
        require(bool(getattr(row, "private_ip", None)), "L2", f"{machine_id} has no private ip")
        image = str(getattr(row, "image_id", "") or "")
        require(
            bool(image) and image != "unknown",
            "L2",
            f"{machine_id} has image id {image!r}",
        )
    if view.dto is not None:
        for ref in view.dto.machine_references:
            if ref.machine_id in ignored:
                continue
            require(ref.result == "succeed", "L2", f"{ref.machine_id} result {ref.result!r}")
    if view.hf_entry is not None:
        require(
            _hf_complete(str(view.hf_entry.get("status"))),
            "L2",
            f"HostFactory status {view.hf_entry.get('status')!r} for a complete request",
        )
        for machine in view.hf_entry.get("machines", []):
            if machine.get("machineId") in ignored:
                continue
            require(machine.get("result") == "succeed", "L2", f"HostFactory machine {machine}")


def _l12_blast_radius(scn: Scenario, cloud: Cloud) -> None:
    """ORB never kills a machine outside the allowed set.

    The allowed set is the explicit return ids, the machines of requests whose
    stragglers a deadline release returns, and the cloud-truth members of every
    resource a cancel deleted; each must also belong to a targeted request.
    """
    killed = cloud.killed()
    if not killed:
        return
    exercise("L12")
    owners = {m.machine_id: m.request_id for m in cloud.machines()}
    for machine_id in sorted(killed):
        owner = owners.get(machine_id)
        allowed = (
            machine_id in scn.return_ids
            or owner in scn.cancelled_requests
            or owner in scn.released_requests
        )
        require(allowed, "L12", f"{machine_id} (request {owner}) was killed without being targeted")
        require(
            owner in scn.request_ids,
            "L12",
            f"{machine_id} belongs to {owner}, which is not a targeted request",
        )


def _l13_dry_run(scn: Scenario, cloud: Cloud, view: RequestView) -> None:
    exercise("L13")
    creates = cloud.calls("RunInstances", "CreateFleet", "CreateDeployment", "CreatePod")
    require(not creates, "L13", f"dry run made create calls: {[c.operation for c in creates]}")
    require(
        view.status == RequestStatus.COMPLETED,
        "L13",
        f"dry run request is {view.status.value}",
    )
    require(not view.machine_ids, "L13", f"dry run has machines {view.machine_ids}")
