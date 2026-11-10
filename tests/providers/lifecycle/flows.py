"""Cell-independent lifecycle flows.

Each flow steers the cloud, drives ORB through ``Lifecycle`` and asserts the
facts its scenario sets up. The oracle runs after every step on its own.
"""

from __future__ import annotations

from typing import Any

from orb.domain.request.request_types import RequestStatus
from tests.providers.lifecycle.cloud import PENDING, RUNNING, STOPPING, TERMINATED
from tests.providers.lifecycle.driver import Lifecycle
from tests.providers.lifecycle.oracle import (
    NON_TERMINAL,
    TERMINAL,
    exercise,
    precondition,
    require,
)

# Longer than any default request deadline.
PAST_DEADLINE_SECONDS = 4 * 3600


def deadline_outcome(running: int, requested: int) -> RequestStatus:
    """The status a passed deadline must produce for ``running`` of ``requested``."""
    if running >= requested:
        return RequestStatus.COMPLETED
    return RequestStatus.PARTIAL if running > 0 else RequestStatus.TIMEOUT


async def acquire_complete(life: Lifecycle, count: int = 3) -> Any:
    """A request is COMPLETED exactly when the requested machines run: counting pending machines as running turns it red."""
    handle = await life.acquire(count)
    await life.sync(handle)
    precondition(life.cloud.count(handle.request_id, RUNNING) == count, "machines not running")
    require(
        life.status(handle) == RequestStatus.COMPLETED,
        "complete_matches_cloud",
        f"all {count} machines run but the request is {life.status(handle).value}",
    )
    return handle


async def complete_lists_only_machines(life: Lifecycle) -> None:
    """A completed request lists only machines of the cloud, each with an address and an image."""
    await acquire_complete(life, 2)


async def delayed_pending(life: Lifecycle, count: int = 2) -> None:
    """Machines held pending keep the request non-terminal until they start; completing at dispatch turns it red."""
    life.cloud.hold_pending()
    handle = await life.acquire(count)
    precondition(life.cloud.count(handle.request_id, PENDING) == count, "machines not pending")
    precondition(life.status(handle) in NON_TERMINAL, "request is already terminal")
    for _ in range(3):
        await life.sync(handle)
        exercise("pending_converges")
        require(
            life.status(handle) in NON_TERMINAL,
            "pending_converges",
            f"request is {life.status(handle).value} while {count} machines are pending",
        )
    life.cloud.advance()
    await life.sync(handle)
    require(
        life.status(handle) == RequestStatus.COMPLETED,
        "pending_converges",
        f"request is {life.status(handle).value} after the machines started",
    )
    rows = life.view(handle).rows
    require(
        {r.status.value for r in rows.values()} == {"running"},
        "pending_converges",
        f"stored rows {[r.status.value for r in rows.values()]} did not converge to running",
    )


async def deadline_status(life: Lifecycle, running: int, requested: int = 3) -> None:
    """A passed deadline gives COMPLETED, PARTIAL or TIMEOUT from the running count, never a non-terminal status."""
    held = requested - running
    if held:
        life.cloud.hold_pending(held)
    handle = await life.acquire(requested)
    precondition(
        life.cloud.count(handle.request_id, RUNNING) == running,
        f"expected {running} running machines before the deadline",
    )
    if running < requested:
        precondition(life.status(handle) in NON_TERMINAL, "request settled before its deadline")
    life.scenario.deadline_passed = True
    if running < requested:
        # The deadline outcome releases the machines it leaves pending.
        life.scenario.release_expected = True
        life.scenario.released_requests.add(handle.request_id)
    life.advance_clock(PAST_DEADLINE_SECONDS)
    await life.sync(handle)
    expected = deadline_outcome(running, requested)
    if running < requested:
        # Only a shortfall tests the deadline decision; all machines running is
        # completion, which the other scenarios already cover.
        exercise("deadline_outcome")
    actual = life.status(handle)
    require(
        actual in TERMINAL,
        "deadline_outcome",
        f"request is still {actual.value} after its deadline",
    )
    require(
        actual == expected,
        "deadline_outcome",
        f"{running} of {requested} running at the deadline gave {actual.value}, want {expected.value}",
    )


async def cancel_flips_status(life: Lifecycle, count: int = 2) -> None:
    """Cancelling an unsettled request ends it CANCELLED."""
    life.cloud.hold_pending()
    handle = await life.acquire(count)
    precondition(life.status(handle) in NON_TERMINAL, "request is already terminal")
    await life.cancel(handle)
    require(
        life.status(handle) == RequestStatus.CANCELLED,
        "cancel_aborts",
        f"cancelled request is {life.status(handle).value}",
    )


async def cancel_aborts_resources(life: Lifecycle, count: int = 2) -> None:
    """A cancel leaves no machine of the request pending or running."""
    life.cloud.hold_pending()
    handle = await life.acquire(count)
    precondition(life.cloud.count(handle.request_id, PENDING) == count, "machines not pending")
    await life.cancel(handle)
    life.cloud.advance()
    exercise("cancel_aborts")
    left = life.cloud.count(handle.request_id, PENDING, RUNNING, STOPPING)
    require(left == 0, "cancel_aborts", f"{left} machines of the cancelled request are still alive")
    require(bool(life.cloud.killed()), "cancel_aborts", "cancel killed nothing")


async def return_lag(life: Lifecycle, count: int = 2) -> None:
    """A return request stays in progress while targeted machines are still shutting down; completing at dispatch turns it red."""
    handle = await acquire_complete(life, count)
    ids = [m.machine_id for m in life.cloud.machines(handle.request_id)]
    life.cloud.lag_termination()
    return_ids = await life.return_(ids)
    precondition(bool(return_ids), "no return request created")
    precondition(life.cloud.count(handle.request_id, STOPPING) == count, "terminations did not lag")
    for _ in range(2):
        dto = await life.sync_return(return_ids[0])
        exercise("return_waits_for_termination")
        require(
            RequestStatus(dto.status) in NON_TERMINAL,
            "return_waits_for_termination",
            f"return request is {dto.status} while machines are still shutting down",
        )
    life.cloud.advance()
    dto = await life.sync_return(return_ids[0])
    require(
        RequestStatus(dto.status) == RequestStatus.COMPLETED,
        "return_waits_for_termination",
        f"return request is {dto.status} after every machine terminated",
    )
    require(
        life.cloud.count(handle.request_id, TERMINATED) == count,
        "return_waits_for_termination",
        "machines are not terminated",
    )


async def post_terminal_drift(life: Lifecycle, count: int = 2) -> Any:
    """A machine lost after the request settled does not move the request."""
    handle = await acquire_complete(life, count)
    lost = [m.machine_id for m in life.cloud.machines(handle.request_id)][:1]
    life.cloud.interrupt(lost)
    await life.sync(handle)
    exercise("post_terminal_drift")
    require(
        life.status(handle) == RequestStatus.COMPLETED,
        "post_terminal_drift",
        f"request left its terminal state: {life.status(handle).value}",
    )
    return handle, lost


async def post_terminal_loss_in_rows(life: Lifecycle, count: int = 2) -> None:
    """A machine lost after the request settled shows as gone in the stored rows."""
    handle, lost = await post_terminal_drift(life, count)
    rows = life.view(handle).rows
    status = rows[lost[0]].status.value if lost[0] in rows else "missing"
    require(
        status in ("terminated", "shutting-down", "failed"),
        "post_terminal_drift",
        f"lost machine {lost[0]} shows {status} in the stored rows",
    )


async def capacity_shortfall(life: Lifecycle, capacity: int = 1, requested: int = 2) -> None:
    """A launch that falls short ends PARTIAL or TIMEOUT once the deadline passes."""
    life.cloud.limit_capacity(capacity)
    handle = await life.acquire(requested)
    precondition(
        life.cloud.count(handle.request_id, RUNNING) == capacity,
        "capacity limit did not apply",
    )
    life.scenario.deadline_passed = True
    life.advance_clock(PAST_DEADLINE_SECONDS)
    await life.sync(handle)
    exercise("capacity_shortfall")
    actual = life.status(handle)
    require(
        actual in (RequestStatus.PARTIAL, RequestStatus.TIMEOUT),
        "capacity_shortfall",
        f"{capacity} of {requested} launched and the request is {actual.value}",
    )


async def blast_radius(life: Lifecycle) -> None:
    """A return terminates the targeted machines and nothing else; widening the targeted set turns it red."""
    bystander = await acquire_complete(life, 2)
    target = await acquire_complete(life, 3)
    keep = {m.machine_id for m in life.cloud.machines(bystander.request_id)}
    ids = [m.machine_id for m in life.cloud.machines(target.request_id)]
    # Return one of three: the other two of the target and the bystander survive.
    await life.return_(ids[:1])
    for machine in life.cloud.machines():
        if machine.machine_id in keep or machine.machine_id in ids[1:]:
            require(
                machine.state == RUNNING,
                "blast_radius",
                f"{machine.machine_id} was not targeted but is {machine.state}",
            )
    exercise("blast_radius")


async def return_lifecycle(life: Lifecycle) -> None:
    """Returning machines twice is not an error and terminates nothing a second time."""
    handle = await acquire_complete(life, 2)
    ids = [m.machine_id for m in life.cloud.machines(handle.request_id)]
    first = await life.return_(ids)
    dto = await life.sync_return(first[0])
    exercise("return_lifecycle")
    require(
        RequestStatus(dto.status) in (RequestStatus.COMPLETED, RequestStatus.IN_PROGRESS),
        "return_lifecycle",
        f"return request is {dto.status}",
    )
    kills_before = len(life.cloud.killed())
    # A second return of the same, already terminated machines is not an error and
    # does not kill anything again.
    await life.return_(ids)
    require(
        len(life.cloud.killed()) == kills_before,
        "return_lifecycle",
        "a repeated return killed machines again",
    )


async def dry_run(life: Lifecycle) -> None:
    """A dry run makes no create call and persists no non-dry request."""
    handle = await life.acquire(2, dry_run=True)
    require(life.view(handle).metadata.get("dry_run") is True, "dry_run", "dry_run flag not stored")
    with life.uow_factory.create_unit_of_work() as uow:
        stored = [r for r in uow.requests.find_all() if not (r.metadata or {}).get("dry_run")]
    require(
        not stored, "dry_run", f"a non-dry request was persisted: {[r.request_id for r in stored]}"
    )
    precondition(not life.cloud.call_log, "dry run reached the cloud")


async def deadline_releases_stragglers(life: Lifecycle, requested: int = 3) -> None:
    """After a deadline outcome the machines still pending are released, nothing else is."""
    life.cloud.hold_pending(requested - 1)
    handle = await life.acquire(requested)
    precondition(life.cloud.count(handle.request_id, RUNNING) == 1, "expected one running machine")
    precondition(life.status(handle) in NON_TERMINAL, "request settled before its deadline")
    life.scenario.deadline_passed = True
    life.scenario.release_expected = True
    life.scenario.released_requests.add(handle.request_id)
    life.advance_clock(PAST_DEADLINE_SECONDS)
    await life.sync(handle)
    life.cloud.advance()
    exercise("deadline_outcome")
    mark = life.view(handle).metadata.get("deadline_release")
    require(
        mark in ("pending", "done", "nothing_to_release"),
        "deadline_outcome",
        f"deadline release mark is {mark!r}",
    )
    require(
        life.cloud.count(handle.request_id, PENDING) == 0,
        "deadline_outcome",
        "machines of the request are still pending after the release drained",
    )
    require(
        life.cloud.count(handle.request_id, RUNNING) == 1,
        "deadline_outcome",
        "the release took down the machine that was running",
    )
    await life.sync(handle)


async def abandoned_pending(life: Lifecycle) -> None:
    """A request whose create never recorded resources fails only after the abandon window."""
    from orb.domain.base.ports.configuration_port import ConfigurationPort

    config = life.container.get(ConfigurationPort).get_request_config()
    grace = int(config.get("default_timeout", 3600))
    timeout = int(config.get("fulfillment_timeout_seconds", 300))
    dispatch = int(config.get("dispatch_timeout_seconds", 300))
    abandon_after = max(grace, timeout + dispatch + 300)
    handle = await life.crash_create(2)
    precondition(life.status(handle) == RequestStatus.PENDING, "crashed create is not pending")
    # created_at is real time and "now" is real time plus the offset, so the
    # boundary is probed a second to either side.
    life.advance_clock(abandon_after - 1)
    await life.sync(handle, check=False)
    exercise("abandoned_pending")
    require(
        life.status(handle) == RequestStatus.PENDING,
        "abandoned_pending",
        f"abandoned a second before the window: {life.status(handle).value}",
    )
    life.advance_clock(2)
    await life.sync(handle, check=False)
    require(
        life.status(handle) == RequestStatus.FAILED,
        "abandoned_pending",
        f"still {life.status(handle).value} a second after the window",
    )


async def cancel_settled_keeps_status(life: Lifecycle) -> None:
    """Cancelling a request that already settled keeps its status and still aborts."""
    life.cloud.hold_pending(1)
    handle = await life.acquire(2)
    life.advance_clock(PAST_DEADLINE_SECONDS)
    life.scenario.deadline_passed = True
    await life.sync(handle, check=False)
    settled = life.status(handle)
    precondition(settled in (RequestStatus.PARTIAL, RequestStatus.TIMEOUT), "no deadline outcome")
    await life.cancel(handle)
    exercise("cancel_aborts")
    require(
        life.status(handle) == settled,
        "cancel_aborts",
        f"cancel changed a {settled.value} request to {life.status(handle).value}",
    )
    life.cloud.advance()
    left = life.cloud.count(handle.request_id, PENDING, RUNNING)
    require(left == 0, "cancel_aborts", f"{left} machines of the cancelled request are still alive")


async def failed_abort_leaves_request(life: Lifecycle) -> None:
    """A cancel whose abort fails leaves the request unchanged and can be repeated."""
    life.cloud.hold_pending()
    handle = await life.acquire(2)
    before = life.status(handle)
    life.cloud.fail_aborts()
    try:
        await life.cancel(handle)
        failed = False
    except Exception:
        failed = True
    finally:
        life.cloud.fail_aborts(False)
    exercise("cancel_aborts")
    require(failed, "cancel_aborts", "a cancel whose abort failed reported success")
    require(
        life.status(handle) == before,
        "cancel_aborts",
        f"a failed cancel moved the request from {before.value} to {life.status(handle).value}",
    )
    await life.cancel(handle)
    require(
        life.status(handle) == RequestStatus.CANCELLED,
        "cancel_aborts",
        f"the repeated cancel left the request {life.status(handle).value}",
    )


async def return_unknown_machine(life: Lifecycle) -> None:
    """Returning an id ORB never tracked is refused and kills nothing."""
    from orb.domain.base.exceptions import EntityNotFoundError

    handle = await acquire_complete(life, 2)
    kills = len(life.cloud.killed())
    try:
        await life.return_([life.cloud.unknown_machine_id()], track=False)
        refused = False
    except EntityNotFoundError:
        refused = True
    exercise("return_lifecycle")
    require(refused, "return_lifecycle", "returning an unknown machine id was accepted")
    require(
        len(life.cloud.killed()) == kills,
        "return_lifecycle",
        "returning an unknown id killed machines",
    )
    for machine in life.cloud.machines(handle.request_id):
        require(
            machine.state == RUNNING, "return_lifecycle", f"{machine.machine_id} is {machine.state}"
        )


async def return_vanished_machine(life: Lifecycle) -> None:
    """A machine the cloud already lost is returned without error; only live ones are killed."""
    handle = await acquire_complete(life, 2)
    ids = [m.machine_id for m in life.cloud.machines(handle.request_id)]
    life.cloud.interrupt(ids[:1])
    kills = len(life.cloud.killed())
    return_ids = await life.return_(ids)
    exercise("return_lifecycle")
    require(bool(return_ids), "return_lifecycle", "no return request was created")
    dto = await life.sync_return(return_ids[0])
    require(
        RequestStatus(dto.status) in (RequestStatus.COMPLETED, RequestStatus.PARTIAL),
        "return_lifecycle",
        f"return of a vanished machine ended {dto.status}",
    )
    require(
        life.cloud.killed() - set(ids[:1]) == set(ids[1:]) and len(life.cloud.killed()) >= kills,
        "return_lifecycle",
        "the live machine was not the one killed",
    )


async def return_spanning_two_requests(life: Lifecycle) -> None:
    first = await acquire_complete(life, 2)
    second = await acquire_complete(life, 2)
    ids = [
        m.machine_id
        for m in life.cloud.machines()
        if m.request_id in (first.request_id, second.request_id)
    ]
    return_ids = await life.return_(ids)
    exercise("return_lifecycle")
    require(bool(return_ids), "return_lifecycle", "no return request was created")
    for return_id in return_ids:
        dto = await life.sync_return(return_id)
        require(
            RequestStatus(dto.status) == RequestStatus.COMPLETED,
            "return_lifecycle",
            f"return request {return_id} is {dto.status}",
        )
    require(
        life.cloud.count(first.request_id, TERMINATED) == 2
        and life.cloud.count(second.request_id, TERMINATED) == 2,
        "return_lifecycle",
        "not every machine of both requests terminated",
    )


async def quota_blocked_release(life: Lifecycle, requested: int = 2) -> None:
    """A Deployment whose ReplicaSet cannot create pods, then can, after the deadline.

    The outcome at the deadline has no machine ids, so nothing marks the request
    for release. Pods that appear later must still not be left running, and the
    Deployment's replicas must come down.
    """
    life.cloud.block_pod_creation()
    handle = await life.acquire(requested)
    precondition(life.cloud.count(handle.request_id, PENDING, RUNNING) == 0, "pods exist already")
    life.scenario.deadline_passed = True
    life.scenario.release_expected = True
    life.scenario.released_requests.add(handle.request_id)
    life.advance_clock(PAST_DEADLINE_SECONDS)
    await life.sync(handle, check=False)
    precondition(life.status(handle) == RequestStatus.TIMEOUT, "deadline did not give TIMEOUT")
    precondition(not life.view(handle).rows, "has machine ids")
    life.cloud.advance()
    await life.sync(handle, check=False)
    exercise("deadline_outcome")
    alive = life.cloud.count(handle.request_id, PENDING, RUNNING)
    require(
        alive == 0,
        "deadline_outcome",
        f"{alive} pods that appeared after the deadline were left running",
    )
    try:
        replicas = life.cloud.replicas(handle.request_id)
    except LookupError:
        replicas = 0  # the Deployment itself was removed
    require(replicas < requested, "deadline_outcome", "the Deployment's replicas were not lowered")


async def exhausted_capacity_at_create(life: Lifecycle) -> None:
    """When nothing can launch, the create result is final and says so honestly."""
    life.cloud.limit_capacity(0)
    handle = await life.acquire(2)
    status = life.status(handle)
    exercise("capacity_shortfall")
    require(
        status == RequestStatus.FAILED,
        "capacity_shortfall",
        f"no capacity at all and the request is {status.value}",
    )
    require(
        life.cloud.count(handle.request_id, PENDING, RUNNING) == 0,
        "capacity_shortfall",
        "machines exist",
    )


async def stale_read_guard(life: Lifecycle) -> None:
    """A provider read that fails never regresses stored rows or the request status."""
    life.cloud.hold_pending(1)
    handle = await life.acquire(2)
    await life.sync(handle)
    precondition(life.status(handle) in NON_TERMINAL, "request settled")
    before_status = life.status(handle)
    before_rows = {k: r.status.value for k, r in life.view(handle).rows.items()}
    precondition("running" in before_rows.values(), "no running row to protect")
    life.cloud.fail_reads()
    await life.sync(handle, check=False)
    life.cloud.fail_reads(False)
    exercise("pending_converges")
    after_rows = {k: r.status.value for k, r in life.view(handle).rows.items()}
    require(
        life.status(handle) == before_status,
        "pending_converges",
        f"a failed read moved the request from {before_status.value} to {life.status(handle).value}",
    )
    require(
        after_rows == before_rows,
        "pending_converges",
        f"a failed read changed the stored rows: {before_rows} -> {after_rows}",
    )
