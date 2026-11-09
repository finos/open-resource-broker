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
from collections.abc import Iterator
from contextlib import contextmanager
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
    "no_machine_in_flight",
    "complete_matches_cloud",
    "pending_converges",
    "deadline_outcome",
    "abandoned_pending",
    "cancel_aborts",
    "return_waits_for_termination",
    "create_result_final",
    "interface_consistency",
    "post_terminal_drift",
    "capacity_shortfall",
    "return_lifecycle",
    "blast_radius",
    "dry_run",
)

# Invariants whose only scenarios are skipped until the behaviour they check exists.
# The session check fails when one of them is exercised, so the entry gets removed.
UNEXERCISED_UNTIL_BEHAVIOUR: dict[str, str] = {}


class InvariantViolation(AssertionError):
    """A lifecycle invariant does not hold. Harness errors use other types."""

    def __init__(self, invariant: str, message: str) -> None:
        super().__init__(f"{invariant}: {message}")
        self.invariant = invariant


class KnownGap(AssertionError):
    """The expected violation of a documented gap, raised by ``expect_violation``."""


@contextmanager
def expect_violation(*invariants: str) -> Iterator[None]:
    """Turn exactly the named invariants' violations into ``KnownGap``.

    A strict ``xfail(raises=KnownGap)`` then counts only that failure as the
    expected one; any other violation, or any other exception, propagates as a
    real failure instead of being absorbed by the expectation.
    """
    try:
        yield
    except InvariantViolation as exc:
        if exc.invariant not in invariants:
            raise
        raise KnownGap(str(exc)) from exc


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


_WIRE_TERMINAL = frozenset({"complete", "complete_with_error"})


def is_terminal(value: Any) -> bool:
    """Whether a status, as an enum, its value or a wire string, is terminal.

    Anything unrecognised counts as non-terminal, so a surprising value fails the
    check that expects a terminal status instead of passing it.
    """
    if isinstance(value, RequestStatus):
        return value in TERMINAL
    text = str(getattr(value, "value", value)).strip().lower()
    if text in _WIRE_TERMINAL:
        return True
    try:
        return RequestStatus(text) in TERMINAL
    except ValueError:
        return False


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
    _blast_radius(scn, cloud)
    if view.request_type != RequestType.ACQUIRE:
        return
    if view.metadata.get("dry_run"):
        _dry_run(scn, cloud, view)
        return
    status = view.status
    owned = cloud.machines(view.request_id)

    # The in-flight, completeness and create-result checks describe the moment of the
    # terminal transition. What happens to machines afterwards (interruption,
    # replacement) is the post-terminal drift check.
    if status in TERMINAL and scn.settled.get(view.request_id) == status:
        return
    if status in TERMINAL:
        scn.settled[view.request_id] = status
    if status in TERMINAL:
        _no_machine_in_flight(scn, view, owned)
    if status == RequestStatus.COMPLETED:
        _complete_matches_cloud(scn, view, owned)
    if phase == "created" and status in TERMINAL:
        # A final create result means every returned machine already runs.
        exercise("create_result_final")
        for machine in owned:
            require(
                machine.state != PENDING,
                "create_result_final",
                f"create returned a final {status.value} request but {machine.machine_id} "
                f"is {machine.state}",
            )


def _no_machine_in_flight(scn: Scenario, view: RequestView, owned: list) -> None:
    if view.request_id in scn.cancelled_requests:
        return
    if scn.deadline_passed and not scn.release_expected:
        return
    exercise("no_machine_in_flight")
    in_flight = [m.machine_id for m in owned if m.state == PENDING]
    require(
        not in_flight,
        "no_machine_in_flight",
        f"request {view.request_id} is {view.status.value} while {in_flight} are still pending",
    )


def _complete_matches_cloud(scn: Scenario, view: RequestView, owned: list) -> None:
    exercise("complete_matches_cloud")
    running = [m for m in owned if m.state == RUNNING]
    require(
        len(running) == scn.requested,
        "complete_matches_cloud",
        f"COMPLETED with {len(running)} running of {scn.requested} requested",
    )
    reported = list(view.machine_ids)
    require(
        len(reported) == len(set(reported)),
        "complete_matches_cloud",
        f"duplicate machine ids {reported}",
    )
    cloud_ids = {m.machine_id for m in running}
    foreign = [i for i in reported if i not in cloud_ids]
    require(not foreign, "complete_matches_cloud", f"ids {foreign} do not belong to this request")
    _same_machines("request", reported, cloud_ids)
    _same_machines("stored rows", list(view.rows), cloud_ids)
    if view.dto is not None:
        refs = [r.machine_id for r in view.dto.machine_references]
        _same_machines("status response", refs, cloud_ids)
    if view.hf_entry is not None:
        listed = [m.get("machineId") for m in view.hf_entry.get("machines", [])]
        _same_machines("HostFactory response", listed, cloud_ids)
    for machine in running:
        require(bool(machine.machine_id), "complete_matches_cloud", "empty machine id")
    for machine_id, row in view.rows.items():
        require(
            bool(getattr(row, "private_ip", None)),
            "complete_matches_cloud",
            f"{machine_id} has no private ip",
        )
        image = str(getattr(row, "image_id", "") or "")
        require(
            bool(image) and image != "unknown",
            "complete_matches_cloud",
            f"{machine_id} has image id {image!r}",
        )
    if view.dto is not None:
        for ref in view.dto.machine_references:
            require(
                ref.result == "succeed",
                "complete_matches_cloud",
                f"{ref.machine_id} result {ref.result!r}",
            )
    if view.hf_entry is not None:
        require(
            _hf_complete(str(view.hf_entry.get("status"))),
            "complete_matches_cloud",
            f"HostFactory status {view.hf_entry.get('status')!r} for a complete request",
        )
        for machine in view.hf_entry.get("machines", []):
            require(
                machine.get("result") == "succeed",
                "complete_matches_cloud",
                f"HostFactory machine {machine}",
            )


def _same_machines(where: str, reported: list, cloud_ids: set) -> None:
    require(
        set(reported) == cloud_ids,
        "complete_matches_cloud",
        f"{where} lists machines {sorted(set(reported))}, the cloud runs {sorted(cloud_ids)}",
    )


def _blast_radius(scn: Scenario, cloud: Cloud) -> None:
    """ORB never kills a machine outside the allowed set.

    The allowed set is the explicit return ids, the machines of requests whose
    stragglers a deadline release returns, and the cloud-truth members of every
    resource a cancel deleted; each must also belong to a targeted request.
    """
    killed = cloud.killed()
    if not killed:
        return
    exercise("blast_radius")
    owners = {m.machine_id: m.request_id for m in cloud.machines()}
    for machine_id in sorted(killed):
        owner = owners.get(machine_id)
        allowed = (
            machine_id in scn.return_ids
            or owner in scn.cancelled_requests
            or owner in scn.released_requests
        )
        require(
            allowed,
            "blast_radius",
            f"{machine_id} (request {owner}) was killed without being targeted",
        )
        require(
            owner in scn.request_ids,
            "blast_radius",
            f"{machine_id} belongs to {owner}, which is not a targeted request",
        )


def _dry_run(scn: Scenario, cloud: Cloud, view: RequestView) -> None:
    exercise("dry_run")
    creates = cloud.calls("RunInstances", "CreateFleet", "CreateDeployment", "CreatePod")
    require(not creates, "dry_run", f"dry run made create calls: {[c.operation for c in creates]}")
    require(
        view.status == RequestStatus.COMPLETED,
        "dry_run",
        f"dry run request is {view.status.value}",
    )
    require(not view.machine_ids, "dry_run", f"dry run has machines {view.machine_ids}")
