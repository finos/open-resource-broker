"""A template rejected at load is reported as invalid, and can be fixed or deleted.

Loading skips a template its provider template rejects so the other templates
still load. The skipped template is recorded with its reason: GetTemplate (and
so CreateRequest and every interface) reports it as invalid instead of "not
found", ValidateTemplate returns the reason, Create treats its id as taken,
Update repairs the stored row and Delete removes it. ``get_template_by_id``
keeps returning None for it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.application.commands.template_handlers import (
    CreateTemplateHandler,
    DeleteTemplateHandler,
    UpdateTemplateHandler,
)
from orb.application.dto.queries import GetTemplateQuery, ValidateTemplateQuery
from orb.application.queries.template_query_handlers import (
    GetTemplateHandler,
    ValidateTemplateHandler,
)
from orb.application.template.commands import (
    CreateTemplateCommand,
    DeleteTemplateCommand,
    UpdateTemplateCommand,
)
from orb.domain.base.exceptions import DuplicateError, ValidationError
from orb.domain.template.factory import TemplateFactory
from orb.infrastructure.adapters.template_configuration_adapter import (
    TemplateConfigurationAdapter,
)
from orb.infrastructure.template.configuration_manager import TemplateConfigurationManager
from orb.infrastructure.template.factories import TemplateDTOFactory
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from tests.providers.aws.template_defaults_support import make_defaults_service

pytestmark = pytest.mark.unit

_VALID = {
    "template_id": "good",
    "provider_type": "aws",
    "provider_api": "EC2Fleet",
    "machine_image": "ami-1",
    "price_type": "spot",
}
_INVALID = {**_VALID, "template_id": "bad", "allocation_strategy": "bogus"}


def _factory() -> TemplateFactory:
    factory = TemplateFactory()
    factory.register_provider_template_class("aws", AWSTemplate)
    return factory


def _manager(rows: list[dict[str, Any]]) -> TemplateConfigurationManager:
    scheduler = MagicMock()
    scheduler.get_template_paths.return_value = ["templates.json"]
    scheduler.load_templates_from_path.side_effect = lambda *_: [dict(r) for r in rows]
    manager = TemplateConfigurationManager(
        config_manager=MagicMock(),
        scheduler_strategy=scheduler,
        logger=MagicMock(),
        storage_service=MagicMock(),
        template_factory=_factory(),
    )
    manager.storage_service.save_template = AsyncMock()  # type: ignore[method-assign]
    manager.storage_service.delete_template = AsyncMock()  # type: ignore[method-assign]
    return manager


@pytest.fixture
def manager():
    manager = _manager([_VALID, _INVALID])
    with patch.object(manager, "_batch_resolve_images", AsyncMock(side_effect=lambda d: d)):
        yield manager


@pytest.fixture
def port(manager) -> TemplateConfigurationAdapter:
    return TemplateConfigurationAdapter(manager, MagicMock())


def _get_handler(port) -> GetTemplateHandler:
    return GetTemplateHandler(
        logger=MagicMock(),
        error_handler=MagicMock(),
        template_manager=port,
        template_defaults_service=make_defaults_service(),
        template_factory=_factory(),
        template_dto_factory=TemplateDTOFactory(),
    )


@pytest.mark.asyncio
async def test_rejected_template_is_recorded_and_others_still_load(manager) -> None:
    templates = await manager.load_templates()
    assert [t.template_id for t in templates] == ["good"]
    assert await manager.get_template_by_id("bad") is None

    rejection = await manager.get_template_rejection("bad")
    assert rejection is not None
    assert rejection.template_id == "bad"
    assert "Unknown allocation strategy 'bogus'" in rejection.reason
    assert rejection.row["allocation_strategy"] == "bogus"
    assert await manager.get_template_rejection("good") is None


@pytest.mark.asyncio
async def test_get_template_reports_invalid_with_the_reason(port) -> None:
    with pytest.raises(ValidationError, match="template bad is invalid: .*bogus"):
        await _get_handler(port).execute_query(GetTemplateQuery(template_id="bad"))


@pytest.mark.asyncio
async def test_get_template_still_reports_not_found_for_unknown_ids(port) -> None:
    from orb.domain.base.exceptions import EntityNotFoundError

    with pytest.raises(EntityNotFoundError):
        await _get_handler(port).execute_query(GetTemplateQuery(template_id="missing"))


@pytest.mark.asyncio
async def test_validate_stored_rejected_template_returns_the_reason(port) -> None:
    handler = ValidateTemplateHandler(
        logger=MagicMock(), template_manager=port, error_handler=MagicMock()
    )
    result = await handler.execute_query(ValidateTemplateQuery(template_id="bad"))
    assert result["valid"] is False
    assert any("bogus" in e for e in result["validation_errors"])


@pytest.mark.asyncio
async def test_validate_file_template_runs_the_provider_template(port) -> None:
    handler = ValidateTemplateHandler(
        logger=MagicMock(),
        template_manager=port,
        error_handler=MagicMock(),
        template_defaults_service=make_defaults_service(),
    )
    result = await handler.execute_query(
        ValidateTemplateQuery(
            template_config={
                **_VALID,
                "template_id": "file",
                "allocation_strategy_on_demand": "diversified",
            }
        )
    )
    assert result["valid"] is False
    assert any("does not apply to on-demand pools" in e for e in result["validation_errors"])


@pytest.mark.asyncio
async def test_validate_file_template_migrates_a_legacy_row_first(port) -> None:
    handler = ValidateTemplateHandler(
        logger=MagicMock(),
        template_manager=port,
        error_handler=MagicMock(),
        template_defaults_service=make_defaults_service(),
    )
    legacy = {
        **_VALID,
        "template_id": "file",
        "price_type": "ondemand",
        "allocation_strategy": "prioritized",
    }
    with pytest.warns(DeprecationWarning):
        result = await handler.execute_query(ValidateTemplateQuery(template_config=legacy))
    assert result["valid"] is True, result


def _command_args(port) -> dict[str, Any]:
    return {
        "template_port": port,
        "logger": MagicMock(),
        "event_publisher": MagicMock(),
        "error_handler": MagicMock(),
    }


@pytest.mark.asyncio
async def test_create_with_a_rejected_id_is_a_duplicate(port) -> None:
    handler = CreateTemplateHandler(**_command_args(port))
    command = CreateTemplateCommand(
        template_id="bad",
        provider_api="EC2Fleet",
        image_id="ami-2",
        configuration={"provider_type": "aws"},
    )
    with pytest.raises(DuplicateError):
        await handler.execute_command(command)


@pytest.mark.asyncio
async def test_update_repairs_a_rejected_template(port, manager) -> None:
    handler = UpdateTemplateHandler(
        **_command_args(port),
        template_defaults_service=make_defaults_service(),
        template_factory=_factory(),
        template_dto_factory=TemplateDTOFactory(),
    )
    command = UpdateTemplateCommand(
        template_id="bad", configuration={"allocation_strategy": "capacityOptimized"}
    )
    await handler.execute_command(command)

    assert command.updated is True, command.validation_errors
    saved = manager.storage_service.save_template.call_args.args[0]
    assert saved.template_id == "bad"
    assert saved.allocation_strategy == "capacityOptimized"
    assert saved.image_id == "ami-1"


@pytest.mark.asyncio
async def test_update_that_stays_invalid_returns_the_reason(port, manager) -> None:
    handler = UpdateTemplateHandler(
        **_command_args(port),
        template_defaults_service=make_defaults_service(),
        template_factory=_factory(),
        template_dto_factory=TemplateDTOFactory(),
    )
    command = UpdateTemplateCommand(template_id="bad", configuration={"name": "renamed"})
    await handler.execute_command(command)

    assert command.updated is False
    assert command.validation_errors
    assert any("bogus" in e for e in command.validation_errors)
    manager.storage_service.save_template.assert_not_called()


@pytest.mark.asyncio
async def test_delete_removes_a_rejected_template(port, manager) -> None:
    handler = DeleteTemplateHandler(**_command_args(port))
    command = DeleteTemplateCommand(template_id="bad")
    await handler.execute_command(command)
    assert command.deleted is True
    manager.storage_service.delete_template.assert_awaited_once_with("bad")


@pytest.fixture
def scoped_load(manager):
    """A provider-scoped load that does not return the rejected row."""
    rows = {None: [_VALID, _INVALID]}
    manager.scheduler_strategy.load_templates_from_path.side_effect = lambda _path, provider: [
        dict(r) for r in rows.get(provider, [_VALID])
    ]
    return manager


@pytest.mark.asyncio
async def test_provider_scoped_load_keeps_the_active_rejections(scoped_load, port) -> None:
    manager = scoped_load
    await manager.load_templates()

    scoped = await manager.load_templates(provider_override="aws-other")
    assert [t.template_id for t in scoped] == ["good"]

    rejection = await manager.get_template_rejection("bad")
    assert rejection is not None and "bogus" in rejection.reason
    with pytest.raises(ValidationError, match="template bad is invalid: .*bogus"):
        await _get_handler(port).execute_query(GetTemplateQuery(template_id="bad"))
    with pytest.raises(DuplicateError):
        await CreateTemplateHandler(**_command_args(port)).execute_command(
            CreateTemplateCommand(
                template_id="bad",
                provider_api="EC2Fleet",
                image_id="ami-2",
                configuration={"provider_type": "aws"},
            )
        )
    update = UpdateTemplateCommand(
        template_id="bad", configuration={"allocation_strategy": "capacityOptimized"}
    )
    await UpdateTemplateHandler(
        **_command_args(port),
        template_defaults_service=make_defaults_service(),
        template_factory=_factory(),
        template_dto_factory=TemplateDTOFactory(),
    ).execute_command(update)
    assert update.updated is True, update.validation_errors
    delete = DeleteTemplateCommand(template_id="bad")
    await DeleteTemplateHandler(**_command_args(port)).execute_command(delete)
    assert delete.deleted is True


@pytest.mark.asyncio
async def test_sync_fallback_load_keeps_the_active_rejections(scoped_load) -> None:
    manager = scoped_load
    await manager.load_templates()

    # The fallback skips provider filtering and deduplication, so it must not replace them.
    manager.scheduler_strategy.load_templates_from_path.side_effect = lambda _path, _provider: [
        dict(_VALID)
    ]
    assert [t.template_id for t in manager._load_templates_sync()] == ["good"]

    rejection = await manager.get_template_rejection("bad")
    assert rejection is not None and "bogus" in rejection.reason
