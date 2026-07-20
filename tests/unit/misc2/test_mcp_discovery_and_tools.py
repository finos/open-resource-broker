"""Unit tests for orb.mcp.discovery and orb.mcp.tools."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.mcp.discovery import MCPToolDefinition, MCPToolDiscovery
from orb.mcp.tools import OpenResourceBrokerMCPTools
from orb.sdk.discovery import MethodInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_method_info(
    name: str = "do_thing",
    description: str = "Does a thing",
    parameters: dict | None = None,
    handler_type: str = "command",
) -> MethodInfo:
    return MethodInfo(
        name=name,
        description=description,
        parameters=parameters or {},
        required_params=[],
        return_type=None,
        handler_type=handler_type,
        original_class=object,
    )


def _make_sdk(methods: dict[str, MethodInfo] | None = None, initialized: bool = True) -> MagicMock:
    """Return an SDK stub with the given method map."""
    sdk = MagicMock()
    sdk.initialized = initialized
    methods = methods or {}
    sdk.list_available_methods.return_value = list(methods.keys())
    sdk.get_method_info.side_effect = lambda name: methods.get(name)
    sdk.get_stats.return_value = {"total_methods": len(methods)}
    return sdk


# ---------------------------------------------------------------------------
# MCPToolDiscovery
# ---------------------------------------------------------------------------


class TestMCPToolDiscovery:
    @pytest.mark.unit
    def test_discover_raises_when_sdk_not_initialized(self) -> None:
        """discover_mcp_tools raises ValueError when SDK is not initialized."""
        discovery = MCPToolDiscovery()
        sdk = _make_sdk(initialized=False)
        with pytest.raises(ValueError, match="initialized"):
            discovery.discover_mcp_tools(sdk)

    @pytest.mark.unit
    def test_discover_returns_empty_dict_for_no_methods(self) -> None:
        """discover_mcp_tools returns empty dict when SDK has no methods."""
        discovery = MCPToolDiscovery()
        sdk = _make_sdk(methods={})
        result = discovery.discover_mcp_tools(sdk)
        assert result == {}

    @pytest.mark.unit
    def test_discover_populates_tools_dict(self) -> None:
        """discover_mcp_tools creates one MCPToolDefinition per SDK method."""
        mi = _make_method_info(name="list_machines", description="List machines")
        sdk = _make_sdk(methods={"list_machines": mi})
        discovery = MCPToolDiscovery()
        result = discovery.discover_mcp_tools(sdk)
        assert "list_machines" in result
        assert isinstance(result["list_machines"], MCPToolDefinition)

    @pytest.mark.unit
    def test_tool_definition_description_from_method_info(self) -> None:
        """Tool description comes from MethodInfo.description."""
        mi = _make_method_info(description="Custom description")
        sdk = _make_sdk(methods={"my_method": mi})
        discovery = MCPToolDiscovery()
        result = discovery.discover_mcp_tools(sdk)
        assert result["my_method"].description == "Custom description"

    @pytest.mark.unit
    def test_tool_definition_description_fallback_from_name(self) -> None:
        """Description is generated from method name when MethodInfo.description is empty."""
        mi = _make_method_info(name="do_action", description="")
        sdk = _make_sdk(methods={"do_action": mi})
        discovery = MCPToolDiscovery()
        result = discovery.discover_mcp_tools(sdk)
        assert (
            "do_action" in result["do_action"].description.lower()
            or "Do Action" in result["do_action"].description
        )

    @pytest.mark.unit
    def test_tool_definition_description_fallback_when_no_method_info(self) -> None:
        """Description is generated from method name when method_info is None."""
        sdk = _make_sdk(methods={"some_op": None})  # type: ignore[dict-item]
        discovery = MCPToolDiscovery()
        result = discovery.discover_mcp_tools(sdk)
        assert "some_op" in result
        assert (
            "some_op" in result["some_op"].description.lower()
            or "Some Op" in result["some_op"].description
        )

    @pytest.mark.unit
    def test_get_tool_definition_returns_none_for_unknown(self) -> None:
        """get_tool_definition returns None for unknown tool."""
        discovery = MCPToolDiscovery()
        assert discovery.get_tool_definition("unknown") is None

    @pytest.mark.unit
    def test_list_tool_names_after_discover(self) -> None:
        """list_tool_names returns discovered names."""
        mi1 = _make_method_info(name="a")
        mi2 = _make_method_info(name="b")
        sdk = _make_sdk(methods={"a": mi1, "b": mi2})
        discovery = MCPToolDiscovery()
        discovery.discover_mcp_tools(sdk)
        assert set(discovery.list_tool_names()) == {"a", "b"}

    @pytest.mark.unit
    def test_get_tools_list_structure(self) -> None:
        """get_tools_list returns list of dicts with name/description/inputSchema."""
        mi = _make_method_info(name="ping")
        sdk = _make_sdk(methods={"ping": mi})
        discovery = MCPToolDiscovery()
        discovery.discover_mcp_tools(sdk)
        tools_list = discovery.get_tools_list()
        assert len(tools_list) == 1
        assert tools_list[0]["name"] == "ping"
        assert "description" in tools_list[0]
        assert "inputSchema" in tools_list[0]

    @pytest.mark.unit
    def test_get_stats_returns_count(self) -> None:
        """get_stats returns tool count."""
        mi = _make_method_info(name="x")
        sdk = _make_sdk(methods={"x": mi})
        discovery = MCPToolDiscovery()
        discovery.discover_mcp_tools(sdk)
        stats = discovery.get_stats()
        assert stats["tools_discovered"] == 1


class TestMCPToolDiscoverySchemaGeneration:
    """Tests for _generate_schema and _convert_param_to_schema."""

    @pytest.mark.unit
    def test_schema_for_no_parameters_has_additionalProperties(self) -> None:
        """Schema with no parameters allows additionalProperties."""
        mi = _make_method_info(parameters=None)
        sdk = _make_sdk(methods={"op": mi})
        discovery = MCPToolDiscovery()
        result = discovery.discover_mcp_tools(sdk)
        schema = result["op"].input_schema
        assert schema.get("additionalProperties") is True

    @pytest.mark.unit
    def test_schema_for_string_parameter(self) -> None:
        """str-typed parameter maps to JSON 'string' type."""
        mi = _make_method_info(
            parameters={"name": {"type": str, "description": "A name", "required": True}}
        )
        sdk = _make_sdk(methods={"op": mi})
        discovery = MCPToolDiscovery()
        result = discovery.discover_mcp_tools(sdk)
        schema = result["op"].input_schema
        assert schema["properties"]["name"]["type"] == "string"
        assert "name" in schema.get("required", [])

    @pytest.mark.unit
    def test_schema_for_int_parameter(self) -> None:
        """int-typed parameter maps to JSON 'integer' type."""
        mi = _make_method_info(
            parameters={"count": {"type": int, "description": "Count", "required": False}}
        )
        sdk = _make_sdk(methods={"op": mi})
        discovery = MCPToolDiscovery()
        result = discovery.discover_mcp_tools(sdk)
        assert result["op"].input_schema["properties"]["count"]["type"] == "integer"

    @pytest.mark.unit
    def test_schema_for_float_parameter(self) -> None:
        """float-typed parameter maps to JSON 'number' type."""
        mi = _make_method_info(parameters={"score": {"type": float, "description": "Score"}})
        sdk = _make_sdk(methods={"op": mi})
        discovery = MCPToolDiscovery()
        result = discovery.discover_mcp_tools(sdk)
        assert result["op"].input_schema["properties"]["score"]["type"] == "number"

    @pytest.mark.unit
    def test_schema_for_bool_parameter(self) -> None:
        """bool-typed parameter maps to JSON 'boolean' type."""
        mi = _make_method_info(parameters={"flag": {"type": bool, "description": "Flag"}})
        sdk = _make_sdk(methods={"op": mi})
        discovery = MCPToolDiscovery()
        result = discovery.discover_mcp_tools(sdk)
        assert result["op"].input_schema["properties"]["flag"]["type"] == "boolean"

    @pytest.mark.unit
    def test_schema_for_list_parameter(self) -> None:
        """list-typed parameter maps to JSON 'array' type."""
        mi = _make_method_info(parameters={"items": {"type": list, "description": "Items"}})
        sdk = _make_sdk(methods={"op": mi})
        discovery = MCPToolDiscovery()
        result = discovery.discover_mcp_tools(sdk)
        assert result["op"].input_schema["properties"]["items"]["type"] == "array"

    @pytest.mark.unit
    def test_schema_for_dict_parameter(self) -> None:
        """dict-typed parameter maps to JSON 'object' type."""
        mi = _make_method_info(parameters={"data": {"type": dict, "description": "Data"}})
        sdk = _make_sdk(methods={"op": mi})
        discovery = MCPToolDiscovery()
        result = discovery.discover_mcp_tools(sdk)
        assert result["op"].input_schema["properties"]["data"]["type"] == "object"

    @pytest.mark.unit
    def test_schema_for_unknown_type_defaults_to_string(self) -> None:
        """Unknown type defaults to JSON 'string' type."""
        mi = _make_method_info(parameters={"x": {"type": "SomeCustomType", "description": "X"}})
        sdk = _make_sdk(methods={"op": mi})
        discovery = MCPToolDiscovery()
        result = discovery.discover_mcp_tools(sdk)
        assert result["op"].input_schema["properties"]["x"]["type"] == "string"

    @pytest.mark.unit
    def test_schema_for_list_type_maps_to_array(self) -> None:
        """list type (not string annotation) maps to 'array'."""
        mi = _make_method_info(parameters={"ids": {"type": list, "description": "IDs"}})
        sdk = _make_sdk(methods={"op": mi})
        discovery = MCPToolDiscovery()
        result = discovery.discover_mcp_tools(sdk)
        assert result["op"].input_schema["properties"]["ids"]["type"] == "array"

    @pytest.mark.unit
    def test_schema_required_only_includes_required_params(self) -> None:
        """Only required=True params appear in the required list."""
        mi = _make_method_info(
            parameters={
                "req_param": {"type": str, "description": "Required", "required": True},
                "opt_param": {"type": str, "description": "Optional", "required": False},
            }
        )
        sdk = _make_sdk(methods={"op": mi})
        discovery = MCPToolDiscovery()
        result = discovery.discover_mcp_tools(sdk)
        schema = result["op"].input_schema
        required = schema.get("required", [])
        assert "req_param" in required
        assert "opt_param" not in required


# ---------------------------------------------------------------------------
# OpenResourceBrokerMCPTools
# ---------------------------------------------------------------------------


class TestMCPTools:
    @pytest.mark.unit
    def test_list_tools_raises_when_not_initialized(self) -> None:
        """list_tools raises ValueError when not initialized."""
        sdk = _make_sdk()
        sdk.initialized = True
        tools = OpenResourceBrokerMCPTools(sdk=sdk)
        with pytest.raises(ValueError, match="not initialized"):
            tools.list_tools()

    @pytest.mark.unit
    def test_get_tool_info_returns_none_when_not_initialized(self) -> None:
        """get_tool_info returns None when not initialized."""
        sdk = _make_sdk()
        tools = OpenResourceBrokerMCPTools(sdk=sdk)
        assert tools.get_tool_info("any") is None

    @pytest.mark.unit
    def test_get_tools_by_type_returns_empty_when_not_initialized(self) -> None:
        """get_tools_by_type returns empty list when not initialized."""
        sdk = _make_sdk()
        tools = OpenResourceBrokerMCPTools(sdk=sdk)
        assert tools.get_tools_by_type("command") == []

    @pytest.mark.unit
    def test_initialized_property_false_initially(self) -> None:
        """initialized property is False before initialize()."""
        sdk = _make_sdk()
        tools = OpenResourceBrokerMCPTools(sdk=sdk)
        assert tools.initialized is False

    @pytest.mark.unit
    def test_get_stats_not_initialized(self) -> None:
        """get_stats returns initialized=False when not yet initialized."""
        sdk = _make_sdk()
        sdk.initialized = False
        tools = OpenResourceBrokerMCPTools(sdk=sdk)
        stats = tools.get_stats()
        assert stats["initialized"] is False
        assert stats["tools_discovered"] == 0

    @pytest.mark.unit
    def test_repr_not_initialized(self) -> None:
        """__repr__ includes 'not initialized' status."""
        sdk = _make_sdk()
        tools = OpenResourceBrokerMCPTools(sdk=sdk)
        assert "not initialized" in repr(tools)

    @pytest.mark.unit
    def test_call_tool_raises_when_not_initialized(self) -> None:
        """call_tool raises ValueError when not initialized."""
        sdk = _make_sdk()
        tools = OpenResourceBrokerMCPTools(sdk=sdk)
        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(ValueError, match="not initialized"):
                loop.run_until_complete(tools.call_tool("any", {}))
        finally:
            loop.close()

    @pytest.mark.unit
    def test_initialize_sets_initialized_true(self) -> None:
        """initialize() sets _initialized to True."""
        mi = _make_method_info(name="ping")
        sdk = _make_sdk(methods={"ping": mi}, initialized=True)
        sdk.initialize = AsyncMock()
        tools = OpenResourceBrokerMCPTools(sdk=sdk)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(tools.initialize())
        finally:
            loop.close()
        assert tools.initialized is True

    @pytest.mark.unit
    def test_initialize_idempotent(self) -> None:
        """Calling initialize() twice does not raise."""
        mi = _make_method_info(name="ping")
        sdk = _make_sdk(methods={"ping": mi}, initialized=True)
        sdk.initialize = AsyncMock()
        tools = OpenResourceBrokerMCPTools(sdk=sdk)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(tools.initialize())
            loop.run_until_complete(tools.initialize())
        finally:
            loop.close()
        assert tools.initialized is True

    @pytest.mark.unit
    def test_list_tools_after_initialize(self) -> None:
        """list_tools returns MCP format after initialize()."""
        mi = _make_method_info(name="ping")
        sdk = _make_sdk(methods={"ping": mi}, initialized=True)
        sdk.initialize = AsyncMock()
        tools = OpenResourceBrokerMCPTools(sdk=sdk)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(tools.initialize())
            result = tools.list_tools()
        finally:
            loop.close()
        assert any(t["name"] == "ping" for t in result)

    @pytest.mark.unit
    def test_call_tool_unknown_raises_value_error(self) -> None:
        """call_tool raises ValueError for unknown tool name."""
        mi = _make_method_info(name="ping")
        sdk = _make_sdk(methods={"ping": mi}, initialized=True)
        sdk.initialize = AsyncMock()
        tools = OpenResourceBrokerMCPTools(sdk=sdk)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(tools.initialize())
            with pytest.raises(ValueError, match="Unknown tool"):
                loop.run_until_complete(tools.call_tool("nonexistent", {}))
        finally:
            loop.close()

    @pytest.mark.unit
    def test_call_tool_missing_sdk_method_returns_error_dict(self) -> None:
        """call_tool returns error dict when SDK method not found on object."""
        mi = _make_method_info(name="missing_method")
        sdk = _make_sdk(methods={"missing_method": mi}, initialized=True)
        sdk.initialize = AsyncMock()
        # Remove the attribute from the sdk so hasattr fails
        del sdk.missing_method
        tools = OpenResourceBrokerMCPTools(sdk=sdk)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(tools.initialize())
            result = loop.run_until_complete(tools.call_tool("missing_method", {}))
        finally:
            loop.close()
        assert "error" in result

    @pytest.mark.unit
    def test_call_tool_returns_success_dict_with_to_dict(self) -> None:
        """call_tool returns success dict when result has to_dict."""
        mi = _make_method_info(name="ping")
        sdk = _make_sdk(methods={"ping": mi}, initialized=True)
        sdk.initialize = AsyncMock()
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {"pong": True}
        sdk.ping = AsyncMock(return_value=mock_result)
        tools = OpenResourceBrokerMCPTools(sdk=sdk)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(tools.initialize())
            result = loop.run_until_complete(tools.call_tool("ping", {}))
        finally:
            loop.close()
        assert result["success"] is True
        assert result["data"] == {"pong": True}

    @pytest.mark.unit
    def test_call_tool_returns_success_for_basic_types(self) -> None:
        """call_tool returns success dict for JSON-serializable types."""
        mi = _make_method_info(name="count")
        sdk = _make_sdk(methods={"count": mi}, initialized=True)
        sdk.initialize = AsyncMock()
        sdk.count = AsyncMock(return_value=42)
        tools = OpenResourceBrokerMCPTools(sdk=sdk)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(tools.initialize())
            result = loop.run_until_complete(tools.call_tool("count", {}))
        finally:
            loop.close()
        assert result["success"] is True
        assert result["data"] == 42

    @pytest.mark.unit
    def test_call_tool_returns_error_on_exception(self) -> None:
        """call_tool returns error dict when SDK method raises."""
        mi = _make_method_info(name="broken")
        sdk = _make_sdk(methods={"broken": mi}, initialized=True)
        sdk.initialize = AsyncMock()
        sdk.broken = AsyncMock(side_effect=RuntimeError("SDK failure"))
        tools = OpenResourceBrokerMCPTools(sdk=sdk)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(tools.initialize())
            result = loop.run_until_complete(tools.call_tool("broken", {}))
        finally:
            loop.close()
        assert "error" in result
        assert result["error"]["type"] == "RuntimeError"

    @pytest.mark.unit
    def test_cleanup_clears_state(self) -> None:
        """cleanup() sets initialized to False and clears tools."""
        mi = _make_method_info(name="ping")
        sdk = _make_sdk(methods={"ping": mi}, initialized=True)
        sdk.initialize = AsyncMock()
        sdk.cleanup = AsyncMock()
        tools = OpenResourceBrokerMCPTools(sdk=sdk)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(tools.initialize())
            assert tools.initialized is True
            loop.run_until_complete(tools.cleanup())
        finally:
            loop.close()
        assert tools.initialized is False
        assert tools.tools == {}

    @pytest.mark.unit
    def test_get_tools_by_type_command(self) -> None:
        """get_tools_by_type returns only command-type tools."""
        mi_cmd = _make_method_info(name="create", handler_type="command")
        mi_qry = _make_method_info(name="list", handler_type="query")
        sdk = _make_sdk(methods={"create": mi_cmd, "list": mi_qry}, initialized=True)
        sdk.initialize = AsyncMock()
        tools = OpenResourceBrokerMCPTools(sdk=sdk)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(tools.initialize())
            cmd_tools = tools.get_tools_by_type("command")
            qry_tools = tools.get_tools_by_type("query")
        finally:
            loop.close()
        assert "create" in cmd_tools
        assert "list" not in cmd_tools
        assert "list" in qry_tools

    @pytest.mark.unit
    def test_get_stats_after_initialize(self) -> None:
        """get_stats returns initialized=True and correct tool count after init."""
        mi = _make_method_info(name="ping")
        sdk = _make_sdk(methods={"ping": mi}, initialized=True)
        sdk.initialize = AsyncMock()
        sdk.get_stats.return_value = {}
        tools = OpenResourceBrokerMCPTools(sdk=sdk)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(tools.initialize())
            stats = tools.get_stats()
        finally:
            loop.close()
        assert stats["initialized"] is True
        assert stats["tools_discovered"] == 1

    @pytest.mark.unit
    def test_repr_after_initialize(self) -> None:
        """__repr__ includes 'initialized' status after init."""
        mi = _make_method_info(name="ping")
        sdk = _make_sdk(methods={"ping": mi}, initialized=True)
        sdk.initialize = AsyncMock()
        tools = OpenResourceBrokerMCPTools(sdk=sdk)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(tools.initialize())
        finally:
            loop.close()
        r = repr(tools)
        assert "initialized" in r
        assert "not initialized" not in r

    @pytest.mark.unit
    def test_aenter_aexit_context_manager(self) -> None:
        """Async context manager initializes and cleans up tools."""
        mi = _make_method_info(name="ping")
        sdk = _make_sdk(methods={"ping": mi}, initialized=True)
        sdk.initialize = AsyncMock()
        sdk.cleanup = AsyncMock()

        async def _run():
            t = OpenResourceBrokerMCPTools(sdk=sdk)
            async with t as mgr:
                assert mgr.initialized is True
            assert t.initialized is False

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_run())
        finally:
            loop.close()

    @pytest.mark.unit
    def test_format_result_converts_unknown_type_to_string(self) -> None:
        """_format_result converts unknown types to string with note."""
        sdk = _make_sdk()
        tools = OpenResourceBrokerMCPTools(sdk=sdk)

        class _Custom:
            def __str__(self):
                return "custom_str"

        result = tools._format_result(_Custom(), "test_tool")
        assert result["success"] is True
        assert result["data"] == "custom_str"
        assert "note" in result
