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
    response = MagicMock()
    response.validation_errors = []
    bus = MagicMock()
    bus.execute = AsyncMock(return_value=response)
    return bus


def _patch_container(bus: MagicMock):
    container = MagicMock()
    container.get.return_value = bus
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
    assert call_args.description == "a description"
    assert call_args.configuration == {"key": "value"}
    assert call_args.template_id == "tpl-1"


@pytest.mark.asyncio
async def test_update_file_not_found_returns_error() -> None:
    """Nonexistent file path returns success=False with 'not found' message."""
    bus = _mock_command_bus()
    args = _make_args(template_id="tpl-1", file="/nonexistent/path/tmpl.json")

    with _patch_container(bus), _patch_dry_run():
        result = await handle_update_template(args)

    assert result["success"] is False
    assert "not found" in result["error"].lower()


@pytest.mark.asyncio
async def test_update_invalid_json_returns_error(tmp_path: Path) -> None:
    """Invalid JSON in file returns success=False with 'Invalid JSON' message."""
    template_file = tmp_path / "bad.json"
    template_file.write_text("{ not valid json }")

    bus = _mock_command_bus()
    args = _make_args(template_id="tpl-1", file=str(template_file))

    with _patch_container(bus), _patch_dry_run():
        result = await handle_update_template(args)

    assert result["success"] is False
    assert "invalid json" in result["error"].lower()


@pytest.mark.asyncio
async def test_update_json_array_returns_error(tmp_path: Path) -> None:
    """JSON array (not object) returns success=False with 'JSON object' message."""
    template_file = tmp_path / "array.json"
    template_file.write_text(json.dumps([1, 2, 3]))

    bus = _mock_command_bus()
    args = _make_args(template_id="tpl-1", file=str(template_file))

    with _patch_container(bus), _patch_dry_run():
        result = await handle_update_template(args)

    assert result["success"] is False
    assert "json object" in result["error"].lower()


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
    assert call_args.description is None
    assert call_args.configuration == {}
