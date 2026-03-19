"""Tests for MCP server tool schema — Tasks 2 & 3.

Verifies that _get_tool_schema returns accurate per-tool property dicts
and that _handle_tools_list uses correct required fields (including
machine_count for request_machines, not the old 'count').
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch


def _make_server():
    """Instantiate OpenResourceBrokerMCPServer with all handler imports mocked."""
    with patch("orb.interface.mcp.server.core.get_logger", return_value=MagicMock()):
        # Patch all handler imports so the server can be constructed without DI
        with (
            patch("orb.interface.request_command_handlers.get_container", return_value=MagicMock()),
            patch("orb.interface.system_command_handlers.get_container", return_value=MagicMock()),
            patch(
                "orb.interface.template_command_handlers.get_container", return_value=MagicMock()
            ),
        ):
            from orb.interface.mcp.server.core import OpenResourceBrokerMCPServer

            server = OpenResourceBrokerMCPServer()
    return server


class TestGetToolSchema:
    def test_get_tool_schema_return_machines_excludes_template_fields(self):
        server = _make_server()
        schema = server._get_tool_schema("return_machines")
        assert "template_id" not in schema

    def test_get_tool_schema_list_requests_contains_status_limit_offset(self):
        server = _make_server()
        schema = server._get_tool_schema("list_requests")
        assert {"status", "limit", "offset"} <= set(schema.keys())

    def test_get_tool_schema_list_templates_contains_active_only_provider_api_limit_offset(self):
        server = _make_server()
        schema = server._get_tool_schema("list_templates")
        assert {"active_only", "provider_api", "limit", "offset"} <= set(schema.keys())

    def test_get_tool_schema_request_machines_has_machine_count_in_required(self):
        server = _make_server()
        result = asyncio.run(server._handle_tools_list({}))
        tools = {t["name"]: t for t in result["tools"]}
        assert "request_machines" in tools
        required = tools["request_machines"]["inputSchema"]["required"]
        assert "machine_count" in required

    def test_handle_tools_call_request_machines_args_has_machine_count(self):
        server = _make_server()
        captured = {}

        async def fake_tool(args):
            captured["machine_count"] = getattr(args, "machine_count", None)
            return {"ok": True}

        server.tools["request_machines"] = fake_tool
        asyncio.run(
            server._handle_tools_call(
                {"name": "request_machines", "arguments": {"machine_count": 2, "template_id": "t1"}}
            )
        )
        assert captured["machine_count"] == 2
