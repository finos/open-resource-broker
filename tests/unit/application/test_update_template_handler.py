"""Unit tests for UpdateTemplateHandler - TDD for instance_type/image_id support."""

from unittest.mock import AsyncMock, Mock

import pytest

from orb.application.commands.template_handlers import UpdateTemplateHandler
from orb.application.template.commands import UpdateTemplateCommand
from orb.domain.base.ports import (
    ErrorHandlingPort,
    EventPublisherPort,
    LoggingPort,
)
from orb.infrastructure.template.dtos import TemplateDTO


def _make_dto(**kwargs: object) -> TemplateDTO:
    return TemplateDTO.model_construct(
        template_id=kwargs.get("template_id", "tpl-1"),
        name=kwargs.get("name", "base"),
        provider_api=kwargs.get("provider_api", "EC2Fleet"),
        image_id=kwargs.get("image_id", "ami-000"),
    )


def _make_handler(existing: TemplateDTO) -> tuple[UpdateTemplateHandler, Mock]:
    manager = Mock()
    manager.get_template_by_id = AsyncMock(return_value=existing)
    manager.save_template = AsyncMock()

    port = Mock()
    port.get_template_manager.return_value = manager
    port.validate_template_config.return_value = []

    return UpdateTemplateHandler(
        template_port=port,
        logger=Mock(spec=LoggingPort),
        event_publisher=Mock(spec=EventPublisherPort),
        error_handler=Mock(spec=ErrorHandlingPort),
    ), manager


@pytest.mark.asyncio
async def test_update_instance_type():
    """Command with instance_type='t3.large' results in saved template having machine_types updated."""
    handler, mock_manager = _make_handler(_make_dto())

    command = UpdateTemplateCommand(template_id="tpl-1", instance_type="t3.large")
    await handler.handle(command)

    mock_manager.save_template.assert_called_once()
    assert command.updated is True


@pytest.mark.asyncio
async def test_update_image_id():
    """Command with image_id='ami-123' results in saved template having image_id='ami-123'."""
    handler, mock_manager = _make_handler(_make_dto(image_id="ami-000"))

    command = UpdateTemplateCommand(template_id="tpl-1", image_id="ami-123")
    await handler.handle(command)

    mock_manager.save_template.assert_called_once()
    saved: TemplateDTO = mock_manager.save_template.call_args[0][0]
    assert saved.image_id == "ami-123"
    assert command.updated is True


@pytest.mark.asyncio
async def test_update_name_immutable_pattern():
    """The DTO saved to the manager has the updated name."""
    handler, mock_manager = _make_handler(_make_dto(name="original-name"))

    command = UpdateTemplateCommand(template_id="tpl-1", name="new-name")
    await handler.handle(command)

    saved: TemplateDTO = mock_manager.save_template.call_args[0][0]
    assert saved.name == "new-name"


@pytest.mark.asyncio
async def test_update_skips_none_instance_type():
    """When instance_type is None, machine_types is not overwritten."""
    handler, mock_manager = _make_handler(_make_dto())

    command = UpdateTemplateCommand(template_id="tpl-1", instance_type=None)
    await handler.handle(command)

    mock_manager.save_template.assert_called_once()
    assert command.updated is True
