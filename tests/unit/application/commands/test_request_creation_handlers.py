from unittest.mock import AsyncMock, Mock

import pytest

from orb.application.commands.request_creation_handlers import CreateMachineRequestHandler
from orb.application.dto.queries import GetTemplateQuery
from orb.domain.template.factory import TemplateFactory
from orb.domain.template.template_aggregate import Template
from orb.infrastructure.template.dtos import TemplateDTO


@pytest.mark.asyncio
async def test_load_template_converts_template_dto_to_domain_template() -> None:
    query_bus = Mock()
    query_bus.execute = AsyncMock(
        return_value=TemplateDTO(
            template_id="azure-cheapest-vmss",
            name="azure-cheapest-vmss",
            provider_type="azure",
            provider_name="azure-default",
            provider_api="VMSS",
            vm_size="Standard_D4s_v5",
            provider_config={
                "resource_group": "rg-test",
                "location": "eastus2",
                "image": {
                    "publisher": "Canonical",
                    "offer": "0001-com-ubuntu-server-jammy",
                    "sku": "22_04-lts-gen2",
                    "version": "latest",
                },
            },
        )
    )
    container = Mock()
    container.get.return_value = TemplateFactory()

    handler = CreateMachineRequestHandler(
        uow_factory=Mock(),
        logger=Mock(),
        container=container,
        event_publisher=Mock(),
        error_handler=Mock(),
        query_bus=query_bus,
        provider_selection_port=Mock(),
        provisioning_service=Mock(),
    )

    template = await handler._load_template("azure-cheapest-vmss")

    query_bus.execute.assert_awaited_once_with(GetTemplateQuery(template_id="azure-cheapest-vmss"))
    assert isinstance(template, Template)
    assert template.template_id == "azure-cheapest-vmss"
    assert template.provider_api == "VMSS"
    assert "provider_config" not in template.model_dump(mode="json", exclude_none=True)
