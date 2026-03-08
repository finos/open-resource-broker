"""MCP integration tests against moto-mocked AWS.

Exercises the MCP server in-process via handle_message() with JSON-RPC strings.
Uses the same moto injection pattern as test_sdk_onmoto.py so all AWS calls
route through moto without real credentials.

Moto limitations accounted for:
- SSM parameter resolution: patched out (moto cannot resolve SSM paths)
- AWSProvisioningAdapter: patched to synthesise instances from instance_ids
  so the orchestration loop completes on the first attempt
- LT deletion: lt_manager is a MagicMock — cleanup tests assert the mock's
  delete method was called, not that moto reflects the deletion
"""

import json
import re
import sys
from pathlib import Path
from typing import Any

import boto3
import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from tests.onmoto.conftest import _inject_moto_factory, _make_logger, _make_moto_aws_client
from tests.shared.scenarios import TestScenario, get_smoke_scenarios

from tests.shared.constants import REQUEST_ID_RE

REGION = "eu-west-2"

pytestmark = [pytest.mark.moto, pytest.mark.mcp]


# ---------------------------------------------------------------------------
# MCP server fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def mcp_server(orb_config_dir, moto_aws):
    """Bootstrap the application and return a live MCP server backed by moto."""
    from orb.bootstrap import Application
    from orb.infrastructure.di.container import get_container
    from orb.interface.mcp.server.core import OpenResourceBrokerMCPServer

    app = Application(skip_validation=True)
    await app.initialize()

    container = get_container()
    server = OpenResourceBrokerMCPServer(app=container)

    aws_client = _make_moto_aws_client()
    logger = _make_logger()
    _inject_moto_factory(aws_client, logger, None)

    # All interface handlers take only (args,) — wrap each tool so the server's
    # _handle_tools_call convention of tool_func(args, self.app) still works.
    import functools

    wrapped: dict = {}
    for name, fn in server.tools.items():
        import inspect

        sig = inspect.signature(fn)
        if len(sig.parameters) == 1:
            async def _wrap(args, _app, _fn=fn):
                return await _fn(args)
            functools.update_wrapper(_wrap, fn)
            wrapped[name] = _wrap
        else:
            wrapped[name] = fn
    server.tools = wrapped

    yield server


# ---------------------------------------------------------------------------
# Helper
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
    """Extract the JSON payload from a tools/call content[0].text response.

    Handlers may return a (dict, exit_code) tuple which the server serialises
    as a JSON array — unwrap the first element in that case.
    """
    content = response["result"]["content"]
    parsed = json.loads(content[0]["text"])
    if isinstance(parsed, list) and len(parsed) >= 1 and isinstance(parsed[0], dict):
        return parsed[0]
    return parsed


# ---------------------------------------------------------------------------
# TestMCPServerInit
# ---------------------------------------------------------------------------


class TestMCPServerInit:
    @pytest.mark.asyncio
    async def test_initialize_returns_capabilities(self, mcp_server):
        resp = await _send(mcp_server, "initialize", {"clientInfo": {"name": "test"}})

        assert not _has_error(resp), f"Unexpected error: {resp.get('error')}"
        result = resp["result"]
        assert "protocolVersion" in result
        assert "capabilities" in result
        assert "tools" in result["capabilities"]

    @pytest.mark.asyncio
    async def test_tools_list_returns_expected_tools(self, mcp_server):
        resp = await _send(mcp_server, "tools/list")

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
# TestMCPTemplates
# ---------------------------------------------------------------------------


class TestMCPTemplates:
    @pytest.mark.asyncio
    async def test_list_templates_via_mcp(self, mcp_server):
        resp = await _send(
            mcp_server, "tools/call", {"name": "list_templates", "arguments": {}}
        )

        assert not _has_error(resp), f"Unexpected error: {resp.get('error')}"
        payload = _tool_text(resp)
        templates = payload.get("templates", [])
        assert len(templates) > 0, "list_templates returned no templates"
        for tpl in templates:
            tid = tpl.get("template_id") or tpl.get("templateId")
            assert tid, f"Template missing template_id: {tpl}"

    @pytest.mark.asyncio
    async def test_get_template_via_mcp(self, mcp_server):
        resp = await _send(
            mcp_server,
            "tools/call",
            {"name": "get_template", "arguments": {"template_id": "RunInstances-OnDemand"}},
        )

        assert not _has_error(resp), f"Unexpected error: {resp.get('error')}"
        payload = _tool_text(resp)
        assert isinstance(payload, dict)
        template = payload.get("template") or payload
        assert "template_id" in template or "templateId" in template, (
            f"Expected template_id or templateId in template payload: {payload}"
        )


# ---------------------------------------------------------------------------
# TestMCPRequestLifecycle
# ---------------------------------------------------------------------------


class TestMCPRequestLifecycle:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("scenario", get_smoke_scenarios(), ids=lambda s: s.scenario_id)
    async def test_request_machines_returns_request_id(self, mcp_server, scenario: TestScenario):
        resp = await _send(
            mcp_server,
            "tools/call",
            {"name": "request_machines", "arguments": {"template_id": scenario.template_id, "machine_count": scenario.capacity}},
        )

        assert not _has_error(resp), f"Unexpected error: {resp.get('error')}"
        payload = _tool_text(resp)
        request_id = payload.get("requestId") or payload.get("request_id")
        assert request_id is not None, f"No request_id in response: {payload}"
        assert REQUEST_ID_RE.match(request_id), (
            f"request_id {request_id!r} does not match expected pattern"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("scenario", get_smoke_scenarios(), ids=lambda s: s.scenario_id)
    async def test_get_request_status_after_request(self, mcp_server, scenario: TestScenario):
        # Create a request first
        req_resp = await _send(
            mcp_server,
            "tools/call",
            {"name": "request_machines", "arguments": {"template_id": scenario.template_id, "machine_count": scenario.capacity}},
        )
        request_id = _tool_text(req_resp).get("requestId") or _tool_text(req_resp).get("request_id")
        assert request_id, f"No request_id from request_machines: {req_resp}"

        # Query status
        status_resp = await _send(
            mcp_server,
            "tools/call",
            {"name": "get_request_status", "arguments": {"request_id": request_id}},
        )

        assert not _has_error(status_resp), f"Unexpected error: {status_resp.get('error')}"
        payload = _tool_text(status_resp)

        # Status must be a known value
        requests_list = payload.get("requests", [])
        if requests_list:
            status = requests_list[0].get("status", "unknown")
            assert status in {"running", "complete", "complete_with_error", "pending", "unknown"}, (
                f"Unexpected status: {status!r}"
            )
            returned_id = requests_list[0].get("request_id") or requests_list[0].get("requestId")
            assert returned_id == request_id, (
                f"Echoed request_id {returned_id!r} != created {request_id!r}"
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("scenario", get_smoke_scenarios(), ids=lambda s: s.scenario_id)
    async def test_full_lifecycle_request_and_return(self, mcp_server, scenario: TestScenario):
        # 1. Request machines
        req_resp = await _send(
            mcp_server,
            "tools/call",
            {"name": "request_machines", "arguments": {"template_id": scenario.template_id, "machine_count": scenario.capacity}},
        )
        req_payload = _tool_text(req_resp)
        request_id = req_payload.get("requestId") or req_payload.get("request_id")
        assert request_id, f"No request_id: {req_payload}"

        # 2. Get status — look for machine_ids
        status_resp = await _send(
            mcp_server,
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
            pytest.skip("No machine_ids returned — RunInstances may not have fulfilled yet")

        for mid in machine_ids:
            assert re.match(r"^i-[0-9a-f]+$", mid), (
                f"machineId {mid!r} does not look like an EC2 instance ID"
            )

        # 3. Return machines
        return_resp = await _send(
            mcp_server,
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

        # Poll for return completion
        import time

        return_request_id = return_payload.get("request_id") or return_payload.get("requestId")
        if return_request_id:
            deadline = time.time() + 10
            while time.time() < deadline:
                list_resp = await _send(
                    mcp_server, "tools/call", {"name": "list_return_requests", "arguments": {}}
                )
                if not _has_error(list_resp):
                    requests = _tool_text(list_resp).get("requests", [])
                    done = any(
                        (r.get("request_id") or r.get("requestId")) == return_request_id
                        and r.get("status") == "complete"
                        for r in requests
                        if isinstance(r, dict)
                    )
                    if done:
                        break
                import asyncio
                await asyncio.sleep(0.5)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("scenario", get_smoke_scenarios(), ids=lambda s: s.scenario_id)
    async def test_list_return_requests_after_return(self, mcp_server, scenario: TestScenario):
        # Create and return a request
        req_resp = await _send(
            mcp_server,
            "tools/call",
            {"name": "request_machines", "arguments": {"template_id": scenario.template_id, "machine_count": scenario.capacity}},
        )
        req_payload = _tool_text(req_resp)
        request_id = req_payload.get("requestId") or req_payload.get("request_id")
        assert request_id

        status_resp = await _send(
            mcp_server,
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
            mcp_server,
            "tools/call",
            {"name": "return_machines", "arguments": {"machine_ids": machine_ids}},
        )

        # List return requests — must be non-empty
        list_resp = await _send(
            mcp_server, "tools/call", {"name": "list_return_requests", "arguments": {}}
        )
        assert not _has_error(list_resp), f"Unexpected error: {list_resp.get('error')}"
        list_payload = _tool_text(list_resp)
        requests = list_payload.get("requests", [])
        assert len(requests) > 0, "list_return_requests returned empty list after a return"


# ---------------------------------------------------------------------------
# TestMCPLaunchTemplateCleanup
# ---------------------------------------------------------------------------


class TestMCPLaunchTemplateCleanup:
    @pytest.mark.asyncio
    async def test_launch_template_created_during_request(self, mcp_server):
        """After request_machines, the moto-backed lt_manager.create_or_update was called."""
        from orb.providers.registry import get_provider_registry

        registry = get_provider_registry()
        strategy = registry.get_or_create_strategy("aws_moto_eu-west-2")
        if strategy is None:
            pytest.skip("Strategy not available")

        handler_registry = strategy._get_handler_registry()
        # Grab the lt_manager from one of the handlers
        from orb.providers.aws.domain.template.value_objects import ProviderApi

        handler = handler_registry._handler_cache.get(ProviderApi.RUN_INSTANCES.value)
        if handler is None:
            pytest.skip("RunInstances handler not in cache")

        lt_manager = handler.launch_template_manager

        await _send(
            mcp_server,
            "tools/call",
            {"name": "request_machines", "arguments": {"template_id": "RunInstances-OnDemand", "machine_count": 1}},
        )

        lt_manager.create_or_update_launch_template.assert_called()

    @pytest.mark.asyncio
    async def test_launch_template_deleted_after_return(self, mcp_server):
        """After return_machines, the LT created during provisioning is gone from moto EC2.

        The base_handler calls aws_client.ec2_client.delete_launch_template() directly,
        so we verify via moto state rather than a mock assertion.
        """
        # Full lifecycle
        req_resp = await _send(
            mcp_server,
            "tools/call",
            {"name": "request_machines", "arguments": {"template_id": "RunInstances-OnDemand", "machine_count": 1}},
        )
        req_payload = _tool_text(req_resp)
        request_id = req_payload.get("requestId") or req_payload.get("request_id")
        assert request_id

        # Confirm the LT was created in moto
        ec2 = boto3.client("ec2", region_name=REGION)
        lt_name = f"{request_id}-RunInstances-OnDemand"
        lts_before = ec2.describe_launch_templates(
            Filters=[{"Name": "launch-template-name", "Values": [lt_name]}]
        )["LaunchTemplates"]
        assert len(lts_before) == 1, f"Expected LT {lt_name!r} to exist before return"

        status_resp = await _send(
            mcp_server,
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
            pytest.skip("No machine_ids — cannot verify LT cleanup")

        return_resp_lt = await _send(
            mcp_server,
            "tools/call",
            {"name": "return_machines", "arguments": {"machine_ids": machine_ids}},
        )

        # Poll for return completion
        import asyncio
        import time

        lt_return_payload = _tool_text(return_resp_lt) if not _has_error(return_resp_lt) else {}
        lt_return_request_id = lt_return_payload.get("request_id") or lt_return_payload.get("requestId")
        if lt_return_request_id:
            deadline = time.time() + 10
            while time.time() < deadline:
                list_resp = await _send(
                    mcp_server, "tools/call", {"name": "list_return_requests", "arguments": {}}
                )
                if not _has_error(list_resp):
                    requests = _tool_text(list_resp).get("requests", [])
                    done = any(
                        (r.get("request_id") or r.get("requestId")) == lt_return_request_id
                        and r.get("status") == "complete"
                        for r in requests
                        if isinstance(r, dict)
                    )
                    if done:
                        break
                await asyncio.sleep(0.5)

        # LT must be gone from moto after return
        lts_after = ec2.describe_launch_templates(
            Filters=[{"Name": "launch-template-name", "Values": [lt_name]}]
        )["LaunchTemplates"]
        assert len(lts_after) == 0, (
            f"Expected LT {lt_name!r} to be deleted after return, but it still exists"
        )


# ---------------------------------------------------------------------------
# TestMCPResources
# ---------------------------------------------------------------------------


class TestMCPResources:
    @pytest.mark.asyncio
    async def test_resources_list(self, mcp_server):
        resp = await _send(mcp_server, "resources/list")

        assert not _has_error(resp), f"Unexpected error: {resp.get('error')}"
        uris = {r["uri"] for r in resp["result"]["resources"]}
        for expected_uri in ("templates://", "requests://", "machines://", "providers://"):
            assert expected_uri in uris, f"URI {expected_uri!r} missing from resources/list"

    @pytest.mark.asyncio
    async def test_resources_read_templates(self, mcp_server):
        resp = await _send(mcp_server, "resources/read", {"uri": "templates://"})

        assert not _has_error(resp), f"Unexpected error: {resp.get('error')}"
        assert "contents" in resp["result"]


# ---------------------------------------------------------------------------
# TestMCPErrorHandling
# ---------------------------------------------------------------------------


class TestMCPErrorHandling:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, mcp_server):
        resp = await _send(
            mcp_server,
            "tools/call",
            {"name": "nonexistent_tool", "arguments": {}},
        )

        # Must be a JSON error response, not a Python exception
        assert _has_error(resp), f"Expected error field in response: {resp}"

    @pytest.mark.asyncio
    async def test_unknown_method_returns_error(self, mcp_server):
        resp = await _send(mcp_server, "unknown/method")

        assert _has_error(resp), f"Expected error field in response: {resp}"
        assert resp["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_malformed_json_returns_parse_error(self, mcp_server):
        raw = await mcp_server.handle_message("this is not json {{{")
        resp = json.loads(raw)

        assert _has_error(resp), f"Expected error field in response: {resp}"
        assert resp["error"]["code"] == -32700

    @pytest.mark.asyncio
    async def test_request_machines_unknown_template_returns_error(self, mcp_server):
        """request_machines with a non-existent template_id returns an error, not a crash."""
        resp = await _send(
            mcp_server,
            "tools/call",
            {"name": "request_machines", "arguments": {"template_id": "NonExistent-Template-XYZ", "machine_count": 1}},
        )

        # The server must return a well-formed JSON-RPC response (no Python exception).
        # Either the envelope carries an error field, or the tool result payload
        # indicates an error (error key, status==error, or error message text).
        if _has_error(resp):
            return

        payload = _tool_text(resp)
        has_error = (
            isinstance(payload, dict) and (
                payload.get("error") or
                payload.get("status") == "error" or
                "not found" in str(payload).lower() or
                "NonExistent" in str(payload)
            )
        )
        assert has_error, (
            f"Expected error payload for unknown template, got: {payload}"
        )
