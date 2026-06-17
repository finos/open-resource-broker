"""Behavior tests for DeprovisioningOrchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.services.deprovisioning_orchestrator import DeprovisioningOrchestrator
from orb.domain.base.operations import OperationResult, OperationType


@pytest.mark.unit
@pytest.mark.application
@pytest.mark.asyncio
async def test_process_resource_group_forwards_merged_request_metadata():
    uow_factory = MagicMock()
    logger = MagicMock()
    container = MagicMock()
    query_bus = MagicMock()
    provider_selection_port = MagicMock()

    scheduler = MagicMock()
    scheduler.format_template_for_provider.return_value = {
        "template_id": "azure-vmss-test",
        "provider_api": "VMSS",
    }
    config_manager = MagicMock()
    container.get.side_effect = [scheduler, config_manager]

    template = MagicMock()
    template.template_id = "azure-vmss-test"
    query_bus.execute = AsyncMock(return_value=template)
    provider_selection_port.execute_operation = AsyncMock(
        return_value=OperationResult.success_result(
            data={"success": True},
            metadata={"provider_data": {}},
        )
    )

    orchestrator = DeprovisioningOrchestrator(
        uow_factory=uow_factory,
        logger=logger,
        container=container,
        query_bus=query_bus,
        provider_selection_port=provider_selection_port,
    )

    machine = MagicMock()
    machine.machine_id.value = "vm-1"
    machine.template_id = "azure-vmss-test"
    machine.request_id = "req-origin"

    request = MagicMock()
    request.request_id = "ret-1"
    request.metadata = {"provider_selection_reason": "configured-default"}
    request.provider_data = {
        "follow_up_context": {
            "resource_group": "orb-test-rg",
            "cyclecloud_credential_secret_path": "op://vault/item/field",
        }
    }

    result = await orchestrator._process_resource_group(
        provider_name="azure-default",
        provider_api="VMSS",
        resource_id="vmss-demo",
        machines=[machine],
        request=request,
    )

    assert result["success"] is True
    operation = provider_selection_port.execute_operation.await_args.args[1]
    assert operation.operation_type == OperationType.TERMINATE_INSTANCES
    assert operation.parameters["request_metadata"]["resource_group"] == "orb-test-rg"
    assert (
        operation.parameters["request_metadata"]["cyclecloud_credential_secret_path"]
        == "op://vault/item/field"
    )
    assert (
        operation.parameters["request_metadata"]["provider_selection_reason"]
        == "configured-default"
    )
