"""SDK-based AWS integration tests for Open Resource Broker.

Exercises the full acquire→return control loop via the Python SDK (src/sdk/),
calling the CQRS bus directly in-process — no shell scripts, no HTTP server.
"""

import logging
import os
import re
import shutil
import time

import boto3.session
import pytest

from tests.onaws import scenarios
from tests.onaws.cleanup_helpers import (
    cleanup_launch_templates_for_request,
    wait_for_instances_terminated,
)
from tests.onaws.scenarios import CUSTOM_TEST_CASES
from tests.onaws.template_processor import TemplateProcessor

try:
    from tests.onaws.scenarios_sdk import (  # type: ignore[import]
        SDK_RUN_CUSTOM_CASES,
        SDK_RUN_DEFAULT_COMBINATIONS,
        SDK_TIMEOUTS,
    )
except ImportError:
    SDK_RUN_DEFAULT_COMBINATIONS = True
    SDK_RUN_CUSTOM_CASES = False
    SDK_TIMEOUTS = {"request_completion": 600, "return_completion": 300, "poll_interval": 5}

# Import AWS validation helpers from test_onaws (guarded for env/creds issues)
try:
    from tests.onaws.test_onaws import (
        _check_all_ec2_hosts_are_being_terminated,
        get_instance_state,
    )
except Exception as exc:  # pragma: no cover
    pytest.skip(
        f"Skipping SDK onaws tests because base onaws helpers failed to import: {exc}",
        allow_module_level=True,
    )

pytestmark = [
    pytest.mark.manual_aws,
    pytest.mark.aws,
    pytest.mark.sdk,
]

os.environ["USE_LOCAL_DEV"] = "1"
os.environ.setdefault("HF_LOGDIR", "./logs")
os.environ.setdefault("AWS_PROVIDER_LOG_DIR", "./logs")
os.environ["LOG_DESTINATION"] = "file"
os.environ.setdefault("AWS_REGION", "eu-west-1")

_boto_session = boto3.session.Session()
_ec2_region = (
    os.environ.get("AWS_REGION")
    or os.environ.get("AWS_DEFAULT_REGION")
    or _boto_session.region_name
    or "eu-west-1"
)
ec2_client = _boto_session.client("ec2", region_name=_ec2_region)

log = logging.getLogger("sdk_test")
log.setLevel(logging.DEBUG)
_formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s"
)
_console = logging.StreamHandler()
_console.setLevel(logging.DEBUG)
_console.setFormatter(_formatter)
log.addHandler(_console)

REQUEST_ID_RE = re.compile(r"^req-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def setup_sdk_test(request, test_session_id):
    """Generate per-test config dir, set env vars, yield config_path, teardown."""
    processor = TemplateProcessor()
    test_name = request.node.name

    overrides = {}
    if hasattr(request, "param") and isinstance(request.param, dict):
        overrides = request.param.get("overrides", {})
    overrides["instanceTags"] = {**overrides.get("instanceTags", {}), "test-session": test_session_id}

    test_config_dir = processor.run_templates_dir / test_name
    if test_config_dir.exists():
        shutil.rmtree(test_config_dir)

    processor.generate_test_templates(test_name, overrides=overrides)

    test_config_dir = processor.run_templates_dir / test_name
    (test_config_dir / "logs").mkdir(exist_ok=True)
    (test_config_dir / "work").mkdir(exist_ok=True)

    os.environ["ORB_CONFIG_DIR"] = str(test_config_dir)
    os.environ["HF_PROVIDER_CONFDIR"] = str(test_config_dir)
    os.environ["HF_PROVIDER_LOGDIR"] = str(test_config_dir / "logs")
    os.environ["HF_PROVIDER_WORKDIR"] = str(test_config_dir / "work")
    os.environ["DEFAULT_PROVIDER_WORKDIR"] = str(test_config_dir / "work")
    os.environ["AWS_PROVIDER_LOG_DIR"] = str(test_config_dir / "logs")
    os.environ["HF_LOGDIR"] = str(test_config_dir / "logs")

    config_path = str(test_config_dir / "config.json")

    _tracked_request_ids: list[str] = []

    yield config_path, _tracked_request_ids

    # Teardown: best-effort AWS resource cleanup before removing config dir
    if _tracked_request_ids:
        log.warning(
            "Fixture teardown: %d request(s) tracked — attempting AWS cleanup",
            len(_tracked_request_ids),
        )
        try:
            import asyncio

            from orb.sdk import OpenResourceBroker

            async def _cleanup() -> None:
                async with OpenResourceBroker(config_path=config_path) as sdk:
                    for req_id in _tracked_request_ids:
                        try:
                            status = await sdk.get_request_status(request_id=req_id)  # type: ignore[attr-defined]
                            machine_ids = _extract_machine_ids(status)
                            if machine_ids:
                                log.warning(
                                    "Fixture teardown: returning %d machine(s) for request %s",
                                    len(machine_ids),
                                    req_id,
                                )
                                await sdk.create_return_request(machine_ids=machine_ids)  # type: ignore[attr-defined]
                        except Exception as exc:
                            log.warning(
                                "Fixture teardown: cleanup failed for request %s: %s", req_id, exc
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
                log.warning("Fixture teardown: async cleanup failed: %s", exc)

        except Exception as exc:
            log.warning("Fixture teardown: could not import SDK for cleanup: %s", exc)

        try:
            wait_for_instances_terminated(
                [mid for req_id in _tracked_request_ids
                 for mid in _get_machine_ids_from_ec2(req_id)],
                ec2_client,
            )
        except Exception as exc:
            log.warning("Fixture teardown: wait_for_instances_terminated failed: %s", exc)

        for req_id in _tracked_request_ids:
            try:
                cleanup_launch_templates_for_request(req_id, ec2_client)
            except Exception as exc:
                log.warning(
                    "Fixture teardown: cleanup_launch_templates failed for %s: %s", req_id, exc
                )

    # Teardown: reset DI container so next test gets a fresh one
    try:
        from orb.infrastructure.di import reset_container

        reset_container()
    except Exception:
        pass

    processor.cleanup_test_templates(test_name)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_machine_ids_from_ec2(request_id: str) -> list[str]:
    """Look up instance IDs tagged with orb:request-id=<request_id> in EC2.

    Used in teardown when the SDK cleanup path has already run and we just need
    instance IDs to pass to wait_for_instances_terminated. Never raises.
    """
    try:
        response = ec2_client.describe_instances(
            Filters=[
                {"Name": "tag:orb:request-id", "Values": [request_id]},
                {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
            ]
        )
        ids = []
        for reservation in response.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                ids.append(inst["InstanceId"])
        return ids
    except Exception as exc:
        log.warning("_get_machine_ids_from_ec2 failed for %s: %s", request_id, exc)
        return []


def _extract_request_status(result) -> str:
    """Extract status string from whatever get_request_status returns."""
    if isinstance(result, dict):
        # HF envelope: {"requests": [{"status": ...}]}
        requests = result.get("requests", [])
        if requests and isinstance(requests[0], dict):
            return requests[0].get("status", "unknown")
        return result.get("status", "unknown")
    # DTO object
    return getattr(result, "status", "unknown")


def _extract_machine_ids(result) -> list[str]:
    """Extract machine IDs from a request status response."""
    if isinstance(result, dict):
        requests = result.get("requests", [])
        if requests and isinstance(requests[0], dict):
            machines = requests[0].get("machines", [])
            return [
                mid for m in machines for mid in [m.get("machineId") or m.get("machine_id")] if mid
            ]
    # DTO object
    machines = getattr(result, "machines", [])
    return [str(mid) for m in machines for mid in [getattr(m, "machine_id", None)] if mid]


# ---------------------------------------------------------------------------
# Core test logic (shared by parametrised and single tests)
# ---------------------------------------------------------------------------


async def _run_full_cycle(sdk, test_case: dict, tracked_request_ids: list[str]) -> None:
    """Full acquire→return cycle via SDK."""
    template_id = test_case.get("template_id") or scenarios.resolve_template_id(
        test_case.get("overrides", {})
    )
    capacity = test_case.get("capacity_to_request", 1)

    log.info("Requesting %d machines with template %s", capacity, template_id)

    # 1. Request machines
    request_result = await sdk.request_machines(template_id=template_id, count=capacity)  # type: ignore[attr-defined]
    log.debug("request_machines result: %s", request_result)

    request_id = (
        request_result.get("requestId") or request_result.get("request_id")
        if isinstance(request_result, dict)
        else getattr(request_result, "request_id", None)
    )
    assert request_id, f"No request_id in response: {request_result}"
    assert REQUEST_ID_RE.match(request_id), f"request_id {request_id!r} does not match expected format"
    tracked_request_ids.append(request_id)
    log.info("Got request_id: %s", request_id)

    # 2. Poll until complete
    import asyncio

    deadline = time.time() + SDK_TIMEOUTS["request_completion"]
    terminal = {"complete", "complete_with_error", "failed", "cancelled", "timeout"}
    status_response = None

    while True:
        status_response = await sdk.get_request_status(request_id=request_id)  # type: ignore[attr-defined]
        log.debug("status: %s", status_response)
        status = _extract_request_status(status_response)
        if status in terminal:
            if status != "complete":
                pytest.fail(f"Request ended with non-success status: {status}")
            break
        if time.time() > deadline:
            pytest.fail("Timed out waiting for request to complete")
        await asyncio.sleep(SDK_TIMEOUTS["poll_interval"])

    # 3. Assert ORB status + AWS-side instance state
    machine_ids = _extract_machine_ids(status_response)
    assert len(machine_ids) == capacity, (
        f"Expected {capacity} machines, got {len(machine_ids)}: {machine_ids}"
    )

    for machine_id in machine_ids:
        state = get_instance_state(machine_id)
        assert state["exists"], f"Instance {machine_id} not found in AWS"
        assert state["state"] in ("running", "pending"), (
            f"Instance {machine_id} in unexpected state: {state['state']}"
        )
    log.info("All %d instances provisioned: %s", capacity, machine_ids)

    # 4. Return machines
    return_result = await sdk.create_return_request(machine_ids=machine_ids)  # type: ignore[attr-defined]
    log.debug("create_return_request result: %s", return_result)

    return_request_id = (
        return_result.get("requestId") or return_result.get("request_id")
        if isinstance(return_result, dict)
        else getattr(return_result, "request_id", None)
    )

    # 5. Poll return completion
    if return_request_id:
        deadline = time.time() + SDK_TIMEOUTS["return_completion"]
        while True:
            ret_status = await sdk.list_return_requests()  # type: ignore[attr-defined]
            requests = (
                ret_status.get("requests", []) if isinstance(ret_status, dict) else ret_status or []
            )
            done = False
            for req in requests:
                rid = (
                    req.get("requestId") or req.get("request_id")
                    if isinstance(req, dict)
                    else getattr(req, "request_id", None)
                )
                s = req.get("status") if isinstance(req, dict) else getattr(req, "status", None)
                if rid == return_request_id and s == "complete":
                    done = True
                    break
            if done:
                break
            if time.time() > deadline:
                pytest.fail("Timed out waiting for return request to complete")
            await asyncio.sleep(SDK_TIMEOUTS["poll_interval"])

    # 6. Assert AWS-side termination
    all_terminated = _check_all_ec2_hosts_are_being_terminated(machine_ids)
    assert all_terminated, f"Some instances not terminated: {machine_ids}"
    log.info("All %d instances terminated", capacity)


# ---------------------------------------------------------------------------
# Parametrised tests
# ---------------------------------------------------------------------------


def _build_default_test_cases():
    if not SDK_RUN_DEFAULT_COMBINATIONS:
        return []
    return scenarios.get_test_cases()


def _build_custom_test_cases():
    if not SDK_RUN_CUSTOM_CASES:
        return []
    return CUSTOM_TEST_CASES


_DEFAULT_CASES = _build_default_test_cases()
_CUSTOM_CASES = _build_custom_test_cases()


@pytest.mark.parametrize(
    "test_case",
    _DEFAULT_CASES,
    ids=[tc["test_name"] for tc in _DEFAULT_CASES],
    indirect=False,
)
@pytest.mark.asyncio
async def test_sdk_full_cycle_default(setup_sdk_test, test_case):
    """Full acquire→return cycle for default scenario combinations."""
    from orb.sdk import OpenResourceBroker

    config_path, tracked_request_ids = setup_sdk_test
    async with OpenResourceBroker(config_path=config_path) as sdk:
        await _run_full_cycle(sdk, test_case, tracked_request_ids)


@pytest.mark.parametrize(
    "test_case",
    _CUSTOM_CASES,
    ids=[tc["test_name"] for tc in _CUSTOM_CASES],
    indirect=False,
)
@pytest.mark.asyncio
async def test_sdk_full_cycle_custom(setup_sdk_test, test_case):
    """Full acquire→return cycle for custom/edge-case scenarios."""
    from orb.sdk import OpenResourceBroker

    config_path, tracked_request_ids = setup_sdk_test
    async with OpenResourceBroker(config_path=config_path) as sdk:
        await _run_full_cycle(sdk, test_case, tracked_request_ids)


# ---------------------------------------------------------------------------
# Smoke test — single scenario, always runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sdk_smoke(setup_sdk_test):
    """Smoke: SDK initialises, lists templates, requests 1 machine, returns it."""
    from orb.sdk import OpenResourceBroker

    config_path, tracked_request_ids = setup_sdk_test

    # Pick the simplest scenario: RunInstances ondemand default scheduler
    test_case = scenarios.get_test_case_by_name("default.RunInstances.ondemand")
    if not test_case:
        # Fallback: first available case
        all_cases = scenarios.get_test_cases()
        assert all_cases, "No test cases available"
        test_case = all_cases[0]

    async with OpenResourceBroker(config_path=config_path) as sdk:
        # Verify SDK initialised and has methods
        methods = sdk.list_available_methods()
        assert "create_request" in methods or "request_machines" in methods, (
            f"Expected CQRS methods not discovered. Available: {methods}"
        )

        await _run_full_cycle(sdk, test_case, tracked_request_ids)
