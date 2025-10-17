from typing import Optional
from unittest.mock import Mock

import pytest

from application.dto.queries import GetTemplateQuery
from application.queries.handlers import GetTemplateHandler
from domain.base.ports.container_port import ContainerPort
from domain.template.factory import TemplateFactory
from infrastructure.template.configuration_manager import TemplateConfigurationManager
from infrastructure.template.dtos import TemplateDTO
from providers.aws.domain.template.aws_template_aggregate import AWSTemplate


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
        configuration=config,
    )

    services = {
        TemplateConfigurationManager: _FakeTemplateManager(template_dto),
        TemplateFactory: TemplateFactory(),
    }
    container = _FakeContainer(services)

    handler = GetTemplateHandler(logger=Mock(), error_handler=None, container=container)
    query = GetTemplateQuery(template_id="EC2FleetInstantTemplate")

    template = await handler.execute_query(query)

    assert isinstance(template, AWSTemplate)
    assert template.launch_template_id == "lt-03fa223ab9c3733a2"
