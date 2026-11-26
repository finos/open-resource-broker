"""A return whose pods cannot be listed, driven through the real sync path.

The Kubernetes registry runs over a fake CoreV1, behind the real
``MachineSyncService``, ``RequestStatusService`` and reconciler, over real
JSON and SQLite storage. What is asserted is what an operator sees: the stored
machine rows and the stored return request.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from kubernetes.client.exceptions import ApiException

from orb.application.commands.request_sync_handlers import PopulateMachineIdsHandler
from orb.application.services.machine_sync_service import MachineSyncService
from orb.application.services.request_reconciler import RequestReconciler
from orb.application.services.request_status_service import RequestStatusService
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
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.strategy.handler_registry import K8sHandlerRegistry
from orb.providers.k8s.strategy.outcome_bridge import _outcome_to_provider_result
from tests.providers.k8s.unit.strategy.fake_core_v1 import FakeCoreV1, fake_client

NS = "orb-team-a"
ACQUIRE_ID = "req-11111111-1111-4111-8111-111111111111"
RETURN_ID = "ret-22222222-2222-4222-8222-222222222222"


class _Uow:
    def __init__(self, requests: Any, machines: Any) -> None:
        self.requests = requests
        self.machines = machines

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
def uow(request: pytest.FixtureRequest, tmp_path: Any) -> _Uow:
    from orb.infrastructure.storage.json.strategy import JSONStorageStrategy
    from orb.infrastructure.storage.repositories.machine_repository import MachineRepositoryImpl
    from orb.infrastructure.storage.repositories.request_repository import RequestRepositoryImpl
    from orb.infrastructure.storage.sql.models import MachineModel, RequestModel
    from orb.infrastructure.storage.sql.strategy import SQLStorageStrategy

    def sql(model: Any, table: str) -> Any:
        columns = {
            c.key: ("TEXT PRIMARY KEY" if c.primary_key else "TEXT")
            for c in model.__table__.columns
        }
        return SQLStorageStrategy(
            config={"type": "sqlite", "name": str(tmp_path / f"{table}.db")},
            table_name=table,
            columns=columns,
        )

    if request.param == "json":
        return _Uow(
            RequestRepositoryImpl(
                JSONStorageStrategy(str(tmp_path / "requests.json"), entity_type="requests")
            ),
            MachineRepositoryImpl(
                JSONStorageStrategy(str(tmp_path / "machines.json"), entity_type="machines")
            ),
        )
    return _Uow(
        RequestRepositoryImpl(sql(RequestModel, "requests")),
        MachineRepositoryImpl(sql(MachineModel, "machines")),
    )


class _K8sProvider:
    """What the provider strategy does for a status read, over the real registry."""

    def __init__(self, registry: K8sHandlerRegistry) -> None:
        self.registry = registry

    async def execute_operation(self, _provider: str, operation: Any) -> ProviderResult:
        params = operation.parameters
        outcome = await self.registry.get_status(
            list(params["instance_ids"]),
            params["request"],
            machine_coordinates=params.get("machine_coordinates"),
        )
        return _outcome_to_provider_result(outcome, fallback_operation="get_instance_status")


class World:
    def __init__(self, uow: _Uow, **config: Any) -> None:
        self.uow = uow
        self.core = FakeCoreV1()
        self.registry = K8sHandlerRegistry(
            config=K8sProviderConfig(  # type: ignore[call-arg]
                namespace="default",
                max_retries=0,
                circuit_breaker_failure_threshold=1000,
                **config,
            ),
            logger=MagicMock(),
            client_provider=lambda: fake_client(self.core),
            watch_manager_provider=lambda: None,
            plugin_factories=lambda: {},
            native_spec_service_provider=lambda: None,
        )
        logger = MagicMock()
        factory = _Factory(uow)
        provider = _K8sProvider(self.registry)
        populate = PopulateMachineIdsHandler(
            uow_factory=factory,  # type: ignore[arg-type]
            logger=logger,
            event_publisher=MagicMock(),
            error_handler=MagicMock(),
            provider_selection_port=provider,  # type: ignore[arg-type]
        )
        sync = MachineSyncService(
            command_bus=MagicMock(execute=AsyncMock(side_effect=populate.execute_command)),
            uow_factory=factory,  # type: ignore[arg-type]
            config_port=MagicMock(),
            logger=logger,
            provider_registry_service=provider,  # type: ignore[arg-type]
        )
        status = RequestStatusService(
            factory,  # type: ignore[arg-type]
            logger,
            state_machine=FulfilmentStateMachine(grace_period_seconds=3600),
        )
        reconcile_config = MagicMock()
        reconcile_config.get_request_config.return_value = {
            "reconcile_enabled": True,
            "reconcile_interval_seconds": 60,
            "reconcile_max_concurrency": 2,
            "reconcile_request_timeout_seconds": 30,
            "list_cache_max_age_seconds": 30,
        }
        self.reconciler = RequestReconciler(
            uow_factory=factory,  # type: ignore[arg-type]
            machine_sync_service=sync,
            status_service=status,
            config_port=reconcile_config,
            circuit_breaker_factory=lambda _key: MagicMock(
                has_state=MagicMock(return_value=True), is_open=MagicMock(return_value=False)
            ),
            logger=logger,
        )

    # -- cluster -----------------------------------------------------------

    def _labels(self) -> dict[str, str]:
        acquire = Request(
            request_id=RequestId(value=ACQUIRE_ID),
            request_type=RequestType.ACQUIRE,
            provider_type="k8s",
            provider_api="Pod",
            template_id="tpl-1",
            requested_count=1,
        )
        body = self.registry.get_handler("Pod")._stamp_native_pod_body(  # type: ignore[attr-defined]
            {}, pod_name="p", machine_id="p", namespace=NS, request=acquire
        )
        return dict(body["metadata"]["labels"])

    def add_pod(self, name: str, **kw: Any) -> None:
        self.core.add_pod(NS, name, self._labels(), **kw)

    def list_fails(self) -> None:
        self.core.list_errors[NS] = ApiException(status=500)

    def list_works(self) -> None:
        self.core.list_errors.clear()

    # -- storage -----------------------------------------------------------

    def add_return(self, names: list[str], *, age_seconds: int = 0) -> None:
        """An in-progress return of ``names``, filed ``age_seconds`` ago."""
        self.uow.requests.save(
            Request(
                request_id=RequestId(value=ACQUIRE_ID),
                request_type=RequestType.ACQUIRE,
                provider_type="k8s",
                provider_name="k8s_default",
                provider_api="Pod",
                template_id="tpl-1",
                requested_count=len(names),
                machine_ids=list(names),
                provider_data={"namespace": NS},
                status=RequestStatus.COMPLETED,
            )
        )
        for name in names:
            self.uow.machines.save(
                Machine(
                    machine_id=MachineId(value=name),
                    template_id="tpl-1",
                    request_id=ACQUIRE_ID,
                    return_request_id=RETURN_ID,
                    provider_type="k8s",
                    provider_name="k8s_default",
                    provider_api="Pod",
                    instance_type=InstanceType(value="k8s/Pod"),
                    image_id="busybox",
                    status=MachineStatus.SHUTTING_DOWN,
                    provider_data={"namespace": NS},
                )
            )
        self.uow.requests.save(
            Request(
                request_id=RequestId(value=RETURN_ID),
                request_type=RequestType.RETURN,
                provider_type="k8s",
                provider_name="k8s_default",
                provider_api="Pod",
                template_id="tpl-1",
                requested_count=len(names),
                machine_ids=list(names),
                status=RequestStatus.IN_PROGRESS,
                created_at=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
            )
        )

    def request(self) -> Request:
        found = self.uow.requests.get_by_id(RequestId(value=RETURN_ID))
        assert found is not None
        return found

    def machine(self, name: str) -> Machine:
        found = self.uow.machines.get_by_id(name)
        assert found is not None
        return found

    async def poll(self) -> Request:
        await self.reconciler.reconcile_request(self.request())
        return self.request()


@pytest.fixture
def world(uow: _Uow) -> World:
    return World(uow)


# ---------------------------------------------------------------------------
# An unreadable return is not a completed return
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unreadable_return_is_not_completed(world: World) -> None:
    world.add_return(["pod-a", "pod-b"])
    world.list_fails()

    request = await world.poll()

    assert request.status == RequestStatus.IN_PROGRESS
    assert {world.machine(n).status for n in ("pod-a", "pod-b")} == {MachineStatus.SHUTTING_DOWN}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unreadable_machine_is_not_closed_with_a_readable_one(world: World) -> None:
    world.add_return(["pod-a", "pod-b"])
    world.list_fails()
    # pod-a lives in a namespace that lists fine and no longer holds it.
    world.uow.machines.save(
        world.machine("pod-a").model_copy(
            update={"provider_data": {"namespace": "orb-team-b"}, "version": 2}
        )
    )

    request = await world.poll()

    assert request.status == RequestStatus.IN_PROGRESS
    assert world.machine("pod-a").status == MachineStatus.TERMINATED
    assert world.machine("pod-b").status == MachineStatus.SHUTTING_DOWN


@pytest.mark.integration
@pytest.mark.asyncio
async def test_placement_survives_an_unreadable_poll(world: World) -> None:
    world.add_return(["pod-a"])
    world.add_pod("pod-a")
    world.list_fails()
    before = world.machine("pod-a").provider_data

    first = await world.poll()
    assert first.status == RequestStatus.IN_PROGRESS
    assert world.machine("pod-a").provider_data == before
    await world.poll()
    assert world.machine("pod-a").provider_data == before

    # The cluster answers again: the pod is read in its own namespace, not the default one.
    world.list_works()
    world.core.calls.clear()
    recovered = await world.poll()

    assert {call[1] for call in world.core.calls} == {NS}
    assert world.machine("pod-a").status == MachineStatus.RUNNING
    assert recovered.status == RequestStatus.IN_PROGRESS
