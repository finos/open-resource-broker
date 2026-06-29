"""MCP integration tests for Open Resource Broker on real AWS.

Exercises the full acquire→return control loop via the MCP server in-process,
calling handle_message() with JSON-RPC 2.0 envelopes — no subprocess, no TCP.
"""

import json
import logging
import os
import shutil
import time

import boto3.session
import pytest

from tests.providers.aws.live import scenarios
from tests.providers.aws.live.cleanup_helpers import (
    cleanup_launch_templates_for_request,
    get_machine_ids_from_ec2 as _get_machine_ids_from_ec2_helper,
    wait_for_instances_terminated,
)
from tests.providers.aws.live.template_processor import TemplateProcessor

try:
    from tests.providers.aws.live.scenarios_mcp import (  # type: ignore[import]
        MCP_RUN_DEFAULT_COMBINATIONS,
        MCP_TIMEOUTS,
    )
except ImportError:
    MCP_RUN_DEFAULT_COMBINATIONS = True
    MCP_TIMEOUTS = {"request_completion": 600, "return_completion": 300, "poll_interval": 5}

# Import AWS validation helpers from test_onaws (guarded for env/creds issues)
try:
    from tests.providers.aws.live.test_onaws import (
        _check_all_ec2_hosts_are_being_terminated,
        get_instance_state,
    )
except Exception as exc:  # pragma: no cover
    pytest.skip(
        f"Skipping MCP onaws tests because base onaws helpers failed to import: {exc}",
        allow_module_level=True,
    )

pytestmark = [
    pytest.mark.manual_aws,
    pytest.mark.aws,
    pytest.mark.mcp,
]

os.environ["USE_LOCAL_DEV"] = "1"
os.environ.setdefault("HF_LOGDIR", "./logs")
os.environ.setdefault("AWS_PROVIDER_LOG_DIR", "./logs")
os.environ["LOG_DESTINATION"] = "file"
os.environ.setdefault("AWS_REGION", "eu-west-1")

_ec2_client = None


def _get_boto_profile_and_region() -> tuple[str | None, str]:
    """Read AWS profile and region from ORB_CONFIG_DIR config."""
    import json as _json

    profile = None
    region = None
    config_dir = os.environ.get("ORB_CONFIG_DIR")
    if config_dir:
        try:
            config_path = os.path.join(config_dir, "config.json")
            with open(config_path) as _f:
                config = _json.load(_f)
            providers = config.get("provider", {}).get("providers", [])
            if providers:
                provider_cfg = providers[0].get("config", {})
                profile = provider_cfg.get("profile")
                region = provider_cfg.get("region")
        except Exception:
            pass
    region = (
        region
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "eu-west-1"
    )
    return profile, region


def _get_ec2_client():
    global _ec2_client
    if _ec2_client is None:
        _profile, _region = _get_boto_profile_and_region()
        _ec2_client = boto3.session.Session(profile_name=_profile, region_name=_region).client(
            "ec2", region_name=_region
        )
    return _ec2_client


log = logging.getLogger("mcp_test")
log.setLevel(logging.DEBUG)
_formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s"
)
_console = logging.StreamHandler()
_console.setLevel(logging.DEBUG)
_console.setFormatter(_formatter)
log.addHandler(_console)

from tests.shared.constants import REQUEST_ID_RE

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def setup_mcp_test(request, test_session_id):
    """Generate per-test config dir, set env vars, construct MCP server, yield, teardown."""
    import asyncio

    from orb.bootstrap import Application
    from orb.infrastructure.di import reset_container
    from orb.infrastructure.di.container import get_container
    from orb.interface.mcp.server.core import OpenResourceBrokerMCPServer

    processor = TemplateProcessor()
    test_name = request.node.name

    overrides = {}
    if hasattr(request, "param") and isinstance(request.param, dict):
        overrides = request.param.get("overrides", {})
    overrides["instanceTags"] = {
        **overrides.get("instanceTags", {}),
        "test-session": test_session_id,
    }

    test_config_dir = processor.run_templates_dir / test_name
    if test_config_dir.exists():
        shutil.rmtree(test_config_dir)

    processor.generate_test_templates(test_name, overrides=overrides)

    test_config_dir = processor.run_templates_dir / test_name
    (test_config_dir / "logs").mkdir(exist_ok=True)
    (test_config_dir / "work").mkdir(exist_ok=True)

    config_dir = test_config_dir / "config"
    os.environ["ORB_CONFIG_DIR"] = str(config_dir)
    os.environ["HF_PROVIDER_CONFDIR"] = str(config_dir)
    os.environ["HF_PROVIDER_LOGDIR"] = str(test_config_dir / "logs")
    os.environ["HF_PROVIDER_WORKDIR"] = str(test_config_dir / "work")
    os.environ["DEFAULT_PROVIDER_WORKDIR"] = str(test_config_dir / "work")
    os.environ["AWS_PROVIDER_LOG_DIR"] = str(test_config_dir / "logs")
    os.environ["HF_LOGDIR"] = str(test_config_dir / "logs")

    # Bootstrap ORB so the DI container has providers, CQRS handlers,
    # configuration etc. registered before MCP tool handlers run.  Without
    # this every MCP request fails with "No strategy found for provider".
    reset_container()
    app = Application(config_path=str(config_dir / "config.json"), skip_validation=True)
    if not asyncio.run(app.initialize()):
        raise RuntimeError("Failed to initialize ORB application for MCP test")

    mcp_server = OpenResourceBrokerMCPServer(app=get_container())

    _tracked_request_ids: list[str] = []

    yield mcp_server, str(test_config_dir), _tracked_request_ids

    # Teardown: best-effort AWS resource cleanup before removing config dir
    if _tracked_request_ids:
        log.warning(
            "Fixture teardown: %d request(s) tracked — attempting MCP cleanup",
            len(_tracked_request_ids),
        )
        import asyncio

        async def _cleanup() -> None:
            for req_id in _tracked_request_ids:
                try:
                    status_resp = await _call_tool(
                        mcp_server, "get_request_status", {"request_id": req_id}
                    )
                    machine_ids = _extract_machine_ids(status_resp)
                    if machine_ids:
                        log.warning(
                            "Fixture teardown: returning %d machine(s) for request %s",
                            len(machine_ids),
                            req_id,
                        )
                        await _call_tool(
                            mcp_server, "return_machines", {"machine_ids": machine_ids}
                        )
                except Exception as exc:
                    log.warning(
                        "Fixture teardown: MCP cleanup failed for request %s: %s", req_id, exc
                    )

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(asyncio.run, _cleanup())
                    future.result(timeout=120)
            else:
                loop.run_until_complete(_cleanup())
        except Exception as exc:
            log.warning("Fixture teardown: async MCP cleanup failed: %s", exc)

        try:
            wait_for_instances_terminated(
                [
                    mid
                    for req_id in _tracked_request_ids
                    for mid in _get_machine_ids_from_ec2(req_id)
                ],
                _get_ec2_client(),
            )
        except Exception as exc:
            log.warning("Fixture teardown: wait_for_instances_terminated failed: %s", exc)

        for req_id in _tracked_request_ids:
            try:
                cleanup_launch_templates_for_request(req_id, _get_ec2_client())
            except Exception as exc:
                log.warning(
                    "Fixture teardown: cleanup_launch_templates failed for %s: %s", req_id, exc
                )

    # Teardown: clean up env vars and reset DI container so next test gets a fresh one
    for key in (
        "ORB_CONFIG_DIR",
        "HF_PROVIDER_CONFDIR",
        "HF_PROVIDER_LOGDIR",
        "HF_PROVIDER_WORKDIR",
        "DEFAULT_PROVIDER_WORKDIR",
        "AWS_PROVIDER_LOG_DIR",
        "HF_LOGDIR",
    ):
        os.environ.pop(key, None)

    try:
        from orb.infrastructure.di import reset_container

        reset_container()
    except Exception as e:
        log.warning("reset_container failed during teardown: %s", e)

    processor.cleanup_test_templates(test_name)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_machine_ids_from_ec2(request_id: str) -> list[str]:
    return _get_machine_ids_from_ec2_helper(request_id, _get_ec2_client())


async def _call_tool(mcp_server, tool_name: str, arguments: dict, msg_id: int = 1) -> dict:
    """Send a tools/call JSON-RPC message and return the parsed inner result dict.

    Asserts the envelope is well-formed (jsonrpc==2.0, no error, content[0].type==text)
    and returns the parsed JSON from content[0].text.  Handles the [dict, int] tuple
    shape that some handlers return.
    """
    message = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
    )
    raw = await mcp_server.handle_message(message)
    envelope = json.loads(raw)

    assert envelope.get("jsonrpc") == "2.0", f"Bad jsonrpc version: {envelope}"
    assert "error" not in envelope or envelope["error"] is None, (
        f"Tool {tool_name!r} returned error: {envelope.get('error')}"
    )

    content = envelope["result"]["content"]
    assert content[0]["type"] == "text", f"Unexpected content type: {content[0]}"

    inner = json.loads(content[0]["text"])
    # Some handlers return [result_dict, exit_code] — unwrap to the dict
    if isinstance(inner, list) and inner and isinstance(inner[0], dict):
        return dict(inner[0])
    return dict(inner)


from tests.shared.response_helpers import (
    extract_machine_ids as _extract_machine_ids,
    extract_request_id as _extract_request_id,
    extract_status as _extract_request_status,
)

# ---------------------------------------------------------------------------
# Core test logic (shared by parametrised and single tests)
# ---------------------------------------------------------------------------


async def _run_full_cycle_mcp(mcp_server, test_case: dict, tracked_request_ids: list[str]) -> None:
    """Full acquire→return cycle via MCP server."""
    import asyncio

    template_id = test_case.get("template_id") or scenarios.resolve_template_id(
        test_case.get("overrides", {})
    )
    capacity = test_case.get("capacity_to_request", 1)

    log.info("Requesting %d machine(s) with template %s via MCP", capacity, template_id)

    # 1. Request machines
    request_result = await _call_tool(
        mcp_server, "request_machines", {"template_id": template_id, "machine_count": capacity}
    )
    log.debug("request_machines result: %s", request_result)

    request_id = _extract_request_id(request_result)
    assert request_id, f"No request_id in response: {request_result}"
    assert REQUEST_ID_RE.match(request_id), (
        f"request_id {request_id!r} does not match expected format"
    )
    tracked_request_ids.append(request_id)
    log.info("Got request_id: %s", request_id)

    # 1a. Verify request is visible via get_request_status
    status_check = await _call_tool(mcp_server, "get_request_status", {"request_id": request_id})
    assert _extract_request_status(status_check) not in ("", "unknown"), (
        f"request_id {request_id!r} not found or has unknown status after creation: {status_check}"
    )
    log.info("get_request_status confirmed request_id: %s", request_id)

    # 1b. Verify request is visible via list_requests
    list_result = await _call_tool(mcp_server, "list_requests", {})
    listed_ids = [
        req.get("request_id") or req.get("requestId")
        for req in list_result.get("requests", [])
        if isinstance(req, dict)
    ]
    assert request_id in listed_ids, (
        f"request_id {request_id!r} not found in list_requests response: {list_result}"
    )
    log.info("list_requests confirmed request_id: %s", request_id)

    # 2. Poll until complete
    deadline = time.time() + MCP_TIMEOUTS["request_completion"]
    terminal = {"complete", "complete_with_error", "partial", "failed", "cancelled", "timeout"}
    status_result = None

    while True:
        status_result = await _call_tool(
            mcp_server, "get_request_status", {"request_id": request_id}
        )
        log.debug("status: %s", status_result)
        status = _extract_request_status(status_result)
        if status in terminal:
            if status != "complete":
                # Capacity-aware: accept complete_with_error / partial when
                # the provider returned some-but-not-all capacity due to an
                # AWS shortfall.
                from tests.providers.aws.live._capacity_helpers import assert_terminal_ok

                assert_terminal_ok(status_result, capacity)
            break
        if time.time() > deadline:
            pytest.fail("Timed out waiting for request to complete")
        await asyncio.sleep(MCP_TIMEOUTS["poll_interval"])

    # 3. Assert ORB status + AWS-side instance state
    returned_id = status_result.get("requests", [{}])[0].get("request_id") or status_result.get(
        "requests", [{}]
    )[0].get("requestId")
    assert returned_id == request_id, (
        f"Status response echoed {returned_id!r}, expected {request_id!r}"
    )

    machine_ids = _extract_machine_ids(status_result)

    # Capacity-unit fulfilment check from provider response fields.
    # target_units / fulfilled_units reflect capacity units for weighted fleets
    # and instance count for unweighted templates (1 unit == 1 instance).
    _req0 = status_result.get("requests", [{}])[0] if isinstance(status_result, dict) else {}
    target_units = _req0["target_units"] if _req0.get("target_units") is not None else capacity
    fulfilled_units = (
        _req0["fulfilled_units"] if _req0.get("fulfilled_units") is not None else len(machine_ids)
    )
    _final_status = _req0.get("status")
    if _final_status == "complete":
        assert fulfilled_units >= target_units, (
            f"Fleet not fully fulfilled despite status=complete: "
            f"fulfilled={fulfilled_units}, target={target_units}"
        )
    else:
        assert fulfilled_units >= 1, (
            f"status={_final_status!r} with zero fulfilled units: target={target_units}"
        )

    # Template-aware instance count sanity check.
    if scenarios.template_uses_weighted_capacity(test_case):
        assert len(machine_ids) >= 1, (
            f"Expected at least 1 machine (weighted template), got: {machine_ids}"
        )
    elif _final_status == "complete":
        assert len(machine_ids) == capacity, (
            f"Expected {capacity} machines (unweighted template, status=complete), "
            f"got {len(machine_ids)}: {machine_ids}"
        )
    else:
        assert 1 <= len(machine_ids) <= capacity, (
            f"Expected 1..{capacity} machines (unweighted template, status={_final_status!r}), "
            f"got {len(machine_ids)}: {machine_ids}"
        )

    for machine_id in machine_ids:
        state = get_instance_state(machine_id)
        assert state["exists"], f"Instance {machine_id} not found in AWS"
        assert state["state"] in ("running", "pending"), (
            f"Instance {machine_id} in unexpected state: {state['state']}"
        )
    log.info("All %d instance(s) provisioned: %s", len(machine_ids), machine_ids)

    # 4. Return machines
    return_result = await _call_tool(mcp_server, "return_machines", {"machine_ids": machine_ids})
    log.debug("return_machines result: %s", return_result)

    return_request_id = _extract_request_id(return_result)

    # 5. Poll return completion via get_request_status(return_request_id).
    # The HostFactory getReturnRequests wire format flattens per-machine items
    # without a per-request status, so the right way to ask "is this return
    # complete?" under any scheduler is get_request_status(return_id).
    if return_request_id:
        deadline = time.time() + MCP_TIMEOUTS["return_completion"]
        terminal = {"complete", "complete_with_error", "failed", "cancelled", "timeout"}
        while True:
            status_resp = await _call_tool(
                mcp_server, "get_request_status", {"request_id": return_request_id}
            )
            status = _extract_request_status(status_resp)
            if status in terminal:
                if status != "complete":
                    pytest.fail(f"Return request ended with non-success status: {status}")
                break
            if time.time() > deadline:
                pytest.fail("Timed out waiting for return request to complete")
            await asyncio.sleep(MCP_TIMEOUTS["poll_interval"])

    # 6. Assert AWS-side termination
    all_terminated = _check_all_ec2_hosts_are_being_terminated(machine_ids)
    assert all_terminated, f"Some instances not terminated: {machine_ids}"
    log.info("All %d instance(s) terminated", capacity)


# ---------------------------------------------------------------------------
# Parametrised tests
# ---------------------------------------------------------------------------


def _build_default_test_cases() -> list[dict]:
    if not MCP_RUN_DEFAULT_COMBINATIONS:
        return []
    return scenarios.get_test_cases()


_DEFAULT_CASES = _build_default_test_cases()


@pytest.mark.parametrize(
    "test_case",
    _DEFAULT_CASES,
    ids=[tc["test_name"] for tc in _DEFAULT_CASES],
    indirect=False,
)
@pytest.mark.asyncio
async def test_mcp_full_cycle_default(setup_mcp_test, test_case):
    """Full acquire→return cycle for default scenario combinations via MCP."""
    mcp_server, _config_path, tracked_request_ids = setup_mcp_test
    await _run_full_cycle_mcp(mcp_server, test_case, tracked_request_ids)


# ---------------------------------------------------------------------------
# Smoke test — single scenario, always runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_smoke(setup_mcp_test):
    """Smoke: MCP server initialises, lists tools, requests 1 machine, returns it."""
    mcp_server, _config_path, tracked_request_ids = setup_mcp_test

    # 1. Initialize MCP session
    init_msg = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"clientInfo": {"name": "pytest-mcp-onaws", "version": "1.0"}},
        }
    )
    init_raw = await mcp_server.handle_message(init_msg)
    init_resp = json.loads(init_raw)
    assert init_resp.get("jsonrpc") == "2.0"
    assert "error" not in init_resp or init_resp["error"] is None
    assert "protocolVersion" in init_resp["result"]

    # 2. tools/list — assert required tools are present
    list_msg = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    list_raw = await mcp_server.handle_message(list_msg)
    list_resp = json.loads(list_raw)
    assert list_resp.get("jsonrpc") == "2.0"
    assert "error" not in list_resp or list_resp["error"] is None

    tool_names = {t["name"] for t in list_resp["result"]["tools"]}
    for required in (
        "request_machines",
        "get_request_status",
        "list_return_requests",
        "return_machines",
    ):
        assert required in tool_names, f"Expected tool {required!r} not in tools/list: {tool_names}"

    # 3. Full lifecycle with the simplest scenario
    test_case = scenarios.get_test_case_by_name("default.RunInstances.ondemand")
    if not test_case:
        all_cases = scenarios.get_test_cases()
        assert all_cases, "No test cases available"
        test_case = all_cases[0]

    # Smoke test always requests exactly 1 machine
    test_case = dict(test_case)
    test_case["capacity_to_request"] = 1

    await _run_full_cycle_mcp(mcp_server, test_case, tracked_request_ids)


@pytest.mark.asyncio
async def test_mcp_unknown_template_returns_error(setup_mcp_test):
    """MCP request_machines with a non-existent template_id returns an error, not a crash."""
    mcp_server, _config_path, _tracked = setup_mcp_test

    message = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "request_machines",
                "arguments": {"template_id": "NonExistent-Template-XYZ", "machine_count": 1},
            },
        }
    )
    raw = await mcp_server.handle_message(message)
    envelope = json.loads(raw)

    assert envelope.get("jsonrpc") == "2.0", f"Bad jsonrpc version: {envelope}"

    # Either the envelope carries an error, or the tool result payload indicates one
    if envelope.get("error"):
        return

    content = envelope.get("result", {}).get("content", [])
    assert content, f"Expected content in response: {envelope}"
    inner = json.loads(content[0]["text"])
    if isinstance(inner, list) and inner and isinstance(inner[0], dict):
        inner = inner[0]

    has_error = isinstance(inner, dict) and (
        inner.get("error")
        or inner.get("status") == "error"
        or "not found" in str(inner).lower()
        or "NonExistent" in str(inner)
    )
    assert has_error, f"Expected error payload for unknown template, got: {inner}"
