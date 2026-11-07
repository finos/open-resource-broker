"""Unit tests for application/commands/system_handlers.py.

Covers ReloadProviderConfigHandler, RefreshTemplatesHandler and
SetConfigurationHandler: happy path, error handling (results captured in
command.result per CQRS), and verification that the correct configuration /
template ports are invoked. Only abstract ports are mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.commands.system import (
    RefreshTemplatesCommand,
    ReloadProviderConfigCommand,
    SetConfigurationCommand,
)
from orb.application.commands.system_handlers import (
    RefreshTemplatesHandler,
    ReloadProviderConfigHandler,
    SetConfigurationHandler,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ports() -> dict:
    return {
        "logger": MagicMock(),
        "event_publisher": MagicMock(),
        "error_handler": MagicMock(),
    }


def _res(command) -> dict:
    """Narrow the optional CQRS result dict for assertions."""
    assert command.result is not None
    return command.result


# ---------------------------------------------------------------------------
# ReloadProviderConfigHandler
# ---------------------------------------------------------------------------


class TestReloadProviderConfigHandler:
    @pytest.mark.asyncio
    async def test_reloads_and_reports_success(self):
        provider_config = MagicMock()
        provider_config.get_mode.return_value.value = "strategy"
        active = MagicMock()
        active.name = "aws"
        provider_config.get_active_providers.return_value = [active]

        config_manager = MagicMock()
        config_manager.get_provider_config.return_value = provider_config

        handler = ReloadProviderConfigHandler(config_manager=config_manager, **_ports())
        command = ReloadProviderConfigCommand(config_path="/tmp/x.yaml")

        await handler.handle(command)

        config_manager.reload.assert_called_once_with("/tmp/x.yaml")
        assert _res(command)["status"] == "success"
        assert _res(command)["active_providers"] == ["aws"]

    @pytest.mark.asyncio
    async def test_failure_captured_in_result(self):
        config_manager = MagicMock()
        config_manager.reload.side_effect = RuntimeError("bad config")

        handler = ReloadProviderConfigHandler(config_manager=config_manager, **_ports())
        command = ReloadProviderConfigCommand(config_path="/tmp/x.yaml")

        await handler.handle(command)

        assert _res(command)["status"] == "failed"
        assert "bad config" in _res(command)["error"]

    @pytest.mark.asyncio
    async def test_no_provider_config_defaults(self):
        config_manager = MagicMock()
        config_manager.get_provider_config.return_value = None

        handler = ReloadProviderConfigHandler(config_manager=config_manager, **_ports())
        command = ReloadProviderConfigCommand()

        await handler.handle(command)

        assert _res(command)["status"] == "success"
        assert _res(command)["provider_mode"] == "strategy"
        assert _res(command)["active_providers"] == []


# ---------------------------------------------------------------------------
# RefreshTemplatesHandler
# ---------------------------------------------------------------------------


class TestRefreshTemplatesHandler:
    @pytest.mark.asyncio
    async def test_refreshes_and_reports_count(self):
        tmpl = MagicMock()
        tmpl.model_dump.return_value = {"template_id": "t1"}
        template_manager = MagicMock()
        template_manager.load_templates = AsyncMock(return_value=[tmpl])

        handler = RefreshTemplatesHandler(template_manager=template_manager, **_ports())
        command = RefreshTemplatesCommand(provider_name="aws")

        await handler.handle(command)

        template_manager.load_templates.assert_awaited_once_with(
            provider_override="aws", force_refresh=True
        )
        assert _res(command)["status"] == "success"
        assert _res(command)["template_count"] == 1
        # The loaded template objects are passed on as they are
        assert _res(command)["templates"] == [tmpl]

    @pytest.mark.asyncio
    async def test_failure_captured_in_result(self):
        template_manager = MagicMock()
        template_manager.load_templates = AsyncMock(side_effect=RuntimeError("no source"))

        handler = RefreshTemplatesHandler(template_manager=template_manager, **_ports())
        command = RefreshTemplatesCommand()

        await handler.handle(command)

        assert _res(command)["status"] == "failed"
        assert "no source" in _res(command)["error"]


# ---------------------------------------------------------------------------
# SetConfigurationHandler
# ---------------------------------------------------------------------------


class TestSetConfigurationHandler:
    @pytest.mark.asyncio
    async def test_sets_value_and_reports_success(self):
        config_manager = MagicMock()

        handler = SetConfigurationHandler(config_manager=config_manager, **_ports())
        command = SetConfigurationCommand(key="log.level", value="DEBUG")

        await handler.handle(command)

        config_manager.set_configuration_value.assert_called_once_with("log.level", "DEBUG")
        assert _res(command)["status"] == "success"
        assert _res(command)["key"] == "log.level"

    @pytest.mark.asyncio
    async def test_persists_to_disk_by_default(self):
        config_manager = MagicMock()
        config_manager.save_config.return_value = "/etc/orb/config.json"

        handler = SetConfigurationHandler(config_manager=config_manager, **_ports())
        command = SetConfigurationCommand(key="log.level", value="DEBUG")

        await handler.handle(command)

        config_manager.save_config.assert_called_once_with(None)
        assert _res(command)["persisted"] is True
        assert _res(command)["persisted_path"] == "/etc/orb/config.json"

    @pytest.mark.asyncio
    async def test_no_persist_skips_disk_write(self):
        config_manager = MagicMock()

        handler = SetConfigurationHandler(config_manager=config_manager, **_ports())
        command = SetConfigurationCommand(key="log.level", value="DEBUG", persist=False)

        await handler.handle(command)

        config_manager.set_configuration_value.assert_called_once_with("log.level", "DEBUG")
        config_manager.save_config.assert_not_called()
        assert _res(command)["persisted"] is False
        assert _res(command)["persisted_path"] is None

    @pytest.mark.asyncio
    async def test_failure_captured_in_result(self):
        config_manager = MagicMock()
        config_manager.set_configuration_value.side_effect = RuntimeError("locked")

        handler = SetConfigurationHandler(config_manager=config_manager, **_ports())
        command = SetConfigurationCommand(key="log.level", value="DEBUG")

        await handler.handle(command)

        assert _res(command)["status"] == "failed"
        assert "locked" in _res(command)["error"]

    @pytest.mark.asyncio
    async def test_missing_key_fails_validation(self):
        config_manager = MagicMock()

        handler = SetConfigurationHandler(config_manager=config_manager, **_ports())
        command = SetConfigurationCommand(key="", value="DEBUG")

        with pytest.raises(ValueError):
            await handler.handle(command)
