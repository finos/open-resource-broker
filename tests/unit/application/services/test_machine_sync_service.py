"""Behavior tests for MachineSyncService request-context propagation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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
                    "pending_resource_cleanup": {
                        "resource_group": "orb-test-rg",
                        "resource_id": "vmss-demo",
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


@pytest.mark.unit
@pytest.mark.application
@pytest.mark.asyncio
async def test_fetch_provider_machines_for_return_rebuilds_cyclecloud_mapping_from_follow_up_context():
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
    request.request_type.value = "return"
    request.resource_ids = []
    request.machine_ids = ["cluster-dynamic-op-0"]
    request.provider_api = "CycleCloud"
    request.template_id = "azure-cyclecloud-test"
    request.request_id = "ret-00000000-0000-0000-0000-000000000003"
    request.provider_name = "azure-default"
    request.provider_type = "azure"
    request.metadata = {}
    request.provider_data = {
        "follow_up_context": {
            "resource_group": "orb-test-rg",
            "cluster_name": "contoso-slurm-lab-cluster",
            "cyclecloud_url": "https://cyclecloud.example.com",
        }
    }

    await service.fetch_provider_machines(request, db_machines=[])

    operation = provider_registry_service.execute_operation.await_args.args[1]
    assert operation.operation_type == OperationType.GET_INSTANCE_STATUS
    assert operation.parameters["provider_api"] == "CycleCloud"
    assert operation.parameters["resource_id"] == "contoso-slurm-lab-cluster"
    assert operation.parameters["resource_mapping"] == {
        "cluster-dynamic-op-0": ("contoso-slurm-lab-cluster", 1)
    }


@pytest.mark.unit
@pytest.mark.application
@pytest.mark.asyncio
async def test_sync_machines_with_provider_updates_existing_resource_id():
    command_bus = MagicMock()
    uow_factory = MagicMock()
    config_port = MagicMock()
    logger = MagicMock()

    service = MachineSyncService(
        command_bus=command_bus,
        uow_factory=uow_factory,
        config_port=config_port,
        logger=logger,
        provider_registry_service=MagicMock(),
    )

    request = MagicMock()
    request.template_id = "azure-cyclecloud-test"
    request.request_id = "req-00000000-0000-0000-0000-000000000004"
    request.provider_type = "azure"
    request.provider_name = "azure-default"
    request.provider_api = "CycleCloud"

    existing = MagicMock()
    existing.machine_id.value = "node-1"
    existing.status = MagicMock()
    existing.private_ip = None
    existing.public_ip = None
    existing.name = "node-1"
    existing.resource_id = "req-legacy-resource-id"
    existing.private_dns_name = None
    existing.public_dns_name = None
    existing.price_type = None
    existing.subnet_id = None
    existing.security_group_ids = []
    existing.vpc_id = None
    existing.launch_time = None
    existing.version = 2
    existing.model_dump.return_value = {
        "machine_id": existing.machine_id,
        "name": "node-1",
        "template_id": "azure-cyclecloud-test",
        "request_id": "req-00000000-0000-0000-0000-000000000004",
        "provider_type": "azure",
        "provider_name": "azure-default",
        "provider_api": "CycleCloud",
        "resource_id": "req-legacy-resource-id",
        "instance_type": "unknown",
        "image_id": "unknown",
        "price_type": None,
        "private_ip": None,
        "public_ip": None,
        "private_dns_name": None,
        "public_dns_name": None,
        "subnet_id": None,
        "security_group_ids": [],
        "vpc_id": None,
        "status": "running",
        "launch_time": None,
        "metadata": {},
        "provider_data": {},
        "version": 2,
    }

    provider_machine = MagicMock()
    provider_machine.machine_id.value = "node-1"
    provider_machine.status = existing.status
    provider_machine.private_ip = None
    provider_machine.public_ip = None
    provider_machine.name = "node-1"
    provider_machine.resource_id = "contoso-slurm-lab-cluster"
    provider_machine.private_dns_name = None
    provider_machine.public_dns_name = None
    provider_machine.price_type = None
    provider_machine.subnet_id = None
    provider_machine.security_group_ids = []
    provider_machine.vpc_id = None
    provider_machine.launch_time = None

    updated_machine = MagicMock()

    uow = MagicMock()
    uow.__enter__.return_value = uow
    uow.__exit__.return_value = None
    uow_factory.create_unit_of_work.return_value = uow

    with patch(
        "orb.application.services.machine_sync_service.Machine.model_validate",
        return_value=updated_machine,
    ) as model_validate:
        synced, _ = await service.sync_machines_with_provider(
            request=request,
            db_machines=[existing],
            provider_machines=[provider_machine],
        )

    assert synced == [updated_machine]
    saved_machine = uow.machines.save.call_args.args[0]
    assert saved_machine is updated_machine
    validated_payload = model_validate.call_args.args[0]
    assert validated_payload["resource_id"] == "contoso-slurm-lab-cluster"


@pytest.mark.unit
@pytest.mark.application
@pytest.mark.asyncio
async def test_fetch_provider_machines_preserves_instance_owned_resource_id_for_multi_resource_requests():
    command_bus = MagicMock()
    uow_factory = MagicMock()
    config_port = MagicMock()
    config_port.get_provider_instance_config.return_value = MagicMock()
    logger = MagicMock()
    provider_registry_service = MagicMock()
    provider_registry_service.execute_operation = AsyncMock(
        return_value=MagicMock(
            success=True,
            data={
                "instances": [
                    {
                        "instance_id": "vmss-b_000001",
                        "status": "running",
                        "instance_type": "Standard_D4s_v5",
                        "provider_type": "azure",
                        "provider_data": {
                            "resource_id": "vmss-b",
                        },
                    }
                ]
            },
            metadata={},
        )
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
    request.resource_ids = ["vmss-a", "vmss-b"]
    request.machine_ids = []
    request.provider_api = "VMSS"
    request.template_id = "azure-cheapest-vmss"
    request.request_id = "req-00000000-0000-0000-0000-000000000003"
    request.provider_name = "azure-default"
    request.provider_type = "azure"
    request.metadata = {}
    request.provider_data = {}

    provider_machines, _metadata = await service.fetch_provider_machines(request, db_machines=[])

    assert len(provider_machines) == 1
    assert provider_machines[0].resource_id == "vmss-b"
