"""Unit tests for MachineSyncService — basic behaviour and OperationOutcome awareness.

The sync service does not directly call acquire/return_machines/get_status; it uses
execute_operation via ProviderRegistryService.  These tests cover the core paths
that interact with the outcome-aware request status logic.

Also covers the instance_type freeze-bug fix: a machine synced while its pod was
pending (instance_type = "k8s/Pod") must have its instance_type refreshed once
the pod is scheduled on a real node and the provider returns the true type.
"""

from unittest.mock import MagicMock

import pytest

from orb.application.services.machine_sync_service import MachineSyncService
from orb.domain.machine.machine_status import MachineStatus


def _make_service() -> MachineSyncService:
    command_bus = MagicMock()
    uow_factory = MagicMock()
    config_port = MagicMock()
    logger = MagicMock()
    return MachineSyncService(
        command_bus=command_bus,
        uow_factory=uow_factory,
        config_port=config_port,
        logger=logger,
    )


def _make_request(request_type: str = "acquire", provider_api: str = "RunInstances"):
    req = MagicMock()
    req.request_id = MagicMock()
    req.request_id.__str__ = lambda self: "req-sync-test"
    req.request_type.value = request_type
    req.provider_name = "aws_default_us-east-1"
    req.provider_api = provider_api
    req.template_id = "tmpl-1"
    req.resource_ids = ["fleet-1"]
    req.machine_ids = []
    req.metadata = {}
    return req


def _make_machine(mid: str, status: MachineStatus = MachineStatus.RUNNING):
    m = MagicMock()
    m.machine_id.value = mid
    m.status = status
    return m


@pytest.mark.unit
class TestFetchProviderMachinesNoRegistryService:
    """fetch_provider_machines returns (db_machines, {}) when no registry service."""

    @pytest.mark.asyncio
    async def test_returns_db_machines_when_registry_service_missing(self):
        svc = _make_service()
        db_machines = [_make_machine("i-1")]
        req = _make_request()

        # No provider_registry_service injected → should raise RuntimeError
        # which is caught internally and returns (db_machines, {})
        machines, meta = await svc.fetch_provider_machines(req, db_machines)  # type: ignore[arg-type]
        assert machines == db_machines
        assert meta == {}


@pytest.mark.unit
class TestFetchProviderMachinesEmpty:
    """fetch_provider_machines returns ([], {}) when no machine context."""

    @pytest.mark.asyncio
    async def test_no_resource_ids_and_no_db_machines_returns_empty(self):
        svc = _make_service()
        req = _make_request()
        req.resource_ids = []
        req.machine_ids = []

        machines, meta = await svc.fetch_provider_machines(req, [])
        assert machines == []
        assert meta == {}


@pytest.mark.unit
class TestFetchProviderMachinesWithMockRegistry:
    """fetch_provider_machines propagates provider instance data."""

    @pytest.mark.asyncio
    async def test_acquire_path_calls_registry_service(self):
        """fetch_provider_machines calls the registry service for acquire requests."""
        from orb.providers.base.strategy.provider_strategy import ProviderResult

        svc = _make_service()

        captured: list = []

        async def _capture(provider_name, operation):
            captured.append(operation.operation_type.value)
            return ProviderResult.success_result(data={"instances": []})

        registry_svc = MagicMock()
        registry_svc.execute_operation = _capture
        svc._provider_registry_service = registry_svc

        req = _make_request(request_type="acquire")
        db_machines: list = []

        machines, _meta = await svc.fetch_provider_machines(req, db_machines)
        # The registry was called and returned empty instances → machines is empty
        assert "describe_resource_instances" in captured
        assert machines == []

    @pytest.mark.asyncio
    async def test_return_path_uses_instance_status_operation(self):
        """For return requests with machine_ids, the GET_INSTANCE_STATUS operation is used."""
        from orb.providers.base.strategy.provider_strategy import ProviderResult

        svc = _make_service()

        called_operations: list = []

        async def _capture(provider_name, operation):
            called_operations.append(operation.operation_type.value)
            return ProviderResult.success_result(
                data={
                    "instances": [
                        {
                            "instance_id": "i-1",
                            "status": "shutting-down",
                            "instance_type": "m5.large",
                        }
                    ]
                },
            )

        registry_svc = MagicMock()
        registry_svc.execute_operation = _capture
        svc._provider_registry_service = registry_svc

        req = _make_request(request_type="return")
        req.machine_ids = ["i-1"]

        db_machines = [_make_machine("i-1", MachineStatus.RUNNING)]
        await svc.fetch_provider_machines(req, db_machines)  # type: ignore[arg-type]

        assert "get_instance_status" in called_operations


# ---------------------------------------------------------------------------
# instance_type freeze-bug fix
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInstanceTypeRefreshOnSync:
    """sync_machines_with_provider must update instance_type when it changes.

    Reproduces the freeze-bug: a k8s pod is created while still pending so its
    initial instance_type in the DB is "k8s/Pod".  Once the pod is scheduled on
    a real node the provider returns the actual instance type (e.g. "c5.2xlarge").
    The sync service must detect the difference and overwrite the stale value.
    """

    def _make_full_machine(
        self,
        mid: str,
        *,
        instance_type: str = "k8s/Pod",
        status: MachineStatus = MachineStatus.RUNNING,
        price_type: str | None = None,
    ) -> MagicMock:
        """Build a MagicMock that behaves enough like a Machine for sync."""
        from orb.domain.base.value_objects import InstanceType, Tags
        from orb.domain.machine.machine_identifiers import MachineId

        m = MagicMock()
        m.machine_id = MachineId(value=mid)
        m.status = status
        m.instance_type = InstanceType(value=instance_type)
        m.price_type = price_type
        m.private_ip = None
        m.public_ip = None
        m.name = mid
        m.private_dns_name = None
        m.public_dns_name = None
        m.tags = Tags(tags={})
        m.subnet_id = None
        m.security_group_ids = []
        m.vpc_id = None
        m.status_reason = None
        m.provider_data = {}
        m.resource_id = None
        m.launch_time = None
        m.version = 1

        # model_dump / model_validate: return a dict then reconstruct a real Machine.

        def _dump() -> dict:
            return {
                "machine_id": mid,
                "name": mid,
                "template_id": "tpl-1",
                "request_id": "req-1",
                "provider_type": "k8s",
                "provider_name": "k8s-dev",
                "provider_api": "Pod",
                "resource_id": None,
                "instance_type": instance_type,
                "image_id": "unknown",
                "price_type": price_type,
                "status": status,
                "private_ip": None,
                "public_ip": None,
                "private_dns_name": None,
                "public_dns_name": None,
                "launch_time": None,
                "subnet_id": None,
                "security_group_ids": [],
                "vpc_id": None,
                "tags": {},
                "metadata": {},
                "provider_data": {},
                "status_reason": None,
                "version": 1,
            }

        m.model_dump = _dump
        return m

    @pytest.mark.asyncio
    async def test_instance_type_updated_from_pending_to_scheduled(self) -> None:
        """instance_type must refresh from "k8s/Pod" → real type after scheduling."""
        from orb.domain.machine.aggregate import Machine

        svc = _make_service()

        # Fake UoW that captures saved machines.
        saved: list = []
        uow_cm = MagicMock()
        uow_cm.__enter__ = lambda s: uow_cm
        uow_cm.__exit__ = MagicMock(return_value=False)
        uow_cm.machines = MagicMock()
        uow_cm.machines.save = lambda m: saved.append(m)
        uow_cm.requests = MagicMock()
        uow_cm.requests.save = MagicMock()
        svc.uow_factory.create_unit_of_work = MagicMock(return_value=uow_cm)

        db_machine = self._make_full_machine("pod-abc", instance_type="k8s/Pod", price_type=None)

        # Provider now knows the real node instance type.
        provider_machine = self._make_full_machine(
            "pod-abc", instance_type="c5.2xlarge", price_type="spot"
        )

        req = MagicMock()
        req.request_id = MagicMock()
        req.request_id.__str__ = lambda s: "req-1"
        req.record_status_check = MagicMock(return_value=req)

        updated, _ = await svc.sync_machines_with_provider(req, [db_machine], [provider_machine])

        # The updated machine list must contain the refreshed instance type.
        assert len(updated) == 1
        assert len(saved) == 1
        saved_machine = saved[0]
        # Validate the machine via the domain model to ensure it can be reconstructed.
        reconstructed = Machine.model_validate(saved_machine.model_dump())
        assert str(reconstructed.instance_type) == "c5.2xlarge"
        assert reconstructed.price_type == "spot"

    @pytest.mark.asyncio
    async def test_instance_type_unchanged_when_equal_no_save(self) -> None:
        """When instance_type is already correct no unnecessary save occurs."""
        svc = _make_service()

        saved: list = []
        uow_cm = MagicMock()
        uow_cm.__enter__ = lambda s: uow_cm
        uow_cm.__exit__ = MagicMock(return_value=False)
        uow_cm.machines = MagicMock()
        uow_cm.machines.save = lambda m: saved.append(m)
        uow_cm.requests = MagicMock()
        uow_cm.requests.save = MagicMock()
        svc.uow_factory.create_unit_of_work = MagicMock(return_value=uow_cm)

        db_machine = self._make_full_machine("pod-xyz", instance_type="m5.large")
        provider_machine = self._make_full_machine("pod-xyz", instance_type="m5.large")

        req = MagicMock()
        req.request_id = MagicMock()
        req.request_id.__str__ = lambda s: "req-2"
        req.record_status_check = MagicMock(return_value=req)

        updated, _ = await svc.sync_machines_with_provider(req, [db_machine], [provider_machine])

        # Nothing changed → no machine save (only the request timestamp save).
        assert len(updated) == 1
        assert len(saved) == 0
