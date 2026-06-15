"""Unit tests for MachineSyncService — basic behaviour and OperationOutcome awareness.

The sync service does not directly call acquire/return_machines/get_status; it uses
execute_operation via ProviderRegistryService.  These tests cover the core paths
that interact with the outcome-aware request status logic.
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
