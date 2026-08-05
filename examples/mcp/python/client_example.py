#!/usr/bin/env python3
"""Example Python MCP client for Open Resource Broker.

Connects to the broker's catalog-driven MCP server over stdio and performs a
few common operations. The server is started as a subprocess with
``orb mcp serve`` (stdio is the default transport), and every tool the server
exposes is derived from the broker's operation catalog.

Install the client library first::

    pip install mcp
"""

import asyncio
import json
import logging
from typing import Any, Optional

from mcp.client.stdio import stdio_client

from mcp import ClientSession, StdioServerParameters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Start the broker's MCP server over stdio. stdio is the default transport, so
# no extra flags are needed; `orb mcp serve --transport http` would serve over
# Streamable HTTP instead (see the integration guide).
SERVER_PARAMS = StdioServerParameters(command="orb", args=["mcp", "serve"])


def _tool_body(result: Any) -> Any:
    """Parse the JSON body from a tool result's first text content block.

    The catalog server renders every tool result as a single text block whose
    text is the operation's canonical JSON body.
    """
    for block in result.content:
        if getattr(block, "type", None) == "text":
            return json.loads(block.text)
    return None


async def list_tools(session: ClientSession) -> list[str]:
    """List the tool names the server exposes."""
    result = await session.list_tools()
    names = [tool.name for tool in result.tools]
    logger.info("Available tools (%d): %s", len(names), names)
    return names


async def call_tool(
    session: ClientSession, name: str, arguments: Optional[dict[str, Any]] = None
) -> Any:
    """Call a tool and return its parsed JSON body."""
    logger.info("Calling tool %s with %s", name, arguments or {})
    result = await session.call_tool(name, arguments or {})
    body = _tool_body(result)
    logger.info("Tool %s isError=%s body=%s", name, result.isError, body)
    return body


async def example_basic_operations() -> None:
    """List the catalog-derived tool set."""
    logger.info("=== Basic MCP Operations Example ===")
    async with stdio_client(SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await list_tools(session)


async def example_infrastructure_workflow() -> None:
    """List providers and templates, then read a single template."""
    logger.info("=== Infrastructure Workflow Example ===")
    async with stdio_client(SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            await call_tool(session, "list_providers")
            await call_tool(session, "get_provider_health")
            await call_tool(session, "list_templates", {"active_only": True})

            # Requesting machines would provision real resources, so it is left
            # commented out. The catalog input field is `requested_count`.
            # await call_tool(
            #     session,
            #     "request_machines",
            #     {"template_id": "RunInstances-OnDemand", "requested_count": 2},
            # )


async def example_error_handling() -> None:
    """An unknown tool is reported as a tool-level error, not a crash."""
    logger.info("=== Error Handling Example ===")
    async with stdio_client(SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("non_existent_tool", {})
            logger.info("Unknown tool isError=%s", result.isError)


async def main() -> None:
    """Run every example in sequence."""
    for example in (
        example_basic_operations,
        example_infrastructure_workflow,
        example_error_handling,
    ):
        try:
            await example()
        except Exception as exc:  # noqa: BLE001 — example is best-effort
            logger.error("Error in %s: %s", example.__name__, exc)


if __name__ == "__main__":
    asyncio.run(main())
