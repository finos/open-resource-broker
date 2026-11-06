"""Release of machines a deadline outcome left that may never have started."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from orb.application.services.deadline_release_service import DeadlineReleaseService
from orb.application.services.machine_sync_service import (
    PROVIDER_FETCH_FAILED,
    FailedProviderFetch,
)
from orb.domain.base.exceptions import ConcurrencyError
from orb.domain.machine.machine_status import MachineStatus
from orb.domain.request.aggregate import Request
from orb.domain.request.exceptions import RequestValidationError
from orb.domain.request.request_types import RequestStatus
from orb.domain.request.value_objects import RequestType

STAMP = "deadline_release"
NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _request(
    *,
    status: RequestStatus = RequestStatus.PARTIAL,
    stamp: Optional[str] = "pending",
    machine_ids: Optional[list[str]] = None,
    completed_ago: float = 60,
) -> Request:
    request = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="tpl",
        machine_count=3,
        provider_type="aws",
        provider_name="aws-default",
    )
    metadata = dict(request.metadata)
    if stamp is not None:
        metadata[STAMP] = stamp
    return request.model_copy(
        update={
            "status": status,
            "resource_ids": ["fleet-1"],
            "machine_ids": machine_ids if machine_ids is not None else ["i-1", "i-2", "i-3"],
            "metadata": metadata,
            "completed_at": NOW - timedelta(seconds=completed_ago),
        }
    )


def _machine(mid: str, status: MachineStatus, return_request_id: Optional[str] = None) -> Any:
    return SimpleNamespace(
        machine_id=SimpleNamespace(value=mid), status=status, return_request_id=return_request_id
    )


class _Requests:
    def __init__(self, rows: list[Request]) -> None:
        self.rows = {str(r.request_id): r for r in rows}
        self.saves: list[Request] = []
        self.fail_saves = 0

    def get_by_id(self, request_id: Any) -> Optional[Request]:
        return self.rows.get(str(request_id))

    def find_by_status(self, status: RequestStatus) -> list[Request]:
        return [r for r in self.rows.values() if r.status == status]

    def save(self, request: Request) -> list[Any]:
        if self.fail_saves:
            self.fail_saves -= 1
            raise ConcurrencyError("lost")
        self.saves.append(request)
        self.rows[str(request.request_id)] = request
        return []


class _Harness:
    def __init__(self, request: Request, *, stored_machines: Optional[list[Any]] = None) -> None:
        self.requests = _Requests([request])
        self.request_id = str(request.request_id)
        uow = MagicMock()
        uow.requests = self.requests
        uow.machines.find_by_request_id.return_value = stored_machines or []
        uow.__enter__ = MagicMock(return_value=uow)
        uow.__exit__ = MagicMock(return_value=False)
        factory = MagicMock()
        factory.create_unit_of_work.return_value = uow
        self.provider_machines: Any = []
        self.provider_metadata: dict[str, Any] = {}
        self.fetches = 0
        sync = MagicMock()
        sync.fetch_provider_machines = self._fetch
        self.commands: list[Any] = []
        self.on_command: Any = None
        bus = MagicMock()
        bus.execute = self._execute
        self.logger = MagicMock()
        self.now = NOW
        self.clock_ticks = 0.0
        self.service = DeadlineReleaseService(
            factory,
            sync,
            bus,
            self.logger,
            clock=lambda: self.now,
            monotonic=lambda: self.clock_ticks,
        )

    async def _fetch(self, request: Request, db_machines: list[Any]) -> Any:
        self.fetches += 1
        return self.provider_machines, self.provider_metadata

    async def _execute(self, command: Any) -> None:
        self.commands.append(command)
        if self.on_command is not None:
            await self.on_command(command)

    @property
    def stored(self) -> Request:
        return self.requests.rows[self.request_id]


@pytest.mark.asyncio
async def test_unstarted_machines_are_returned_and_the_mark_stays_pending():
    h = _Harness(_request())
    h.provider_machines = [
        _machine("i-1", MachineStatus.RUNNING),
        _machine("i-2", MachineStatus.PENDING),
        _machine("i-3", MachineStatus.LAUNCHING),
    ]

    result = await h.service.release(h.request_id)

    assert result == "pending"
    assert [c.machine_ids for c in h.commands] == [["i-2", "i-3"]]
    assert h.commands[0].force_return is False
    assert h.stored.metadata[STAMP] == "pending"


@pytest.mark.asyncio
async def test_machine_that_started_between_the_stored_read_and_the_release_is_not_returned():
    h = _Harness(_request())
    # Stored state says pending; the fresh provider read says running.
    h.provider_machines = [_machine(m, MachineStatus.RUNNING) for m in ("i-1", "i-2", "i-3")]

    result = await h.service.release(h.request_id)

    assert result == "nothing_to_release"
    assert h.commands == []
    assert h.stored.metadata[STAMP] == "nothing_to_release"


@pytest.mark.asyncio
async def test_a_second_run_after_the_machines_are_gone_marks_done_and_issues_nothing():
    h = _Harness(
        _request(),
        stored_machines=[_machine("i-2", MachineStatus.PENDING, return_request_id="ret-1")],
    )
    h.provider_machines = [
        _machine("i-1", MachineStatus.RUNNING),
        _machine("i-2", MachineStatus.TERMINATED),
    ]

    assert await h.service.release(h.request_id) == "done"
    assert await h.service.release(h.request_id) == "skipped"
    assert h.commands == []


@pytest.mark.asyncio
async def test_losing_the_mark_write_to_a_concurrent_writer_issues_no_command():
    h = _Harness(_request())
    h.provider_machines = [_machine("i-1", MachineStatus.RUNNING)]
    h.requests.fail_saves = 3

    await h.service.release(h.request_id)

    assert h.commands == []
    assert h.stored.metadata[STAMP] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["raises", "unrecorded", "skipped-not-found"])
async def test_a_return_that_did_not_happen_leaves_the_mark_pending_and_is_retried(failure):
    h = _Harness(_request())
    h.provider_machines = [_machine("i-2", MachineStatus.PENDING)]

    async def fail(command: Any) -> None:
        if failure == "raises":
            raise RuntimeError("boom")
        if failure == "unrecorded":
            command.unrecorded_failure_request_ids = ["ret-9"]
        else:
            command.skipped_machines = [{"machine_id": "i-2", "reason": "Machine not found"}]

    h.on_command = fail

    assert await h.service.release(h.request_id) == "pending"
    assert await h.service.release(h.request_id) == "pending"

    assert len(h.commands) == 2
    assert h.stored.metadata[STAMP] == "pending"


@pytest.mark.asyncio
async def test_an_unreachable_provider_releases_nothing_and_keeps_the_mark():
    h = _Harness(_request())
    h.provider_machines = FailedProviderFetch([_machine("i-2", MachineStatus.PENDING)])
    h.provider_metadata = {PROVIDER_FETCH_FAILED: True}

    assert await h.service.release(h.request_id) == "pending"

    assert h.commands == []
    assert h.stored.metadata[STAMP] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_",
    [
        _request(stamp=None),
        _request(stamp="done"),
        _request(status=RequestStatus.COMPLETED),
        _request(status=RequestStatus.IN_PROGRESS),
    ],
)
async def test_a_request_that_does_not_owe_a_release_is_never_released(request_):
    h = _Harness(request_)
    h.provider_machines = [_machine("i-2", MachineStatus.PENDING)]

    assert await h.service.release(h.request_id) == "skipped"
    assert h.commands == []
    assert h.fetches == 0


@pytest.mark.asyncio
async def test_a_stale_mark_is_still_released_and_warned_about_once_a_day():
    h = _Harness(_request(completed_ago=3 * 24 * 3600))
    h.provider_machines = [_machine("i-2", MachineStatus.PENDING)]

    await h.service.release(h.request_id)
    await h.service.release(h.request_id)

    assert len(h.commands) == 2
    stale = [c for c in h.logger.warning.call_args_list if "24 hours" in c.args[0]]
    assert len(stale) == 1
    h.clock_ticks += 25 * 3600
    await h.service.release(h.request_id)
    stale = [c for c in h.logger.warning.call_args_list if "24 hours" in c.args[0]]
    assert len(stale) == 2


@pytest.mark.asyncio
async def test_replacement_machines_outside_the_request_are_never_released():
    h = _Harness(_request(machine_ids=["i-1"]))
    h.provider_machines = [
        _machine("i-1", MachineStatus.RUNNING),
        _machine("i-replacement", MachineStatus.PENDING),
    ]

    assert await h.service.release(h.request_id) == "nothing_to_release"
    assert h.commands == []


@pytest.mark.asyncio
async def test_a_rejected_return_is_handled_and_keeps_the_mark():
    h = _Harness(_request())
    h.provider_machines = [_machine("i-2", MachineStatus.PENDING)]

    async def reject(command: Any) -> None:
        raise RequestValidationError("Machine i-2 already has pending return request: ret-5")

    h.on_command = reject

    assert await h.service.release(h.request_id) == "pending"
    assert h.stored.metadata[STAMP] == "pending"


@pytest.mark.asyncio
async def test_a_return_that_never_ran_is_not_marked_done_and_is_named_after_an_hour():
    h = _Harness(
        _request(completed_ago=2 * 3600),
        stored_machines=[_machine("i-2", MachineStatus.PENDING, return_request_id="ret-5")],
    )
    h.provider_machines = [_machine("i-2", MachineStatus.PENDING)]

    async def already_flagged(command: Any) -> None:
        command.skipped_machines = [
            {
                "machine_id": "i-2",
                "reason": "Machine already has pending return request: ret-5",
            }
        ]

    h.on_command = already_flagged

    assert await h.service.release(h.request_id) == "pending"

    assert h.stored.metadata[STAMP] == "pending"
    named = [c for c in h.logger.warning.call_args_list if "ret-5" in str(c.args)]
    assert len(named) == 1
    await h.service.release(h.request_id)
    named = [c for c in h.logger.warning.call_args_list if "ret-5" in str(c.args)]
    assert len(named) == 1


@pytest.mark.asyncio
async def test_cancelling_the_caller_does_not_cancel_the_release():
    h = _Harness(_request())
    h.provider_machines = [_machine("i-2", MachineStatus.PENDING)]
    started, finish = asyncio.Event(), asyncio.Event()
    completed: list[bool] = []

    async def slow(command: Any) -> None:
        started.set()
        await finish.wait()
        completed.append(True)

    h.on_command = slow

    async def caller() -> None:
        h.service.enqueue(h.request_id)
        await asyncio.sleep(3600)

    caller_task = asyncio.ensure_future(caller())
    await asyncio.wait_for(started.wait(), 2)
    caller_task.cancel()
    (outcome,) = await asyncio.gather(caller_task, return_exceptions=True)
    assert isinstance(outcome, asyncio.CancelledError)
    finish.set()
    await h.service.drain(2)

    assert completed == [True]


@pytest.mark.asyncio
async def test_enqueue_returns_at_once_whatever_the_return_takes():
    h = _Harness(_request())
    h.provider_machines = [_machine("i-2", MachineStatus.PENDING)]
    finish = asyncio.Event()

    async def slow(command: Any) -> None:
        await finish.wait()

    h.on_command = slow

    assert await asyncio.wait_for(_enqueue(h), 0.5) is True
    finish.set()
    await h.service.drain(2)


async def _enqueue(h: _Harness) -> bool:
    return h.service.enqueue(h.request_id)


@pytest.mark.asyncio
async def test_a_wait_for_timeout_around_the_caller_does_not_cancel_the_release():
    h = _Harness(_request())
    h.provider_machines = [_machine("i-2", MachineStatus.PENDING)]
    started, finish = asyncio.Event(), asyncio.Event()
    completed: list[bool] = []

    async def slow(command: Any) -> None:
        started.set()
        await finish.wait()
        completed.append(True)

    h.on_command = slow

    async def reconcile_like() -> None:
        h.service.enqueue(h.request_id)
        await asyncio.sleep(3600)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(reconcile_like(), timeout=0.05)
    await asyncio.wait_for(started.wait(), 2)
    finish.set()
    await h.service.drain(2)

    assert completed == [True]


@pytest.mark.asyncio
async def test_a_list_of_owed_requests_starts_at_most_the_cap():
    rows = [_request() for _ in range(20)]
    h = _Harness(rows[0])
    for row in rows[1:]:
        h.requests.rows[str(row.request_id)] = row
    h.provider_machines = [_machine("i-2", MachineStatus.PENDING)]
    finish = asyncio.Event()

    async def slow(command: Any) -> None:
        await finish.wait()

    h.on_command = slow

    started = h.service.enqueue_many(list(h.requests.rows))
    finish.set()
    await h.service.drain(2)

    assert started == 5


@pytest.mark.asyncio
async def test_the_same_request_is_not_enqueued_again_within_the_throttle_window():
    h = _Harness(_request())
    h.provider_machines = [_machine("i-2", MachineStatus.PENDING)]

    assert h.service.enqueue(h.request_id) is True
    await h.service.drain(2)
    assert h.service.enqueue(h.request_id) is False
    h.clock_ticks += 61
    assert h.service.enqueue(h.request_id) is True
    await h.service.drain(2)


@pytest.mark.asyncio
async def test_at_most_two_releases_run_at_once():
    rows = [_request() for _ in range(4)]
    h = _Harness(rows[0])
    for row in rows[1:]:
        h.requests.rows[str(row.request_id)] = row
    h.provider_machines = [_machine("i-2", MachineStatus.PENDING)]
    running = peak = 0
    finish = asyncio.Event()

    async def slow(command: Any) -> None:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await finish.wait()
        running -= 1

    h.on_command = slow
    for row in rows:
        h.service.enqueue(str(row.request_id))
    await asyncio.sleep(0.05)
    finish.set()
    await h.service.drain(2)

    assert peak == 2


@pytest.mark.asyncio
async def test_a_scan_enqueues_only_marked_partial_and_timeout_requests():
    marked = _request(status=RequestStatus.TIMEOUT)
    h = _Harness(marked)
    for other in (
        _request(stamp=None),
        _request(stamp="done"),
        _request(status=RequestStatus.COMPLETED),
    ):
        h.requests.rows[str(other.request_id)] = other
    h.provider_machines = [_machine("i-2", MachineStatus.PENDING)]

    assert await h.service.scan_and_enqueue() == 1
    await h.service.drain(2)

    assert len(h.commands) == 1


@pytest.mark.asyncio
async def test_scans_are_spaced_by_the_scan_interval_when_asked():
    h = _Harness(_request())
    h.provider_machines = [_machine("i-2", MachineStatus.PENDING)]

    assert await h.service.scan_and_enqueue(respect_interval=True) == 1
    await h.service.drain(2)
    h.clock_ticks += 120
    assert await h.service.scan_and_enqueue(respect_interval=True) == 0
    h.clock_ticks += 400
    assert await h.service.scan_and_enqueue(respect_interval=True) == 1
    await h.service.drain(2)


@pytest.mark.asyncio
async def test_an_empty_read_is_unknown_and_leaves_the_mark_pending():
    h = _Harness(_request())
    h.provider_machines = []

    assert await h.service.release(h.request_id) == "pending"

    assert h.commands == []
    assert h.stored.metadata[STAMP] == "pending"


@pytest.mark.asyncio
async def test_an_empty_read_without_resource_ids_or_stored_machines_leaves_the_mark_pending():
    h = _Harness(_request().model_copy(update={"resource_ids": []}))
    h.provider_machines = []

    assert await h.service.release(h.request_id) == "pending"
    assert h.stored.metadata[STAMP] == "pending"


@pytest.mark.asyncio
async def test_a_read_with_only_replacement_machines_leaves_the_mark_pending():
    h = _Harness(_request())
    h.provider_machines = [_machine("i-replacement", MachineStatus.RUNNING)]

    assert await h.service.release(h.request_id) == "pending"
    assert h.stored.metadata[STAMP] == "pending"


@pytest.mark.asyncio
async def test_an_empty_read_is_accepted_when_every_stored_machine_is_already_terminal():
    h = _Harness(
        _request(),
        stored_machines=[
            _machine("i-1", MachineStatus.TERMINATED),
            _machine("i-2", MachineStatus.TERMINATED),
        ],
    )
    h.provider_machines = []

    assert await h.service.release(h.request_id) == "nothing_to_release"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [MachineStatus.RUNNING, MachineStatus.TERMINATED])
async def test_a_read_with_one_of_our_machines_in_any_state_still_finalises(status):
    h = _Harness(_request())
    h.provider_machines = [_machine("i-1", status)]

    assert await h.service.release(h.request_id) == "nothing_to_release"


@pytest.mark.asyncio
async def test_a_machine_with_an_unknown_status_is_not_returned():
    h = _Harness(_request())
    h.provider_machines = [_machine("i-1", MachineStatus.UNKNOWN)]

    await h.service.release(h.request_id)

    assert h.commands == []


@pytest.mark.asyncio
async def test_cancelling_the_wrapper_keeps_the_slot_and_in_flight_mark_until_the_release_ends():
    h = _Harness(_request())
    h.provider_machines = [_machine("i-2", MachineStatus.PENDING)]
    started, finish = asyncio.Event(), asyncio.Event()

    async def slow(command: Any) -> None:
        started.set()
        await finish.wait()

    h.on_command = slow
    h.service.enqueue(h.request_id)
    await asyncio.wait_for(started.wait(), 2)
    wrapper = next(t for t in asyncio.all_tasks() if t.get_name().startswith("orb-deadline"))
    wrapper.cancel()
    await asyncio.gather(wrapper, return_exceptions=True)

    assert h.request_id in h.service._in_flight
    assert h.service.enqueue(h.request_id) is False
    finish.set()
    await h.service.drain(2)
    assert h.request_id not in h.service._in_flight


@pytest.mark.asyncio
async def test_per_request_bookkeeping_stays_bounded():
    h = _Harness(_request())
    for n in range(3000):
        h.service._last_enqueued[f"old-{n}"] = -1000.0
        h.service._last_warned[(f"old-{n}", "stale")] = -100000.0
    h.clock_ticks = 100000.0
    h.provider_machines = [_machine("i-2", MachineStatus.PENDING)]

    h.service.enqueue(h.request_id)
    await h.service.drain(2)

    assert len(h.service._last_enqueued) < 10
