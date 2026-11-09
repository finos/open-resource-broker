"""Release the machines a deadline outcome left that may never have started.

When a request reaches the deadline as PARTIAL or TIMEOUT, the state machine marks
it (``metadata["deadline_release"] == "pending"``) in the same save as the status.
This service acts on that mark: it reads the provider afresh, and returns the
machines of the request that are still pending or launching through the normal
return path, which also lowers the capacity of the fleet or group so nothing is
launched in their place.

The release is finished only when a fresh provider read shows nothing left to
release. A return that was created but never ran, a failed return, an unreachable
provider or a lost write all leave the mark in place, so the next run tries again.

Releases run as background tasks: they await provider termination, which must not
add to the latency of a poll or be cancelled with the caller. Known limit: a
request that reached the deadline with only a resource id and no machine ids is
not marked, so what its resource launches afterwards is not released here.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from orb.application.dto.commands import CreateReturnRequestCommand
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.services.machine_sync_service import (
    PROVIDER_FETCH_FAILED,
    FailedProviderFetch,
    MachineSyncService,
)
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.exceptions import ConcurrencyError
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.machine.machine_status import MachineStatus
from orb.domain.request.aggregate import Request
from orb.domain.request.deadline_release import (
    DEADLINE_RELEASE_KEY,
    RELEASE_DONE,
    RELEASE_NOTHING,
    RELEASE_PENDING,
)
from orb.domain.request.exceptions import RequestValidationError
from orb.domain.request.request_types import RequestStatus, RequestType
from orb.domain.request.value_objects import RequestId

_UNSTARTED = (MachineStatus.PENDING, MachineStatus.LAUNCHING)
_RETURN_ID = re.compile(r"pending return request:\s*(\S+)")

_STAMP_WRITE_ATTEMPTS = 3
_STALE_AFTER_SECONDS = 24 * 3600
_STUCK_RETURN_AFTER_SECONDS = 3600
_WARN_EVERY_SECONDS = 24 * 3600


_TERMINAL_MACHINE = (MachineStatus.TERMINATED, MachineStatus.RETURNED, MachineStatus.FAILED)
_MAX_TRACKED = 1024


class DeadlineReleaseService:
    """Releases never-started machines of requests that reached their deadline."""

    _active: Optional["DeadlineReleaseService"] = None

    @classmethod
    def active(cls) -> Optional["DeadlineReleaseService"]:
        """The instance created in this process, None when none was built."""
        return cls._active

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        machine_sync_service: MachineSyncService,
        command_bus: CommandBusPort,
        logger: LoggingPort,
        *,
        max_concurrency: int = 2,
        throttle_seconds: float = 60.0,
        per_pass_cap: int = 5,
        scan_interval_seconds: float = 300.0,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._uow_factory = uow_factory
        self._machine_sync_service = machine_sync_service
        self._command_bus = command_bus
        self._logger = logger
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self._throttle_seconds = throttle_seconds
        self._per_pass_cap = max(1, per_pass_cap)
        self._scan_interval_seconds = scan_interval_seconds
        self._clock = clock
        self._monotonic = monotonic
        # Strong references: a task nobody references can be collected mid-run.
        self._tasks: set["asyncio.Future[None]"] = set()
        self._in_flight: set[str] = set()
        self._last_enqueued: dict[str, float] = {}
        self._last_warned: dict[tuple[str, str], float] = {}
        self._last_scan: Optional[float] = None
        DeadlineReleaseService._active = self

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    @staticmethod
    def is_owed(request: Request) -> bool:
        """True when ``request`` is a finished acquire that still owes a release."""
        return (
            request.request_type == RequestType.ACQUIRE
            and request.status in (RequestStatus.PARTIAL, RequestStatus.TIMEOUT)
            and request.metadata.get(DEADLINE_RELEASE_KEY) == RELEASE_PENDING
        )

    def enqueue(self, request_id: str) -> bool:
        """Start a background release for ``request_id``; never blocks or raises.

        Returns False when one is already running, one was started within the
        throttle window, or there is no running event loop to run it on.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._logger.debug("No event loop to release request %s on", request_id)
            return False
        now = self._monotonic()
        last = self._last_enqueued.get(request_id)
        if request_id in self._in_flight or (
            last is not None and now - last < self._throttle_seconds
        ):
            return False
        self._prune(now)
        self._last_enqueued[request_id] = now
        self._in_flight.add(request_id)
        task = loop.create_task(self._run(request_id), name=f"orb-deadline-release-{request_id}")
        self._track(task)
        return True

    def _prune(self, now: float) -> None:
        """Keep the per-request bookkeeping bounded."""
        if len(self._last_enqueued) > _MAX_TRACKED:
            self._last_enqueued = {
                k: t for k, t in self._last_enqueued.items() if now - t < self._throttle_seconds
            }
        if len(self._last_warned) > _MAX_TRACKED:
            self._last_warned = {
                k: t for k, t in self._last_warned.items() if now - t < _WARN_EVERY_SECONDS
            }

    def enqueue_if_owed(self, request: Request) -> bool:
        """Enqueue a release when ``request`` owes one."""
        return self.is_owed(request) and self.enqueue(str(request.request_id))

    def enqueue_many(self, request_ids: list[str]) -> int:
        """Enqueue releases for up to the per-pass cap of ``request_ids``."""
        started = 0
        for request_id in request_ids:
            if started >= self._per_pass_cap:
                break
            if self.enqueue(request_id):
                started += 1
        return started

    async def scan_and_enqueue(self, *, respect_interval: bool = False) -> int:
        """Enqueue releases for requests that still owe one; returns how many started.

        With ``respect_interval`` a scan that ran within the scan interval is skipped.
        """
        now = self._monotonic()
        if (
            respect_interval
            and self._last_scan is not None
            and now - self._last_scan < self._scan_interval_seconds
        ):
            return 0
        self._last_scan = now
        owed = await asyncio.to_thread(self._owed_request_ids)
        return self.enqueue_many(owed)

    def _owed_request_ids(self) -> list[str]:
        # A full read of the PARTIAL and TIMEOUT requests: O(those rows) per scan,
        # at most every scan interval. Add a store-level index on the mark if the
        # number of such rows grows large.
        owed: list[str] = []
        with self._uow_factory.create_unit_of_work() as uow:
            for status in (RequestStatus.PARTIAL, RequestStatus.TIMEOUT):
                owed.extend(
                    str(r.request_id)
                    for r in uow.requests.find_by_status(status)
                    if self.is_owed(r)
                )
        return owed

    async def drain(self, timeout: float) -> None:
        """Wait up to ``timeout`` seconds for running releases (one-shot processes)."""
        pending = [t for t in self._tasks if not t.done()]
        if pending:
            await asyncio.wait(pending, timeout=timeout)

    def _track(self, task: "asyncio.Future[None]") -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, request_id: str) -> None:
        # The release is shielded: cancelling this wrapper (for example at
        # shutdown) leaves it running, and the in-flight mark and the concurrency
        # slot are held by the release itself until it ends.
        inner = asyncio.ensure_future(self._guarded(request_id))
        self._track(inner)
        await asyncio.shield(inner)

    async def _guarded(self, request_id: str) -> None:
        try:
            async with self._semaphore:
                await self._release_logged(request_id)
        finally:
            self._in_flight.discard(request_id)

    async def _release_logged(self, request_id: str) -> None:
        try:
            await self.release(request_id)
        except Exception as exc:
            self._logger.warning("Release for request %s failed: %s", request_id, exc)

    # ------------------------------------------------------------------
    # The release
    # ------------------------------------------------------------------

    async def release(self, request_id: str) -> str:
        """Release what is left of one request; returns the resulting mark.

        Returns ``"skipped"`` when the request does not owe a release. The mark
        stays ``"pending"`` unless a fresh provider read shows nothing left.
        """
        request, stored_machines = await asyncio.to_thread(self._load, request_id)
        if request is None or not self.is_owed(request):
            return "skipped"
        self._warn_if_stale(request)

        provider_machines, metadata = await self._machine_sync_service.fetch_provider_machines(
            request, stored_machines
        )
        if isinstance(provider_machines, FailedProviderFetch) or metadata.get(
            PROVIDER_FETCH_FAILED
        ):
            self._logger.warning(
                "Provider unreachable; keeping the release of request %s pending", request_id
            )
            return RELEASE_PENDING

        ours = set(request.machine_ids)
        mine = [m for m in provider_machines if m.machine_id.value in ours]
        if not mine and not (
            stored_machines and all(m.status in _TERMINAL_MACHINE for m in stored_machines)
        ):
            # A read that returned none of the request's machines may be a lookup
            # that has not found them yet; it is not evidence that nothing is left.
            self._logger.debug(
                "Provider read returned none of request %s's machines; keeping the release pending",
                request_id,
            )
            return RELEASE_PENDING
        if any(m.status == MachineStatus.UNKNOWN for m in mine):
            self._logger.debug(
                "Provider read of request %s has machines without a known status; "
                "keeping the release pending",
                request_id,
            )
            return RELEASE_PENDING
        leftover = [m.machine_id.value for m in mine if m.status in _UNSTARTED]
        if not leftover:
            mark = (
                RELEASE_DONE
                if any(m.return_request_id for m in stored_machines)
                else RELEASE_NOTHING
            )
            await asyncio.to_thread(self._write_mark, request_id, mark)
            return mark

        await self._issue_return(request, leftover)
        return RELEASE_PENDING

    async def _issue_return(self, request: Request, leftover: list[str]) -> None:
        request_id = str(request.request_id)
        command = CreateReturnRequestCommand(machine_ids=leftover, force_return=False)
        try:
            await self._command_bus.execute(command)
        except RequestValidationError as exc:
            self._note_skip(request, str(exc))
            return
        except Exception as exc:
            self._logger.warning("Return for request %s failed, will retry: %s", request_id, exc)
            return
        for skipped in command.skipped_machines or []:
            self._note_skip(request, str(skipped.get("reason", "")))
        if command.unrecorded_failure_request_ids:
            self._logger.warning(
                "Return %s for request %s failed; will retry",
                ", ".join(command.unrecorded_failure_request_ids),
                request_id,
            )

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    def _load(self, request_id: str):  # type: ignore[no-untyped-def]
        with self._uow_factory.create_unit_of_work() as uow:
            request = uow.requests.get_by_id(RequestId(value=request_id))
            machines = (
                list(uow.machines.find_by_request_id(request_id)) if request is not None else []
            )
        return request, machines

    def _write_mark(self, request_id: str, mark: str) -> bool:
        """Record the outcome on a fresh read; False when the mark is not pending any more."""
        for attempt in range(_STAMP_WRITE_ATTEMPTS):
            try:
                with self._uow_factory.create_unit_of_work() as uow:
                    current = uow.requests.get_by_id(RequestId(value=request_id))
                    if current is None or not self.is_owed(current):
                        return False
                    uow.requests.save(current.update_metadata({DEADLINE_RELEASE_KEY: mark}))
                return True
            except ConcurrencyError:
                if attempt + 1 >= _STAMP_WRITE_ATTEMPTS:
                    self._logger.warning(
                        "Could not record the release of request %s: concurrent writes", request_id
                    )
                    return False
        return False

    # ------------------------------------------------------------------
    # Operator warnings
    # ------------------------------------------------------------------

    def _pending_seconds(self, request: Request) -> float:
        since = request.completed_at or request.last_transition_at
        if since is None:
            return 0.0
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        return (self._clock() - since).total_seconds()

    def _warn_once_per_day(self, request: Request, kind: str, message: str, *args: object) -> None:
        key = (str(request.request_id), kind)
        now = self._monotonic()
        last = self._last_warned.get(key)
        if last is not None and now - last < _WARN_EVERY_SECONDS:
            return
        self._last_warned[key] = now
        self._logger.warning(message, *args)

    def _warn_if_stale(self, request: Request) -> None:
        if self._pending_seconds(request) > _STALE_AFTER_SECONDS:
            self._warn_once_per_day(
                request,
                "stale",
                "Release of request %s has been pending for over 24 hours; still retrying",
                request.request_id,
            )

    def _note_skip(self, request: Request, reason: str) -> None:
        """Log why machines were not returned; name a stuck return once it is old."""
        self._logger.info("Release of request %s skipped machines: %s", request.request_id, reason)
        if self._pending_seconds(request) <= _STUCK_RETURN_AFTER_SECONDS:
            return
        match = _RETURN_ID.search(reason)
        if match:
            self._warn_once_per_day(
                request,
                "stuck-return",
                "Request %s still has machines held by return request %s; "
                "if that return is stuck, run 'orb machines return --force'",
                request.request_id,
                match.group(1),
            )
