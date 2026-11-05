"""Reconciliation and sync end to end over real JSON and SQLite storage.

Real repositories, ``MachineSyncService``, ``RequestStatusService`` and
``FulfilmentStateMachine``; only the provider is faked. The fake provider can
run a callback in the middle of its call, which is where a cancel (or any other
write) lands in real use, because the request was loaded before the call.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.commands.request_sync_handlers import PopulateMachineIdsHandler
from orb.application.dto.queries import (
    SyncAndGetRequestQuery,
    SyncAndListActiveRequestsQuery,
)
from orb.application.queries.request_query_handlers import (
    SyncAndGetRequestHandler,
    SyncAndListActiveRequestsHandler,
)
from orb.application.services.machine_sync_service import MachineSyncService
from orb.application.services.request_reconciler import RequestReconciler
from orb.application.services.request_status_service import RequestStatusService
from orb.domain.base.exceptions import ConcurrencyError
from orb.domain.base.provider_fulfilment import ProviderFulfilment
from orb.domain.base.value_objects import InstanceType
from orb.domain.machine.aggregate import Machine
from orb.domain.machine.machine_identifiers import MachineId
from orb.domain.machine.machine_status import MachineStatus
from orb.domain.request.aggregate import Request
from orb.domain.request.fulfilment_state_machine import FulfilmentStateMachine
from orb.domain.request.request_identifiers import RequestId
from orb.domain.request.request_types import RequestStatus
from orb.domain.request.value_objects import RequestType
from orb.providers.base.strategy.provider_strategy import ProviderResult

_ACQUIRE_ID = "req-00000000-0000-0000-0000-0000000000a1"
_RETURN_ID = "ret-00000000-0000-0000-0000-0000000000b1"


class _Uow:
    def __init__(self, requests, machines) -> None:
        self.requests = requests
        self.machines = machines
        self.other_requests: Callable[[], Any] = lambda: requests
        self.kind = "json"

    def __enter__(self) -> "_Uow":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


class _Factory:
    def __init__(self, uow: _Uow) -> None:
        self._uow = uow

    def create_unit_of_work(self) -> _Uow:
        return self._uow


@pytest.fixture(params=["json", "sql"])
def uow(request, tmp_path) -> _Uow:
    from orb.infrastructure.storage.json.strategy import JSONStorageStrategy
    from orb.infrastructure.storage.repositories.machine_repository import MachineRepositoryImpl
    from orb.infrastructure.storage.repositories.request_repository import RequestRepositoryImpl
    from orb.infrastructure.storage.sql.models import MachineModel, RequestModel
    from orb.infrastructure.storage.sql.strategy import SQLStorageStrategy

    def sql(model, table):
        columns = {
            c.key: ("TEXT PRIMARY KEY" if c.primary_key else "TEXT")
            for c in model.__table__.columns  # type: ignore[attr-defined]
        }
        return SQLStorageStrategy(
            config={"type": "sqlite", "name": str(tmp_path / f"{table}.db")},
            table_name=table,
            columns=columns,
        )

    other_requests: Callable[[], RequestRepositoryImpl]
    if request.param == "json":

        def other_requests() -> RequestRepositoryImpl:
            return RequestRepositoryImpl(
                JSONStorageStrategy(str(tmp_path / "requests.json"), entity_type="requests")
            )

        requests = RequestRepositoryImpl(
            JSONStorageStrategy(str(tmp_path / "requests.json"), entity_type="requests")
        )
        machines = MachineRepositoryImpl(
            JSONStorageStrategy(str(tmp_path / "machines.json"), entity_type="machines")
        )
    else:

        def other_requests() -> RequestRepositoryImpl:
            return RequestRepositoryImpl(sql(RequestModel, "requests"))

        requests = RequestRepositoryImpl(sql(RequestModel, "requests"))
        machines = MachineRepositoryImpl(sql(MachineModel, "machines"))
    result = _Uow(requests, machines)
    result.kind = request.param
    result.other_requests = other_requests  # a second writer on the same storage
    return result


class _Provider:
    """Fake provider service; ``on_call`` runs while the call is in flight."""

    def __init__(self) -> None:
        self.on_call: Callable[[], None] | None = None
        self.on_nth_call: dict[int, Callable[[], None]] = {}
        self.instances: list[dict[str, Any]] = []
        self.verdict: ProviderFulfilment | None = ProviderFulfilment(
            state="failed", message="no capacity", final=True
        )
        self.fail = False
        self.calls = 0

    async def execute_operation(self, _provider: str, _operation: object) -> ProviderResult:
        self.calls += 1
        if self.on_call:
            self.on_call()
        if self.calls in self.on_nth_call:
            self.on_nth_call[self.calls]()
        if self.fail:
            return ProviderResult.error_result("throttled", "THROTTLED")
        metadata = {"provider_fulfilment": self.verdict} if self.verdict else {}
        return ProviderResult.success_result({"instances": list(self.instances)}, metadata)


class _World:
    def __init__(self, uow: _Uow) -> None:
        self.uow = uow
        self.provider = _Provider()
        self.logger = MagicMock()
        self.state_machine = FulfilmentStateMachine(grace_period_seconds=3600)
        factory = _Factory(uow)
        self.factory = factory
        populate = PopulateMachineIdsHandler(
            uow_factory=factory,  # type: ignore[arg-type]
            logger=self.logger,
            event_publisher=MagicMock(),
            error_handler=MagicMock(),
            provider_selection_port=self.provider,  # type: ignore[arg-type]
        )
        self.sync = MachineSyncService(
            command_bus=MagicMock(execute=AsyncMock(side_effect=populate.execute_command)),
            uow_factory=factory,  # type: ignore[arg-type]
            config_port=MagicMock(),
            logger=self.logger,
            provider_registry_service=self.provider,  # type: ignore[arg-type]
        )
        self.status = RequestStatusService(factory, self.logger, state_machine=self.state_machine)  # type: ignore[arg-type]
        config = MagicMock()
        config.get_request_config.return_value = {
            "reconcile_enabled": True,
            "reconcile_interval_seconds": 60,
            "reconcile_max_concurrency": 2,
            "reconcile_request_timeout_seconds": 30,
            "list_cache_max_age_seconds": 30,
        }
        self.breaker = MagicMock()
        self.breaker.has_state.return_value = True
        self.breaker.is_open.return_value = False
        self.reconciler = RequestReconciler(
            uow_factory=factory,  # type: ignore[arg-type]
            machine_sync_service=self.sync,
            status_service=self.status,
            config_port=config,
            circuit_breaker_factory=lambda _key: self.breaker,
            logger=self.logger,
        )

    def stored(self, request_id: str) -> Request:
        found = self.uow.requests.get_by_id(RequestId(value=request_id))
        assert found is not None
        return found

    def add_acquire(self) -> Request:
        request = Request(
            request_id=RequestId(value=_ACQUIRE_ID),
            request_type=RequestType.ACQUIRE,
            provider_type="aws",
            provider_name="p1",
            provider_api="EC2Fleet",
            template_id="tmpl",
            requested_count=1,
            resource_ids=["fleet-1"],
            status=RequestStatus.IN_PROGRESS,
        )
        self.uow.requests.save(request)
        return self.stored(_ACQUIRE_ID)

    def add_acquire_with_machine(self) -> Machine:
        request = self.add_acquire().model_copy(update={"machine_ids": ["i-1"]})
        self.uow.requests.save(request.model_copy(update={"version": request.version + 1}))
        self.uow.machines.save(
            Machine(
                machine_id=MachineId(value="i-1"),
                template_id="tmpl",
                request_id=_ACQUIRE_ID,
                provider_type="aws",
                provider_name="p1",
                provider_api="EC2Fleet",
                instance_type=InstanceType(value="t3.micro"),
                image_id="ami-1",
                status=MachineStatus.PENDING,
            )
        )
        stored = self.uow.machines.get_by_id("i-1")
        assert stored is not None
        return stored

    def mark_returned_midflight(self) -> None:
        """On the provider call, the return flow claims the machine for a return request."""

        def _claim() -> None:
            current = self.uow.machines.get_by_id("i-1")
            assert current is not None
            self.uow.machines.save(
                current.model_copy(
                    update={"return_request_id": _RETURN_ID, "version": current.version + 1}
                )
            )

        self.provider.on_nth_call[1] = _claim

    def add_return(self) -> Request:
        # A return request has no capacity verdict: its status comes from the
        # machines (an instance missing from the provider answer is terminated).
        self.provider.verdict = None
        self.uow.machines.save(
            Machine(
                machine_id=MachineId(value="i-1"),
                template_id="tmpl",
                request_id=_ACQUIRE_ID,
                return_request_id=_RETURN_ID,
                provider_type="aws",
                provider_name="p1",
                provider_api="EC2Fleet",
                instance_type=InstanceType(value="t3.micro"),
                image_id="ami-1",
                status=MachineStatus.SHUTTING_DOWN,
            )
        )
        request = Request(
            request_id=RequestId(value=_RETURN_ID),
            request_type=RequestType.RETURN,
            provider_type="aws",
            provider_name="p1",
            provider_api="EC2Fleet",
            template_id="tmpl",
            requested_count=1,
            machine_ids=["i-1"],
            status=RequestStatus.IN_PROGRESS,
        )
        self.uow.requests.save(request)
        return self.stored(_RETURN_ID)

    def cancel_and_create_return_by_other_writer(self, call: int) -> None:
        """On provider call ``call``, a second writer cancels the acquire and files a return."""

        def _other() -> None:
            other = self.uow.other_requests()
            current = other.get_by_id(RequestId(value=_ACQUIRE_ID))
            assert current is not None
            other.save(current.update_status(RequestStatus.CANCELLED, "cancelled", force=True))
            other.save(
                Request(
                    request_id=RequestId(value=_RETURN_ID),
                    request_type=RequestType.RETURN,
                    provider_type="aws",
                    provider_name="p1",
                    provider_api="EC2Fleet",
                    template_id="tmpl",
                    requested_count=1,
                    machine_ids=["i-9"],
                    status=RequestStatus.IN_PROGRESS,
                )
            )

        self.provider.on_nth_call[call] = _other

    def cancel_midflight(self, request_id: str) -> None:
        def _cancel() -> None:
            current = self.stored(request_id)
            self.uow.requests.save(
                current.update_status(RequestStatus.CANCELLED, "cancelled by user", force=True)
            )

        self.provider.on_call = _cancel


@pytest.fixture
def world(uow: _Uow) -> _World:
    return _World(uow)


# ---------------------------------------------------------------------------
# reconcile_once
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_plain_pass_updates_status_and_freshness(world: _World) -> None:
    initial = world.add_acquire()

    summary = await world.reconciler.reconcile_once()

    assert (summary.reconciled, summary.failed) == (1, 0)
    stored = world.stored(_ACQUIRE_ID)
    assert stored.status == RequestStatus.FAILED
    assert stored.last_status_check is not None
    assert "last_fulfilment" in stored.metadata
    assert stored.version > initial.version
    world.breaker.record_success.assert_called_once()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cancel_during_fetch_stays_cancelled(world: _World) -> None:
    world.add_acquire()
    world.cancel_midflight(_ACQUIRE_ID)

    summary = await world.reconciler.reconcile_once()

    assert summary.failed == 0
    stored = world.stored(_ACQUIRE_ID)
    assert stored.status == RequestStatus.CANCELLED
    assert stored.last_status_check is None
    assert "last_fulfilment" not in stored.metadata


@pytest.mark.integration
@pytest.mark.asyncio
async def test_return_request_cancel_during_fetch_stays_cancelled(world: _World) -> None:
    world.add_return()
    world.cancel_midflight(_RETURN_ID)

    summary = await world.reconciler.reconcile_once()

    assert summary.failed == 0
    assert world.stored(_RETURN_ID).status == RequestStatus.CANCELLED


@pytest.mark.integration
@pytest.mark.asyncio
async def test_return_request_without_a_cancel_still_completes(world: _World) -> None:
    world.add_return()

    summary = await world.reconciler.reconcile_once()

    assert (summary.reconciled, summary.failed) == (1, 0)
    assert world.stored(_RETURN_ID).status == RequestStatus.COMPLETED


@pytest.mark.integration
@pytest.mark.asyncio
async def test_metadata_edit_during_fetch_is_kept_and_status_still_updates(
    world: _World,
) -> None:
    world.add_acquire()

    def _edit() -> None:
        current = world.stored(_ACQUIRE_ID)
        world.uow.requests.save(current.update_metadata({"note": "edited meanwhile"}))

    world.provider.on_call = _edit

    summary = await world.reconciler.reconcile_once()

    assert (summary.reconciled, summary.failed) == (1, 0)
    stored = world.stored(_ACQUIRE_ID)
    assert stored.metadata.get("note") == "edited meanwhile"
    assert stored.status == RequestStatus.FAILED
    assert stored.last_status_check is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fetch_failure_is_counted_and_does_not_refresh_freshness(world: _World) -> None:
    world.add_acquire()
    world.provider.fail = True

    summary = await world.reconciler.reconcile_once()

    assert summary.failed == 1
    stored = world.stored(_ACQUIRE_ID)
    assert stored.status == RequestStatus.IN_PROGRESS
    assert stored.last_status_check is None
    world.breaker.record_failure.assert_called_once()


def _fail_saves(world: _World, times: int) -> list[int]:
    """Make the next ``times`` request saves raise a concurrency conflict."""
    repo = world.uow.requests
    real_save = repo.save
    attempts = [0]

    def flaky(request: Request) -> None:
        if attempts[0] < times:
            attempts[0] += 1
            raise ConcurrencyError("injected conflict")
        real_save(request)

    repo.save = flaky  # type: ignore[method-assign]
    return attempts


@pytest.mark.integration
@pytest.mark.asyncio
async def test_one_concurrency_conflict_is_retried_on_a_fresh_read(world: _World) -> None:
    world.add_acquire()
    attempts = _fail_saves(world, 1)

    summary = await world.reconciler.reconcile_once()

    assert attempts[0] == 1
    assert (summary.reconciled, summary.failed) == (1, 0)
    stored = world.stored(_ACQUIRE_ID)
    assert stored.status == RequestStatus.FAILED
    assert stored.last_status_check is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_persistent_conflict_is_counted_failed_and_saves_nothing_stale(
    world: _World,
) -> None:
    before = world.add_acquire()
    _fail_saves(world, 10_000)

    summary = await world.reconciler.reconcile_once()

    assert summary.failed == 1
    stored = world.stored(_ACQUIRE_ID)
    assert stored.status == RequestStatus.IN_PROGRESS
    assert stored.version == before.version
    assert stored.last_status_check is None


# ---------------------------------------------------------------------------
# Query paths (no reconciler): the same writes go through the same services
# ---------------------------------------------------------------------------


def _get_handler(world: _World) -> SyncAndGetRequestHandler:
    return SyncAndGetRequestHandler(
        uow_factory=world.factory,  # type: ignore[arg-type]
        logger=world.logger,
        error_handler=MagicMock(),
        provider_registry_service=MagicMock(),
        machine_sync_service=world.sync,
        cache_service=MagicMock(is_caching_enabled=MagicMock(return_value=False)),
        event_publisher=MagicMock(),
        state_machine=world.state_machine,
    )


def _list_handler(world: _World) -> SyncAndListActiveRequestsHandler:
    filters = MagicMock()
    filters.apply_filters.side_effect = lambda items, _: items
    return SyncAndListActiveRequestsHandler(
        uow_factory=world.factory,  # type: ignore[arg-type]
        logger=world.logger,
        error_handler=MagicMock(),
        generic_filter_service=filters,
        machine_sync_service=world.sync,
        state_machine=world.state_machine,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_status_query_updates_the_stored_request(world: _World) -> None:
    world.add_acquire()

    await _get_handler(world).execute_query(SyncAndGetRequestQuery(request_id=_ACQUIRE_ID))

    stored = world.stored(_ACQUIRE_ID)
    assert stored.status == RequestStatus.FAILED
    assert stored.last_status_check is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_status_query_does_not_undo_a_cancel_made_during_the_fetch(
    world: _World,
) -> None:
    world.add_acquire()
    world.cancel_midflight(_ACQUIRE_ID)

    await _get_handler(world).execute_query(SyncAndGetRequestQuery(request_id=_ACQUIRE_ID))

    assert world.stored(_ACQUIRE_ID).status == RequestStatus.CANCELLED


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_query_updates_the_stored_request(world: _World) -> None:
    world.add_acquire()

    await _list_handler(world).execute_query(SyncAndListActiveRequestsQuery(all_resources=True))

    stored = world.stored(_ACQUIRE_ID)
    assert stored.status == RequestStatus.FAILED
    assert stored.last_status_check is not None


# ---------------------------------------------------------------------------
# Populate phase (machine ids are discovered before the fetch step)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cancel_during_machine_id_discovery_stays_cancelled(
    world: _World,
) -> None:
    world.add_acquire()
    world.provider.instances = [{"instance_id": "i-1"}]
    world.cancel_and_create_return_by_other_writer(call=1)

    summary = await world.reconciler.reconcile_once()

    assert summary.failed == 0
    stored = world.stored(_ACQUIRE_ID)
    assert stored.status == RequestStatus.CANCELLED
    assert stored.machine_ids == []
    assert stored.last_status_check is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_return_request_filed_by_another_writer_survives_the_loop(
    world: _World,
) -> None:
    world.add_acquire()
    world.provider.instances = [{"instance_id": "i-1"}]
    world.cancel_and_create_return_by_other_writer(call=1)

    await world.reconciler.reconcile_once()

    survivor = world.uow.requests.get_by_id(RequestId(value=_RETURN_ID))
    assert survivor is not None
    assert survivor.status == RequestStatus.IN_PROGRESS


@pytest.mark.integration
@pytest.mark.asyncio
async def test_machine_ids_are_stored_when_nothing_changed_meanwhile(world: _World) -> None:
    world.add_acquire()
    world.provider.instances = [{"instance_id": "i-1"}]

    summary = await world.reconciler.reconcile_once()

    assert summary.failed == 0
    assert world.stored(_ACQUIRE_ID).machine_ids == ["i-1"]


# ---------------------------------------------------------------------------
# Machine rows written by the sync step
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_return_claim_made_during_the_fetch_survives_the_machine_update(
    world: _World,
) -> None:
    world.add_acquire_with_machine()
    world.provider.instances = [
        {
            "instance_id": "i-1",
            "status": "running",
            "private_ip": "10.0.0.5",
            "instance_type": "t3.micro",
            "image_id": "ami-1",
        }
    ]
    world.mark_returned_midflight()

    summary = await world.reconciler.reconcile_once()

    assert summary.failed == 0
    machine = world.uow.machines.get_by_id("i-1")
    assert machine is not None
    assert machine.return_request_id == _RETURN_ID  # not reverted to the pre-fetch copy
    assert machine.status == MachineStatus.RUNNING  # provider-owned fields still applied
    assert machine.private_ip == "10.0.0.5"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_machine_update_without_concurrent_change_applies_provider_state(
    world: _World,
) -> None:
    world.add_acquire_with_machine()
    world.provider.instances = [
        {
            "instance_id": "i-1",
            "status": "running",
            "private_ip": "10.0.0.5",
            "instance_type": "t3.micro",
            "image_id": "ami-1",
        }
    ]

    summary = await world.reconciler.reconcile_once()

    assert summary.failed == 0
    machine = world.uow.machines.get_by_id("i-1")
    assert machine is not None
    assert (machine.status, machine.private_ip) == (MachineStatus.RUNNING, "10.0.0.5")


# ---------------------------------------------------------------------------
# A request still being created
# ---------------------------------------------------------------------------


def _add_pending(world: _World) -> Request:
    world.uow.requests.save(
        Request(
            request_id=RequestId(value=_ACQUIRE_ID),
            request_type=RequestType.ACQUIRE,
            provider_type="aws",
            provider_name="p1",
            provider_api="EC2Fleet",
            template_id="tmpl",
            requested_count=1,
        )
    )
    return world.stored(_ACQUIRE_ID)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pending_request_is_left_to_the_create_flow(world: _World) -> None:
    before = _add_pending(world)

    summary = await world.reconciler.reconcile_once()

    assert summary.skipped_provisioning == 1
    assert (summary.reconciled, summary.failed) == (0, 0)
    assert world.provider.calls == 0
    world.logger.warning.assert_not_called()
    stored = world.stored(_ACQUIRE_ID)
    assert stored.version == before.version
    assert stored.last_status_check is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_status_read_of_a_pending_request_writes_nothing(world: _World) -> None:
    before = _add_pending(world)

    await _get_handler(world).execute_query(SyncAndGetRequestQuery(request_id=_ACQUIRE_ID))

    stored = world.stored(_ACQUIRE_ID)
    assert stored.version == before.version
    assert stored.last_status_check is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_read_of_a_pending_request_writes_nothing(world: _World) -> None:
    before = _add_pending(world)

    await _list_handler(world).execute_query(SyncAndListActiveRequestsQuery(all_resources=True))

    assert world.provider.calls == 0
    world.logger.warning.assert_not_called()
    stored = world.stored(_ACQUIRE_ID)
    assert stored.version == before.version
    assert stored.last_status_check is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_instances_request_with_pending_machines_is_synced_and_stays_in_progress(
    world: _World,
) -> None:
    """A launched request has resource ids, so it is polled, and pending machines keep it open."""
    world.uow.requests.save(
        Request(
            request_id=RequestId(value=_ACQUIRE_ID),
            request_type=RequestType.ACQUIRE,
            provider_type="aws",
            provider_name="p1",
            provider_api="RunInstances",
            template_id="tmpl",
            requested_count=1,
            resource_ids=["r-1"],
            machine_ids=["i-1"],
            status=RequestStatus.ACQUIRING,
        )
    )
    world.uow.machines.save(
        Machine(
            machine_id=MachineId(value="i-1"),
            template_id="tmpl",
            request_id=_ACQUIRE_ID,
            provider_type="aws",
            provider_name="p1",
            provider_api="RunInstances",
            instance_type=InstanceType(value="t3.micro"),
            image_id="ami-1",
            status=MachineStatus.PENDING,
        )
    )
    world.provider.verdict = ProviderFulfilment(state="in_progress", message="pending", final=False)
    world.provider.instances = [
        {
            "instance_id": "i-1",
            "status": "pending",
            "instance_type": "t3.micro",
            "image_id": "ami-1",
        }
    ]

    summary = await world.reconciler.reconcile_once()

    assert summary.skipped_provisioning == 0
    assert world.provider.calls >= 1
    assert summary.failed == 0
    stored = world.stored(_ACQUIRE_ID)
    assert stored.last_status_check is not None
    assert not stored.status.is_terminal()
    machine = world.uow.machines.get_by_id("i-1")
    assert machine is not None
    assert machine.status == MachineStatus.PENDING


def _add_expired_acquiring(world: _World) -> Request:
    """An ACQUIRING request with no ids whose create flow never finished."""
    from datetime import datetime, timedelta, timezone

    world.uow.requests.save(
        Request(
            request_id=RequestId(value=_ACQUIRE_ID),
            request_type=RequestType.ACQUIRE,
            provider_type="aws",
            provider_name="p1",
            provider_api="EC2Fleet",
            template_id="tmpl",
            requested_count=1,
            status=RequestStatus.ACQUIRING,
            deadline_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
    )
    return world.stored(_ACQUIRE_ID)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_expired_request_without_ids_ends_terminal_via_reconcile(world: _World) -> None:
    _add_expired_acquiring(world)

    summary = await world.reconciler.reconcile_once()

    assert world.provider.calls == 0
    assert summary.failed == 0
    assert world.stored(_ACQUIRE_ID).status.is_terminal()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("lightweight", [False, True])
async def test_expired_request_without_ids_ends_terminal_via_get(
    world: _World, lightweight: bool
) -> None:
    _add_expired_acquiring(world)

    await _get_handler(world).execute_query(
        SyncAndGetRequestQuery(request_id=_ACQUIRE_ID, lightweight=lightweight)
    )

    assert world.provider.calls == 0
    assert world.stored(_ACQUIRE_ID).status.is_terminal()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_expired_request_without_ids_ends_terminal_via_list(world: _World) -> None:
    _add_expired_acquiring(world)

    await _list_handler(world).execute_query(SyncAndListActiveRequestsQuery(all_resources=True))

    assert world.provider.calls == 0
    assert world.stored(_ACQUIRE_ID).status.is_terminal()


# ---------------------------------------------------------------------------
# The create flow while another writer touches the same request
# ---------------------------------------------------------------------------


class _CreateProvider:
    """Fake provider for the create call; ``on_create`` runs while it is in flight."""

    def __init__(self) -> None:
        self.on_create: Callable[[], None] | None = None

    async def execute_operation(self, _provider: str, _operation: object) -> ProviderResult:
        if self.on_create:
            self.on_create()
        return ProviderResult.success_result(
            {
                "resource_ids": ["fleet-1"],
                "instances": [{"instance_id": "i-1", "resource_id": "fleet-1"}],
                "instance_ids": ["i-1"],
            },
            {},
        )


def _create_handler(world: _World, provider: _CreateProvider):
    from types import SimpleNamespace

    from orb.application.commands.request_creation_handlers import CreateMachineRequestHandler
    from orb.application.services.provisioning_orchestration_service import (
        ProvisioningOrchestrationService,
    )
    from orb.domain.base.results import ProviderSelectionResult

    config = MagicMock()
    config.get_request_config.return_value = {
        "fulfillment_max_retries": 0,
        "fulfillment_timeout_seconds": 60,
        "dispatch_timeout_seconds": 30,
    }
    provisioning = ProvisioningOrchestrationService(
        logger=world.logger,
        uow_factory=world.factory,  # type: ignore[arg-type]
        scheduler=MagicMock(),
        provider_selection_port=provider,  # type: ignore[arg-type]
        provider_config_port=MagicMock(),
        config_port=config,
        circuit_breaker_factory=lambda _key: MagicMock(),
        state_machine=world.state_machine,
    )
    validation = MagicMock()
    validation.select_and_validate_provider = AsyncMock(
        return_value=ProviderSelectionResult(
            provider_type="aws", provider_name="p1", selection_reason="test"
        )
    )
    query_bus = MagicMock()
    query_bus.execute = AsyncMock(
        return_value=SimpleNamespace(template_id="tmpl", provider_api="EC2Fleet")
    )
    return CreateMachineRequestHandler(
        uow_factory=world.factory,  # type: ignore[arg-type]
        logger=world.logger,
        event_publisher=MagicMock(),
        error_handler=MagicMock(),
        query_bus=query_bus,
        provider_selection_port=MagicMock(),
        provisioning_service=provisioning,
        provider_validation_service=validation,
        state_machine=world.state_machine,
    )


async def _create(world: _World, provider: _CreateProvider) -> None:
    from orb.application.dto.commands import CreateRequestCommand

    command = CreateRequestCommand(request_id=_ACQUIRE_ID, template_id="tmpl", requested_count=1)
    await _create_handler(world, provider).execute_command(command)


def _stamp_after_checkpoint(world: _World) -> None:
    """Run a status-check stamp when the create flow saves its machines.

    That is after the resource-id checkpoint and before the final save, while
    the create flow still holds its own copy of the request.
    """
    from datetime import datetime, timezone

    machines = world.uow.machines
    real_save_batch = machines.save_batch

    def save_batch(batch: list[Machine]) -> None:
        world.sync._stamp_status_check(world.stored(_ACQUIRE_ID), datetime.now(timezone.utc))
        real_save_batch(batch)

    machines.save_batch = save_batch  # type: ignore[method-assign]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_flow_keeps_its_result_over_a_concurrent_status_stamp(
    world: _World,
) -> None:
    _stamp_after_checkpoint(world)

    await _create(world, _CreateProvider())

    stored = world.stored(_ACQUIRE_ID)
    assert stored.resource_ids == ["fleet-1"]
    assert stored.machine_ids == ["i-1"]
    assert stored.status == RequestStatus.COMPLETED
    assert stored.last_status_check is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_flow_records_launched_capacity_on_a_request_cancelled_meanwhile(
    world: _World,
) -> None:
    provider = _CreateProvider()
    provider.on_create = lambda: world.uow.requests.save(
        world.stored(_ACQUIRE_ID).update_status(RequestStatus.CANCELLED, "cancelled", force=True)
    )

    await _create(world, provider)

    stored = world.stored(_ACQUIRE_ID)
    assert stored.status == RequestStatus.CANCELLED
    assert stored.resource_ids == ["fleet-1"]
    assert stored.machine_ids == ["i-1"]
