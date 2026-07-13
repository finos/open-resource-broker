"""MCP delivery-surface tests against kmock-backed Kubernetes.

Exercises the MCP server in-process via handle_message() with JSON-RPC strings.
Uses the same kmock injection pattern as test_sdk_kmocked.py so all kubernetes
SDK calls route through the emulator without a real cluster.

kmock limitations accounted for:
- kmock provides an in-process aiohttp server emulating the Kubernetes
  apiserver at HTTP level.
- K8sClient is swapped post-bootstrap to point at the kmock URL.
- k8s machine_ids are ``orb-...`` pod names, not EC2 instance IDs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from tests.providers.k8s.mocked.kmock_delivery_conftest import (  # noqa: E402
    _inject_kmock_factory,
    _make_k8s_logger,
    _register_pod_resource,
)
from tests.shared.constants import REQUEST_ID_RE  # noqa: E402

pytestmark = [pytest.mark.kmock, pytest.mark.mcp]

_K8S_TEMPLATE_ID = "k8s-pod-example"
_K8S_CAPACITY = 1


# ---------------------------------------------------------------------------
# MCP server fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def mcp_server_k8s(orb_config_dir_k8s, kmock_k8s):
    """Bootstrap the application and return a live MCP server backed by kmock."""
    from orb.bootstrap import Application
    from orb.infrastructure.di.container import get_container
    from orb.interface.mcp.server.core import OpenResourceBrokerMCPServer

    _register_pod_resource(kmock_k8s)

    app = Application(skip_validation=True)
    await app.initialize()

    logger = _make_k8s_logger()
    _inject_kmock_factory(kmock_k8s, logger)

    container = get_container()
    server = OpenResourceBrokerMCPServer(app=container)

    yield server


# ---------------------------------------------------------------------------
# Helpers  (mirrors test_mcp_onmoto.py)
# ---------------------------------------------------------------------------


async def _send(server, method: str, params: dict | None = None, msg_id: int = 1) -> dict:
    """Send a JSON-RPC message to the MCP server and return the parsed response."""
    msg = json.dumps({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}})
    raw = await server.handle_message(msg)
    return json.loads(raw)


def _has_error(response: dict) -> bool:
    """Return True if the JSON-RPC response carries a non-null error."""
    return bool(response.get("error"))


def _tool_text(response: dict) -> Any:  # type: ignore[return]
    """Extract the JSON payload from a tools/call content[0].text response."""
    content = response["result"]["content"]
    parsed = json.loads(content[0]["text"])
    if isinstance(parsed, list) and len(parsed) >= 1 and isinstance(parsed[0], dict):
        return parsed[0]
    return parsed


# ---------------------------------------------------------------------------
# TestMCPK8sServerInit
# ---------------------------------------------------------------------------


class TestMCPK8sServerInit:
    @pytest.mark.asyncio
    async def test_initialize_returns_capabilities(self, mcp_server_k8s):
        resp = await _send(mcp_server_k8s, "initialize", {"clientInfo": {"name": "test"}})

        assert not _has_error(resp), f"Unexpected error: {resp.get('error')}"
        result = resp["result"]
        assert "protocolVersion" in result
        assert "capabilities" in result
        assert "tools" in result["capabilities"]

    @pytest.mark.asyncio
    async def test_tools_list_returns_expected_tools(self, mcp_server_k8s):
        resp = await _send(mcp_server_k8s, "tools/list")

        assert not _has_error(resp), f"Unexpected error: {resp.get('error')}"
        tool_names = {t["name"] for t in resp["result"]["tools"]}
        for expected in (
            "list_templates",
            "request_machines",
            "get_request_status",
            "return_machines",
            "list_return_requests",
        ):
            assert expected in tool_names, f"Tool {expected!r} missing from tools/list"


# ---------------------------------------------------------------------------
# TestMCPK8sTemplates
# ---------------------------------------------------------------------------


class TestMCPK8sTemplates:
    @pytest.mark.asyncio
    async def test_list_templates_via_mcp(self, mcp_server_k8s):
        resp = await _send(
            mcp_server_k8s, "tools/call", {"name": "list_templates", "arguments": {}}
        )

        assert not _has_error(resp), f"Unexpected error: {resp.get('error')}"
        payload = _tool_text(resp)
        templates = payload.get("templates", [])
        assert len(templates) > 0, "list_templates returned no templates"
        for tpl in templates:
            tid = tpl.get("template_id") or tpl.get("templateId")
            assert tid, f"Template missing template_id: {tpl}"

    @pytest.mark.asyncio
    async def test_get_template_via_mcp(self, mcp_server_k8s):
        resp = await _send(
            mcp_server_k8s,
            "tools/call",
            {"name": "get_template", "arguments": {"template_id": _K8S_TEMPLATE_ID}},
        )

        assert not _has_error(resp), f"Unexpected error: {resp.get('error')}"
        payload = _tool_text(resp)
        assert isinstance(payload, dict)
        template = payload.get("template") or payload
        assert "template_id" in template or "templateId" in template, (
            f"Expected template_id or templateId in template payload: {payload}"
        )


# ---------------------------------------------------------------------------
# TestMCPK8sRequestLifecycle
# ---------------------------------------------------------------------------


class TestMCPK8sRequestLifecycle:
    @pytest.mark.asyncio
    async def test_request_machines_returns_request_id(self, mcp_server_k8s):
        resp = await _send(
            mcp_server_k8s,
            "tools/call",
            {
                "name": "request_machines",
                "arguments": {
                    "template_id": _K8S_TEMPLATE_ID,
                    "machine_count": _K8S_CAPACITY,
                },
            },
        )

        assert not _has_error(resp), f"Unexpected error: {resp.get('error')}"
        payload = _tool_text(resp)
        request_id = payload.get("requestId") or payload.get("request_id")
        assert request_id is not None, f"No request_id in response: {payload}"
        assert REQUEST_ID_RE.match(request_id), (
            f"request_id {request_id!r} does not match expected pattern"
        )

    @pytest.mark.asyncio
    async def test_get_request_status_after_request(self, mcp_server_k8s):
        req_resp = await _send(
            mcp_server_k8s,
            "tools/call",
            {
                "name": "request_machines",
                "arguments": {
                    "template_id": _K8S_TEMPLATE_ID,
                    "machine_count": _K8S_CAPACITY,
                },
            },
        )
        request_id = _tool_text(req_resp).get("requestId") or _tool_text(req_resp).get("request_id")
        assert request_id, f"No request_id from request_machines: {req_resp}"

        status_resp = await _send(
            mcp_server_k8s,
            "tools/call",
            {"name": "get_request_status", "arguments": {"request_id": request_id}},
        )

        assert not _has_error(status_resp), f"Unexpected error: {status_resp.get('error')}"
        payload = _tool_text(status_resp)

        requests_list = payload.get("requests", [])
        if requests_list:
            status = requests_list[0].get("status", "unknown")
            assert status in {
                "running",
                "complete",
                "complete_with_error",
                "pending",
                "unknown",
            }, f"Unexpected status: {status!r}"
            returned_id = requests_list[0].get("request_id") or requests_list[0].get("requestId")
            assert returned_id == request_id, (
                f"Echoed request_id {returned_id!r} != created {request_id!r}"
            )

    @pytest.mark.asyncio
    async def test_full_lifecycle_request_and_return(self, mcp_server_k8s):
        # 1. Request machines
        req_resp = await _send(
            mcp_server_k8s,
            "tools/call",
            {
                "name": "request_machines",
                "arguments": {
                    "template_id": _K8S_TEMPLATE_ID,
                    "machine_count": _K8S_CAPACITY,
                },
            },
        )
        req_payload = _tool_text(req_resp)
        request_id = req_payload.get("requestId") or req_payload.get("request_id")
        assert request_id, f"No request_id: {req_payload}"

        # 2. Get status — look for machine_ids
        status_resp = await _send(
            mcp_server_k8s,
            "tools/call",
            {"name": "get_request_status", "arguments": {"request_id": request_id}},
        )
        status_payload = _tool_text(status_resp)
        requests_list = status_payload.get("requests", [])
        machine_ids: list[str] = []
        if requests_list:
            machines = requests_list[0].get("machines", [])
            machine_ids = [
                m.get("machineId") or m.get("machine_id")
                for m in machines
                if m.get("machineId") or m.get("machine_id")
            ]

        if not machine_ids:
            pytest.skip("No machine_ids returned — k8s pod may not have been fulfilled yet")

        for mid in machine_ids:
            assert mid.startswith("orb-"), f"k8s machine_id {mid!r} does not start with 'orb-'"

        # 3. Return machines
        return_resp = await _send(
            mcp_server_k8s,
            "tools/call",
            {"name": "return_machines", "arguments": {"machine_ids": machine_ids}},
        )
        assert not _has_error(return_resp), f"Unexpected error: {return_resp.get('error')}"
        return_payload = _tool_text(return_resp)
        has_id = return_payload.get("request_id") or return_payload.get("requestId")
        has_msg = return_payload.get("message")
        assert has_id or has_msg, (
            f"return_machines response missing request_id or message: {return_payload}"
        )

    @pytest.mark.asyncio
    async def test_list_return_requests_after_return(self, mcp_server_k8s):
        req_resp = await _send(
            mcp_server_k8s,
            "tools/call",
            {
                "name": "request_machines",
                "arguments": {
                    "template_id": _K8S_TEMPLATE_ID,
                    "machine_count": _K8S_CAPACITY,
                },
            },
        )
        req_payload = _tool_text(req_resp)
        request_id = req_payload.get("requestId") or req_payload.get("request_id")
        assert request_id

        status_resp = await _send(
            mcp_server_k8s,
            "tools/call",
            {"name": "get_request_status", "arguments": {"request_id": request_id}},
        )
        status_payload = _tool_text(status_resp)
        requests_list = status_payload.get("requests", [])
        machine_ids: list[str] = []
        if requests_list:
            machines = requests_list[0].get("machines", [])
            machine_ids = [
                m.get("machineId") or m.get("machine_id")
                for m in machines
                if m.get("machineId") or m.get("machine_id")
            ]

        if not machine_ids:
            pytest.skip("No machine_ids — cannot create a return request")

        await _send(
            mcp_server_k8s,
            "tools/call",
            {"name": "return_machines", "arguments": {"machine_ids": machine_ids}},
        )

        list_resp = await _send(
            mcp_server_k8s, "tools/call", {"name": "list_return_requests", "arguments": {}}
        )
        assert not _has_error(list_resp), f"Unexpected error: {list_resp.get('error')}"
        list_payload = _tool_text(list_resp)
        requests = list_payload.get("requests", [])
        assert len(requests) > 0, "list_return_requests returned empty list after a return"


# ---------------------------------------------------------------------------
# TestMCPK8sResources
# ---------------------------------------------------------------------------


class TestMCPK8sResources:
    @pytest.mark.asyncio
    async def test_resources_list(self, mcp_server_k8s):
        resp = await _send(mcp_server_k8s, "resources/list")

        assert not _has_error(resp), f"Unexpected error: {resp.get('error')}"
        uris = {r["uri"] for r in resp["result"]["resources"]}
        for expected_uri in ("templates://", "requests://", "machines://", "providers://"):
            assert expected_uri in uris, f"URI {expected_uri!r} missing from resources/list"

    @pytest.mark.asyncio
    async def test_resources_read_templates(self, mcp_server_k8s):
        resp = await _send(mcp_server_k8s, "resources/read", {"uri": "templates://"})

        assert not _has_error(resp), f"Unexpected error: {resp.get('error')}"
        assert "contents" in resp["result"]


# ---------------------------------------------------------------------------
# TestMCPK8sErrorHandling
# ---------------------------------------------------------------------------


class TestMCPK8sErrorHandling:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, mcp_server_k8s):
        resp = await _send(
            mcp_server_k8s,
            "tools/call",
            {"name": "nonexistent_tool", "arguments": {}},
        )

        assert _has_error(resp), f"Expected error field in response: {resp}"

    @pytest.mark.asyncio
    async def test_unknown_method_returns_error(self, mcp_server_k8s):
        resp = await _send(mcp_server_k8s, "unknown/method")

        assert _has_error(resp), f"Expected error field in response: {resp}"
        assert resp["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_malformed_json_returns_parse_error(self, mcp_server_k8s):
        raw = await mcp_server_k8s.handle_message("this is not json {{{")
        resp = json.loads(raw)

        assert _has_error(resp), f"Expected error field in response: {resp}"
        assert resp["error"]["code"] == -32700

    @pytest.mark.asyncio
    async def test_request_machines_unknown_template_returns_error(self, mcp_server_k8s):
        """request_machines with a non-existent template_id returns an error, not a crash."""
        resp = await _send(
            mcp_server_k8s,
            "tools/call",
            {
                "name": "request_machines",
                "arguments": {"template_id": "NonExistent-K8s-Template-XYZ", "machine_count": 1},
            },
        )

        if _has_error(resp):
            return

        payload = _tool_text(resp)
        has_error = isinstance(payload, dict) and (
            payload.get("error")
            or payload.get("status") == "error"
            or "not found" in str(payload).lower()
            or "NonExistent" in str(payload)
        )
        assert has_error, f"Expected error payload for unknown template, got: {payload}"
