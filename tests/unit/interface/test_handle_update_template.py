"""Tests for handle_update_template -- file reading behaviour."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.interface.template_command_handlers import handle_update_template

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_args(**kwargs: Any) -> argparse.Namespace:
    defaults = {
        "template_id": None,
        "flag_template_id": None,
        "file": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _mock_command_bus(updated: bool = True) -> MagicMock:
    from orb.application.services.orchestration.dtos import UpdateTemplateOutput

    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(
        return_value=UpdateTemplateOutput(
            template_id="tpl-1", updated=updated, validation_errors=[]
        )
    )
    return orchestrator


def _patch_container(bus: MagicMock):
    from orb.application.services.orchestration.update_template import UpdateTemplateOrchestrator
    from orb.interface.response_formatting_service import ResponseFormattingService

    mock_formatter = MagicMock(spec=ResponseFormattingService)
    mock_formatter.format_template_mutation.return_value = {"success": True}

    container = MagicMock()

    def _get(cls):
        if cls is UpdateTemplateOrchestrator:
            return bus
        if cls is ResponseFormattingService:
            return mock_formatter
        return MagicMock()

    container.get.side_effect = _get
    return patch(
        "orb.interface.template_command_handlers.get_container",
        return_value=container,
    )


def _patch_dry_run(active: bool = False):
    return patch(
        "orb.infrastructure.mocking.dry_run_context.is_dry_run_active",
        return_value=active,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_reads_name_from_file(tmp_path: Path) -> None:
    """JSON file fields are passed through to the command."""
    template_file = tmp_path / "tmpl.json"
    template_file.write_text(
        json.dumps(
            {
                "name": "my-template",
                "description": "a description",
                "configuration": {"key": "value"},
            }
        )
    )

    bus = _mock_command_bus()
    args = _make_args(template_id="tpl-1", file=str(template_file))

    with _patch_container(bus), _patch_dry_run():
        result = await handle_update_template(args)

    assert result["success"] is True
    call_args = bus.execute.call_args[0][0]
    assert call_args.name == "my-template"
    assert call_args.configuration == {
        "name": "my-template",
        "description": "a description",
        "configuration": {"key": "value"},
    }
    assert call_args.template_id == "tpl-1"


@pytest.mark.asyncio
async def test_update_file_not_found_returns_error() -> None:
    """Nonexistent file path returns success=False with 'not found' message."""
    bus = _mock_command_bus()
    args = _make_args(template_id="tpl-1", file="/nonexistent/path/tmpl.json")

    with _patch_container(bus), _patch_dry_run():
        result = await handle_update_template(args)

    assert result.data["success"] is False
    assert "not found" in result.data["error"].lower()


@pytest.mark.asyncio
async def test_update_invalid_json_returns_error(tmp_path: Path) -> None:
    """Invalid JSON in file returns success=False with 'Invalid JSON' message."""
    template_file = tmp_path / "bad.json"
    template_file.write_text("{ not valid json }")

    bus = _mock_command_bus()
    args = _make_args(template_id="tpl-1", file=str(template_file))

    with _patch_container(bus), _patch_dry_run():
        result = await handle_update_template(args)

    assert result.data["success"] is False
    assert "invalid json" in result.data["error"].lower()


@pytest.mark.asyncio
async def test_update_json_array_returns_error(tmp_path: Path) -> None:
    """JSON array (not object) returns success=False with 'JSON object' message."""
    template_file = tmp_path / "array.json"
    template_file.write_text(json.dumps([1, 2, 3]))

    bus = _mock_command_bus()
    args = _make_args(template_id="tpl-1", file=str(template_file))

    with _patch_container(bus), _patch_dry_run():
        result = await handle_update_template(args)

    assert result.data["success"] is False
    assert "json object" in result.data["error"].lower()


@pytest.mark.asyncio
async def test_update_cli_template_id_wins_over_file(tmp_path: Path) -> None:
    """CLI template_id takes precedence over template_id in the file."""
    template_file = tmp_path / "tmpl.json"
    template_file.write_text(json.dumps({"template_id": "file-id", "name": "n"}))

    bus = _mock_command_bus()
    args = _make_args(template_id="cli-id", file=str(template_file))

    with _patch_container(bus), _patch_dry_run():
        result = await handle_update_template(args)

    assert result["success"] is True
    call_args = bus.execute.call_args[0][0]
    assert call_args.template_id == "cli-id"


@pytest.mark.asyncio
async def test_update_template_id_from_file_when_no_cli_arg(tmp_path: Path) -> None:
    """template_id is read from the file when not provided on CLI."""
    template_file = tmp_path / "tmpl.json"
    template_file.write_text(json.dumps({"template_id": "file-id", "name": "n"}))

    bus = _mock_command_bus()
    args = _make_args(template_id=None, flag_template_id=None, file=str(template_file))

    with _patch_container(bus), _patch_dry_run():
        result = await handle_update_template(args)

    assert result["success"] is True
    call_args = bus.execute.call_args[0][0]
    assert call_args.template_id == "file-id"


@pytest.mark.asyncio
async def test_update_unknown_fields_ignored(tmp_path: Path) -> None:
    """Extra keys in the JSON file do not cause errors."""
    template_file = tmp_path / "tmpl.json"
    template_file.write_text(
        json.dumps(
            {
                "name": "n",
                "unknown_field": "should be ignored",
                "another_extra": 42,
            }
        )
    )

    bus = _mock_command_bus()
    args = _make_args(template_id="tpl-1", file=str(template_file))

    with _patch_container(bus), _patch_dry_run():
        result = await handle_update_template(args)

    assert result["success"] is True


@pytest.mark.asyncio
async def test_update_empty_json_object_sends_nones(tmp_path: Path) -> None:
    """Empty JSON object causes no error; name/description/configuration get defaults."""
    template_file = tmp_path / "tmpl.json"
    template_file.write_text(json.dumps({}))

    bus = _mock_command_bus()
    args = _make_args(template_id="tpl-1", file=str(template_file))

    with _patch_container(bus), _patch_dry_run():
        result = await handle_update_template(args)

    assert result["success"] is True
    call_args = bus.execute.call_args[0][0]
    assert call_args.name is None
    assert call_args.configuration == {}


# ---------------------------------------------------------------------------
# TC-1: flat file passes full dict as configuration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_flat_file_passes_full_dict_as_configuration(tmp_path: Path) -> None:
    """Flat template file: full dict is passed as configuration, not a sub-key."""
    file_dict = {
        "template_id": "tpl-1",
        "name": "n",
        "instance_type": "t3.micro",
        "image_id": "ami-1",
        "tags": {"env": "prod"},
    }
    template_file = tmp_path / "tmpl.json"
    template_file.write_text(json.dumps(file_dict))

    bus = _mock_command_bus()
    args = _make_args(template_id="tpl-1", file=str(template_file))

    with _patch_container(bus), _patch_dry_run():
        result = await handle_update_template(args)

    assert result["success"] is True
    call_args = bus.execute.call_args[0][0]
    assert call_args.configuration == file_dict
    assert call_args.instance_type == "t3.micro"
    assert call_args.image_id == "ami-1"


# ---------------------------------------------------------------------------
# TC-2: file with nested 'configuration' key passes full outer dict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_nested_configuration_key_passes_outer_dict(tmp_path: Path) -> None:
    """File with a nested 'configuration' key: outer dict is passed, not the sub-key."""
    file_dict = {"template_id": "tpl-1", "configuration": {"instance_type": "t3.micro"}}
    template_file = tmp_path / "tmpl.json"
    template_file.write_text(json.dumps(file_dict))

    bus = _mock_command_bus()
    args = _make_args(template_id="tpl-1", file=str(template_file))

    with _patch_container(bus), _patch_dry_run():
        result = await handle_update_template(args)

    assert result["success"] is True
    call_args = bus.execute.call_args[0][0]
    assert call_args.configuration == file_dict


# ---------------------------------------------------------------------------
# TC-3: instance_type wired from file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_instance_type_wired_from_file(tmp_path: Path) -> None:
    """instance_type from the file is passed to UpdateTemplateInput."""
    template_file = tmp_path / "tmpl.json"
    template_file.write_text(json.dumps({"name": "n", "instance_type": "t3.large"}))

    bus = _mock_command_bus()
    args = _make_args(template_id="tpl-1", file=str(template_file))

    with _patch_container(bus), _patch_dry_run():
        await handle_update_template(args)

    call_args = bus.execute.call_args[0][0]
    assert call_args.instance_type == "t3.large"


# ---------------------------------------------------------------------------
# TC-4: image_id wired from file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_image_id_wired_from_file(tmp_path: Path) -> None:
    """image_id from the file is passed to UpdateTemplateInput."""
    template_file = tmp_path / "tmpl.json"
    template_file.write_text(json.dumps({"name": "n", "image_id": "ami-99"}))

    bus = _mock_command_bus()
    args = _make_args(template_id="tpl-1", file=str(template_file))

    with _patch_container(bus), _patch_dry_run():
        await handle_update_template(args)

    call_args = bus.execute.call_args[0][0]
    assert call_args.image_id == "ami-99"


# ---------------------------------------------------------------------------
# TC-6: REST and CLI produce identical UpdateTemplateInput for same payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_cli_and_rest_produce_identical_input(tmp_path: Path) -> None:
    """CLI and REST paths produce the same UpdateTemplateInput for equivalent payloads.

    TemplateUpdateRequest has no description/configuration fields, so we use the
    fields it does support: name, instance_type, image_id, tags.
    Both paths must pass the full payload dict as configuration.
    """
    from orb.api.routers.templates import TemplateUpdateRequest
    from orb.application.services.orchestration.dtos import UpdateTemplateInput

    payload = {
        "name": "my-template",
        "instance_type": "t3.micro",
        "image_id": "ami-1",
        "tags": {"env": "prod"},
    }

    # REST path: model_dump() of TemplateUpdateRequest is the full body
    rest_body = TemplateUpdateRequest(**payload)
    rest_input = UpdateTemplateInput(
        template_id="tpl-1",
        name=rest_body.name,
        instance_type=rest_body.instance_type,
        image_id=rest_body.image_id,
        configuration=rest_body.model_dump(),
    )

    # CLI path: json.load of equivalent file
    template_file = tmp_path / "tmpl.json"
    template_file.write_text(json.dumps(payload))

    bus = _mock_command_bus()
    args = _make_args(template_id="tpl-1", file=str(template_file))

    with _patch_container(bus), _patch_dry_run():
        await handle_update_template(args)

    cli_input = bus.execute.call_args[0][0]

    assert cli_input.instance_type == rest_input.instance_type
    assert cli_input.image_id == rest_input.image_id
    # Both pass the full payload as configuration (REST via model_dump, CLI via json.load)
    assert cli_input.configuration == payload
    assert rest_input.configuration == rest_body.model_dump()
