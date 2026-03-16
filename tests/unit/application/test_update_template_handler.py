"""Unit tests for UpdateTemplateHandler - TDD for instance_type/image_id support."""

from unittest.mock import Mock

import pytest

from orb.application.commands.template_handlers import UpdateTemplateHandler
from orb.application.template.commands import UpdateTemplateCommand
from orb.domain.base.ports import (
    ContainerPort,
    ErrorHandlingPort,
    EventPublisherPort,
    LoggingPort,
)
from orb.domain.template.template_aggregate import Template


def _make_template(**kwargs) -> Template:
    defaults = dict(
        template_id="tpl-1",
        name="base",
        provider_api="EC2Fleet",
        image_id="ami-000",
        instance_type="t3.medium",
    )
    defaults.update(kwargs)
    return Template(**defaults)


def _make_uow_factory(template: Template):
    mock_uow = Mock()
    mock_uow.templates.get_by_id.return_value = template
    mock_uow.templates.save = Mock()
    mock_uow.commit = Mock()
    mock_uow.__enter__ = Mock(return_value=mock_uow)
    mock_uow.__exit__ = Mock(return_value=False)

    mock_factory = Mock()
    mock_factory.create_unit_of_work.return_value = mock_uow
    return mock_factory, mock_uow


def _make_handler(uow_factory) -> UpdateTemplateHandler:
    mock_container = Mock(spec=ContainerPort)
    # validate_template_config returns no errors
    mock_template_port = Mock()
    mock_template_port.validate_template_config.return_value = []
    mock_container.get.return_value = mock_template_port

    return UpdateTemplateHandler(
        uow_factory=uow_factory,
        logger=Mock(spec=LoggingPort),
        container=mock_container,
        event_publisher=Mock(spec=EventPublisherPort),
        error_handler=Mock(spec=ErrorHandlingPort),
    )


@pytest.mark.asyncio
async def test_update_instance_type():
    """Command with instance_type='t3.large' results in saved template having instance_type='t3.large'."""
    template = _make_template(instance_type="t3.medium")
    uow_factory, mock_uow = _make_uow_factory(template)
    handler = _make_handler(uow_factory)

    command = UpdateTemplateCommand(template_id="tpl-1", instance_type="t3.large")
    await handler.handle(command)

    saved = mock_uow.templates.save.call_args[0][0]
    assert saved.instance_type == "t3.large"
    assert command.updated is True


@pytest.mark.asyncio
async def test_update_image_id():
    """Command with image_id='ami-123' results in saved template having image_id='ami-123'."""
    template = _make_template(image_id="ami-000")
    uow_factory, mock_uow = _make_uow_factory(template)
    handler = _make_handler(uow_factory)

    command = UpdateTemplateCommand(template_id="tpl-1", image_id="ami-123")
    await handler.handle(command)

    saved = mock_uow.templates.save.call_args[0][0]
    assert saved.image_id == "ami-123"
    assert command.updated is True


@pytest.mark.asyncio
async def test_update_name_immutable_pattern():
    """The template saved to the repo is the updated instance, not the original."""
    original_name = "original-name"
    template = _make_template(name=original_name)
    uow_factory, mock_uow = _make_uow_factory(template)
    handler = _make_handler(uow_factory)

    command = UpdateTemplateCommand(template_id="tpl-1", name="new-name")
    await handler.handle(command)

    saved = mock_uow.templates.save.call_args[0][0]
    assert saved.name == "new-name"
    assert saved is not template  # immutable — a new instance was produced


@pytest.mark.asyncio
async def test_update_skips_none_instance_type():
    """When instance_type is None, update_instance_type is not called and original value is preserved."""
    template = _make_template(instance_type="t3.medium")
    uow_factory, mock_uow = _make_uow_factory(template)
    handler = _make_handler(uow_factory)

    command = UpdateTemplateCommand(template_id="tpl-1", instance_type=None)
    await handler.handle(command)

    saved = mock_uow.templates.save.call_args[0][0]
    assert saved.instance_type == "t3.medium"
