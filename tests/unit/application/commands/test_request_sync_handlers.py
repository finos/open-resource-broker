from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.commands.request_sync_handlers import PopulateMachineIdsHandler
from orb.application.dto.commands import PopulateMachineIdsCommand
from orb.domain.base.operations import OperationType


@pytest.mark.unit
@pytest.mark.application
@pytest.mark.asyncio
async def test_populate_machine_ids_forwards_provider_context_for_azure():
    uow = MagicMock()
    uow.__enter__.return_value = uow
    uow.__exit__.return_value = None

    request = MagicMock()
    request.needs_machine_id_population.return_value = True
    request.resource_ids = ["vmss-demo"]
    request.provider_api = "VMSS"
    request.template_id = "azure-cheapest-vmss"
    request.provider_name = "azure-default"
    request.request_id = "req-00000000-0000-0000-0000-000000000001"
    request.metadata = {"provider_selection_reason": "configured-default"}
    request.provider_data = {
        "follow_up_context": {
            "resource_group": "orb-test-rg",
            "vmss_name": "vmss-demo",
        }
    }

    updated_request = MagicMock()
    request.update_machine_ids.return_value = updated_request
    uow.requests.get_by_id.return_value = request

    uow_factory = MagicMock()
    uow_factory.create_unit_of_work.return_value = uow

    provider_selection_port = MagicMock()
    provider_selection_port.execute_operation = AsyncMock(
        return_value=MagicMock(success=True, data={"instances": [{"instance_id": "vm-1"}]})
    )

    container = MagicMock()
    logger = MagicMock()
    event_publisher = MagicMock()
    error_handler = MagicMock()

    handler = PopulateMachineIdsHandler(
        uow_factory=uow_factory,
        logger=logger,
        container=container,
        event_publisher=event_publisher,
        error_handler=error_handler,
        provider_selection_port=provider_selection_port,
    )

    await handler.execute_command(PopulateMachineIdsCommand(request_id=str(request.request_id)))

    operation = provider_selection_port.execute_operation.await_args.args[1]
    assert operation.operation_type == OperationType.DESCRIBE_RESOURCE_INSTANCES
    assert operation.parameters["resource_ids"] == ["vmss-demo"]
    assert operation.parameters["provider_api"] == "VMSS"
    assert operation.parameters["template_id"] == "azure-cheapest-vmss"
    assert operation.parameters["request_metadata"]["resource_group"] == "orb-test-rg"
    assert operation.parameters["request_metadata"]["vmss_name"] == "vmss-demo"
    assert (
        operation.parameters["request_metadata"]["provider_selection_reason"]
        == "configured-default"
    )
    request.update_machine_ids.assert_called_once_with(["vm-1"])
    uow.requests.save.assert_called_once_with(updated_request)
