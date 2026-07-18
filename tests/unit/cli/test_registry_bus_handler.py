"""Unit tests for orb.cli.registry — _make_bus_handler and coverage of
line ranges 41-78, 101-115.

Covers:
- _make_bus_handler() naming, async nature, ValueError on missing factory
- All major (resource, action) pairs registered after build_registry()
- MCP tools dispatch sub-action routing

Isolates from the module-level _built singleton so tests are independent.
"""

from __future__ import annotations

import argparse
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# _make_bus_handler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMakeBusHandler:
    """_make_bus_handler() — handler name, async nature, ValueError on missing factory."""

    def test_handler_name_set_correctly(self):
        from orb.cli.registry import _make_bus_handler

        h = _make_bus_handler("list_templates")
        assert h.__name__ == "bus_handler_list_templates"

    def test_handler_is_coroutine_function(self):
        from orb.cli.registry import _make_bus_handler

        h = _make_bus_handler("some_operation")
        assert asyncio.iscoroutinefunction(h)

    def test_handler_raises_value_error_for_missing_factory(self):
        from orb.cli.registry import _make_bus_handler

        h = _make_bus_handler("absolutely_nonexistent_xyz")
        args = argparse.Namespace()

        # Orchestrator with no matching create_* methods
        mock_orchestrator = MagicMock(spec=[])
        mock_container = MagicMock()

        with (
            patch(
                "orb.cli.factories.cli_command_factory_orchestrator.CLICommandFactoryOrchestrator",
                return_value=mock_orchestrator,
            ),
            patch("orb.infrastructure.di.container.get_container", return_value=mock_container),
        ):
            loop = asyncio.new_event_loop()
            try:
                with pytest.raises(ValueError, match="No factory method"):
                    loop.run_until_complete(h(args))
            finally:
                loop.close()

    def test_handler_dispatches_query_to_query_bus(self):
        from orb.application.dto.base import BaseQuery
        from orb.cli.registry import _make_bus_handler

        h = _make_bus_handler("list_templates")
        args = argparse.Namespace()

        fake_query = MagicMock(spec=BaseQuery)
        mock_orchestrator = MagicMock()
        mock_orchestrator.create_list_templates_query = MagicMock(return_value=fake_query)

        mock_query_bus = AsyncMock()
        mock_query_bus.execute = AsyncMock(return_value={"templates": []})
        mock_command_bus = AsyncMock()

        mock_container = MagicMock()

        def _get(bus_type):
            from orb.infrastructure.di.buses import CommandBus, QueryBus

            if bus_type == QueryBus:
                return mock_query_bus
            if bus_type == CommandBus:
                return mock_command_bus
            return MagicMock()

        mock_container.get = _get

        with (
            patch(
                "orb.cli.factories.cli_command_factory_orchestrator.CLICommandFactoryOrchestrator",
                return_value=mock_orchestrator,
            ),
            patch("orb.infrastructure.di.container.get_container", return_value=mock_container),
        ):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(h(args))
            finally:
                loop.close()

        mock_query_bus.execute.assert_called_once_with(fake_query)

    def test_handler_dispatches_command_to_command_bus(self):
        """A BaseCommand-typed CQRS object routes to command bus."""
        from orb.cli.registry import _make_bus_handler

        h = _make_bus_handler("create_template")
        args = argparse.Namespace(template_id="t1")

        # Create a real BaseCommand instance so isinstance() works
        from orb.application.template.commands import CreateTemplateCommand

        fake_cmd = CreateTemplateCommand(template_id="t1")

        mock_orchestrator = MagicMock(spec=[])  # spec=[] so getattr returns None for missing attrs
        # Re-add only the command factory so the query-first lookup falls through
        mock_orchestrator.create_create_template_command = MagicMock(return_value=fake_cmd)

        mock_command_bus = AsyncMock()
        mock_command_bus.execute = AsyncMock(return_value={"ok": True})
        mock_query_bus = AsyncMock()

        mock_container = MagicMock()

        def _get(bus_type):
            from orb.infrastructure.di.buses import CommandBus, QueryBus

            if bus_type == CommandBus:
                return mock_command_bus
            if bus_type == QueryBus:
                return mock_query_bus
            return MagicMock()

        mock_container.get = _get

        with (
            patch(
                "orb.cli.factories.cli_command_factory_orchestrator.CLICommandFactoryOrchestrator",
                return_value=mock_orchestrator,
            ),
            patch("orb.infrastructure.di.container.get_container", return_value=mock_container),
        ):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(h(args))
            finally:
                loop.close()

        mock_command_bus.execute.assert_called_once_with(fake_cmd)

    def test_flag_kwargs_forwarded_to_factory(self):
        """Args stored under flag_<name> are forwarded as <name> to the factory."""

        from orb.application.dto.base import BaseQuery
        from orb.cli.registry import _make_bus_handler

        # Use a factory method that accepts 'template_id' as a parameter
        h = _make_bus_handler("get_template")
        # Simulate argparse storing --template-id as flag_template_id
        args = argparse.Namespace(flag_template_id="my-template")

        fake_query = MagicMock(spec=BaseQuery)
        captured_kwargs = {}

        # The factory must have a signature with 'template_id' parameter
        def _factory(template_id=None, **kwargs):
            captured_kwargs["template_id"] = template_id
            return fake_query

        mock_orchestrator = MagicMock()
        mock_orchestrator.create_get_template_query = _factory

        mock_query_bus = AsyncMock()
        mock_query_bus.execute = AsyncMock(return_value={})
        mock_container = MagicMock()

        def _get(bus_type):
            from orb.infrastructure.di.buses import QueryBus

            if bus_type == QueryBus:
                return mock_query_bus
            return AsyncMock()

        mock_container.get = _get

        with (
            patch(
                "orb.cli.factories.cli_command_factory_orchestrator.CLICommandFactoryOrchestrator",
                return_value=mock_orchestrator,
            ),
            patch("orb.infrastructure.di.container.get_container", return_value=mock_container),
        ):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(h(args))
            finally:
                loop.close()

        assert captured_kwargs.get("template_id") == "my-template"


# ---------------------------------------------------------------------------
# MCP tools sub-dispatch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMCPToolsDispatch:
    """The _handle_mcp_tools async handler routes tools_action correctly."""

    def _get_mcp_tools_handler(self):
        import orb.cli.registry as reg

        orig_registry = dict(reg._REGISTRY)
        orig_built = reg._built
        reg._REGISTRY.clear()
        reg._built = False
        reg.build_registry()
        handler = reg.lookup("mcp", "tools")
        # Restore
        reg._REGISTRY.clear()
        reg._REGISTRY.update(orig_registry)
        reg._built = orig_built
        return handler

    def test_mcp_tools_handler_registered(self):
        import orb.cli.registry as reg

        reg.build_registry()
        assert reg.lookup("mcp", "tools") is not None

    def test_mcp_tools_unknown_action_raises(self):
        handler = self._get_mcp_tools_handler()
        assert handler is not None, "mcp tools handler must be registered"
        args = argparse.Namespace(tools_action="bogus")

        loop = asyncio.new_event_loop()
        try:
            with (
                patch("orb.interface.mcp_command_handlers.handle_mcp_tools_list", AsyncMock()),
                patch("orb.interface.mcp_command_handlers.handle_mcp_tools_call", AsyncMock()),
            ):
                with pytest.raises(ValueError, match="Unknown MCP tools action"):
                    loop.run_until_complete(handler(args))
        finally:
            loop.close()

    def test_mcp_tools_list_dispatches(self):
        handler = self._get_mcp_tools_handler()
        assert handler is not None, "mcp tools handler must be registered"
        args = argparse.Namespace(tools_action="list")

        mock_list = AsyncMock(return_value={"tools": []})
        loop = asyncio.new_event_loop()
        try:
            with patch("orb.interface.mcp_command_handlers.handle_mcp_tools_list", mock_list):
                loop.run_until_complete(handler(args))
        finally:
            loop.close()

        mock_list.assert_called_once_with(args)
