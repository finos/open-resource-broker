"""Unit tests for mcp_command_handlers (additional coverage).

Covers:
- handle_mcp_tools_list: json format, table format, with type filter, empty list
- handle_mcp_tools_call: file not found, invalid JSON, valid file, args string, table format
- handle_mcp_tools_info: not found, found without method_info, found with method_info, table format
- handle_mcp_validate: tool execution with query tools (pass/warn/error), table format
- _format_tools_table: empty, query/command classification
- _format_result_table: error, dict data, list data, unknown data
- _format_tool_info_table: with/without required params and parameters
- _format_validation_table: pass/warn/fail status symbols
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_args(**kwargs):
    defaults = {"format": "json", "config": None}
    defaults.update(kwargs)
    return type("Args", (), defaults)()


def _make_tools_mock(tools=None, stats=None, tool_info=None):
    """Return a context-manager-style mock for OpenResourceBrokerMCPTools."""
    mock = MagicMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    mock.list_tools.return_value = tools or []
    mock.get_stats.return_value = stats or {"tools_discovered": 0}
    mock.get_tool_info.return_value = tool_info
    mock.get_tools_by_type.return_value = []
    mock.call_tool = AsyncMock(return_value={"data": {}})
    return mock


# ---------------------------------------------------------------------------
# handle_mcp_tools_list
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleMcpToolsList:
    @pytest.mark.asyncio
    async def test_returns_tool_list_json(self):
        from orb.interface.mcp_command_handlers import handle_mcp_tools_list

        tool_list = [{"name": "list_requests", "description": "List requests"}]
        mock_tools = _make_tools_mock(tools=tool_list)

        with patch(
            "orb.interface.mcp_command_handlers.OpenResourceBrokerMCPTools", return_value=mock_tools
        ):
            result = await handle_mcp_tools_list(_make_args(format="json"))

        data = result.data if hasattr(result, "data") else result
        assert "tools" in data
        assert len(data["tools"]) == 1

    @pytest.mark.asyncio
    async def test_table_format_returns_table_key(self):
        from orb.interface.mcp_command_handlers import handle_mcp_tools_list

        mock_tools = _make_tools_mock(
            tools=[{"name": "t1", "description": "Query operation for t1"}]
        )

        with patch(
            "orb.interface.mcp_command_handlers.OpenResourceBrokerMCPTools", return_value=mock_tools
        ):
            result = await handle_mcp_tools_list(_make_args(format="table"))

        data = result.data if hasattr(result, "data") else result
        assert "table" in data
        assert "summary" in data

    @pytest.mark.asyncio
    async def test_type_filter_applied(self):
        from orb.interface.mcp_command_handlers import handle_mcp_tools_list

        query_tool = MagicMock()
        query_tool.method_info = MagicMock()
        query_tool.method_info.handler_type = "query"

        command_tool = MagicMock()
        command_tool.method_info = MagicMock()
        command_tool.method_info.handler_type = "command"

        mock_tools = _make_tools_mock(
            tools=[
                {"name": "t1"},
                {"name": "t2"},
            ]
        )

        def get_tool_info_side(name):
            if name == "t1":
                return query_tool
            return command_tool

        mock_tools.get_tool_info.side_effect = get_tool_info_side

        args = _make_args(type="query")

        with patch(
            "orb.interface.mcp_command_handlers.OpenResourceBrokerMCPTools", return_value=mock_tools
        ):
            result = await handle_mcp_tools_list(args)

        data = result.data if hasattr(result, "data") else result
        assert len(data["tools"]) == 1
        assert data["tools"][0]["name"] == "t1"

    @pytest.mark.asyncio
    async def test_empty_tool_list_returns_empty_tools(self):
        from orb.interface.mcp_command_handlers import handle_mcp_tools_list

        mock_tools = _make_tools_mock(tools=[])

        with patch(
            "orb.interface.mcp_command_handlers.OpenResourceBrokerMCPTools", return_value=mock_tools
        ):
            result = await handle_mcp_tools_list(_make_args())

        data = result.data if hasattr(result, "data") else result
        assert data["tools"] == []


# ---------------------------------------------------------------------------
# handle_mcp_tools_call
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleMcpToolsCall:
    @pytest.mark.asyncio
    async def test_file_not_found_returns_error(self):
        from orb.interface.mcp_command_handlers import handle_mcp_tools_call

        args = _make_args(file="/nonexistent/file.json", tool_name="list_requests", args=None)

        result = await handle_mcp_tools_call(args)

        data = result.data if hasattr(result, "data") else result
        assert "error" in data
        assert result.exit_code == 1

    @pytest.mark.asyncio
    async def test_file_with_invalid_json_returns_error(self, tmp_path):
        from orb.interface.mcp_command_handlers import handle_mcp_tools_call

        bad_json = tmp_path / "args.json"
        bad_json.write_text("{not valid json}")

        args = _make_args(file=str(bad_json), tool_name="list_requests", args=None)

        result = await handle_mcp_tools_call(args)

        data = result.data if hasattr(result, "data") else result
        assert "Invalid JSON" in data["error"]
        assert result.exit_code == 1

    @pytest.mark.asyncio
    async def test_file_read_os_error_returns_error(self, tmp_path):
        from orb.interface.mcp_command_handlers import handle_mcp_tools_call

        args_file = tmp_path / "args.json"
        args_file.write_text('{"key": "val"}')

        args = _make_args(file=str(args_file), tool_name="list_requests", args=None)

        with patch("builtins.open", side_effect=PermissionError("denied")):
            result = await handle_mcp_tools_call(args)

        data = result.data if hasattr(result, "data") else result
        assert "Failed to read" in data["error"]
        assert result.exit_code == 1

    @pytest.mark.asyncio
    async def test_file_with_valid_json_executes_tool(self, tmp_path):
        from orb.interface.mcp_command_handlers import handle_mcp_tools_call

        args_file = tmp_path / "args.json"
        args_file.write_text('{"limit": 10}')

        mock_tools = _make_tools_mock()
        mock_tools.call_tool.return_value = {"data": {"results": []}}

        args = _make_args(file=str(args_file), tool_name="list_requests", args=None, format="json")

        with patch(
            "orb.interface.mcp_command_handlers.OpenResourceBrokerMCPTools", return_value=mock_tools
        ):
            result = await handle_mcp_tools_call(args)

        mock_tools.call_tool.assert_awaited_once_with("list_requests", {"limit": 10})
        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_args_string_invalid_json_returns_error(self):
        from orb.interface.mcp_command_handlers import handle_mcp_tools_call

        args = _make_args(file=None, tool_name="list_requests", args="not-json-at-all")

        with patch("orb.infrastructure.utilities.json_utils.safe_json_loads", return_value=None):
            result = await handle_mcp_tools_call(args)

        data = result.data if hasattr(result, "data") else result
        assert "Invalid JSON" in data["error"]
        assert result.exit_code == 1

    @pytest.mark.asyncio
    async def test_args_string_valid_json_executes_tool(self):
        from orb.interface.mcp_command_handlers import handle_mcp_tools_call

        mock_tools = _make_tools_mock()
        mock_tools.call_tool.return_value = {"data": {"requests": []}}

        args = _make_args(file=None, tool_name="list_requests", args='{"limit": 5}', format="json")

        with patch(
            "orb.interface.mcp_command_handlers.OpenResourceBrokerMCPTools", return_value=mock_tools
        ):
            result = await handle_mcp_tools_call(args)

        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_table_format_applied_when_data_in_result(self):
        from orb.interface.mcp_command_handlers import handle_mcp_tools_call

        mock_tools = _make_tools_mock()
        mock_tools.call_tool.return_value = {"data": {"key": "value"}}

        args = _make_args(file=None, tool_name="list_requests", args=None, format="table")

        with patch(
            "orb.interface.mcp_command_handlers.OpenResourceBrokerMCPTools", return_value=mock_tools
        ):
            result = await handle_mcp_tools_call(args)

        data = result.data if hasattr(result, "data") else result
        # Table format wraps dict data in result_table
        assert "result_table" in data or "summary" in data

    @pytest.mark.asyncio
    async def test_no_file_no_args_executes_tool_with_empty_dict(self):
        from orb.interface.mcp_command_handlers import handle_mcp_tools_call

        mock_tools = _make_tools_mock()
        mock_tools.call_tool.return_value = {"result": "ok"}

        # Build an args with neither file nor args attributes
        args = type("Args", (), {"tool_name": "list_requests", "format": "json"})()

        with patch(
            "orb.interface.mcp_command_handlers.OpenResourceBrokerMCPTools", return_value=mock_tools
        ):
            result = await handle_mcp_tools_call(args)

        mock_tools.call_tool.assert_awaited_once_with("list_requests", {})
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# handle_mcp_tools_info
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleMcpToolsInfo:
    @pytest.mark.asyncio
    async def test_tool_not_found_returns_error(self):
        from orb.interface.mcp_command_handlers import handle_mcp_tools_info

        mock_tools = _make_tools_mock(tool_info=None)
        args = _make_args(tool_name="nonexistent")

        with patch(
            "orb.interface.mcp_command_handlers.OpenResourceBrokerMCPTools", return_value=mock_tools
        ):
            result = await handle_mcp_tools_info(args)

        data = result.data if hasattr(result, "data") else result
        assert "error" in data
        assert result.exit_code == 1

    @pytest.mark.asyncio
    async def test_tool_found_returns_info_dict(self):
        from orb.interface.mcp_command_handlers import handle_mcp_tools_info

        tool_def = MagicMock()
        tool_def.name = "list_requests"
        tool_def.description = "List requests"
        tool_def.input_schema = {"type": "object"}
        tool_def.method_name = "handle_list_requests"
        tool_def.method_info = None

        mock_tools = _make_tools_mock(tool_info=tool_def)
        args = _make_args(tool_name="list_requests")

        with patch(
            "orb.interface.mcp_command_handlers.OpenResourceBrokerMCPTools", return_value=mock_tools
        ):
            result = await handle_mcp_tools_info(args)

        data = result.data if hasattr(result, "data") else result
        assert data["name"] == "list_requests"
        assert data["description"] == "List requests"

    @pytest.mark.asyncio
    async def test_tool_found_with_method_info_includes_handler_type(self):
        from orb.interface.mcp_command_handlers import handle_mcp_tools_info

        tool_def = MagicMock()
        tool_def.name = "list_requests"
        tool_def.description = "List requests"
        tool_def.input_schema = {}
        tool_def.method_name = "handle_list_requests"
        tool_def.method_info = MagicMock()
        tool_def.method_info.handler_type = "query"
        tool_def.method_info.parameters = {"limit": {"type": "integer"}}
        tool_def.method_info.required_params = ["request_id"]

        mock_tools = _make_tools_mock(tool_info=tool_def)
        args = _make_args(tool_name="list_requests")

        with patch(
            "orb.interface.mcp_command_handlers.OpenResourceBrokerMCPTools", return_value=mock_tools
        ):
            result = await handle_mcp_tools_info(args)

        data = result.data if hasattr(result, "data") else result
        assert data["handler_type"] == "query"
        assert data["required_params"] == ["request_id"]

    @pytest.mark.asyncio
    async def test_table_format_returns_info_table(self):
        from orb.interface.mcp_command_handlers import handle_mcp_tools_info

        tool_def = MagicMock()
        tool_def.name = "get_template"
        tool_def.description = "Get a template"
        tool_def.input_schema = {}
        tool_def.method_name = "handle_get_template"
        tool_def.method_info = None

        mock_tools = _make_tools_mock(tool_info=tool_def)
        args = _make_args(tool_name="get_template", format="table")

        with patch(
            "orb.interface.mcp_command_handlers.OpenResourceBrokerMCPTools", return_value=mock_tools
        ):
            result = await handle_mcp_tools_info(args)

        data = result.data if hasattr(result, "data") else result
        assert "info_table" in data


# ---------------------------------------------------------------------------
# handle_mcp_validate — tool execution paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleMcpValidateToolExecution:
    @pytest.mark.asyncio
    async def test_tool_execution_pass_when_no_error_in_result(self):
        from orb.interface.mcp_command_handlers import handle_mcp_validate

        mock_tools = _make_tools_mock(stats={"tools_discovered": 3})
        mock_tools.get_tools_by_type.return_value = ["list_requests"]
        mock_tools.call_tool.return_value = {"data": {"requests": []}}  # no "error" key

        with patch(
            "orb.interface.mcp_command_handlers.OpenResourceBrokerMCPTools", return_value=mock_tools
        ):
            result = await handle_mcp_validate(_make_args())

        data = result.data if hasattr(result, "data") else result
        exec_check = next((c for c in data["checks"] if "Tool Execution" in c["check"]), None)
        assert exec_check is not None
        assert exec_check["status"] == "PASS"

    @pytest.mark.asyncio
    async def test_tool_execution_warning_when_error_in_result(self):
        from orb.interface.mcp_command_handlers import handle_mcp_validate

        mock_tools = _make_tools_mock(stats={"tools_discovered": 2})
        mock_tools.get_tools_by_type.return_value = ["list_requests"]
        mock_tools.call_tool.return_value = {
            "error": {"message": "no provider", "type": "AppError"}
        }

        with patch(
            "orb.interface.mcp_command_handlers.OpenResourceBrokerMCPTools", return_value=mock_tools
        ):
            result = await handle_mcp_validate(_make_args())

        data = result.data if hasattr(result, "data") else result
        exec_check = next((c for c in data["checks"] if "Tool Execution" in c["check"]), None)
        assert exec_check is not None
        assert exec_check["status"] == "WARNING"

    @pytest.mark.asyncio
    async def test_tool_execution_warning_when_call_raises(self):
        from orb.interface.mcp_command_handlers import handle_mcp_validate

        mock_tools = _make_tools_mock(stats={"tools_discovered": 2})
        mock_tools.get_tools_by_type.return_value = ["list_requests"]
        mock_tools.call_tool.side_effect = RuntimeError("connection refused")

        with patch(
            "orb.interface.mcp_command_handlers.OpenResourceBrokerMCPTools", return_value=mock_tools
        ):
            result = await handle_mcp_validate(_make_args())

        data = result.data if hasattr(result, "data") else result
        exec_check = next((c for c in data["checks"] if "Tool Execution" in c["check"]), None)
        assert exec_check is not None
        assert exec_check["status"] == "WARNING"
        assert "connection refused" in exec_check["details"]

    @pytest.mark.asyncio
    async def test_config_file_invalid_json_marks_fail(self, tmp_path):
        from orb.interface.mcp_command_handlers import handle_mcp_validate

        bad_config = tmp_path / "config.json"
        bad_config.write_text("!!invalid json!!")

        mock_tools = _make_tools_mock(stats={"tools_discovered": 0})

        args = _make_args(config=str(bad_config))

        with patch(
            "orb.interface.mcp_command_handlers.OpenResourceBrokerMCPTools", return_value=mock_tools
        ):
            result = await handle_mcp_validate(args)

        data = result.data if hasattr(result, "data") else result
        assert data["valid"] is False
        config_check = next((c for c in data["checks"] if "Configuration File" in c["check"]), None)
        assert config_check is not None
        assert config_check["status"] == "FAIL"


# ---------------------------------------------------------------------------
# _format_tools_table
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatToolsTable:
    def test_empty_list_returns_no_tools_message(self):
        from orb.interface.mcp_command_handlers import _format_tools_table

        result = _format_tools_table([])
        assert "message" in result

    def test_query_operation_classified_as_query(self):
        from orb.interface.mcp_command_handlers import _format_tools_table

        tools = [{"name": "t1", "description": "Query operation for listing things"}]
        result = _format_tools_table(tools)
        row = result["table"]["rows"][0]
        assert row[2] == "query"

    def test_command_operation_classified_as_command(self):
        from orb.interface.mcp_command_handlers import _format_tools_table

        tools = [{"name": "t2", "description": "Command operation to create resources"}]
        result = _format_tools_table(tools)
        row = result["table"]["rows"][0]
        assert row[2] == "command"

    def test_unknown_operation_classified_as_unknown(self):
        from orb.interface.mcp_command_handlers import _format_tools_table

        tools = [{"name": "t3", "description": "Does something"}]
        result = _format_tools_table(tools)
        row = result["table"]["rows"][0]
        assert row[2] == "unknown"

    def test_description_truncated_at_max_length(self):
        from orb.infrastructure.constants import MAX_DESCRIPTION_LENGTH
        from orb.interface.mcp_command_handlers import _format_tools_table

        long_desc = "x" * (MAX_DESCRIPTION_LENGTH + 20)
        tools = [{"name": "t1", "description": long_desc}]
        result = _format_tools_table(tools)
        row = result["table"]["rows"][0]
        # The cell value is truncated description + "..."
        assert row[1].endswith("...")


# ---------------------------------------------------------------------------
# _format_result_table
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatResultTable:
    def test_error_result_returns_error_table(self):
        from orb.interface.mcp_command_handlers import _format_result_table

        result = {"error": {"type": "AppError", "message": "not found"}}
        formatted = _format_result_table(result, "get_template")
        assert "error_table" in formatted

    def test_dict_data_returns_result_table(self):
        from orb.interface.mcp_command_handlers import _format_result_table

        result = {"data": {"template_id": "t-1", "name": "small"}}
        formatted = _format_result_table(result, "get_template")
        assert "result_table" in formatted
        assert "summary" in formatted

    def test_list_data_returns_result_and_summary(self):
        from orb.interface.mcp_command_handlers import _format_result_table

        result = {"data": [{"id": "a"}, {"id": "b"}]}
        formatted = _format_result_table(result, "list_requests")
        assert "result" in formatted
        assert "summary" in formatted
        assert "2 items" in formatted["summary"]

    def test_unknown_data_returns_result_unchanged(self):
        from orb.interface.mcp_command_handlers import _format_result_table

        result = {"meta": "data"}
        formatted = _format_result_table(result, "unknown_tool")
        # Falls through to return result unchanged
        assert formatted == result


# ---------------------------------------------------------------------------
# _format_tool_info_table
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatToolInfoTable:
    def test_basic_info_rows_present(self):
        from orb.interface.mcp_command_handlers import _format_tool_info_table

        info = {
            "name": "list_requests",
            "description": "List all requests",
            "method_name": "handle_list_requests",
            "input_schema": {},
        }
        result = _format_tool_info_table(info)
        assert "info_table" in result
        names = [row[0] for row in result["info_table"]["rows"]]
        assert "Name" in names

    def test_required_params_row_added_when_present(self):
        from orb.interface.mcp_command_handlers import _format_tool_info_table

        info = {
            "name": "cancel_request",
            "description": "Cancel a request",
            "method_name": "handle_cancel_request",
            "required_params": ["request_id"],
            "parameters": {"request_id": {}, "force": {}},
            "input_schema": {},
        }
        result = _format_tool_info_table(info)
        row_names = [row[0] for row in result["info_table"]["rows"]]
        assert "Required Parameters" in row_names
        assert "Total Parameters" in row_names

    def test_no_required_params_row_absent(self):
        from orb.interface.mcp_command_handlers import _format_tool_info_table

        info = {
            "name": "list_requests",
            "description": "List",
            "method_name": "handle_list",
            "input_schema": {},
        }
        result = _format_tool_info_table(info)
        row_names = [row[0] for row in result["info_table"]["rows"]]
        assert "Required Parameters" not in row_names


# ---------------------------------------------------------------------------
# _format_validation_table
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatValidationTable:
    def test_pass_status_in_rows(self):
        from orb.interface.mcp_command_handlers import _format_validation_table

        validation = {
            "valid": True,
            "checks": [{"check": "Init", "status": "PASS", "details": "OK"}],
        }
        result = _format_validation_table(validation)
        assert "validation_table" in result
        row = result["validation_table"]["rows"][0]
        assert "PASS" in row[1]

    def test_warn_status_in_rows(self):
        from orb.interface.mcp_command_handlers import _format_validation_table

        validation = {
            "valid": True,
            "checks": [{"check": "Test", "status": "WARNING", "details": "minor issue"}],
        }
        result = _format_validation_table(validation)
        row = result["validation_table"]["rows"][0]
        assert "WARN" in row[1]

    def test_fail_status_in_rows(self):
        from orb.interface.mcp_command_handlers import _format_validation_table

        validation = {
            "valid": False,
            "checks": [{"check": "Config", "status": "FAIL", "details": "file missing"}],
        }
        result = _format_validation_table(validation)
        row = result["validation_table"]["rows"][0]
        assert "FAIL" in row[1]

    def test_summary_says_invalid_when_not_valid(self):
        from orb.interface.mcp_command_handlers import _format_validation_table

        validation = {"valid": False, "checks": []}
        result = _format_validation_table(validation)
        assert "INVALID" in result["summary"]

    def test_details_truncated_at_80_chars(self):
        from orb.interface.mcp_command_handlers import _format_validation_table

        long_details = "x" * 100
        validation = {
            "valid": True,
            "checks": [{"check": "Init", "status": "PASS", "details": long_details}],
        }
        result = _format_validation_table(validation)
        row = result["validation_table"]["rows"][0]
        assert row[2].endswith("...")
