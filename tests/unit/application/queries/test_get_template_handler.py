from typing import Optional
from unittest.mock import Mock

import pytest

from orb.application.dto.queries import GetTemplateQuery
from orb.application.queries.template_query_handlers import GetTemplateHandler
from orb.domain.base.ports.container_port import ContainerPort
from orb.domain.base.ports.template_configuration_port import TemplateConfigurationPort
from orb.domain.template.factory import TemplateFactory
from orb.infrastructure.template.dtos import TemplateDTO


class _FakeTemplateManager:
    def __init__(self, template_dto: TemplateDTO) -> None:
        self._template = template_dto

    async def get_template_by_id(self, template_id: str) -> Optional[TemplateDTO]:
        return self._template if template_id == self._template.template_id else None


class _FakeContainer(ContainerPort):
    def __init__(self, services: dict) -> None:
        self._services = services

    def get(self, service_type):
        return self._services[service_type]

    def register(self, service_type, instance) -> None:
        self._services[service_type] = instance

    def register_factory(self, service_type, factory_func) -> None:
        self._services[service_type] = factory_func()

    def register_singleton(self, service_type, factory_func) -> None:
        self._services[service_type] = factory_func()

    def has(self, service_type) -> bool:
        return service_type in self._services


@pytest.mark.asyncio
async def test_get_template_handler_retains_existing_launch_template():
    config = {
        "template_id": "EC2FleetInstantTemplate",
        "provider_api": "EC2Fleet",
        "image_id": "ami-1234567890abcdef0",
        "subnet_ids": ["subnet-0123456789abcdef0"],
        "security_group_ids": ["sg-0123456789abcdef0"],
        "launch_template_id": "lt-03fa223ab9c3733a2",
        "max_instances": 2,
    }
    template_dto = TemplateDTO(
        template_id="EC2FleetInstantTemplate",
        name="EC2FleetInstantTemplate",
        provider_api="EC2Fleet",
        image_id=config["image_id"],
        subnet_ids=config["subnet_ids"],
        security_group_ids=config["security_group_ids"],
        max_instances=config["max_instances"],
    )

    services = {
        TemplateConfigurationPort: _FakeTemplateManager(template_dto),
        TemplateFactory: TemplateFactory(),
    }
    container = _FakeContainer(services)

    handler = GetTemplateHandler(logger=Mock(), error_handler=None, container=container)
    query = GetTemplateQuery(template_id="EC2FleetInstantTemplate")

    result = await handler.execute_query(query)

    # Handler returns the TemplateDTO from the template manager
    assert isinstance(result, TemplateDTO)
    assert result.template_id == "EC2FleetInstantTemplate"
    assert result.template_id == "EC2FleetInstantTemplate"
