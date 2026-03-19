"""Unit tests for all 6 template orchestrators (issue 2012)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.queries import GetTemplateQuery, ValidateTemplateQuery
from orb.application.services.orchestration.create_template import CreateTemplateOrchestrator
from orb.application.services.orchestration.delete_template import DeleteTemplateOrchestrator
from orb.application.services.orchestration.dtos import (
    CreateTemplateInput,
    CreateTemplateOutput,
    DeleteTemplateInput,
    DeleteTemplateOutput,
    GetTemplateInput,
    GetTemplateOutput,
    RefreshTemplatesInput,
    RefreshTemplatesOutput,
    UpdateTemplateInput,
    UpdateTemplateOutput,
    ValidateTemplateInput,
    ValidateTemplateOutput,
)
from orb.application.services.orchestration.get_template import GetTemplateOrchestrator
from orb.application.services.orchestration.refresh_templates import RefreshTemplatesOrchestrator
from orb.application.services.orchestration.update_template import UpdateTemplateOrchestrator
from orb.application.services.orchestration.validate_template import ValidateTemplateOrchestrator
from orb.application.template.commands import (
    CreateTemplateCommand,
    DeleteTemplateCommand,
    UpdateTemplateCommand,
)
from orb.domain.base.exceptions import EntityNotFoundError

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_command_bus():
    bus = MagicMock()
    bus.execute = AsyncMock()
    return bus


@pytest.fixture
def mock_query_bus():
    bus = MagicMock()
    bus.execute = AsyncMock()
    return bus


@pytest.fixture
def mock_logger():
    return MagicMock()


# ---------------------------------------------------------------------------
# TestCreateTemplateOrchestrator
# ---------------------------------------------------------------------------


@pytest.fixture
def create_orch(mock_command_bus, mock_query_bus, mock_logger):
    return CreateTemplateOrchestrator(
        command_bus=mock_command_bus, query_bus=mock_query_bus, logger=mock_logger
    )


@pytest.mark.unit
@pytest.mark.application
class TestCreateTemplateOrchestrator:
    @pytest.mark.asyncio
    async def test_execute_dispatches_create_template_command(self, create_orch, mock_command_bus):
        async def _set(cmd):
            cmd.created = True
            cmd.validation_errors = []

        mock_command_bus.execute.side_effect = _set
        await create_orch.execute(
            CreateTemplateInput(template_id="t-1", provider_api="EC2Fleet", image_id="ami-abc")
        )
        mock_command_bus.execute.assert_awaited_once()
        cmd = mock_command_bus.execute.call_args[0][0]
        assert isinstance(cmd, CreateTemplateCommand)
        assert cmd.template_id == "t-1"
        assert cmd.provider_api == "EC2Fleet"
        assert cmd.image_id == "ami-abc"

    @pytest.mark.asyncio
    async def test_execute_returns_create_template_output(self, create_orch, mock_command_bus):
        async def _set(cmd):
            cmd.created = True
            cmd.validation_errors = []

        mock_command_bus.execute.side_effect = _set
        result = await create_orch.execute(
            CreateTemplateInput(template_id="t-1", provider_api="EC2Fleet", image_id="ami-abc")
        )
        assert isinstance(result, CreateTemplateOutput)
        assert result.template_id == "t-1"
        assert result.created is True
        assert result.validation_errors == []

    @pytest.mark.asyncio
    async def test_execute_propagates_validation_errors(self, create_orch, mock_command_bus):
        async def _set(cmd):
            cmd.created = False
            cmd.validation_errors = ["field X required"]

        mock_command_bus.execute.side_effect = _set
        result = await create_orch.execute(
            CreateTemplateInput(template_id="t-1", provider_api="EC2Fleet", image_id="ami-abc")
        )
        assert result.validation_errors == ["field X required"]

    @pytest.mark.asyncio
    async def test_execute_logs_template_id(self, create_orch, mock_logger, mock_command_bus):
        async def _set(cmd):
            cmd.created = True
            cmd.validation_errors = []

        mock_command_bus.execute.side_effect = _set
        await create_orch.execute(
            CreateTemplateInput(template_id="t-log", provider_api="EC2Fleet", image_id="ami-x")
        )
        mock_logger.info.assert_called_once()
        assert "t-log" in str(mock_logger.info.call_args)

    @pytest.mark.asyncio
    async def test_execute_raw_contains_expected_keys(self, create_orch, mock_command_bus):
        async def _set(cmd):
            cmd.created = True
            cmd.validation_errors = []

        mock_command_bus.execute.side_effect = _set
        result = await create_orch.execute(
            CreateTemplateInput(template_id="t-1", provider_api="EC2Fleet", image_id="ami-abc")
        )
        assert result.template_id == "t-1"
        assert result.created is not None
        assert isinstance(result.validation_errors, list)


# ---------------------------------------------------------------------------
# TestUpdateTemplateOrchestrator
# ---------------------------------------------------------------------------


@pytest.fixture
def update_orch(mock_command_bus, mock_query_bus, mock_logger):
    return UpdateTemplateOrchestrator(
        command_bus=mock_command_bus, query_bus=mock_query_bus, logger=mock_logger
    )


@pytest.mark.unit
@pytest.mark.application
class TestUpdateTemplateOrchestrator:
    @pytest.mark.asyncio
    async def test_execute_dispatches_update_template_command(self, update_orch, mock_command_bus):
        async def _set(cmd):
            cmd.updated = True
            cmd.validation_errors = []

        mock_command_bus.execute.side_effect = _set
        await update_orch.execute(UpdateTemplateInput(template_id="t-2", name="new-name"))
        mock_command_bus.execute.assert_awaited_once()
        cmd = mock_command_bus.execute.call_args[0][0]
        assert isinstance(cmd, UpdateTemplateCommand)
        assert cmd.template_id == "t-2"
        assert cmd.name == "new-name"

    @pytest.mark.asyncio
    async def test_execute_returns_update_template_output(self, update_orch, mock_command_bus):
        async def _set(cmd):
            cmd.updated = True
            cmd.validation_errors = []

        mock_command_bus.execute.side_effect = _set
        result = await update_orch.execute(UpdateTemplateInput(template_id="t-2", name="new-name"))
        assert isinstance(result, UpdateTemplateOutput)
        assert result.updated is True
        assert result.template_id == "t-2"

    @pytest.mark.asyncio
    async def test_execute_updated_false_when_not_found(self, update_orch, mock_command_bus):
        async def _set(cmd):
            cmd.updated = False
            cmd.validation_errors = []

        mock_command_bus.execute.side_effect = _set
        result = await update_orch.execute(UpdateTemplateInput(template_id="t-2"))
        assert result.updated is False

    @pytest.mark.asyncio
    async def test_execute_raw_contains_expected_keys(self, update_orch, mock_command_bus):
        async def _set(cmd):
            cmd.updated = True
            cmd.validation_errors = []

        mock_command_bus.execute.side_effect = _set
        result = await update_orch.execute(UpdateTemplateInput(template_id="t-2"))
        assert result.template_id == "t-2"
        assert result.updated is not None
        assert isinstance(result.validation_errors, list)


# ---------------------------------------------------------------------------
# TestDeleteTemplateOrchestrator
# ---------------------------------------------------------------------------


@pytest.fixture
def delete_orch(mock_command_bus, mock_query_bus, mock_logger):
    return DeleteTemplateOrchestrator(
        command_bus=mock_command_bus, query_bus=mock_query_bus, logger=mock_logger
    )


@pytest.mark.unit
@pytest.mark.application
class TestDeleteTemplateOrchestrator:
    @pytest.mark.asyncio
    async def test_happy_path_deleted_true(self, delete_orch, mock_command_bus):
        async def _set(cmd):
            cmd.deleted = True

        mock_command_bus.execute.side_effect = _set
        result = await delete_orch.execute(DeleteTemplateInput(template_id="tpl-del"))
        assert isinstance(result, DeleteTemplateOutput)
        assert result.deleted is True
        assert result.template_id == "tpl-del"

    @pytest.mark.asyncio
    async def test_deleted_false_when_command_sets_false(self, delete_orch, mock_command_bus):
        async def _set(cmd):
            cmd.deleted = False

        mock_command_bus.execute.side_effect = _set
        result = await delete_orch.execute(DeleteTemplateInput(template_id="tpl-del"))
        assert result.deleted is False

    @pytest.mark.asyncio
    async def test_dispatches_delete_command(self, delete_orch, mock_command_bus):
        mock_command_bus.execute.return_value = None
        await delete_orch.execute(DeleteTemplateInput(template_id="tpl-x"))
        cmd = mock_command_bus.execute.call_args[0][0]
        assert isinstance(cmd, DeleteTemplateCommand)
        assert cmd.template_id == "tpl-x"


# ---------------------------------------------------------------------------
# TestGetTemplateOrchestrator
# ---------------------------------------------------------------------------


@pytest.fixture
def get_orch(mock_command_bus, mock_query_bus, mock_logger):
    return GetTemplateOrchestrator(
        command_bus=mock_command_bus, query_bus=mock_query_bus, logger=mock_logger
    )


@pytest.mark.unit
@pytest.mark.application
class TestGetTemplateOrchestrator:
    @pytest.mark.asyncio
    async def test_execute_dispatches_get_template_query(self, get_orch, mock_query_bus):
        mock_query_bus.execute.return_value = MagicMock()
        await get_orch.execute(GetTemplateInput(template_id="t-4", provider_name="aws"))
        mock_query_bus.execute.assert_awaited_once()
        query = mock_query_bus.execute.call_args[0][0]
        assert isinstance(query, GetTemplateQuery)
        assert query.template_id == "t-4"
        assert query.provider_name == "aws"

    @pytest.mark.asyncio
    async def test_execute_returns_template_on_success(self, get_orch, mock_query_bus):
        fake_template = MagicMock()
        mock_query_bus.execute.return_value = fake_template
        result = await get_orch.execute(GetTemplateInput(template_id="t-4"))
        assert isinstance(result, GetTemplateOutput)
        assert result.template is fake_template

    @pytest.mark.asyncio
    async def test_execute_returns_none_template_on_not_found(self, get_orch, mock_query_bus):
        mock_query_bus.execute.side_effect = EntityNotFoundError("Template", "t-4")
        result = await get_orch.execute(GetTemplateInput(template_id="t-4"))
        assert result.template is None

    @pytest.mark.asyncio
    async def test_execute_provider_name_none_by_default(self, get_orch, mock_query_bus):
        mock_query_bus.execute.return_value = MagicMock()
        await get_orch.execute(GetTemplateInput(template_id="t-5"))
        query = mock_query_bus.execute.call_args[0][0]
        assert query.provider_name is None


# ---------------------------------------------------------------------------
# TestValidateTemplateOrchestrator
# ---------------------------------------------------------------------------


@pytest.fixture
def validate_orch(mock_command_bus, mock_query_bus, mock_logger):
    return ValidateTemplateOrchestrator(
        command_bus=mock_command_bus, query_bus=mock_query_bus, logger=mock_logger
    )


@pytest.mark.unit
@pytest.mark.application
class TestValidateTemplateOrchestrator:
    @pytest.mark.asyncio
    async def test_execute_dispatches_validate_template_query(self, validate_orch, mock_query_bus):
        mock_query_bus.execute.return_value = {
            "valid": True,
            "validation_errors": [],
            "message": "ok",
        }
        await validate_orch.execute(ValidateTemplateInput(template_id="t-6", config={"key": "val"}))
        mock_query_bus.execute.assert_awaited_once()
        query = mock_query_bus.execute.call_args[0][0]
        assert isinstance(query, ValidateTemplateQuery)
        assert query.template_id == "t-6"
        assert query.template_config == {"key": "val"}

    @pytest.mark.asyncio
    async def test_execute_returns_valid_true(self, validate_orch, mock_query_bus):
        mock_query_bus.execute.return_value = {
            "valid": True,
            "validation_errors": [],
            "message": "ok",
        }
        result = await validate_orch.execute(ValidateTemplateInput(template_id="t-6"))
        assert isinstance(result, ValidateTemplateOutput)
        assert result.valid is True
        assert result.errors == []
        assert result.message == "ok"

    @pytest.mark.asyncio
    async def test_execute_returns_valid_false_with_errors(self, validate_orch, mock_query_bus):
        mock_query_bus.execute.return_value = {
            "valid": False,
            "validation_errors": ["bad field"],
            "message": "fail",
        }
        result = await validate_orch.execute(ValidateTemplateInput(template_id="t-6"))
        assert result.valid is False
        assert result.errors == ["bad field"]

    @pytest.mark.asyncio
    async def test_execute_handles_non_dict_result(self, validate_orch, mock_query_bus):
        mock_query_bus.execute.return_value = None
        result = await validate_orch.execute(ValidateTemplateInput())
        assert result.valid is False
        assert result.errors == []
        assert result.message == ""

    @pytest.mark.asyncio
    async def test_execute_config_none_defaults_to_empty_dict(self, validate_orch, mock_query_bus):
        mock_query_bus.execute.return_value = {
            "valid": True,
            "validation_errors": [],
            "message": "",
        }
        await validate_orch.execute(ValidateTemplateInput(template_id="t-7", config=None))
        query = mock_query_bus.execute.call_args[0][0]
        assert query.template_config == {}


# ---------------------------------------------------------------------------
# TestRefreshTemplatesOrchestrator
# ---------------------------------------------------------------------------


@pytest.fixture
def refresh_orch(mock_command_bus, mock_query_bus, mock_logger):
    return RefreshTemplatesOrchestrator(
        command_bus=mock_command_bus, query_bus=mock_query_bus, logger=mock_logger
    )


@pytest.mark.unit
@pytest.mark.application
class TestRefreshTemplatesOrchestrator:
    @pytest.mark.asyncio
    async def test_execute_dispatches_refresh_templates_command(
        self, refresh_orch, mock_command_bus
    ):
        from orb.application.commands.system import RefreshTemplatesCommand

        async def _set(cmd):
            cmd.result = {"templates": []}

        mock_command_bus.execute.side_effect = _set
        await refresh_orch.execute(RefreshTemplatesInput(provider_name="aws"))
        mock_command_bus.execute.assert_awaited_once()
        cmd = mock_command_bus.execute.call_args[0][0]
        assert isinstance(cmd, RefreshTemplatesCommand)
        assert cmd.provider_name == "aws"

    @pytest.mark.asyncio
    async def test_execute_returns_templates_from_command_result(
        self, refresh_orch, mock_command_bus
    ):
        async def _set(cmd):
            cmd.result = {"templates": [{"template_id": "t-1"}, {"template_id": "t-2"}]}

        mock_command_bus.execute.side_effect = _set
        result = await refresh_orch.execute(RefreshTemplatesInput())
        assert isinstance(result, RefreshTemplatesOutput)
        assert len(result.templates) == 2

    @pytest.mark.asyncio
    async def test_execute_returns_empty_list_when_result_is_none(
        self, refresh_orch, mock_command_bus
    ):
        async def _set(cmd):
            cmd.result = None

        mock_command_bus.execute.side_effect = _set
        result = await refresh_orch.execute(RefreshTemplatesInput())
        assert result.templates == []

    @pytest.mark.asyncio
    async def test_execute_returns_empty_list_when_templates_key_missing(
        self, refresh_orch, mock_command_bus
    ):
        async def _set(cmd):
            cmd.result = {"other_key": "value"}

        mock_command_bus.execute.side_effect = _set
        result = await refresh_orch.execute(RefreshTemplatesInput())
        assert result.templates == []

    @pytest.mark.asyncio
    async def test_execute_provider_name_passed_to_command(self, refresh_orch, mock_command_bus):
        async def _set(cmd):
            cmd.result = {"templates": []}

        mock_command_bus.execute.side_effect = _set
        await refresh_orch.execute(RefreshTemplatesInput(provider_name="custom-provider"))
        cmd = mock_command_bus.execute.call_args[0][0]
        assert cmd.provider_name == "custom-provider"
