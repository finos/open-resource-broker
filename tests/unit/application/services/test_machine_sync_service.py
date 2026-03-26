"""Behavior tests for MachineSyncService request-context propagation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.services.machine_sync_service import MachineSyncService
from orb.domain.base.operations import OperationType


@pytest.mark.unit
@pytest.mark.application
@pytest.mark.asyncio
async def test_fetch_provider_machines_replays_persisted_request_metadata():
    command_bus = MagicMock()
    uow_factory = MagicMock()
    config_port = MagicMock()
    config_port.get_provider_instance_config.return_value = MagicMock()
    logger = MagicMock()
    provider_registry_service = MagicMock()
    provider_registry_service.execute_operation = AsyncMock(
        return_value=MagicMock(success=True, data={"instances": []}, metadata={})
    )

    service = MachineSyncService(
        command_bus=command_bus,
        uow_factory=uow_factory,
        config_port=config_port,
        logger=logger,
        provider_registry_service=provider_registry_service,
    )

    request = MagicMock()
    request.request_type.value = "acquire"
    request.resource_ids = ["vmss-demo"]
    request.machine_ids = []
    request.provider_api = "VMSS"
    request.template_id = "azure-cheapest-vmss"
    request.request_id = "req-00000000-0000-0000-0000-000000000001"
    request.provider_name = "azure-default"
    request.provider_type = "azure"
    request.metadata = {"provider_selection_reason": "configured-default"}
    request.provider_data = {
        "follow_up_context": {
            "resource_group": "orb-test-rg",
            "deployment_name": "dep-1234",
        }
    }

    await service.fetch_provider_machines(request, db_machines=[])

    operation = provider_registry_service.execute_operation.await_args.args[1]
    assert operation.operation_type == OperationType.DESCRIBE_RESOURCE_INSTANCES
    assert operation.parameters["request_metadata"]["resource_group"] == "orb-test-rg"
    assert operation.parameters["request_metadata"]["deployment_name"] == "dep-1234"
    assert (
        operation.parameters["request_metadata"]["provider_selection_reason"]
        == "configured-default"
    )


@pytest.mark.unit
@pytest.mark.application
@pytest.mark.asyncio
async def test_fetch_provider_machines_for_return_forwards_azure_resource_mapping():
    command_bus = MagicMock()
    uow_factory = MagicMock()
    config_port = MagicMock()
    config_port.get_provider_instance_config.return_value = MagicMock()
    logger = MagicMock()
    provider_registry_service = MagicMock()
    provider_registry_service.execute_operation = AsyncMock(
        return_value=MagicMock(success=True, data={"machines": []}, metadata={})
    )

    service = MachineSyncService(
        command_bus=command_bus,
        uow_factory=uow_factory,
        config_port=config_port,
        logger=logger,
        provider_registry_service=provider_registry_service,
    )

    request = MagicMock()
    request.request_type.value = "return"
    request.resource_ids = []
    request.machine_ids = ["vmss-demo_000001"]
    request.provider_api = "VMSS"
    request.template_id = "azure-cheapest-vmss"
    request.request_id = "ret-00000000-0000-0000-0000-000000000001"
    request.provider_name = "azure-default"
    request.provider_type = "azure"
    request.metadata = {"provider_selection_reason": "configured-default"}
    request.provider_data = {"follow_up_context": {"resource_group": "orb-test-rg"}}

    db_machine = MagicMock()
    db_machine.machine_id.value = "vmss-demo_000001"
    db_machine.resource_id = "vmss-demo"

    await service.fetch_provider_machines(request, db_machines=[db_machine])

    operation = provider_registry_service.execute_operation.await_args.args[1]
    assert operation.operation_type == OperationType.GET_INSTANCE_STATUS
    assert operation.parameters["provider_api"] == "VMSS"
    assert operation.parameters["resource_id"] == "vmss-demo"
    assert operation.parameters["resource_mapping"] == {"vmss-demo_000001": ("vmss-demo", 1)}
    assert operation.parameters["request_metadata"]["resource_group"] == "orb-test-rg"


@pytest.mark.unit
@pytest.mark.application
@pytest.mark.asyncio
async def test_fetch_provider_machines_for_return_rebuilds_vmss_mapping_from_follow_up_context():
    command_bus = MagicMock()
    uow_factory = MagicMock()
    config_port = MagicMock()
    config_port.get_provider_instance_config.return_value = MagicMock()
    logger = MagicMock()
    provider_registry_service = MagicMock()
    provider_registry_service.execute_operation = AsyncMock(
        return_value=MagicMock(success=True, data={"machines": []}, metadata={})
    )

    service = MachineSyncService(
        command_bus=command_bus,
        uow_factory=uow_factory,
        config_port=config_port,
        logger=logger,
        provider_registry_service=provider_registry_service,
    )

    request = MagicMock()
    request.request_type.value = "return"
    request.resource_ids = []
    request.machine_ids = ["vmss-demo_000001"]
    request.provider_api = "VMSS"
    request.template_id = "azure-cheapest-vmss"
    request.request_id = "ret-00000000-0000-0000-0000-000000000002"
    request.provider_name = "azure-default"
    request.provider_type = "azure"
    request.metadata = {"provider_selection_reason": "configured-default"}
    request.provider_data = {
        "follow_up_context": {
            "resource_group": "orb-test-rg",
            "termination_requests": [
                {
                    "pending_vmss_cleanup": {
                        "resource_group": "orb-test-rg",
                        "vmss_name": "vmss-demo",
                        "machine_ids": ["vmss-demo_000001"],
                        "delete_vmss_when_empty": True,
                    }
                }
            ],
        }
    }

    await service.fetch_provider_machines(request, db_machines=[])

    operation = provider_registry_service.execute_operation.await_args.args[1]
    assert operation.operation_type == OperationType.GET_INSTANCE_STATUS
    assert operation.parameters["provider_api"] == "VMSS"
    assert operation.parameters["resource_id"] == "vmss-demo"
    assert operation.parameters["resource_mapping"] == {"vmss-demo_000001": ("vmss-demo", 1)}
    assert operation.parameters["request_metadata"]["resource_group"] == "orb-test-rg"
