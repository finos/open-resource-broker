"""Unit tests for the catalog-driven MCP server.

These tests pin the guarantee the catalog server is built to deliver: the tool
set is exactly the catalog's MCP-exposed operations, each tool's input schema is
derived from that operation's input DTO, and a tool call renders its body through
the same ``Interface.MCP`` renderer every other interface would use for that
operation. The catalog is the single source; the MCP surface follows it.
"""

from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.server.context import ServerRequestContext

from orb.application.dto.interface_response import InterfaceResponse
from orb.application.machine.dto import MachineDTO
from orb.application.services.orchestration.acquire_machines import AcquireMachinesOrchestrator
from orb.application.services.orchestration.dtos import (
    AcquireMachinesOutput,
    ListMachinesInput,
    ListMachinesOutput,
)
from orb.application.services.orchestration.list_machines import ListMachinesOrchestrator
from orb.infrastructure.di.container import DIContainer
from orb.interface.catalog import OPERATION_CATALOG, Interface
from orb.interface.mcp.catalog_server import (
    _MCP_TOOL_MIN_ROLE,
    build_server,
    schema_from_input_dto,
)
from orb.interface.response_formatting_service import ResponseFormattingService


def _mcp_keys() -> set[str]:
    """Catalog keys exposed on the MCP interface."""
    return {key for key, entry in OPERATION_CATALOG.items() if Interface.MCP in entry.exposed_on}


def _make_ctx(request: Any = None) -> ServerRequestContext[Any, Any]:
    """Build a ``ServerRequestContext`` for direct handler invocation in tests.

    ``request`` carries the fake HTTP request (or ``None`` for stdio-style
    calls with no caller identity), the way the Streamable HTTP transport
    attaches it in production.
    """
    return ServerRequestContext(
        session=MagicMock(),
        lifespan_context={},
        protocol_version="2026-07-28",
        method="tools/call",
        request=request,
    )


async def _list_tools(server: Any) -> list[Any]:
    """Invoke the server's registered tools/list handler directly."""
    entry = server.get_request_handler("tools/list")
    result = await entry.handler(_make_ctx(), None)
    return result.tools


@pytest.mark.asyncio
async def test_list_tools_matches_catalog_mcp_entries() -> None:
    """list_tools returns exactly the catalog's MCP-exposed operations."""
    server = build_server(MagicMock(spec=DIContainer))
    tools = await _list_tools(server)

    tool_names = {tool.name for tool in tools}
    assert tool_names == _mcp_keys()


@pytest.mark.asyncio
async def test_each_tool_schema_carries_its_dto_fields() -> None:
    """Every tool's inputSchema exposes exactly its input DTO's fields."""
    server = build_server(MagicMock(spec=DIContainer))
    tools = await _list_tools(server)

    for tool in tools:
        entry = OPERATION_CATALOG[tool.name]
        expected_fields = {f.name for f in dataclasses.fields(entry.input_dto)}
        schema_fields = set(tool.input_schema["properties"].keys())
        assert schema_fields == expected_fields, (
            f"{tool.name}: schema properties {schema_fields} != DTO fields {expected_fields}"
        )
        assert tool.input_schema["type"] == "object"


def test_schema_marks_required_fields_without_defaults() -> None:
    """A DTO field with no default is required; fields with defaults are not."""
    # GetMachineInput has a single required field (machine_id) and no defaults.
    from orb.application.services.orchestration.dtos import GetMachineInput

    schema = schema_from_input_dto(GetMachineInput)
    assert schema["required"] == ["machine_id"]
    assert schema["properties"]["machine_id"]["type"] == "string"

    # ListMachinesInput fields all carry defaults, so nothing is required.
    list_schema = schema_from_input_dto(ListMachinesInput)
    assert "required" not in list_schema
    assert list_schema["properties"]["limit"]["type"] == "integer"
    assert list_schema["properties"]["sync"]["type"] == "boolean"
    assert list_schema["properties"]["filter_expressions"]["type"] == "array"


async def _call_tool(server: Any, name: str, arguments: dict[str, Any], request: Any = None) -> Any:
    """Invoke the server's registered tools/call handler directly and return the result."""
    import mcp.types as mcp_types

    entry = server.get_request_handler("tools/call")
    return await entry.handler(
        _make_ctx(request), mcp_types.CallToolRequestParams(name=name, arguments=arguments)
    )


@pytest.mark.asyncio
async def test_call_tool_returns_mcp_rendered_body() -> None:
    """call_tool for list_machines returns the Interface.MCP rendered body as JSON."""
    machine = MachineDTO(
        machine_id="machine-001",
        name="test-machine",
        status="running",
        instance_type="t3.medium",
        private_ip="10.0.0.1",
        result="succeed",
    )
    output = ListMachinesOutput(machines=[machine], count=1, next_cursor=None, total_count=1)

    orchestrator = AsyncMock(spec=ListMachinesOrchestrator)
    orchestrator.execute.return_value = output

    # A real ResponseFormattingService over the dependency-free default strategy,
    # so the tool body is the genuine Interface.MCP render rather than a stub.
    from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy

    formatter = ResponseFormattingService(DefaultSchedulerStrategy(logger=MagicMock()))

    container = MagicMock(spec=DIContainer)
    container.get.side_effect = lambda cls: {
        ListMachinesOrchestrator: orchestrator,
        ResponseFormattingService: formatter,
    }.get(cls)

    server = build_server(container)
    tool_result = await _call_tool(server, "list_machines", {"limit": 100})

    assert tool_result.is_error is False
    assert len(tool_result.content) == 1
    parsed = json.loads(tool_result.content[0].text)

    # The parsed tool body must equal the catalog's Interface.MCP render of the
    # same output DTO — the MCP surface renders through the shared seam.
    entry = OPERATION_CATALOG["list_machines"]
    expected: InterfaceResponse = entry.renderer_for(Interface.MCP)(formatter, output)
    assert parsed == expected.data


@pytest.mark.asyncio
async def test_call_tool_unknown_name_is_error_result() -> None:
    """An unknown tool name yields an isError result, not a raised exception."""
    server = build_server(MagicMock(spec=DIContainer))
    tool_result = await _call_tool(server, "does_not_exist", {})

    assert tool_result.is_error is True
    assert "Unknown tool" in tool_result.content[0].text


@pytest.mark.asyncio
async def test_call_tool_execution_failure_is_error_result() -> None:
    """An orchestrator failure is surfaced as an isError result."""
    orchestrator = AsyncMock(spec=ListMachinesOrchestrator)
    orchestrator.execute.side_effect = RuntimeError("boom")

    container = MagicMock(spec=DIContainer)
    container.get.side_effect = lambda cls: {
        ListMachinesOrchestrator: orchestrator,
        ResponseFormattingService: MagicMock(spec=ResponseFormattingService),
    }.get(cls)

    server = build_server(container)
    tool_result = await _call_tool(server, "list_machines", {})

    assert tool_result.is_error is True
    assert "list_machines failed" in tool_result.content[0].text


# --------------------------------------------------------------------------- #
# Role enforcement for the Streamable HTTP transport                         #
# --------------------------------------------------------------------------- #


def _acquire_machines_container() -> tuple[MagicMock, AsyncMock]:
    """A container that resolves request_machines' orchestrator and formatter.

    Returns the container and the orchestrator mock so a test can assert
    whether the orchestrator was actually invoked.
    """
    orchestrator = AsyncMock(spec=AcquireMachinesOrchestrator)
    orchestrator.execute.return_value = AcquireMachinesOutput(
        request_id="req-1", status="pending", machine_ids=[]
    )

    container = MagicMock(spec=DIContainer)
    container.get.side_effect = lambda cls: {
        AcquireMachinesOrchestrator: orchestrator,
        ResponseFormattingService: MagicMock(spec=ResponseFormattingService),
    }.get(cls)
    return container, orchestrator


def _http_request(user_roles: list[str] | None) -> Any:
    """A fake HTTP request carrying the given resolved roles on ``.state``.

    Mirrors how the Streamable HTTP transport attaches the Starlette
    ``Request`` to the current MCP request's context so ``call_tool`` can
    read ``request.state.user_roles`` the same way it would for a real HTTP
    call, without driving a full ASGI/uvicorn request.
    """
    return SimpleNamespace(state=SimpleNamespace(user_roles=user_roles))


@pytest.mark.asyncio
async def test_call_tool_denies_mutating_tool_for_viewer_role() -> None:
    """A viewer-role HTTP caller is refused on an operator-only tool."""
    container, orchestrator = _acquire_machines_container()

    server = build_server(container)
    tool_result = await _call_tool(
        server,
        "request_machines",
        {"template_id": "t1", "requested_count": 1},
        request=_http_request(["viewer"]),
    )

    assert tool_result.is_error is True
    assert "insufficient permissions" in tool_result.content[0].text
    orchestrator.execute.assert_not_called()


@pytest.mark.asyncio
async def test_call_tool_allows_mutating_tool_for_operator_role() -> None:
    """An operator-role HTTP caller reaches the orchestrator on an operator-only tool."""
    container, orchestrator = _acquire_machines_container()

    server = build_server(container)
    tool_result = await _call_tool(
        server,
        "request_machines",
        {"template_id": "t1", "requested_count": 1},
        request=_http_request(["operator"]),
    )

    assert tool_result.is_error is False
    orchestrator.execute.assert_called_once()


@pytest.mark.asyncio
async def test_call_tool_denies_mutating_tool_with_no_identity() -> None:
    """An HTTP caller with no resolved role (auth disabled) is treated as viewer."""
    container, orchestrator = _acquire_machines_container()

    server = build_server(container)
    tool_result = await _call_tool(
        server,
        "request_machines",
        {"template_id": "t1", "requested_count": 1},
        request=_http_request(None),
    )

    assert tool_result.is_error is True
    assert "insufficient permissions" in tool_result.content[0].text
    orchestrator.execute.assert_not_called()


@pytest.mark.asyncio
async def test_call_tool_mutating_tool_unaffected_outside_http_context() -> None:
    """With no HTTP request in scope (stdio, or a direct handler call), role is not checked."""
    container, orchestrator = _acquire_machines_container()

    server = build_server(container)
    tool_result = await _call_tool(
        server, "request_machines", {"template_id": "t1", "requested_count": 1}
    )

    assert tool_result.is_error is False
    orchestrator.execute.assert_called_once()


def test_every_mcp_tool_has_an_explicit_role_entry() -> None:
    """Every catalog entry exposed on the MCP interface has a role classification.

    A tool reaching ``_MCP_TOOL_MIN_ROLE``'s fallback would default to the
    stricter "operator" floor rather than silently granting viewer access,
    but a tool should never need that fallback: this test fails as soon as a
    new MCP-exposed catalog entry is added without an explicit classification,
    forcing it to be reviewed instead of inheriting a default.
    """
    assert _mcp_keys() <= set(_MCP_TOOL_MIN_ROLE)

    known_roles = {"viewer", "operator", "admin"}
    for tool_name in _mcp_keys():
        role = _MCP_TOOL_MIN_ROLE[tool_name]
        assert role in known_roles, f"{tool_name}: unknown role {role!r}"


@pytest.mark.asyncio
async def test_call_tool_read_only_tool_allowed_for_viewer_role() -> None:
    """A viewer-role HTTP caller can still invoke a read-only tool."""
    machine = MachineDTO(
        machine_id="machine-001",
        name="test-machine",
        status="running",
        instance_type="t3.medium",
        private_ip="10.0.0.1",
        result="succeed",
    )
    output = ListMachinesOutput(machines=[machine], count=1, next_cursor=None, total_count=1)
    orchestrator = AsyncMock(spec=ListMachinesOrchestrator)
    orchestrator.execute.return_value = output

    container = MagicMock(spec=DIContainer)
    container.get.side_effect = lambda cls: {
        ListMachinesOrchestrator: orchestrator,
        ResponseFormattingService: MagicMock(spec=ResponseFormattingService),
    }.get(cls)

    server = build_server(container)
    tool_result = await _call_tool(server, "list_machines", {}, request=_http_request(["viewer"]))

    assert tool_result.is_error is False


@pytest.mark.asyncio
async def test_call_tool_rejects_wrong_typed_argument() -> None:
    """An argument whose type contradicts the tool's input schema is refused."""
    container, orchestrator = _acquire_machines_container()

    server = build_server(container)
    tool_result = await _call_tool(
        server, "request_machines", {"template_id": "t1", "requested_count": "abc"}
    )

    assert tool_result.is_error is True
    assert "Input validation error" in tool_result.content[0].text
    assert "'abc' is not of type 'integer'" in tool_result.content[0].text
    orchestrator.execute.assert_not_called()


@pytest.mark.asyncio
async def test_call_tool_rejects_scalar_for_array_argument() -> None:
    """A scalar where the schema declares an array is refused."""
    container = MagicMock(spec=DIContainer)
    server = build_server(container)

    tool_result = await _call_tool(server, "return_machines", {"machine_ids": "i-123"})

    assert tool_result.is_error is True
    assert "Input validation error" in tool_result.content[0].text
    container.get.assert_not_called()


@pytest.mark.asyncio
async def test_call_tool_rejects_missing_required_argument() -> None:
    """Omitting a required field is reported as a validation error."""
    container, orchestrator = _acquire_machines_container()

    server = build_server(container)
    tool_result = await _call_tool(server, "request_machines", {"template_id": "t1"})

    assert tool_result.is_error is True
    assert "Input validation error" in tool_result.content[0].text
    assert "requested_count" in tool_result.content[0].text
    orchestrator.execute.assert_not_called()


@pytest.mark.asyncio
async def test_call_tool_accepts_valid_arguments() -> None:
    """Arguments matching the schema reach the orchestrator."""
    container, orchestrator = _acquire_machines_container()

    server = build_server(container)
    tool_result = await _call_tool(
        server, "request_machines", {"template_id": "t1", "requested_count": 2}
    )

    assert tool_result.is_error is False
    orchestrator.execute.assert_called_once()
