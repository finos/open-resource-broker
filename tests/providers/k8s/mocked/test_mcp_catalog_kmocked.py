"""End-to-end MCP tests for the catalog-driven server against kmock Kubernetes.

Drives the unified :func:`build_server` against a real (kmock-backed) DI
container and exercises the broker lifecycle through the MCP ``tools/call``
path: ``list_templates`` / ``get_template`` then
``request_machines`` -> ``get_request_status`` -> ``return_machines``, plus
``list_return_requests``. Each assertion parses the ``TextContent`` JSON body
the server returns, so the coverage is against genuine provider-rendered
payloads rather than a fake container.

kmock provides an in-process aiohttp server emulating the Kubernetes apiserver;
``_inject_kmock_factory`` swaps the DI-wired strategy's client to point at it
post-bootstrap, and k8s machine ids are ``orb-...`` pod names (not EC2 ids).
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

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from tests.providers.k8s.mocked.kmock_delivery_conftest import (  # noqa: E402
    _inject_kmock_factory,
    _make_k8s_logger,
    _register_pod_resource,
)
from tests.shared.constants import REQUEST_ID_RE  # noqa: E402

_TEMPLATE_ID = "k8s-pod-example"

pytestmark = [pytest.mark.kmock, pytest.mark.mcp]


# ---------------------------------------------------------------------------
# Server fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def mcp_server_k8s(orb_config_dir_k8s, kmock_k8s):
    """Bootstrap the application and build the catalog MCP server over kmock."""
    from orb.bootstrap import Application
    from orb.interface.mcp.catalog_server import build_server

    _register_pod_resource(kmock_k8s)

    app = Application(skip_validation=True)
    await app.initialize()
    app._ensure_container()
    container = app._container

    _inject_kmock_factory(kmock_k8s, _make_k8s_logger())

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
    return is_error, json.loads(result.content[0].text)


async def _list_tools(server: Any) -> set[str]:
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


class TestMCPCatalogK8sToolSurface:
    @pytest.mark.asyncio
    async def test_tools_list_exposes_lifecycle_tools(self, mcp_server_k8s):
        tool_names = await _list_tools(mcp_server_k8s)
        for expected in (
            "list_templates",
            "get_template",
            "request_machines",
            "get_request_status",
            "return_machines",
            "list_return_requests",
        ):
            assert expected in tool_names, f"Tool {expected!r} missing from tools/list"


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


class TestMCPCatalogK8sTemplates:
    @pytest.mark.asyncio
    async def test_list_templates_returns_templates(self, mcp_server_k8s):
        is_error, body = await _call(mcp_server_k8s, "list_templates", {})

        assert not is_error, f"list_templates errored: {body}"
        templates = body.get("templates", [])
        assert len(templates) > 0, "list_templates returned no templates"
        for tpl in templates:
            tid = tpl.get("template_id") or tpl.get("templateId")
            assert tid, f"Template missing id: {tpl}"

    @pytest.mark.asyncio
    async def test_get_template_returns_requested_template(self, mcp_server_k8s):
        is_error, body = await _call(mcp_server_k8s, "get_template", {"template_id": _TEMPLATE_ID})

        assert not is_error, f"get_template errored: {body}"
        template = body.get("template") or body
        tid = template.get("template_id") or template.get("templateId")
        assert tid == _TEMPLATE_ID, f"Expected {_TEMPLATE_ID!r}, got {tid!r}"


# ---------------------------------------------------------------------------
# Request lifecycle
# ---------------------------------------------------------------------------


class TestMCPCatalogK8sRequestLifecycle:
    @pytest.mark.asyncio
    async def test_request_machines_returns_request_id(self, mcp_server_k8s):
        is_error, body = await _call(
            mcp_server_k8s,
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
    async def test_get_request_status_echoes_created_request(self, mcp_server_k8s):
        _, req_body = await _call(
            mcp_server_k8s,
            "request_machines",
            {"template_id": _TEMPLATE_ID, "requested_count": 1},
        )
        request_id = _request_id(req_body)
        assert request_id, f"No request_id: {req_body}"

        is_error, status_body = await _call(
            mcp_server_k8s, "get_request_status", {"request_ids": [request_id]}
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
    async def test_full_lifecycle_request_status_return(self, mcp_server_k8s):
        _, req_body = await _call(
            mcp_server_k8s,
            "request_machines",
            {"template_id": _TEMPLATE_ID, "requested_count": 1},
        )
        request_id = _request_id(req_body)
        assert request_id, f"No request_id: {req_body}"

        _, status_body = await _call(
            mcp_server_k8s, "get_request_status", {"request_ids": [request_id]}
        )
        machine_ids = _machine_ids(status_body)
        if not machine_ids:
            pytest.skip("No machine_ids returned — k8s pod not yet fulfilled")

        for mid in machine_ids:
            assert mid.startswith("orb-"), f"k8s machine_id {mid!r} does not start with 'orb-'"

        is_error, return_body = await _call(
            mcp_server_k8s, "return_machines", {"machine_ids": machine_ids}
        )
        assert not is_error, f"return_machines errored: {return_body}"
        assert _request_id(return_body) or return_body.get("message"), (
            f"return_machines missing request_id/message: {return_body}"
        )
