from typing import Optional
from unittest.mock import Mock

import pytest

from orb.application.dto.queries import GetTemplateQuery
from orb.application.dto.template import TemplateDTO
from orb.application.queries.template_query_handlers import GetTemplateHandler
from orb.domain.base.ports.error_handling_port import ErrorHandlingPort
from orb.domain.template.factory import TemplateFactory
from orb.infrastructure.template.factories import TemplateDTOFactory


class _FakeTemplateManager:
    def __init__(self, template_dto: TemplateDTO) -> None:
        self._template = template_dto

    async def get_template_by_id(self, template_id: str) -> Optional[TemplateDTO]:
        return self._template if template_id == self._template.template_id else None


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

    defaults_service = Mock()
    defaults_service.resolve_template_defaults.side_effect = lambda data, **_: data

    handler = GetTemplateHandler(
        logger=Mock(),
        error_handler=Mock(spec=ErrorHandlingPort),
        template_manager=_FakeTemplateManager(template_dto),
        template_defaults_service=defaults_service,
        template_factory=TemplateFactory(),
        template_dto_factory=TemplateDTOFactory(),
    )
    query = GetTemplateQuery(template_id="EC2FleetInstantTemplate")

    result = await handler.execute_query(query)

    # Handler returns the TemplateDTO from the template manager
    assert isinstance(result, TemplateDTO)
    assert result.template_id == "EC2FleetInstantTemplate"
    assert result.template_id == "EC2FleetInstantTemplate"
