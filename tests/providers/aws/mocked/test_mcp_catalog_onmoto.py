"""End-to-end MCP tests for the catalog-driven server against moto-mocked AWS.

Drives the unified :func:`build_server` against a real (moto-backed) DI
container and exercises the full broker lifecycle through the MCP ``tools/call``
path: ``list_templates`` / ``get_template`` for the catalog surface, then
``request_machines`` -> ``get_request_status`` -> ``return_machines`` for the
request lifecycle, plus ``list_machines`` / ``list_return_requests`` for the
read surfaces. Each assertion parses the ``TextContent`` JSON body the server
returns, so the coverage is against genuine provider-rendered payloads rather
than a fake container.

Moto limitations are handled by the shared ``patch_moto_compat`` autouse
fixture (SSM resolution disabled, provisioning synthesised from instance ids)
inherited from ``tests.providers.aws.mocked.conftest``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import mcp.types as mcp_types
import pytest
import pytest_asyncio
from mcp.server.context import ServerRequestContext

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from tests.providers.aws.mocked.conftest import (
    _inject_moto_factory,
    _make_logger,
    _make_moto_aws_client,
)
from tests.shared.constants import REQUEST_ID_RE

REGION = "eu-west-2"
_TEMPLATE_ID = "RunInstances-OnDemand"

pytestmark = [pytest.mark.moto, pytest.mark.mcp]


# ---------------------------------------------------------------------------
# Server fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def mcp_server(orb_config_dir, moto_aws):
    """Bootstrap the application and build the catalog MCP server over moto.

    Mirrors the SDK moto fixture: initialise the application so the DI
    container is fully wired, inject the moto-backed AWS client factory, then
    hand back the low-level ``Server`` built from the wired container.
    """
    from orb.bootstrap import Application
    from orb.interface.mcp.catalog_server import build_server

    app = Application(skip_validation=True)
    await app.initialize()
    app._ensure_container()
    container = app._container

    _inject_moto_factory(_make_moto_aws_client(), _make_logger(), None)

    yield build_server(container)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx() -> ServerRequestContext[Any, Any]:
    """A ``ServerRequestContext`` with no HTTP request in scope (stdio-style)."""
    return ServerRequestContext(
        session=MagicMock(),
        lifespan_context={},
        protocol_version="2026-07-28",
        method="tools/call",
        request=None,
    )


async def _call(server: Any, name: str, arguments: dict[str, Any]) -> tuple[bool, Any]:
    """Invoke tools/call directly and return (isError, parsed JSON body)."""
    entry = server.get_request_handler("tools/call")
    result = await entry.handler(
        _make_ctx(), mcp_types.CallToolRequestParams(name=name, arguments=arguments)
    )
    is_error = bool(getattr(result, "is_error", False))
    text = result.content[0].text
    # Successful calls carry a JSON body; tool-level errors carry a plain
    # message string, so fall back to the raw text when it is not JSON.
    try:
        return is_error, json.loads(text)
    except json.JSONDecodeError:
        return is_error, text


async def _list_tools(server: Any) -> set[str]:
    """Return the set of advertised tool names via tools/list."""
    entry = server.get_request_handler("tools/list")
    result = await entry.handler(_make_ctx(), None)
    return {tool.name for tool in result.tools}


def _request_id(body: dict) -> str | None:
    return body.get("requestId") or body.get("request_id")


def _machine_ids(status_body: dict) -> list[str]:
    requests_list = status_body.get("requests", [])
    if not requests_list:
        return []
    machines = requests_list[0].get("machines", [])
    return [mid for m in machines for mid in [m.get("machineId") or m.get("machine_id")] if mid]


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------


class TestMCPCatalogToolSurface:
    @pytest.mark.asyncio
    async def test_tools_list_exposes_lifecycle_tools(self, mcp_server):
        tool_names = await _list_tools(mcp_server)
        for expected in (
            "list_templates",
            "get_template",
            "request_machines",
            "get_request_status",
            "return_machines",
            "list_machines",
            "list_return_requests",
        ):
            assert expected in tool_names, f"Tool {expected!r} missing from tools/list"


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


class TestMCPCatalogTemplates:
    @pytest.mark.asyncio
    async def test_list_templates_returns_aws_templates(self, mcp_server):
        is_error, body = await _call(mcp_server, "list_templates", {})

        assert not is_error, f"list_templates errored: {body}"
        templates = body.get("templates", [])
        assert len(templates) > 0, "list_templates returned no templates"
        for tpl in templates:
            tid = tpl.get("template_id") or tpl.get("templateId")
            assert tid, f"Template missing id: {tpl}"

    @pytest.mark.asyncio
    async def test_get_template_returns_requested_template(self, mcp_server):
        is_error, body = await _call(mcp_server, "get_template", {"template_id": _TEMPLATE_ID})

        assert not is_error, f"get_template errored: {body}"
        template = body.get("template") or body
        tid = template.get("template_id") or template.get("templateId")
        assert tid == _TEMPLATE_ID, f"Expected {_TEMPLATE_ID!r}, got {tid!r}"


# ---------------------------------------------------------------------------
# Request lifecycle
# ---------------------------------------------------------------------------


class TestMCPCatalogRequestLifecycle:
    @pytest.mark.asyncio
    async def test_request_machines_returns_request_id(self, mcp_server):
        is_error, body = await _call(
            mcp_server,
            "request_machines",
            {"template_id": _TEMPLATE_ID, "requested_count": 1},
        )

        assert not is_error, f"request_machines errored: {body}"
        request_id = _request_id(body)
        assert request_id is not None, f"No request_id in body: {body}"
        assert REQUEST_ID_RE.match(request_id), (
            f"request_id {request_id!r} does not match expected pattern"
        )

    @pytest.mark.asyncio
    async def test_get_request_status_echoes_created_request(self, mcp_server):
        _, req_body = await _call(
            mcp_server,
            "request_machines",
            {"template_id": _TEMPLATE_ID, "requested_count": 1},
        )
        request_id = _request_id(req_body)
        assert request_id, f"No request_id: {req_body}"

        is_error, status_body = await _call(
            mcp_server, "get_request_status", {"request_ids": [request_id]}
        )

        assert not is_error, f"get_request_status errored: {status_body}"
        requests_list = status_body.get("requests", [])
        assert requests_list, f"No requests in status body: {status_body}"
        first = requests_list[0]
        status = first.get("status", "unknown")
        assert status in {
            "running",
            "complete",
            "complete_with_error",
            "pending",
            "unknown",
        }, f"Unexpected status: {status!r}"
        returned_id = first.get("request_id") or first.get("requestId")
        assert returned_id == request_id, (
            f"Echoed request_id {returned_id!r} != created {request_id!r}"
        )

    @pytest.mark.asyncio
    async def test_full_lifecycle_request_status_return(self, mcp_server):
        # 1. Request machines.
        _, req_body = await _call(
            mcp_server,
            "request_machines",
            {"template_id": _TEMPLATE_ID, "requested_count": 1},
        )
        request_id = _request_id(req_body)
        assert request_id, f"No request_id: {req_body}"

        # 2. Status must fulfil at least one moto instance id.
        _, status_body = await _call(
            mcp_server, "get_request_status", {"request_ids": [request_id]}
        )
        machine_ids = _machine_ids(status_body)
        assert machine_ids, f"No machine_ids after request: {status_body}"
        for mid in machine_ids:
            assert mid.startswith("i-"), f"machineId {mid!r} is not an EC2 instance id"

        # 3. The machines must be visible through list_machines.
        _, machines_body = await _call(mcp_server, "list_machines", {})
        listed = machines_body.get("machines", [])
        listed_ids = {m.get("machineId") or m.get("machine_id") for m in listed}
        assert any(mid in listed_ids for mid in machine_ids), (
            f"None of {machine_ids} present in list_machines: {listed_ids}"
        )

        # 4. Return the machines.
        is_error, return_body = await _call(
            mcp_server, "return_machines", {"machine_ids": machine_ids}
        )
        assert not is_error, f"return_machines errored: {return_body}"
        assert _request_id(return_body) or return_body.get("message"), (
            f"return_machines missing request_id/message: {return_body}"
        )

    @pytest.mark.asyncio
    async def test_list_return_requests_after_return(self, mcp_server):
        _, req_body = await _call(
            mcp_server,
            "request_machines",
            {"template_id": _TEMPLATE_ID, "requested_count": 1},
        )
        request_id = _request_id(req_body)
        assert request_id

        _, status_body = await _call(
            mcp_server, "get_request_status", {"request_ids": [request_id]}
        )
        machine_ids = _machine_ids(status_body)
        assert machine_ids, f"No machine_ids to return: {status_body}"

        await _call(mcp_server, "return_machines", {"machine_ids": machine_ids})

        is_error, list_body = await _call(mcp_server, "list_return_requests", {})
        assert not is_error, f"list_return_requests errored: {list_body}"
        assert len(list_body.get("requests", [])) > 0, "list_return_requests empty after a return"


# ---------------------------------------------------------------------------
# Error surface
# ---------------------------------------------------------------------------


class TestMCPCatalogErrors:
    @pytest.mark.asyncio
    async def test_unknown_template_is_error_result(self, mcp_server):
        is_error, body = await _call(
            mcp_server,
            "request_machines",
            {"template_id": "NonExistent-Template-XYZ", "requested_count": 1},
        )

        # Either the tool-level isError flag is set, or the rendered body
        # signals the failure — both are well-formed (no raised exception).
        signalled = is_error or (
            isinstance(body, dict)
            and (
                body.get("error")
                or body.get("status") == "error"
                or "not found" in str(body).lower()
                or "NonExistent" in str(body)
            )
        )
        assert signalled, f"Expected an error signal for unknown template, got: {body}"
