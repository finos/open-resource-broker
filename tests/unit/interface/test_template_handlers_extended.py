"""Extended unit tests for template_command_handlers — uncovered branch paths."""

from __future__ import annotations

import argparse
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.application.dto.interface_response import InterfaceResponse
from orb.application.services.orchestration.create_template import CreateTemplateOrchestrator
from orb.application.services.orchestration.delete_template import DeleteTemplateOrchestrator
from orb.application.services.orchestration.get_template import GetTemplateOrchestrator
from orb.application.services.orchestration.list_templates import ListTemplatesOrchestrator
from orb.application.services.orchestration.refresh_templates import RefreshTemplatesOrchestrator
from orb.application.services.orchestration.update_template import UpdateTemplateOrchestrator
from orb.application.services.orchestration.validate_template import ValidateTemplateOrchestrator
from orb.domain.base.exceptions import DuplicateError, EntityNotFoundError
from orb.domain.base.ports.console_port import ConsolePort
from orb.interface.response_formatting_service import ResponseFormattingService

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_args(**kwargs) -> argparse.Namespace:
    ns = argparse.Namespace()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def _make_container(**overrides):
    from orb.application.ports.scheduler_port import SchedulerPort
    from orb.application.services.orchestration.dtos import (
        CreateTemplateOutput,
        DeleteTemplateOutput,
        GetTemplateOutput,
        ListTemplatesOutput,
        RefreshTemplatesOutput,
        UpdateTemplateOutput,
        ValidateTemplateOutput,
    )
    from orb.infrastructure.di.buses import QueryBus

    container = MagicMock()

    mock_formatter = MagicMock(spec=ResponseFormattingService)
    mock_formatter.format_template_list.return_value = InterfaceResponse(data={"templates": []})
    mock_formatter.format_template_mutation.return_value = InterfaceResponse(data={"success": True})
    mock_formatter.format_error.return_value = InterfaceResponse(
        data={"success": False, "error": "err"}, exit_code=1
    )
    mock_formatter.format_config.return_value = InterfaceResponse(data={"config": {}})

    mock_scheduler = MagicMock(spec=SchedulerPort)
    mock_scheduler.format_template_for_display.return_value = {}

    mock_console = MagicMock(spec=ConsolePort)

    # Default orchestrator mocks
    list_orch = MagicMock(spec=ListTemplatesOrchestrator)
    list_orch.execute = AsyncMock(return_value=ListTemplatesOutput(templates=[]))
    get_orch = MagicMock(spec=GetTemplateOrchestrator)
    get_orch.execute = AsyncMock(return_value=GetTemplateOutput(template={"template_id": "t1"}))
    create_orch = MagicMock(spec=CreateTemplateOrchestrator)
    create_orch.execute = AsyncMock(
        return_value=CreateTemplateOutput(template_id="t1", created=True, validation_errors=[])
    )
    update_orch = MagicMock(spec=UpdateTemplateOrchestrator)
    update_orch.execute = AsyncMock(
        return_value=UpdateTemplateOutput(template_id="t1", updated=True, validation_errors=[])
    )
    delete_orch = MagicMock(spec=DeleteTemplateOrchestrator)
    delete_orch.execute = AsyncMock(
        return_value=DeleteTemplateOutput(template_id="t1", deleted=True)
    )
    validate_orch = MagicMock(spec=ValidateTemplateOrchestrator)
    validate_orch.execute = AsyncMock(
        return_value=ValidateTemplateOutput(
            template_id="t1", valid=True, errors=[], message="valid"
        )
    )
    refresh_orch = MagicMock(spec=RefreshTemplatesOrchestrator)
    refresh_orch.execute = AsyncMock(return_value=RefreshTemplatesOutput(templates=[]))
    mock_query_bus = MagicMock(spec=QueryBus)

    dispatch = {
        ResponseFormattingService: mock_formatter,
        SchedulerPort: mock_scheduler,
        ConsolePort: mock_console,
        ListTemplatesOrchestrator: list_orch,
        GetTemplateOrchestrator: get_orch,
        CreateTemplateOrchestrator: create_orch,
        UpdateTemplateOrchestrator: update_orch,
        DeleteTemplateOrchestrator: delete_orch,
        ValidateTemplateOrchestrator: validate_orch,
        RefreshTemplatesOrchestrator: refresh_orch,
        QueryBus: mock_query_bus,
        **overrides,
    }

    container.get.side_effect = lambda t: dispatch.get(t, MagicMock())

    return container, {
        "formatter": mock_formatter,
        "scheduler": mock_scheduler,
        "console": mock_console,
        "list_orch": list_orch,
        "get_orch": get_orch,
        "create_orch": create_orch,
        "update_orch": update_orch,
        "delete_orch": delete_orch,
        "validate_orch": validate_orch,
        "refresh_orch": refresh_orch,
        "query_bus": mock_query_bus,
    }


# ---------------------------------------------------------------------------
# handle_list_templates
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleListTemplates:
    @pytest.mark.asyncio
    async def test_empty_result_prints_getting_started_help(self):
        """Empty template list → console.info called and getting_started help printed."""
        from orb.interface.template_command_handlers import handle_list_templates

        container, mocks = _make_container()
        mocks["list_orch"].execute.return_value.__class__  # ensure mock

        from orb.application.services.orchestration.dtos import ListTemplatesOutput

        mocks["list_orch"].execute = AsyncMock(return_value=ListTemplatesOutput(templates=[]))

        args = _make_args()
        args._container = container

        with patch("orb.cli.help_utils.print_getting_started_help", return_value=None) as mock_help:
            await handle_list_templates(args)

        mock_help.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_empty_result_does_not_print_help(self):
        """Non-empty template list → getting_started help NOT printed."""
        from orb.application.services.orchestration.dtos import ListTemplatesOutput
        from orb.interface.template_command_handlers import handle_list_templates

        container, mocks = _make_container()
        mocks["list_orch"].execute = AsyncMock(
            return_value=ListTemplatesOutput(templates=[{"template_id": "t1"}])
        )

        args = _make_args()
        args._container = container

        with patch("orb.cli.help_utils.print_getting_started_help", return_value=None) as mock_help:
            await handle_list_templates(args)

        mock_help.assert_not_called()

    @pytest.mark.asyncio
    async def test_input_data_provider_api_forwarded(self):
        """input_data.provider_api is forwarded to orchestrator input."""
        from orb.application.services.orchestration.dtos import (
            ListTemplatesInput,
            ListTemplatesOutput,
        )
        from orb.interface.template_command_handlers import handle_list_templates

        container, mocks = _make_container()
        mocks["list_orch"].execute = AsyncMock(
            return_value=ListTemplatesOutput(templates=[{"template_id": "x"}])
        )

        args = _make_args(
            input_data={"provider_api": "EC2Fleet", "active_only": False, "limit": 10, "offset": 0}
        )
        args._container = container

        await handle_list_templates(args)

        call_input: ListTemplatesInput = mocks["list_orch"].execute.call_args[0][0]
        assert call_input.provider_api == "EC2Fleet"
        assert call_input.active_only is False
        assert call_input.limit == 10

    @pytest.mark.asyncio
    async def test_args_limit_offset_forwarded(self):
        """Explicit limit/offset from args are forwarded to orchestrator."""
        from orb.application.services.orchestration.dtos import (
            ListTemplatesInput,
            ListTemplatesOutput,
        )
        from orb.interface.template_command_handlers import handle_list_templates

        container, mocks = _make_container()
        mocks["list_orch"].execute = AsyncMock(return_value=ListTemplatesOutput(templates=[]))

        args = _make_args(
            limit=25, offset=10, provider_name=None, provider_type=None, provider_api=None
        )
        args._container = container

        with patch("orb.cli.help_utils.print_getting_started_help"):
            await handle_list_templates(args)

        call_input: ListTemplatesInput = mocks["list_orch"].execute.call_args[0][0]
        assert call_input.limit == 25
        assert call_input.offset == 10


# ---------------------------------------------------------------------------
# handle_get_template
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleGetTemplate:
    @pytest.mark.asyncio
    async def test_missing_template_id_returns_error(self):
        """No template_id → InterfaceResponse with exit_code=1."""
        from orb.interface.template_command_handlers import handle_get_template

        container, mocks = _make_container()
        args = _make_args()
        args._container = container

        result = await handle_get_template(args)

        assert isinstance(result, InterfaceResponse)
        assert result.exit_code == 1
        assert "Template ID is required" in result.data["error"]
        mocks["get_orch"].execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_flag_template_id_accepted(self):
        """flag_template_id attribute is accepted as template_id fallback."""
        from orb.application.services.orchestration.dtos import GetTemplateOutput
        from orb.interface.template_command_handlers import handle_get_template

        container, mocks = _make_container()
        mocks["get_orch"].execute = AsyncMock(
            return_value=GetTemplateOutput(template={"template_id": "t99"})
        )

        args = _make_args(flag_template_id="t99")
        args._container = container

        await handle_get_template(args)

        mocks["get_orch"].execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_entity_not_found_returns_formatted_error(self):
        """EntityNotFoundError → formatter.format_error called."""
        from orb.interface.template_command_handlers import handle_get_template

        container, mocks = _make_container()
        mocks["get_orch"].execute = AsyncMock(side_effect=EntityNotFoundError("template", "t1"))

        args = _make_args(template_id="t1")
        args._container = container

        await handle_get_template(args)

        mocks["formatter"].format_error.assert_called_once()

    @pytest.mark.asyncio
    async def test_none_template_in_result_returns_not_found_response(self):
        """Orchestrator returns result.template=None → InterfaceResponse exit_code=1."""
        from orb.application.services.orchestration.dtos import GetTemplateOutput
        from orb.interface.template_command_handlers import handle_get_template

        container, mocks = _make_container()
        mocks["get_orch"].execute = AsyncMock(return_value=GetTemplateOutput(template=None))

        args = _make_args(template_id="t1")
        args._container = container

        result = await handle_get_template(args)

        assert isinstance(result, InterfaceResponse)
        assert result.exit_code == 1
        assert "not found" in result.data["error"].lower()


# ---------------------------------------------------------------------------
# handle_create_template — error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleCreateTemplateErrors:
    @pytest.mark.asyncio
    async def test_file_not_found_returns_error(self, tmp_path):
        """Non-existent template file → InterfaceResponse exit_code=1."""
        from orb.interface.template_command_handlers import handle_create_template

        container, _ = _make_container()
        args = _make_args(file=str(tmp_path / "ghost.json"))
        args._container = container

        with patch(
            "orb.infrastructure.mocking.dry_run_context.is_dry_run_active", return_value=False
        ):
            result = await handle_create_template(args)

        assert isinstance(result, InterfaceResponse)
        assert result.exit_code == 1
        assert "not found" in result.data["error"].lower()

    @pytest.mark.asyncio
    async def test_invalid_json_returns_error(self, tmp_path):
        """Invalid JSON in template file → InterfaceResponse exit_code=1."""
        from orb.interface.template_command_handlers import handle_create_template

        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json {{{")

        container, _ = _make_container()
        args = _make_args(file=str(bad_file))
        args._container = container

        with patch(
            "orb.infrastructure.mocking.dry_run_context.is_dry_run_active", return_value=False
        ):
            result = await handle_create_template(args)

        assert isinstance(result, InterfaceResponse)
        assert result.exit_code == 1
        assert "Invalid JSON" in result.data["error"]

    @pytest.mark.asyncio
    async def test_missing_template_id_in_file_returns_error(self, tmp_path):
        """Template file without template_id → InterfaceResponse exit_code=1."""
        from orb.interface.template_command_handlers import handle_create_template

        data = {"provider_api": "EC2Fleet"}
        f = tmp_path / "t.json"
        f.write_text(json.dumps(data))

        container, _ = _make_container()
        args = _make_args(file=str(f))
        args._container = container

        with patch(
            "orb.infrastructure.mocking.dry_run_context.is_dry_run_active", return_value=False
        ):
            result = await handle_create_template(args)

        assert isinstance(result, InterfaceResponse)
        assert result.exit_code == 1
        assert "template_id" in result.data["error"]

    @pytest.mark.asyncio
    async def test_missing_provider_api_in_file_returns_error(self, tmp_path):
        """Template file without provider_api → InterfaceResponse exit_code=1."""
        from orb.interface.template_command_handlers import handle_create_template

        data = {"template_id": "t1"}
        f = tmp_path / "t.json"
        f.write_text(json.dumps(data))

        container, _ = _make_container()
        args = _make_args(file=str(f))
        args._container = container

        with patch(
            "orb.infrastructure.mocking.dry_run_context.is_dry_run_active", return_value=False
        ):
            result = await handle_create_template(args)

        assert isinstance(result, InterfaceResponse)
        assert result.exit_code == 1
        assert "provider_api" in result.data["error"]

    @pytest.mark.asyncio
    async def test_no_file_arg_returns_error(self):
        """No file attr → InterfaceResponse exit_code=1."""
        from orb.interface.template_command_handlers import handle_create_template

        container, _ = _make_container()
        args = _make_args()
        args._container = container

        with patch(
            "orb.infrastructure.mocking.dry_run_context.is_dry_run_active", return_value=False
        ):
            result = await handle_create_template(args)

        assert isinstance(result, InterfaceResponse)
        assert result.exit_code == 1

    @pytest.mark.asyncio
    async def test_duplicate_error_returns_conflict_response(self, tmp_path):
        """DuplicateError → InterfaceResponse exit_code=1 with 'already exists'."""
        from orb.interface.template_command_handlers import handle_create_template

        data = {"template_id": "t1", "provider_api": "EC2Fleet"}
        f = tmp_path / "t.json"
        f.write_text(json.dumps(data))

        container, mocks = _make_container()
        mocks["create_orch"].execute = AsyncMock(side_effect=DuplicateError("t1"))

        args = _make_args(file=str(f))
        args._container = container

        with patch(
            "orb.infrastructure.mocking.dry_run_context.is_dry_run_active", return_value=False
        ):
            result = await handle_create_template(args)

        assert isinstance(result, InterfaceResponse)
        assert result.exit_code == 1
        assert "already exists" in result.data["error"]

    @pytest.mark.asyncio
    async def test_validation_errors_return_error_response(self, tmp_path):
        """Orchestrator returns validation_errors → InterfaceResponse exit_code=1."""
        from orb.application.services.orchestration.dtos import CreateTemplateOutput
        from orb.interface.template_command_handlers import handle_create_template

        data = {"template_id": "t1", "provider_api": "EC2Fleet"}
        f = tmp_path / "t.json"
        f.write_text(json.dumps(data))

        container, mocks = _make_container()
        mocks["create_orch"].execute = AsyncMock(
            return_value=CreateTemplateOutput(
                template_id="t1", created=False, validation_errors=["missing field"]
            )
        )

        args = _make_args(file=str(f))
        args._container = container

        with patch(
            "orb.infrastructure.mocking.dry_run_context.is_dry_run_active", return_value=False
        ):
            result = await handle_create_template(args)

        assert isinstance(result, InterfaceResponse)
        assert result.exit_code == 1
        assert "validation failed" in result.data["error"].lower()

    @pytest.mark.asyncio
    async def test_dry_run_returns_dry_run_dict(self, tmp_path):
        """is_dry_run_active=True → dry-run dict returned without calling orchestrator."""
        from orb.interface.template_command_handlers import handle_create_template

        container, mocks = _make_container()
        args = _make_args(file=str(tmp_path / "t.json"))
        args._container = container

        with patch(
            "orb.infrastructure.mocking.dry_run_context.is_dry_run_active", return_value=True
        ):
            result = await handle_create_template(args)

        assert isinstance(result, dict)
        assert result["dry_run"] is True
        mocks["create_orch"].execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# handle_delete_template
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleDeleteTemplate:
    @pytest.mark.asyncio
    async def test_missing_template_id_returns_error(self):
        """No template_id → InterfaceResponse exit_code=1."""
        from orb.interface.template_command_handlers import handle_delete_template

        container, _ = _make_container()
        args = _make_args()
        args._container = container

        with patch(
            "orb.infrastructure.mocking.dry_run_context.is_dry_run_active", return_value=False
        ):
            result = await handle_delete_template(args)

        assert isinstance(result, InterfaceResponse)
        assert result.exit_code == 1

    @pytest.mark.asyncio
    async def test_dry_run_returns_dry_run_dict(self):
        """is_dry_run_active=True → dry-run dict without calling orchestrator."""
        from orb.interface.template_command_handlers import handle_delete_template

        container, mocks = _make_container()
        args = _make_args(template_id="t1")
        args._container = container

        with patch(
            "orb.infrastructure.mocking.dry_run_context.is_dry_run_active", return_value=True
        ):
            result = await handle_delete_template(args)

        assert isinstance(result, dict)
        assert result["dry_run"] is True
        mocks["delete_orch"].execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_force_flag_returns_error(self):
        """force=False → format_error called about --force flag."""
        from orb.interface.template_command_handlers import handle_delete_template

        container, mocks = _make_container()
        args = _make_args(template_id="t1", force=False)
        args._container = container

        with patch(
            "orb.infrastructure.mocking.dry_run_context.is_dry_run_active", return_value=False
        ):
            await handle_delete_template(args)

        mocks["formatter"].format_error.assert_called_once()
        mocks["delete_orch"].execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_entity_not_found_returns_not_found_response(self):
        """EntityNotFoundError → InterfaceResponse exit_code=1 not_found."""
        from orb.interface.template_command_handlers import handle_delete_template

        container, mocks = _make_container()
        mocks["delete_orch"].execute = AsyncMock(side_effect=EntityNotFoundError("template", "t1"))

        args = _make_args(template_id="t1", force=True)
        args._container = container

        with patch(
            "orb.infrastructure.mocking.dry_run_context.is_dry_run_active", return_value=False
        ):
            result = await handle_delete_template(args)

        assert isinstance(result, InterfaceResponse)
        assert result.exit_code == 1

    @pytest.mark.asyncio
    async def test_delete_failed_returns_error_response(self):
        """Orchestrator returns deleted=False → InterfaceResponse exit_code=1."""
        from orb.application.services.orchestration.dtos import DeleteTemplateOutput
        from orb.interface.template_command_handlers import handle_delete_template

        container, mocks = _make_container()
        mocks["delete_orch"].execute = AsyncMock(
            return_value=DeleteTemplateOutput(template_id="t1", deleted=False)
        )

        args = _make_args(template_id="t1", force=True)
        args._container = container

        with patch(
            "orb.infrastructure.mocking.dry_run_context.is_dry_run_active", return_value=False
        ):
            result = await handle_delete_template(args)

        assert isinstance(result, InterfaceResponse)
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# handle_validate_template
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleValidateTemplate:
    @pytest.mark.asyncio
    async def test_no_args_returns_error(self):
        """No template_id, no file, no all → InterfaceResponse exit_code=1."""
        from orb.interface.template_command_handlers import handle_validate_template

        container, _ = _make_container()
        args = _make_args()
        args._container = container

        result = await handle_validate_template(args)

        assert isinstance(result, InterfaceResponse)
        assert result.exit_code == 1

    @pytest.mark.asyncio
    async def test_template_id_path_happy(self):
        """template_id provided → ValidateTemplateOrchestrator called."""
        from orb.interface.template_command_handlers import handle_validate_template

        container, mocks = _make_container()
        args = _make_args(template_id="t1")
        args._container = container

        await handle_validate_template(args)

        mocks["validate_orch"].execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_template_id_entity_not_found_returns_error(self):
        """template_id path EntityNotFoundError → formatter.format_error."""
        from orb.interface.template_command_handlers import handle_validate_template

        container, mocks = _make_container()
        mocks["validate_orch"].execute = AsyncMock(
            side_effect=EntityNotFoundError("template", "t1")
        )

        args = _make_args(template_id="t1")
        args._container = container

        await handle_validate_template(args)

        mocks["formatter"].format_error.assert_called_once()

    @pytest.mark.asyncio
    async def test_file_not_found_returns_error(self, tmp_path):
        """Non-existent file → InterfaceResponse exit_code=1."""
        from orb.interface.template_command_handlers import handle_validate_template

        container, _ = _make_container()
        args = _make_args(file=str(tmp_path / "ghost.json"))
        args._container = container

        result = await handle_validate_template(args)

        assert isinstance(result, InterfaceResponse)
        assert result.exit_code == 1

    @pytest.mark.asyncio
    async def test_file_json_path_happy(self, tmp_path):
        """Valid JSON file → ValidateTemplateOrchestrator called with config."""
        from orb.interface.template_command_handlers import handle_validate_template

        data = {"template_id": "file-t1", "provider_api": "EC2Fleet"}
        f = tmp_path / "t.json"
        f.write_text(json.dumps(data))

        container, mocks = _make_container()
        args = _make_args(file=str(f))
        args._container = container

        await handle_validate_template(args)

        mocks["validate_orch"].execute.assert_awaited_once()
        call_input = mocks["validate_orch"].execute.call_args[0][0]
        assert call_input.template_id == "file-t1"

    @pytest.mark.asyncio
    async def test_all_flag_iterates_templates(self):
        """all=True → ListTemplatesOrchestrator called, then ValidateTemplate for each."""
        from orb.application.services.orchestration.dtos import (
            ListTemplatesOutput,
            ValidateTemplateOutput,
        )
        from orb.interface.template_command_handlers import handle_validate_template

        container, mocks = _make_container()
        mocks["list_orch"].execute = AsyncMock(
            return_value=ListTemplatesOutput(
                templates=[{"template_id": "t1"}, {"template_id": "t2"}]
            )
        )
        mocks["validate_orch"].execute = AsyncMock(
            return_value=ValidateTemplateOutput(
                template_id="t1", valid=True, errors=[], message="ok"
            )
        )

        args = _make_args(all=True)
        args._container = container

        result = await handle_validate_template(args)

        assert isinstance(result, dict)
        assert result["success"] is True
        assert mocks["validate_orch"].execute.await_count == 2


# ---------------------------------------------------------------------------
# handle_refresh_templates
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleRefreshTemplates:
    @pytest.mark.asyncio
    async def test_calls_refresh_orchestrator_and_returns_formatter_result(self):
        """handle_refresh_templates calls RefreshTemplatesOrchestrator."""
        from orb.interface.template_command_handlers import handle_refresh_templates

        container, mocks = _make_container()
        args = _make_args(provider_name="aws-prod")
        args._container = container

        await handle_refresh_templates(args)

        mocks["refresh_orch"].execute.assert_awaited_once()
        call_input = mocks["refresh_orch"].execute.call_args[0][0]
        assert call_input.provider_name == "aws-prod"


# ---------------------------------------------------------------------------
# handle_get_multiple_templates
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleGetMultipleTemplates:
    @pytest.mark.asyncio
    async def test_no_ids_returns_error(self):
        """No template IDs provided → error dict returned."""
        from orb.interface.template_command_handlers import handle_get_multiple_templates

        container, _ = _make_container()
        args = _make_args()
        args._container = container

        result = await handle_get_multiple_templates(args)

        assert "error" in result

    @pytest.mark.asyncio
    async def test_template_ids_forwarded_to_query_bus(self):
        """template_ids list → QueryBus.execute called with GetMultipleTemplatesQuery."""
        from orb.infrastructure.di.buses import QueryBus
        from orb.interface.template_command_handlers import handle_get_multiple_templates

        mock_qbus = MagicMock(spec=QueryBus)

        # QueryBus returns whatever the query handler returns — mock it as a plain object
        mock_result = MagicMock()
        mock_result.templates = []
        mock_result.found_count = 0
        mock_result.not_found_ids = []
        mock_result.total_requested = 0
        mock_qbus.execute = AsyncMock(return_value=mock_result)

        container, _unused = _make_container()
        # Wire the mock query bus into the dispatch via side_effect override
        orig_side_effect = container.get.side_effect

        def _get(cls):
            if cls is QueryBus:
                return mock_qbus
            return orig_side_effect(cls)

        container.get.side_effect = _get
        args = _make_args(template_ids=["t1", "t2"])
        args._container = container

        result = await handle_get_multiple_templates(args)

        mock_qbus.execute.assert_awaited_once()
        assert "templates" in result
        assert "found_count" in result

    @pytest.mark.asyncio
    async def test_flag_ids_also_accepted(self):
        """flag_ids attribute is accepted as an additional source of IDs."""
        from orb.infrastructure.di.buses import QueryBus
        from orb.interface.template_command_handlers import handle_get_multiple_templates

        mock_qbus = MagicMock(spec=QueryBus)
        mock_result = MagicMock()
        mock_result.templates = []
        mock_result.found_count = 0
        mock_result.not_found_ids = []
        mock_result.total_requested = 1
        mock_qbus.execute = AsyncMock(return_value=mock_result)

        container, _ = _make_container()
        orig_side_effect = container.get.side_effect

        def _get(cls):
            if cls is QueryBus:
                return mock_qbus
            return orig_side_effect(cls)

        container.get.side_effect = _get
        args = _make_args(flag_ids=["flag-t1"])
        args._container = container

        await handle_get_multiple_templates(args)

        mock_qbus.execute.assert_awaited_once()
        query_arg = mock_qbus.execute.call_args[0][0]
        assert "flag-t1" in query_arg.template_ids
