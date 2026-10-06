"""MCP protocol-compliance tests for the catalog-driven server.

Where ``test_mcp_catalog_server.py`` pins the catalog-to-tool mapping, this
module pins the parts of the Model Context Protocol contract the server must
honour regardless of which operations the catalog happens to expose:

* every advertised tool carries a JSON-serialisable object input schema;
* a call for an unknown tool resolves to an ``isError`` result rather than
  raising a protocol-level exception;
* a handler that raises is surfaced as an ``isError`` result, not a crash;
* a successful call returns ``TextContent`` whose text is the JSON body the
  shared ``Interface.MCP`` renderer produced.

These are asserted by driving the low-level ``Server``'s registered handlers
directly, which is how a transport would dispatch ``tools/list`` and
``tools/call`` requests.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import mcp.types as mcp_types
import pytest
from mcp.server.context import ServerRequestContext

from orb.application.dto.interface_response import InterfaceResponse
from orb.application.machine.dto import MachineDTO
from orb.application.services.orchestration.dtos import ListMachinesOutput
from orb.application.services.orchestration.list_machines import ListMachinesOrchestrator
from orb.infrastructure.di.container import DIContainer
from orb.interface.catalog import OPERATION_CATALOG, Interface
from orb.interface.mcp.catalog_server import build_server, list_catalog_tools
from orb.interface.response_formatting_service import ResponseFormattingService


def _mcp_exposed_keys() -> set[str]:
    """Catalog keys whose entry lists Interface.MCP in ``exposed_on``."""
    return {key for key, entry in OPERATION_CATALOG.items() if Interface.MCP in entry.exposed_on}


def _make_ctx() -> ServerRequestContext[Any, Any]:
    """A ``ServerRequestContext`` with no HTTP request in scope (stdio-style)."""
    return ServerRequestContext(
        session=MagicMock(),
        lifespan_context={},
        protocol_version="2026-07-28",
        method="tools/call",
        request=None,
    )


async def _list_tools(server: Any) -> list[Any]:
    """Invoke the server's registered tools/list handler directly."""
    entry = server.get_request_handler("tools/list")
    result = await entry.handler(_make_ctx(), None)
    return result.tools


async def _call_tool(server: Any, name: str, arguments: dict[str, Any]) -> Any:
    """Invoke the server's registered tools/call handler directly and return the result."""
    entry = server.get_request_handler("tools/call")
    return await entry.handler(
        _make_ctx(), mcp_types.CallToolRequestParams(name=name, arguments=arguments)
    )


@pytest.mark.asyncio
async def test_served_tool_set_matches_mcp_exposed_catalog_entries() -> None:
    """The served tool set is exactly the catalog's MCP-exposed operations.

    Guards against silent drift: a tool served without an MCP-exposed catalog
    entry (or an MCP-exposed entry with no tool) is a bug this pins down.
    """
    served = {tool.name for tool in await _list_tools(build_server(MagicMock(spec=DIContainer)))}
    assert served == _mcp_exposed_keys()

    # The offline catalog derivation must agree with the served set.
    assert {tool.name for tool in list_catalog_tools()} == _mcp_exposed_keys()


@pytest.mark.asyncio
async def test_every_tool_input_schema_is_a_serialisable_object_schema() -> None:
    """Each advertised tool exposes a JSON-serialisable object input schema.

    The MCP client relies on ``inputSchema`` being a JSON-Schema object with a
    ``properties`` map to render tool arguments, so a drifted or non-object
    schema would break any conformant client.
    """
    server = build_server(MagicMock(spec=DIContainer))
    tools = await _list_tools(server)

    assert tools, "server advertised no tools"
    for tool in tools:
        schema = tool.input_schema
        assert isinstance(schema, dict), f"{tool.name}: inputSchema is not an object"
        assert schema.get("type") == "object", f"{tool.name}: inputSchema type is not 'object'"
        assert isinstance(schema.get("properties"), dict), (
            f"{tool.name}: inputSchema has no properties object"
        )
        # Must round-trip through JSON exactly as it will over the wire.
        json.loads(json.dumps(schema))


@pytest.mark.asyncio
@pytest.mark.unit
async def test_optional_typed_fields_accept_explicit_null() -> None:
    """An Optional[T] field advertises the null type so an explicit null validates.

    The SDK validates incoming arguments against ``inputSchema`` before the
    handler runs; a conformant client that serialises an unset optional as an
    explicit ``null`` must not be rejected. A property whose declared type is a
    scalar-plus-null therefore accepts null. (A non-optional field with a default,
    e.g. ``str = ""``, is intentionally not nullable — the DTO rejects None too.)
    """
    import jsonschema

    from orb.interface.catalog import OPERATION_CATALOG, Interface
    from orb.interface.mcp.catalog_server import schema_from_input_dto

    checked = 0
    for entry in OPERATION_CATALOG.values():
        if Interface.MCP not in entry.exposed_on:
            continue
        schema = schema_from_input_dto(entry.input_dto)
        for name, prop in schema["properties"].items():
            declared = prop.get("type")
            if isinstance(declared, list) and "null" in declared:
                # This property advertises null; an explicit null must validate.
                jsonschema.validate({name: None}, {**schema, "required": []})
                checked += 1
    assert checked, "no nullable optional properties were exercised"


@pytest.mark.asyncio
async def test_unknown_tool_resolves_to_error_result_not_a_raised_exception() -> None:
    """Calling an unknown tool completes with an isError CallToolResult.

    A conformant server reports an unknown tool through the result envelope so
    the client sees a tool error, not a transport/protocol fault.
    """
    server = build_server(MagicMock(spec=DIContainer))

    # The call must return normally (no exception escapes the handler).
    result = await _call_tool(server, "no_such_tool", {})

    assert isinstance(result, mcp_types.CallToolResult)
    assert result.is_error is True


@pytest.mark.asyncio
async def test_handler_that_raises_is_surfaced_as_error_result() -> None:
    """An orchestrator exception becomes an isError result with text content."""
    orchestrator = AsyncMock(spec=ListMachinesOrchestrator)
    orchestrator.execute.side_effect = RuntimeError("provider unavailable")

    container = MagicMock(spec=DIContainer)
    container.get.side_effect = lambda cls: {
        ListMachinesOrchestrator: orchestrator,
        ResponseFormattingService: MagicMock(spec=ResponseFormattingService),
    }.get(cls)

    server = build_server(container)
    result = await _call_tool(server, "list_machines", {})

    assert isinstance(result, mcp_types.CallToolResult)
    assert result.is_error is True
    assert isinstance(result.content[0], mcp_types.TextContent)
    assert result.content[0].type == "text"


@pytest.mark.asyncio
async def test_successful_call_returns_text_content_carrying_the_rendered_body() -> None:
    """A successful call returns TextContent whose JSON is the MCP-rendered body."""
    machine = MachineDTO(
        machine_id="i-0abc",
        name="node",
        status="running",
        instance_type="t3.medium",
        private_ip="10.0.0.2",
        result="succeed",
    )
    output = ListMachinesOutput(machines=[machine], count=1, next_cursor=None, total_count=1)

    orchestrator = AsyncMock(spec=ListMachinesOrchestrator)
    orchestrator.execute.return_value = output

    from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy

    formatter = ResponseFormattingService(DefaultSchedulerStrategy(logger=MagicMock()))

    container = MagicMock(spec=DIContainer)
    container.get.side_effect = lambda cls: {
        ListMachinesOrchestrator: orchestrator,
        ResponseFormattingService: formatter,
    }.get(cls)

    server = build_server(container)
    result = await _call_tool(server, "list_machines", {"limit": 50})

    assert isinstance(result, mcp_types.CallToolResult)
    assert result.is_error is False
    content = result.content

    assert len(content) == 1
    part = content[0]
    assert isinstance(part, mcp_types.TextContent)
    assert part.type == "text"

    entry = OPERATION_CATALOG["list_machines"]
    expected: InterfaceResponse = entry.renderer_for(Interface.MCP)(formatter, output)
    assert json.loads(part.text) == expected.data
