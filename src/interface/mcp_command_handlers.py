"""
MCP command handlers for CLI integration.

Provides command handlers for MCP operations following the existing
interface layer patterns and error handling conventions.
"""

import json
from pathlib import Path
from typing import Any, Dict

from infrastructure.error.decorators import handle_interface_exceptions
from mcp.tools import OpenHFPluginMCPTools


@handle_interface_exceptions(context="mcp_tools_list", interface_type="cli")
async def handle_mcp_tools_list(args) -> Dict[str, Any]:
    """
    Handle 'ohfp mcp tools list' command.

    Args:
        args: Parsed command line arguments

    Returns:
        Formatted tool list response
    """
    async with OpenHFPluginMCPTools() as tools:
        tool_list = tools.list_tools()

        # Filter by type if specified
        if hasattr(args, "type") and args.type:
            filtered_tools = []
            for tool in tool_list:
                tool_def = tools.get_tool_info(tool["name"])
                if (
                    tool_def
                    and tool_def.method_info
                    and tool_def.method_info.handler_type == args.type
                ):
                    filtered_tools.append(tool)
            tool_list = filtered_tools

        if args.format == "table":
            return _format_tools_table(tool_list)
        else:
            return {"tools": tool_list}


@handle_interface_exceptions(context="mcp_tools_call", interface_type="cli")
async def handle_mcp_tools_call(args) -> Dict[str, Any]:
    """
    Handle 'ohfp mcp tools call' command.

    Args:
        args: Parsed command line arguments

    Returns:
        Tool execution result
    """
    # Parse arguments
    tool_args = {}

    if hasattr(args, "file") and args.file:
        # Load arguments from file
        file_path = Path(args.file)
        if not file_path.exists():
            return {"error": f"Arguments file not found: {args.file}"}

        try:
            with open(file_path, "r") as f:
                tool_args = json.load(f)
        except json.JSONDecodeError as e:
            return {"error": f"Invalid JSON in arguments file: {str(e)}"}
        except Exception as e:
            return {"error": f"Failed to read arguments file: {str(e)}"}

    elif hasattr(args, "args") and args.args:
        # Parse arguments from command line JSON string
        try:
            tool_args = json.loads(args.args)
        except json.JSONDecodeError as e:
            return {"error": f"Invalid JSON in arguments: {str(e)}"}

    # Execute tool
    async with OpenHFPluginMCPTools() as tools:
        result = await tools.call_tool(args.tool_name, tool_args)

        if args.format == "table" and "data" in result:
            return _format_result_table(result, args.tool_name)
        else:
            return result


@handle_interface_exceptions(context="mcp_tools_info", interface_type="cli")
async def handle_mcp_tools_info(args) -> Dict[str, Any]:
    """
    Handle 'ohfp mcp tools info' command.

    Args:
        args: Parsed command line arguments

    Returns:
        Tool information
    """
    async with OpenHFPluginMCPTools() as tools:
        tool_def = tools.get_tool_info(args.tool_name)

        if not tool_def:
            return {"error": f"Tool not found: {args.tool_name}"}

        info = {
            "name": tool_def.name,
            "description": tool_def.description,
            "input_schema": tool_def.input_schema,
            "method_name": tool_def.method_name,
        }

        if tool_def.method_info:
            info.update(
                {
                    "handler_type": tool_def.method_info.handler_type,
                    "parameters": tool_def.method_info.parameters,
                    "required_params": tool_def.method_info.required_params,
                }
            )

        if args.format == "table":
            return _format_tool_info_table(info)
        else:
            return info


@handle_interface_exceptions(context="mcp_validate", interface_type="cli")
async def handle_mcp_validate(args) -> Dict[str, Any]:
    """
    Handle 'ohfp mcp validate' command.

    Args:
        args: Parsed command line arguments

    Returns:
        Validation result
    """
    validation_result = {"valid": True, "checks": []}

    # Test MCP tools initialization
    try:
        async with OpenHFPluginMCPTools() as tools:
            stats = tools.get_stats()
            validation_result["checks"].append(
                {
                    "check": "MCP Tools Initialization",
                    "status": "PASS",
                    "details": f"Discovered {stats['tools_discovered']} tools",
                }
            )

            # Test a simple tool call
            if stats["tools_discovered"] > 0:
                # Try to call a simple query tool
                query_tools = tools.get_tools_by_type("query")
                if query_tools:
                    try:
                        # Test with minimal arguments
                        test_result = await tools.call_tool(query_tools[0], {})
                        if "error" not in test_result:
                            validation_result["checks"].append(
                                {
                                    "check": "Tool Execution Test",
                                    "status": "PASS",
                                    "details": f"Successfully executed {query_tools[0]}",
                                }
                            )
                        else:
                            validation_result["checks"].append(
                                {
                                    "check": "Tool Execution Test",
                                    "status": "WARNING",
                                    "details": f"Tool execution returned error (may be expected): {test_result.get('error', {}).get('message', 'Unknown error')}",
                                }
                            )
                    except Exception as e:
                        validation_result["checks"].append(
                            {
                                "check": "Tool Execution Test",
                                "status": "WARNING",
                                "details": f"Tool execution failed (may be expected): {str(e)}",
                            }
                        )

    except Exception as e:
        validation_result["valid"] = False
        validation_result["checks"].append(
            {"check": "MCP Tools Initialization", "status": "FAIL", "details": str(e)}
        )

    # Validate configuration file if provided
    if hasattr(args, "config") and args.config:
        config_path = Path(args.config)
        if config_path.exists():
            try:
                with open(config_path, "r") as f:
                    config_data = json.load(f)
                validation_result["checks"].append(
                    {
                        "check": "Configuration File",
                        "status": "PASS",
                        "details": f"Valid JSON configuration with {len(config_data)} keys",
                    }
                )
            except Exception as e:
                validation_result["valid"] = False
                validation_result["checks"].append(
                    {"check": "Configuration File", "status": "FAIL", "details": str(e)}
                )
        else:
            validation_result["valid"] = False
            validation_result["checks"].append(
                {
                    "check": "Configuration File",
                    "status": "FAIL",
                    "details": f"File not found: {args.config}",
                }
            )

    if args.format == "table":
        return _format_validation_table(validation_result)
    else:
        return validation_result


def _format_tools_table(tools: list) -> Dict[str, Any]:
    """Format tools list as table."""
    if not tools:
        return {"message": "No MCP tools found"}

    headers = ["Name", "Description", "Type"]
    rows = []

    for tool in tools:
        # Extract handler type from description or default to "unknown"
        handler_type = "unknown"
        if "Query operation" in tool.get("description", ""):
            handler_type = "query"
        elif "Command operation" in tool.get("description", ""):
            handler_type = "command"

        rows.append(
            [
                tool["name"],
                tool.get("description", "No description")[:60]
                + ("..." if len(tool.get("description", "")) > 60 else ""),
                handler_type,
            ]
        )

    return {
        "table": {"headers": headers, "rows": rows},
        "summary": f"Found {len(tools)} MCP tools",
    }


def _format_result_table(result: Dict[str, Any], tool_name: str) -> Dict[str, Any]:
    """Format tool execution result as table."""
    if "error" in result:
        return {
            "error_table": {
                "headers": ["Field", "Value"],
                "rows": [
                    ["Tool", tool_name],
                    ["Status", "ERROR"],
                    ["Error Type", result["error"].get("type", "Unknown")],
                    ["Message", result["error"].get("message", "No message")],
                ],
            }
        }

    if "data" in result:
        data = result["data"]
        if isinstance(data, dict):
            return {
                "result_table": {
                    "headers": ["Field", "Value"],
                    "rows": [[str(k), str(v)] for k, v in data.items()],
                },
                "summary": f"Tool '{tool_name}' executed successfully",
            }
        elif isinstance(data, list):
            return {
                "result": data,
                "summary": f"Tool '{tool_name}' returned {len(data)} items",
            }

    return result


def _format_tool_info_table(info: Dict[str, Any]) -> Dict[str, Any]:
    """Format tool information as table."""
    headers = ["Property", "Value"]
    rows = [
        ["Name", info["name"]],
        ["Description", info["description"]],
        ["Method Name", info["method_name"]],
        ["Handler Type", info.get("handler_type", "unknown")],
    ]

    if "required_params" in info and info["required_params"]:
        rows.append(["Required Parameters", ", ".join(info["required_params"])])

    if "parameters" in info and info["parameters"]:
        param_count = len(info["parameters"])
        rows.append(["Total Parameters", str(param_count)])

    return {
        "info_table": {"headers": headers, "rows": rows},
        "schema": info.get("input_schema", {}),
    }


def _format_validation_table(result: Dict[str, Any]) -> Dict[str, Any]:
    """Format validation result as table."""
    headers = ["Check", "Status", "Details"]
    rows = []

    for check in result["checks"]:
        status_symbol = {"PASS": "PASS", "WARNING": "WARN", "FAIL": "FAIL"}.get(
            check["status"], "UNKNOWN"
        )

        rows.append(
            [
                check["check"],
                f"{status_symbol} {check['status']}",
                check["details"][:80] + ("..." if len(check["details"]) > 80 else ""),
            ]
        )

    overall_status = "VALID" if result["valid"] else "INVALID"

    return {
        "validation_table": {"headers": headers, "rows": rows},
        "summary": f"MCP Validation: {overall_status}",
    }
