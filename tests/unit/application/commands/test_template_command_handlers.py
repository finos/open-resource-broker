"""Unit tests for template command handlers — TDD for store-unification fix."""

from unittest.mock import AsyncMock, Mock

import pytest

from orb.application.commands.template_handlers import (
    CreateTemplateHandler,
    DeleteTemplateHandler,
    UpdateTemplateHandler,
)
from orb.application.template.commands import (
    CreateTemplateCommand,
    DeleteTemplateCommand,
    UpdateTemplateCommand,
)
from orb.domain.base.exceptions import DuplicateError, EntityNotFoundError
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


def _make_manager(existing: TemplateDTO | None = None) -> Mock:
    manager = Mock()
    manager.get_template_by_id = AsyncMock(return_value=existing)
    manager.save_template = AsyncMock()
    manager.delete_template = AsyncMock()
    return manager


def _make_template_port(manager: Mock, validation_errors: list[str] | None = None) -> Mock:
    port = Mock()
    port.get_template_manager.return_value = manager
    port.validate_template_config.return_value = validation_errors or []
    return port


def _make_create_handler(template_port: Mock) -> CreateTemplateHandler:
    container = Mock()
    container.get.return_value = template_port
    return CreateTemplateHandler(
        template_port=template_port,
        logger=Mock(spec=LoggingPort),
        event_publisher=Mock(spec=EventPublisherPort),
        error_handler=Mock(spec=ErrorHandlingPort),
    )


def _make_update_handler(template_port: Mock) -> UpdateTemplateHandler:
    container = Mock()
    container.get.return_value = template_port
    return UpdateTemplateHandler(
        template_port=template_port,
        logger=Mock(spec=LoggingPort),
        event_publisher=Mock(spec=EventPublisherPort),
        error_handler=Mock(spec=ErrorHandlingPort),
    )


def _make_delete_handler(template_port: Mock) -> DeleteTemplateHandler:
    return DeleteTemplateHandler(
        template_port=template_port,
        logger=Mock(spec=LoggingPort),
        event_publisher=Mock(spec=EventPublisherPort),
        error_handler=Mock(spec=ErrorHandlingPort),
    )


# ---------------------------------------------------------------------------
# CreateTemplateHandler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_saves_via_manager():
    """save_template is called on the manager and command.created is True."""
    manager = _make_manager(existing=None)
    port = _make_template_port(manager)
    handler = _make_create_handler(port)

    command = CreateTemplateCommand(
        template_id="tpl-1",
        provider_api="EC2Fleet",
        image_id="ami-000",
    )
    await handler.handle(command)

    manager.save_template.assert_called_once()
    saved: TemplateDTO = manager.save_template.call_args[0][0]
    assert saved.template_id == "tpl-1"
    assert command.created is True


@pytest.mark.asyncio
async def test_create_duplicate_raises():
    """BusinessRuleError is raised when template already exists."""
    existing = _make_dto()
    manager = _make_manager(existing=existing)
    port = _make_template_port(manager)
    handler = _make_create_handler(port)

    command = CreateTemplateCommand(
        template_id="tpl-1",
        provider_api="EC2Fleet",
        image_id="ami-000",
    )

    with pytest.raises(DuplicateError):
        await handler.handle(command)

    manager.save_template.assert_not_called()


# ---------------------------------------------------------------------------
# UpdateTemplateHandler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_saves_via_manager():
    """save_template is called with updated fields and command.updated is True."""
    existing = _make_dto(name="old-name")
    manager = _make_manager(existing=existing)
    port = _make_template_port(manager)
    handler = _make_update_handler(port)

    command = UpdateTemplateCommand(template_id="tpl-1", name="new-name")
    await handler.handle(command)

    manager.save_template.assert_called_once()
    saved: TemplateDTO = manager.save_template.call_args[0][0]
    assert saved.name == "new-name"
    assert command.updated is True


@pytest.mark.asyncio
async def test_update_not_found_raises():
    """EntityNotFoundError is raised when template does not exist."""
    manager = _make_manager(existing=None)
    port = _make_template_port(manager)
    handler = _make_update_handler(port)

    command = UpdateTemplateCommand(template_id="tpl-missing")
    with pytest.raises(EntityNotFoundError):
        await handler.handle(command)

    manager.save_template.assert_not_called()


# ---------------------------------------------------------------------------
# DeleteTemplateHandler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_deletes_via_manager():
    """delete_template is called on the manager and command.deleted is True."""
    existing = _make_dto()
    manager = _make_manager(existing=existing)
    port = _make_template_port(manager)
    handler = _make_delete_handler(port)

    command = DeleteTemplateCommand(template_id="tpl-1")
    await handler.handle(command)

    manager.delete_template.assert_called_once_with("tpl-1")
    assert command.deleted is True


@pytest.mark.asyncio
async def test_delete_not_found_raises():
    """EntityNotFoundError is raised when template does not exist."""
    manager = _make_manager(existing=None)
    port = _make_template_port(manager)
    handler = _make_delete_handler(port)

    command = DeleteTemplateCommand(template_id="tpl-missing")
    with pytest.raises(EntityNotFoundError):
        await handler.handle(command)

    manager.delete_template.assert_not_called()


# ---------------------------------------------------------------------------
# CreateTemplateHandler — validation errors path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_validation_errors_sets_created_false() -> None:
    """When validate_template_config returns errors, command.created=False and errors are set."""
    manager = _make_manager(existing=None)
    port = _make_template_port(manager, validation_errors=["image_id is required"])
    handler = _make_create_handler(port)

    command = CreateTemplateCommand(
        template_id="tpl-bad",
        provider_api="EC2Fleet",
        image_id="ami-000",
    )
    await handler.handle(command)

    assert command.created is False
    assert command.validation_errors == ["image_id is required"]
    manager.save_template.assert_not_called()


# ---------------------------------------------------------------------------
# UpdateTemplateHandler — validation errors path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_validation_errors_sets_updated_false() -> None:
    """When validate_template_config returns errors, command.updated=False and errors are set."""
    manager = _make_manager(existing=_make_dto())
    port = _make_template_port(manager, validation_errors=["invalid field"])
    handler = _make_update_handler(port)

    command = UpdateTemplateCommand(
        template_id="tpl-1",
        configuration={"bad_field": "x"},
    )
    await handler.handle(command)

    assert command.updated is False
    assert command.validation_errors == ["invalid field"]
    manager.save_template.assert_not_called()
