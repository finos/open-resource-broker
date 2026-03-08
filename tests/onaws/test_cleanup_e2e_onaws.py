"""End-to-end cleanup verification tests on real AWS.

Verifies that after returning ALL machines, the backing resource (ASG/fleet)
is deleted and the launch template is deleted. One test per resource type.
Uses session UUID tag for cleanup safety.
"""

import asyncio
import logging
import os
import shutil
import time

import boto3.session
import pytest

from tests.onaws import scenarios
from tests.onaws.cleanup_helpers import (
    cleanup_launch_templates_for_request,
    get_machine_ids_from_ec2 as _get_machine_ids_from_ec2_helper,
    wait_for_instances_terminated,
)
from tests.onaws.scenarios import SPOT_VM_TYPES
from tests.onaws.template_processor import TemplateProcessor

try:
    from tests.onaws.scenarios_sdk import SDK_TIMEOUTS  # type: ignore[import]
except ImportError:
    SDK_TIMEOUTS = {"request_completion": 600, "return_completion": 300, "poll_interval": 5}

try:
    from tests.onaws.test_onaws import (
        _check_all_ec2_hosts_are_being_terminated,
        _get_resource_id_from_instance,
        get_instance_state,
    )
except Exception as exc:  # pragma: no cover
    pytest.skip(
        f"Skipping cleanup e2e onaws tests because base onaws helpers failed to import: {exc}",
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
asg_client = _boto_session.client("autoscaling", region_name=_ec2_region)

log = logging.getLogger("cleanup_e2e_test")
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
# Test cases
# ---------------------------------------------------------------------------

CLEANUP_E2E_CASES = [
    {
        "test_name": "cleanup_e2e.ASG.ondemand",
        "template_id": None,  # resolved via resolve_template_id
        "capacity_to_request": 2,
        "overrides": {
            "providerApi": "ASG",
            "priceType": "ondemand",
            "scheduler": "hostfactory",
        },
    },
    {
        "test_name": "cleanup_e2e.EC2Fleet.maintain.ondemand",
        "template_id": None,
        "capacity_to_request": 2,
        "overrides": {
            "providerApi": "EC2Fleet",
            "fleetType": "maintain",
            "priceType": "ondemand",
            "scheduler": "hostfactory",
        },
    },
    {
        "test_name": "cleanup_e2e.EC2Fleet.request.ondemand",
        "template_id": None,
        "capacity_to_request": 2,
        "overrides": {
            "providerApi": "EC2Fleet",
            "fleetType": "request",
            "priceType": "ondemand",
            "scheduler": "hostfactory",
        },
    },
    {
        "test_name": "cleanup_e2e.EC2Fleet.instant.ondemand",
        "template_id": None,
        "capacity_to_request": 2,
        "overrides": {
            "providerApi": "EC2Fleet",
            "fleetType": "instant",
            "priceType": "ondemand",
            "scheduler": "hostfactory",
        },
    },
    {
        "test_name": "cleanup_e2e.SpotFleet.maintain.spot",
        "template_id": None,
        "capacity_to_request": 2,
        "overrides": {
            "providerApi": "SpotFleet",
            "fleetType": "maintain",
            "priceType": "spot",
            "scheduler": "hostfactory",
            "vmTypes": SPOT_VM_TYPES,
        },
    },
    {
        "test_name": "cleanup_e2e.SpotFleet.request.spot",
        "template_id": None,
        "capacity_to_request": 2,
        "overrides": {
            "providerApi": "SpotFleet",
            "fleetType": "request",
            "priceType": "spot",
            "scheduler": "hostfactory",
            "vmTypes": SPOT_VM_TYPES,
        },
    },
]

# Resolve template IDs at module load time
for _case in CLEANUP_E2E_CASES:
    if not _case["template_id"]:
        _case["template_id"] = scenarios.resolve_template_id(_case["overrides"])


# ---------------------------------------------------------------------------
# Backing resource verification helpers
# ---------------------------------------------------------------------------


def _assert_asg_deleted(asg_name: str, timeout: int = 120) -> None:
    """Assert ASG is deleted or has zero desired capacity with no instances."""
    deadline = time.time() + timeout
    last_state = None
    while time.time() < deadline:
        try:
            resp = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
            groups = resp.get("AutoScalingGroups", [])
            if not groups:
                log.info("ASG %s: not found (deleted)", asg_name)
                return
            g = groups[0]
            desired = g.get("DesiredCapacity", -1)
            instances = g.get("Instances", [])
            last_state = f"DesiredCapacity={desired}, Instances={len(instances)}"
            if desired == 0 and not instances:
                log.info("ASG %s: DesiredCapacity=0 with no instances", asg_name)
                return
            log.debug("ASG %s: %s — waiting", asg_name, last_state)
        except Exception as exc:
            log.warning("_assert_asg_deleted: describe failed for %s: %s", asg_name, exc)
        time.sleep(10)
    pytest.fail(
        f"ASG {asg_name} not deleted or zeroed within {timeout}s. Last state: {last_state}"
    )


def _assert_fleet_deleted(fleet_id: str, timeout: int = 120) -> None:
    """Assert EC2 Fleet is deleted or has zero total target capacity."""
    deadline = time.time() + timeout
    last_state = None
    deleted_states = {"deleted", "deleted-running", "deleted-terminating"}
    while time.time() < deadline:
        try:
            resp = ec2_client.describe_fleets(FleetIds=[fleet_id])
            fleets = resp.get("Fleets", [])
            if not fleets:
                log.info("EC2 Fleet %s: not found (deleted)", fleet_id)
                return
            f = fleets[0]
            state = f.get("FleetState", "")
            capacity = (
                f.get("TargetCapacitySpecification", {}).get("TotalTargetCapacity", -1)
            )
            last_state = f"state={state}, TotalTargetCapacity={capacity}"
            if state in deleted_states or capacity == 0:
                log.info("EC2 Fleet %s: %s", fleet_id, last_state)
                return
            log.debug("EC2 Fleet %s: %s — waiting", fleet_id, last_state)
        except Exception as exc:
            if "InvalidFleetId" in str(exc) or "NotFound" in str(exc):
                log.info("EC2 Fleet %s: not found (deleted)", fleet_id)
                return
            log.warning("_assert_fleet_deleted: describe failed for %s: %s", fleet_id, exc)
        time.sleep(10)
    pytest.fail(
        f"EC2 Fleet {fleet_id} not deleted or zeroed within {timeout}s. Last state: {last_state}"
    )


def _assert_spot_fleet_deleted(sfr_id: str, timeout: int = 120) -> None:
    """Assert Spot Fleet request is cancelled/deleted or has zero target capacity."""
    deadline = time.time() + timeout
    last_state = None
    terminal_states = {"cancelled", "cancelled_running", "cancelled_terminating", "failed"}
    while time.time() < deadline:
        try:
            resp = ec2_client.describe_spot_fleet_requests(SpotFleetRequestIds=[sfr_id])
            configs = resp.get("SpotFleetRequestConfigs", [])
            if not configs:
                log.info("Spot Fleet %s: not found (deleted)", sfr_id)
                return
            c = configs[0]
            state = c.get("SpotFleetRequestState", "")
            capacity = c.get("SpotFleetRequestConfig", {}).get("TargetCapacity", -1)
            last_state = f"state={state}, TargetCapacity={capacity}"
            if state in terminal_states or capacity == 0:
                log.info("Spot Fleet %s: %s", sfr_id, last_state)
                return
            log.debug("Spot Fleet %s: %s — waiting", sfr_id, last_state)
        except Exception as exc:
            if "NotFound" in str(exc) or "InvalidSpotFleetRequestId" in str(exc):
                log.info("Spot Fleet %s: not found (deleted)", sfr_id)
                return
            log.warning("_assert_spot_fleet_deleted: describe failed for %s: %s", sfr_id, exc)
        time.sleep(10)
    pytest.fail(
        f"Spot Fleet {sfr_id} not deleted or zeroed within {timeout}s. Last state: {last_state}"
    )


def _assert_launch_templates_deleted(request_id: str) -> None:
    """Assert no launch templates tagged orb:request-id=<request_id> exist."""
    try:
        resp = ec2_client.describe_launch_templates(
            Filters=[{"Name": "tag:orb:request-id", "Values": [request_id]}]
        )
        templates = resp.get("LaunchTemplates", [])
        assert not templates, (
            f"Expected 0 launch templates for request {request_id}, "
            f"found {len(templates)}: {[lt['LaunchTemplateId'] for lt in templates]}"
        )
        log.info("Launch templates for request %s: confirmed deleted", request_id)
    except AssertionError:
        raise
    except Exception as exc:
        pytest.fail(f"Failed to describe launch templates for request {request_id}: {exc}")


# ---------------------------------------------------------------------------
# Response extraction helpers
# ---------------------------------------------------------------------------

from tests.shared.response_helpers import extract_machine_ids as _extract_machine_ids
from tests.shared.response_helpers import extract_request_id as _extract_request_id
from tests.shared.response_helpers import extract_status as _extract_request_status


def _extract_resource_ids(result) -> list[str]:
    """Extract resource_ids from status response (ASG name / fleet ID)."""
    if isinstance(result, dict):
        requests = result.get("requests", [])
        if requests and isinstance(requests[0], dict):
            return requests[0].get("resource_ids", []) or []
    return []


def _get_machine_ids_from_ec2(request_id: str) -> list[str]:
    return _get_machine_ids_from_ec2_helper(request_id, ec2_client)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def setup_cleanup_e2e(request, test_session_id):
    """Generate per-test config dir, set env vars, yield state, teardown."""
    processor = TemplateProcessor()
    test_name = request.node.name

    overrides: dict = {}
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

    # Teardown: best-effort AWS resource cleanup
    if _tracked_request_ids:
        log.warning(
            "Fixture teardown: %d request(s) tracked — attempting AWS cleanup",
            len(_tracked_request_ids),
        )
        try:
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
            all_machine_ids = [
                mid
                for req_id in _tracked_request_ids
                for mid in _get_machine_ids_from_ec2(req_id)
            ]
            wait_for_instances_terminated(all_machine_ids, ec2_client)
        except Exception as exc:
            log.warning("Fixture teardown: wait_for_instances_terminated failed: %s", exc)

        for req_id in _tracked_request_ids:
            try:
                cleanup_launch_templates_for_request(req_id, ec2_client)
            except Exception as exc:
                log.warning(
                    "Fixture teardown: cleanup_launch_templates failed for %s: %s", req_id, exc
                )

    try:
        from orb.infrastructure.di import reset_container

        reset_container()
    except Exception:
        pass

    processor.cleanup_test_templates(test_name)


# ---------------------------------------------------------------------------
# Core cleanup verification logic
# ---------------------------------------------------------------------------


async def _run_cleanup_verification(
    sdk,
    test_case: dict,
    tracked_request_ids: list[str],
) -> None:
    """Full acquire → return ALL → verify cleanup cycle."""
    template_id = test_case["template_id"]
    capacity = test_case.get("capacity_to_request", 2)
    overrides = test_case.get("overrides", {})
    provider_api = overrides.get("providerApi", "")
    fleet_type = overrides.get("fleetType", "")

    log.info(
        "Requesting %d machines with template %s (providerApi=%s fleetType=%s)",
        capacity,
        template_id,
        provider_api,
        fleet_type,
    )

    # 1. Request machines
    request_result = await sdk.request_machines(template_id=template_id, count=capacity)  # type: ignore[attr-defined]
    log.debug("request_machines result: %s", request_result)

    request_id = _extract_request_id(request_result)
    assert request_id, f"No request_id in response: {request_result}"
    assert REQUEST_ID_RE.match(request_id), f"request_id {request_id!r} does not match expected format"
    tracked_request_ids.append(request_id)
    log.info("Got request_id: %s", request_id)

    # 2. Poll provisioning until complete
    deadline = time.time() + SDK_TIMEOUTS["request_completion"]
    terminal = {"complete", "complete_with_error", "failed", "cancelled", "timeout"}
    status_response = None

    while True:
        status_response = await sdk.get_request_status(request_id=request_id)  # type: ignore[attr-defined]
        status = _extract_request_status(status_response)
        log.debug("provisioning status: %s", status)
        if status in terminal:
            if status != "complete":
                pytest.fail(f"Request ended with non-success status: {status}")
            break
        if time.time() > deadline:
            pytest.fail("Timed out waiting for request to complete")
        await asyncio.sleep(SDK_TIMEOUTS["poll_interval"])

    # 3. Assert instances provisioned
    machine_ids = _extract_machine_ids(status_response)
    assert len(machine_ids) == capacity, (
        f"Expected {capacity} machines, got {len(machine_ids)}: {machine_ids}"
    )

    returned_id = (
        status_response.get("requests", [{}])[0].get("request_id")
        or status_response.get("requests", [{}])[0].get("requestId")
    )
    assert returned_id == request_id, f"Status response echoed {returned_id!r}, expected {request_id!r}"

    for machine_id in machine_ids:
        state = get_instance_state(machine_id)
        assert state["exists"], f"Instance {machine_id} not found in AWS"
        assert state["state"] in ("running", "pending"), (
            f"Instance {machine_id} in unexpected state: {state['state']}"
        )
    log.info("All %d instances provisioned: %s", capacity, machine_ids)

    # 4. Discover backing resource ID before returning
    resource_ids = _extract_resource_ids(status_response)
    resource_id: str | None = resource_ids[0] if resource_ids else None
    if not resource_id and machine_ids:
        resource_id = _get_resource_id_from_instance(machine_ids[0], provider_api)
        log.debug("Resource ID discovered via tag fallback: %s", resource_id)
    else:
        log.debug("Resource ID from status response: %s", resource_id)

    # 5. Return ALL machines
    return_result = await sdk.create_return_request(machine_ids=machine_ids)  # type: ignore[attr-defined]
    log.debug("create_return_request result: %s", return_result)

    return_request_id = _extract_request_id(return_result)

    # 6. Poll return completion
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

    # 7. Wait for instance termination
    wait_for_instances_terminated(machine_ids, ec2_client, timeout=300)

    # 8. Assert all instances terminated
    all_terminated = _check_all_ec2_hosts_are_being_terminated(machine_ids)
    assert all_terminated, f"Some instances not in shutting-down/terminated state: {machine_ids}"
    log.info("All %d instances terminated", capacity)

    # Remove from tracked list — cleanup succeeded
    tracked_request_ids.remove(request_id)

    # 9. Assert backing resource deleted / zeroed
    if resource_id:
        if provider_api == "ASG":
            _assert_asg_deleted(resource_id)
        elif provider_api == "EC2Fleet":
            _assert_fleet_deleted(resource_id)
        elif provider_api == "SpotFleet":
            _assert_spot_fleet_deleted(resource_id)
        else:
            log.warning("No backing resource check for providerApi=%s", provider_api)
    else:
        log.warning(
            "Could not determine resource_id for %s — skipping backing resource check",
            provider_api,
        )

    # 10. Assert launch templates deleted
    _assert_launch_templates_deleted(request_id)

    log.info(
        "Cleanup verification passed for %s (providerApi=%s fleetType=%s)",
        request_id,
        provider_api,
        fleet_type,
    )


# ---------------------------------------------------------------------------
# Parametrised tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "test_case",
    CLEANUP_E2E_CASES,
    ids=[tc["test_name"] for tc in CLEANUP_E2E_CASES],
)
@pytest.mark.slow
@pytest.mark.asyncio
async def test_cleanup_e2e(setup_cleanup_e2e, test_case):
    """After returning all machines, verify backing resource and launch templates are deleted."""
    from orb.sdk import OpenResourceBroker

    config_path, tracked_request_ids = setup_cleanup_e2e
    async with OpenResourceBroker(config_path=config_path) as sdk:
        await _run_cleanup_verification(sdk, test_case, tracked_request_ids)
