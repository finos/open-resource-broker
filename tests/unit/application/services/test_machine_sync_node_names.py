"""Slurm node names for machines that the provider reports only after creation.

``orb machines request <template> N --nodes <hostlist>`` stores the Slurm node
names on the request. Providers that fulfil asynchronously (the Kubernetes Pod
handler, AWS ASG) return no instances from the create call, so their machines
are first written by ``MachineSyncService.sync_machines_with_provider``. These
tests drive that path against the real JSON and SQLite storage backends and
check that the names reach the machine records, which ``resumeProgram.sh``
(``.name``) and ``terminate --nodes`` rely on.
"""

from datetime import datetime, timezone
from typing import Any, Callable, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.services.machine_sync_service import MachineSyncService
from orb.application.services.node_name_assignment import (
    ASSIGNMENTS_METADATA_KEY,
    NODE_NAME_TAG,
    NodeNameReconciler,
    reconcile_assignments,
)
from orb.domain.base.value_objects import Tags
from orb.domain.machine.aggregate import Machine
from orb.domain.machine.machine_status import MachineStatus
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestStatus, RequestType

NODE_NAMES = ["k8s-001", "k8s-002", "k8s-003"]


@pytest.fixture(params=["json", "sql"])
def make_uow(request, tmp_path) -> Callable[[], Any]:
    if request.param == "sql":
        from sqlalchemy import create_engine

        from orb.infrastructure.storage.sql.unit_of_work import SQLUnitOfWork

        engine = create_engine(f"sqlite:///{tmp_path}/orb.db")
        return lambda: SQLUnitOfWork(engine)

    from orb.infrastructure.storage.json.unit_of_work import JSONUnitOfWork

    return lambda: JSONUnitOfWork(str(tmp_path), backup_enabled=False)


def _factory(make: Callable[[], Any]) -> MagicMock:
    factory = MagicMock()
    factory.create_unit_of_work.side_effect = make
    return factory


def _stored_request(make: Callable[[], Any], node_names: Optional[list] = None) -> Request:
    request = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="slurm-compute",
        machine_count=len(NODE_NAMES),
        provider_type="k8s",
        provider_name="k8s_in-cluster",
        provider_api="Pod",
    )
    if node_names:
        request = request.update_metadata({"node_names": node_names})
    with make() as uow:
        uow.requests.save(request)
    return request


def _pod(
    request: Request,
    seq: int,
    *,
    status: MachineStatus = MachineStatus.PENDING,
    ip: Optional[str] = None,
    tags: Optional[dict] = None,
) -> Machine:
    """A machine as the Kubernetes provider reports it: named after the pod."""
    pod_name = f"orb-pod-{seq:04d}"
    return Machine(
        machine_id=pod_name,
        name=pod_name,
        template_id="slurm-compute",
        request_id=str(request.request_id),
        provider_type="k8s",
        provider_name="k8s_in-cluster",
        provider_api="Pod",
        instance_type="k8s/Pod",
        image_id="registry.example/slurm:1",
        status=status,
        private_ip=ip,
        tags=Tags(tags=tags if tags is not None else {"orb.io/managed": "true"}),
    )


def _service(make: Callable[[], Any], registry: Any = None) -> MachineSyncService:
    return MachineSyncService(
        command_bus=MagicMock(),
        uow_factory=_factory(make),
        config_port=MagicMock(),
        logger=MagicMock(),
        provider_registry_service=registry,
    )


async def _sync(svc: MachineSyncService, make: Callable[[], Any], request_id: Any, provider: list):
    with make() as uow:
        request = uow.requests.get_by_id(request_id)
        stored = uow.machines.find_by_request_id(str(request_id))
    machines, _ = await svc.sync_machines_with_provider(request, stored, provider)
    return machines


def _names(make: Callable[[], Any], request: Request, *, live_only: bool = False) -> dict:
    with make() as uow:
        return {
            str(m.machine_id.value): m.name
            for m in uow.machines.find_by_request_id(str(request.request_id))
            if not (live_only and m.status.is_terminal)
        }


def _assignments(make: Callable[[], Any], request: Request) -> dict:
    with make() as uow:
        stored = uow.requests.get_by_id(request.request_id)
        return dict(stored.metadata.get(ASSIGNMENTS_METADATA_KEY) or {})


@pytest.mark.unit
class TestNodeNamesOnSyncDiscovery:
    @pytest.mark.asyncio
    async def test_first_sync_assigns_slurm_node_names(self, make_uow):
        request = _stored_request(make_uow, NODE_NAMES)
        svc = _service(make_uow)

        returned = await _sync(
            svc, make_uow, request.request_id, [_pod(request, i) for i in range(3)]
        )

        assert sorted(str(m.name) for m in returned) == NODE_NAMES
        with make_uow() as uow:
            stored = sorted(
                uow.machines.find_by_request_id(str(request.request_id)),
                key=lambda m: str(m.machine_id.value),
            )
        assert [m.name for m in stored] == NODE_NAMES
        assert [m.tags.tags.get(NODE_NAME_TAG) for m in stored] == NODE_NAMES
        assert _assignments(make_uow, request) == {
            "orb-pod-0000": "k8s-001",
            "orb-pod-0001": "k8s-002",
            "orb-pod-0002": "k8s-003",
        }

    @pytest.mark.asyncio
    async def test_names_survive_later_syncs_that_report_new_state(self, make_uow):
        request = _stored_request(make_uow, NODE_NAMES)
        svc = _service(make_uow)
        await _sync(svc, make_uow, request.request_id, [_pod(request, i) for i in range(3)])

        running = [
            _pod(request, i, status=MachineStatus.RUNNING, ip=f"10.0.0.{i + 10}") for i in range(3)
        ]
        await _sync(svc, make_uow, request.request_id, running)

        with make_uow() as uow:
            stored = sorted(
                uow.machines.find_by_request_id(str(request.request_id)),
                key=lambda m: str(m.machine_id.value),
            )
        assert [m.name for m in stored] == NODE_NAMES
        assert [m.tags.tags.get(NODE_NAME_TAG) for m in stored] == NODE_NAMES
        assert [str(m.private_ip) for m in stored] == ["10.0.0.10", "10.0.0.11", "10.0.0.12"]

    @pytest.mark.asyncio
    async def test_assignment_does_not_depend_on_provider_listing_order(self, make_uow):
        request = _stored_request(make_uow, NODE_NAMES)

        await _sync(
            _service(make_uow), make_uow, request.request_id, [_pod(request, i) for i in (2, 0, 1)]
        )

        names = _names(make_uow, request)
        assert [names[k] for k in sorted(names)] == NODE_NAMES

    @pytest.mark.asyncio
    async def test_machines_reported_late_take_the_remaining_names(self, make_uow):
        request = _stored_request(make_uow, NODE_NAMES)
        svc = _service(make_uow)

        await _sync(svc, make_uow, request.request_id, [_pod(request, 1)])
        await _sync(svc, make_uow, request.request_id, [_pod(request, i) for i in range(3)])

        assert sorted(_names(make_uow, request).values()) == NODE_NAMES
        assert _names(make_uow, request)["orb-pod-0001"] == "k8s-001"

    @pytest.mark.asyncio
    async def test_replacement_in_the_same_sync_inherits_the_name(self, make_uow):
        request = _stored_request(make_uow, NODE_NAMES)
        svc = _service(make_uow)
        await _sync(svc, make_uow, request.request_id, [_pod(request, i) for i in range(3)])

        provider = [
            _pod(request, 0, status=MachineStatus.RUNNING),
            _pod(request, 1, status=MachineStatus.TERMINATED),
            _pod(request, 2, status=MachineStatus.RUNNING),
            _pod(request, 3),
        ]
        await _sync(svc, make_uow, request.request_id, provider)

        live = _names(make_uow, request, live_only=True)
        assert sorted(live.values()) == NODE_NAMES
        assert live["orb-pod-0003"] == "k8s-002"

    @pytest.mark.asyncio
    async def test_replacement_seen_while_the_old_machine_shuts_down_is_named_later(self, make_uow):
        request = _stored_request(make_uow, ["k8s-001"])
        svc = _service(make_uow)
        await _sync(svc, make_uow, request.request_id, [_pod(request, 0)])

        # The replacement appears while the old machine is still shutting down,
        # so the name is not free yet.
        await _sync(
            svc,
            make_uow,
            request.request_id,
            [_pod(request, 0, status=MachineStatus.SHUTTING_DOWN), _pod(request, 1)],
        )
        assert _names(make_uow, request)["orb-pod-0001"] == "orb-pod-0001"

        # Once the old machine is gone, the next sync hands its name over.
        await _sync(
            svc,
            make_uow,
            request.request_id,
            [_pod(request, 0, status=MachineStatus.TERMINATED), _pod(request, 1)],
        )
        assert _names(make_uow, request, live_only=True) == {"orb-pod-0001": "k8s-001"}

    @pytest.mark.asyncio
    async def test_requests_without_node_names_keep_provider_names(self, make_uow):
        request = _stored_request(make_uow)

        await _sync(
            _service(make_uow), make_uow, request.request_id, [_pod(request, i) for i in range(2)]
        )

        assert _names(make_uow, request) == {
            "orb-pod-0000": "orb-pod-0000",
            "orb-pod-0001": "orb-pod-0001",
        }
        assert _assignments(make_uow, request) == {}

    @pytest.mark.asyncio
    async def test_finished_requests_are_left_alone(self, make_uow):
        request = _stored_request(make_uow, NODE_NAMES)
        with make_uow() as uow:
            stored = uow.requests.get_by_id(request.request_id)
            uow.requests.save(stored.update_status(RequestStatus.COMPLETED, "done"))

        await _sync(_service(make_uow), make_uow, request.request_id, [_pod(request, 0)])

        assert _names(make_uow, request) == {"orb-pod-0000": "orb-pod-0000"}


@pytest.mark.unit
class TestConcurrentReconcilers:
    @pytest.mark.asyncio
    async def test_two_processes_never_hand_out_the_same_name(self, make_uow):
        """Process B reads the request, then process A names its machine and
        commits, then B saves. B must lose the version check and re-read."""
        request = _stored_request(make_uow, ["k8s-001", "k8s-002"])
        with make_uow() as uow:
            uow.machines.save(_pod(request, 0))
            uow.machines.save(_pod(request, 1))
        reconciler_a = NodeNameReconciler(_factory(make_uow), MagicMock())

        interleaved = {"done": False}

        def make_b():
            uow = make_uow()
            find = uow.machines.find_by_request_id

            def find_then_let_a_run(request_id):
                machines = find(request_id)
                if not interleaved["done"]:
                    interleaved["done"] = True
                    # A sees only pod 1 and takes the first name.
                    with make_uow() as other:
                        assignments = reconcile_assignments(
                            ["k8s-001", "k8s-002"], {}, [other.machines.get_by_id("orb-pod-0001")]
                        )
                        current = other.requests.get_by_id(request.request_id)
                        other.requests.save(
                            current.update_metadata({ASSIGNMENTS_METADATA_KEY: assignments})
                        )
                    reconciler_a._apply_assignments(request.request_id)
                return machines

            uow.machines.find_by_request_id = find_then_let_a_run
            return uow

        reconciler_b = NodeNameReconciler(_factory(make_b), MagicMock())
        await reconciler_b.reconcile(request.request_id)

        names = _names(make_uow, request)
        assert sorted(names.values()) == ["k8s-001", "k8s-002"]
        assert names["orb-pod-0001"] == "k8s-001"

    @pytest.mark.asyncio
    async def test_an_unrelated_request_write_does_not_lose_the_names(self, make_uow):
        request = _stored_request(make_uow, ["k8s-001"])
        with make_uow() as uow:
            uow.machines.save(_pod(request, 0))

        first = {"done": False}

        def make_with_write():
            uow = make_uow()
            find = uow.machines.find_by_request_id

            def find_then_write_request(request_id):
                machines = find(request_id)
                if not first["done"]:
                    first["done"] = True
                    with make_uow() as other:
                        current = other.requests.get_by_id(request.request_id)
                        other.requests.save(
                            current.record_status_check(now=datetime.now(timezone.utc))
                        )
                return machines

            uow.machines.find_by_request_id = find_then_write_request
            return uow

        await NodeNameReconciler(_factory(make_with_write), MagicMock()).reconcile(
            request.request_id
        )

        assert _names(make_uow, request) == {"orb-pod-0000": "k8s-001"}
        assert _assignments(make_uow, request) == {"orb-pod-0000": "k8s-001"}


@pytest.mark.unit
class TestTagPush:
    def _registry(self, supports: bool = True) -> MagicMock:
        registry = MagicMock()
        registry.get_strategy_capabilities.return_value.supports_operation.return_value = supports
        registry.execute_operation = AsyncMock(return_value=MagicMock(success=True))
        return registry

    @pytest.mark.asyncio
    async def test_tags_are_pushed_until_the_provider_reports_them(self, make_uow):
        request = _stored_request(make_uow, ["k8s-001"])
        registry = self._registry()
        svc = _service(make_uow, registry)

        await _sync(svc, make_uow, request.request_id, [_pod(request, 0)])
        # The push failed or has not landed: the provider still lacks the tag.
        await _sync(svc, make_uow, request.request_id, [_pod(request, 0)])
        # Now the provider reports it.
        await _sync(
            svc, make_uow, request.request_id, [_pod(request, 0, tags={NODE_NAME_TAG: "k8s-001"})]
        )

        pushed = [
            call.args[1].parameters["instance_tags"]
            for call in registry.execute_operation.await_args_list
        ]
        assert pushed == [
            {"orb-pod-0000": {NODE_NAME_TAG: "k8s-001"}},
            {"orb-pod-0000": {NODE_NAME_TAG: "k8s-001"}},
        ]

    @pytest.mark.asyncio
    async def test_providers_that_cannot_tag_are_skipped(self, make_uow):
        request = _stored_request(make_uow, ["k8s-001"])
        registry = self._registry(supports=False)

        await _sync(_service(make_uow, registry), make_uow, request.request_id, [_pod(request, 0)])

        registry.execute_operation.assert_not_awaited()
        assert _names(make_uow, request) == {"orb-pod-0000": "k8s-001"}


@pytest.mark.unit
class TestReconcileAssignments:
    def _machine(self, mid: str, status: MachineStatus = MachineStatus.RUNNING) -> Machine:
        return Machine(
            machine_id=mid,
            name=mid,
            template_id="t",
            request_id="req-1",
            provider_type="k8s",
            provider_name="k8s",
            provider_api="Pod",
            instance_type="k8s/Pod",
            image_id="img",
            status=status,
        )

    def test_duplicate_listings_do_not_waste_a_name(self):
        m = self._machine("m-1")
        assert reconcile_assignments(["n1", "n2"], {}, [m, m]) == {"m-1": "n1"}

    def test_machines_beyond_the_names_get_none(self):
        machines = [self._machine(f"m-{i}") for i in range(3)]
        assert reconcile_assignments(["n1"], {}, machines) == {"m-0": "n1"}

    def test_assignments_for_unstored_machines_are_kept(self):
        assert reconcile_assignments(["n1", "n2"], {"m-x": "n1"}, [self._machine("m-1")]) == {
            "m-x": "n1",
            "m-1": "n2",
        }

    def test_stale_or_duplicate_assignments_are_dropped(self):
        machines = [self._machine("m-1"), self._machine("m-2")]
        assert reconcile_assignments(
            ["n1", "n2"], {"m-1": "n1", "m-2": "n1", "m-9": "gone"}, machines
        ) == {"m-1": "n1", "m-2": "n2"}
