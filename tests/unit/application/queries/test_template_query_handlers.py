"""Unit tests for GetTemplateHandler, ValidateTemplateHandler, GetConfigurationHandler,
and additional ListTemplatesHandler coverage."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from orb.application.dto.queries import (
    GetConfigurationQuery,
    GetTemplateQuery,
    ListTemplatesQuery,
    ValidateTemplateQuery,
)
from orb.application.queries.template_query_handlers import (
    GetConfigurationHandler,
    GetTemplateHandler,
    ListTemplatesHandler,
    ValidateTemplateHandler,
)
from orb.domain.base.exceptions import EntityNotFoundError
from orb.domain.base.ports import ErrorHandlingPort, LoggingPort
from orb.domain.base.ports.template_configuration_port import TemplateConfigurationPort
from orb.infrastructure.template.dtos import TemplateDTO

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_template_dto(template_id: str = "tpl-1") -> TemplateDTO:
    return TemplateDTO.model_construct(
        template_id=template_id,
        name=template_id,
        provider_api="EC2Fleet",
        image_id="ami-000",
    )


def _make_container(template_manager=None, config_port=None, has_defaults: bool = False):
    container = MagicMock()

    def _get(service_type):
        if service_type is TemplateConfigurationPort:
            return template_manager
        # ConfigurationPort
        return config_port

    container.get.side_effect = _get
    container.has.return_value = has_defaults
    return container


def _make_get_handler(container) -> GetTemplateHandler:
    from orb.domain.template.factory import TemplateFactory

    return GetTemplateHandler(
        logger=Mock(spec=LoggingPort),
        error_handler=Mock(spec=ErrorHandlingPort),
        container=container,
        template_factory=TemplateFactory(),
    )


def _make_validate_handler(container) -> ValidateTemplateHandler:
    return ValidateTemplateHandler(
        logger=Mock(spec=LoggingPort),
        container=container,
        error_handler=Mock(spec=ErrorHandlingPort),
    )


def _make_config_handler(container) -> GetConfigurationHandler:
    return GetConfigurationHandler(
        logger=Mock(spec=LoggingPort),
        container=container,
        error_handler=Mock(spec=ErrorHandlingPort),
    )


def _make_list_handler(template_manager, dtos) -> ListTemplatesHandler:
    template_manager.load_templates = AsyncMock(return_value=dtos)
    template_manager.get_templates_by_provider = AsyncMock(return_value=dtos)

    container = MagicMock()
    container.get.return_value = template_manager

    generic_filter_service = MagicMock()
    generic_filter_service.apply_filters.side_effect = lambda items, _: items

    return ListTemplatesHandler(
        logger=Mock(spec=LoggingPort),
        error_handler=Mock(spec=ErrorHandlingPort),
        container=container,
        generic_filter_service=generic_filter_service,
    )


# ---------------------------------------------------------------------------
# GetTemplateHandler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_template_not_found_raises_entity_not_found_error() -> None:
    template_manager = MagicMock()
    template_manager.get_template_by_id = AsyncMock(return_value=None)
    container = _make_container(template_manager=template_manager)
    handler = _make_get_handler(container)

    with pytest.raises(EntityNotFoundError):
        await handler.execute_query(GetTemplateQuery(template_id="missing"))


@pytest.mark.asyncio
async def test_get_template_defaults_resolution_branch() -> None:
    """When TemplateDefaultsService is registered, resolve_template_defaults is called."""
    dto = _make_template_dto("tpl-defaults")
    template_manager = MagicMock()
    template_manager.get_template_by_id = AsyncMock(return_value=dto)

    defaults_service = MagicMock()
    # Return a dict that still has the required keys so TemplateFactory can build it
    defaults_service.resolve_template_defaults.side_effect = lambda data, **_: data

    from orb.application.services.template_defaults_service import TemplateDefaultsService
    from orb.domain.template.factory import TemplateFactory

    container = MagicMock()

    def _get(service_type):
        if service_type is TemplateConfigurationPort:
            return template_manager
        if service_type is TemplateDefaultsService:
            return defaults_service
        return None

    container.get.side_effect = _get
    container.has.return_value = True  # has TemplateDefaultsService

    handler = GetTemplateHandler(
        logger=Mock(spec=LoggingPort),
        error_handler=Mock(spec=ErrorHandlingPort),
        container=container,
        template_factory=TemplateFactory(),
    )

    result = await handler.execute_query(GetTemplateQuery(template_id="tpl-defaults"))

    defaults_service.resolve_template_defaults.assert_called_once()
    assert result.template_id == "tpl-defaults"


@pytest.mark.asyncio
async def test_get_template_generic_exception_reraises() -> None:
    template_manager = MagicMock()
    template_manager.get_template_by_id = AsyncMock(side_effect=RuntimeError("storage down"))
    container = _make_container(template_manager=template_manager)
    handler = _make_get_handler(container)

    with pytest.raises(RuntimeError, match="storage down"):
        await handler.execute_query(GetTemplateQuery(template_id="tpl-1"))


# ---------------------------------------------------------------------------
# ValidateTemplateHandler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_handler_loads_by_template_id() -> None:
    """When only template_id is given, handler loads from storage."""
    dto = _make_template_dto("tpl-stored")
    template_manager = MagicMock()
    template_manager.get_template_by_id = AsyncMock(return_value=dto)
    template_manager.validate_template_config = MagicMock(return_value=[])

    container = MagicMock()

    def _get(service_type):
        return template_manager

    container.get.side_effect = _get

    handler = _make_validate_handler(container)
    result = await handler.execute_query(ValidateTemplateQuery(template_id="tpl-stored"))

    template_manager.get_template_by_id.assert_awaited_once_with("tpl-stored")
    assert result["template_id"] == "tpl-stored"


@pytest.mark.asyncio
async def test_validate_handler_not_found_propagates() -> None:
    """EntityNotFoundError from storage is re-raised, not swallowed."""
    template_manager = MagicMock()
    template_manager.get_template_by_id = AsyncMock(return_value=None)

    container = MagicMock()
    container.get.return_value = template_manager

    handler = _make_validate_handler(container)

    with pytest.raises(EntityNotFoundError):
        await handler.execute_query(ValidateTemplateQuery(template_id="tpl-gone"))


@pytest.mark.asyncio
async def test_validate_handler_validation_pass() -> None:
    """When validate_template_config returns [], valid=True."""
    template_config = {"template_id": "tpl-v", "provider_api": "EC2Fleet", "image_id": "ami-1"}
    template_manager = MagicMock()
    template_manager.validate_template_config = MagicMock(return_value=[])

    container = MagicMock()
    container.get.return_value = template_manager

    handler = _make_validate_handler(container)
    result = await handler.execute_query(ValidateTemplateQuery(template_config=template_config))

    assert result["valid"] is True
    assert result["validation_errors"] == []


@pytest.mark.asyncio
async def test_validate_handler_validation_fail() -> None:
    """When validate_template_config returns errors, valid=False and errors list is set."""
    # Include extra keys so the handler doesn't treat this as a bare template_id lookup
    template_config = {"template_id": "tpl-bad", "provider_api": "EC2Fleet"}
    template_manager = MagicMock()
    template_manager.validate_template_config = MagicMock(
        return_value=["image_id is required", "provider_api is required"]
    )

    container = MagicMock()
    container.get.return_value = template_manager

    handler = _make_validate_handler(container)
    result = await handler.execute_query(ValidateTemplateQuery(template_config=template_config))

    assert result["valid"] is False
    assert len(result["validation_errors"]) == 2
    assert "image_id is required" in result["validation_errors"]


# ---------------------------------------------------------------------------
# GetConfigurationHandler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_configuration_happy_path() -> None:
    config_port = MagicMock()
    config_port.get_configuration_value = MagicMock(return_value="us-east-1")

    container = MagicMock()
    container.get.return_value = config_port

    handler = _make_config_handler(container)
    result = await handler.execute_query(GetConfigurationQuery(key="aws.region", default=None))

    assert result["key"] == "aws.region"
    assert result["value"] == "us-east-1"
    config_port.get_configuration_value.assert_called_once_with("aws.region", None)


@pytest.mark.asyncio
async def test_get_configuration_key_not_found_returns_default() -> None:
    config_port = MagicMock()
    config_port.get_configuration_value = MagicMock(return_value="default-val")

    container = MagicMock()
    container.get.return_value = config_port

    handler = _make_config_handler(container)
    result = await handler.execute_query(
        GetConfigurationQuery(key="nonexistent.key", default="default-val")
    )

    assert result["value"] == "default-val"
    assert result["default"] == "default-val"


# ---------------------------------------------------------------------------
# ListTemplatesHandler — filter branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_templates_provider_name_filter() -> None:
    dtos = [MagicMock() for _ in range(3)]
    for d in dtos:
        d.is_active = True

    template_manager = MagicMock()
    template_manager.load_templates = AsyncMock(return_value=dtos)
    template_manager.get_templates_by_provider = AsyncMock(return_value=dtos)

    handler = _make_list_handler(template_manager, dtos)

    query = ListTemplatesQuery(provider_name="aws", active_only=False)
    result = await handler.execute_query(query)

    template_manager.load_templates.assert_awaited_once_with(provider_override="aws")
    assert len(result) == 3


@pytest.mark.asyncio
async def test_list_templates_provider_api_filter() -> None:
    dtos = [MagicMock(), MagicMock()]
    for d in dtos:
        d.is_active = True

    template_manager = MagicMock()
    template_manager.load_templates = AsyncMock(return_value=[])
    template_manager.get_templates_by_provider = AsyncMock(return_value=dtos)

    handler = _make_list_handler(template_manager, dtos)

    query = ListTemplatesQuery(provider_api="EC2Fleet", active_only=False)
    result = await handler.execute_query(query)

    template_manager.get_templates_by_provider.assert_awaited_once_with("EC2Fleet")
    assert len(result) == 2
