"""REST API-based AWS integration tests for Open Host Factory Plugin."""

import json
import logging
import os
import subprocess
import time
from typing import List, Optional

import boto3
import pytest
import requests

from tests.onaws import scenarios_rest_api
from tests.onaws.template_processor import TemplateProcessor

# Import AWS validation functions from test_onaws (guarded to allow skip on import failures)
try:
    from tests.onaws.test_onaws import (
        MAX_TIME_WAIT_FOR_CAPACITY_PROVISIONING_SEC,
        _check_all_ec2_hosts_are_being_terminated,
        _cleanup_asg_resources,
        _get_capacity,
        _get_resource_id_from_instance,
        _verify_all_resources_cleaned,
        _wait_for_capacity_change,
        _wait_for_fleet_stable,
        get_instance_state,
        validate_all_instances_price_type,
        validate_random_instance_attributes,
        verify_abis_enabled_for_instance,
    )
except Exception as exc:  # pragma: no cover - defensive guard for env/creds issues
    import pytest

    pytest.skip(
        f"Skipping REST API onaws tests because base onaws helpers failed to import: {exc}",
        allow_module_level=True,
    )

pytestmark = [
    pytest.mark.manual_aws,
    pytest.mark.aws,
    pytest.mark.rest_api,
]

# Set environment variables for local development
os.environ["USE_LOCAL_DEV"] = "1"
os.environ.setdefault("HF_LOGDIR", "./logs")
os.environ.setdefault("AWS_PROVIDER_LOG_DIR", "./logs")
os.environ["LOG_DESTINATION"] = "file"

# AWS client setup
_boto_session = boto3.session.Session()
_ec2_region = (
    os.environ.get("AWS_REGION")
    or os.environ.get("AWS_DEFAULT_REGION")
    or _boto_session.region_name
    or "eu-west-1"
)
ec2_client = _boto_session.client("ec2", region_name=_ec2_region)
asg_client = _boto_session.client("autoscaling", region_name=_ec2_region)

# Logger setup
log = logging.getLogger("rest_api_test")
log.setLevel(logging.DEBUG)
formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s"
)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
console_handler.setFormatter(formatter)

log_dir = os.environ.get("HF_LOGDIR", "./logs")
os.makedirs(log_dir, exist_ok=True)
file_handler = logging.FileHandler(os.path.join(log_dir, "rest_api_test.log"))
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(formatter)

log.addHandler(console_handler)
log.addHandler(file_handler)

# Centralized timeouts/constants from scenarios
REST_TIMEOUTS = scenarios_rest_api.REST_API_TIMEOUTS


class OhfpServerManager:
    """Manage OHFP server lifecycle for testing."""

    def __init__(
        self,
        host: str = scenarios_rest_api.REST_API_SERVER["host"],
        port: int = scenarios_rest_api.REST_API_SERVER["port"],
        log_path: Optional[str] = None,
    ):
        self.host = host
        self.port = port
        self.process = None
        self.base_url = f"http://{host}:{port}"
        self.log_path = log_path
        self._log_file_handle = None

    def start(self, timeout: int | None = None):
        """Start OHFP server: ohfp system serve --host 0.0.0.0 --port 8000"""
        cmd = ["ohfp", "system", "serve", "--host", self.host, "--port", str(self.port)]
        log.info(f"Starting OHFP server: {' '.join(cmd)}")

        stdout_target = subprocess.PIPE
        stderr_target = subprocess.PIPE

        # If a log path is provided, write combined stdout/stderr to that file
        if self.log_path:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            self._log_file_handle = open(self.log_path, "w", encoding="utf-8")
            stdout_target = self._log_file_handle
            stderr_target = subprocess.STDOUT

        if timeout is None:
            timeout = REST_TIMEOUTS["server_start"]

        self.process = subprocess.Popen(
            cmd,
            stdout=stdout_target,
            stderr=stderr_target,
            text=True,
        )

        # Wait for server to be ready
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                response = requests.get(
                    f"{self.base_url}/health",
                    timeout=scenarios_rest_api.REST_API_SERVER["start_probe_timeout"],
                )
                if response.status_code == 200:
                    log.info(f"OHFP server started successfully on {self.base_url}")
                    return
            except requests.exceptions.RequestException:
                time.sleep(scenarios_rest_api.REST_API_SERVER["start_probe_interval"])

        # Server failed to start - capture output
        try:
            stdout, stderr = self.process.communicate(
                timeout=scenarios_rest_api.REST_API_SERVER["start_capture_timeout"]
            )
            error_msg = f"OHFP server failed to start within {timeout}s. stderr: {stderr}"
        except subprocess.TimeoutExpired:
            error_msg = f"OHFP server failed to start within {timeout}s (process still running)"

        raise RuntimeError(error_msg)

    def stop(self):
        """Terminate OHFP server process."""
        if self.process:
            log.info("Stopping OHFP server")
            self.process.terminate()
            try:
                self.process.wait(timeout=scenarios_rest_api.REST_API_SERVER["stop_wait_timeout"])
                log.info("OHFP server stopped gracefully")
            except subprocess.TimeoutExpired:
                log.warning("OHFP server did not stop gracefully, killing process")
                self.process.kill()
                self.process.wait(timeout=scenarios_rest_api.REST_API_SERVER["stop_kill_timeout"])
                log.info("OHFP server killed")
            finally:
                if self._log_file_handle:
                    try:
                        self._log_file_handle.flush()
                    finally:
                        self._log_file_handle.close()
                    self._log_file_handle = None


class RestApiClient:
    """HTTP client for Open Host Factory Plugin REST API."""

    def __init__(
        self,
        base_url: str,
        timeout: int | None = None,
        api_prefix: str = scenarios_rest_api.REST_API_PREFIX,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_prefix = api_prefix
        self.timeout = timeout or scenarios_rest_api.REST_API_TIMEOUT
        self.session = requests.Session()

    def _url(self, path: str) -> str:
        """Construct full URL with API prefix."""
        return f"{self.base_url}{self.api_prefix}{path}"

    def _handle_response(self, response: requests.Response) -> dict:
        """Handle HTTP response and raise errors if needed."""
        if response.status_code >= 400:
            try:
                error_data = response.json()
                message = error_data.get("message") or str(error_data)
            except ValueError:
                message = response.text
            raise requests.HTTPError(
                f"API error {response.status_code}: {message}", response=response
            )
        return response.json()

    def get_templates(self) -> dict:
        """GET /api/v1/templates"""
        log.debug("GET /api/v1/templates")
        response = self.session.get(self._url("/templates"), timeout=self.timeout)
        return self._handle_response(response)

    def request_machines(self, template_id: str, machine_count: int) -> dict:
        """POST /api/v1/machines/request"""
        payload = {
            "template_id": template_id,
            "machine_count": machine_count,
        }
        log.debug(f"POST /api/v1/machines/request: {json.dumps(payload)}")
        response = self.session.post(
            self._url("/machines/request"),
            json=payload,
            timeout=self.timeout,
        )
        return self._handle_response(response)

    def get_request_status(self, request_id: str, long: bool = True) -> dict:
        """GET /api/v1/requests/{request_id}/status"""
        params = {"long": "true"} if long else {}
        log.debug(f"GET /api/v1/requests/{request_id}/status?long={long}")
        response = self.session.get(
            self._url(f"/requests/{request_id}/status"),
            params=params,
            timeout=self.timeout,
        )
        return self._handle_response(response)

    def return_machines(self, machine_ids: List[str]) -> dict:
        """POST /api/v1/machines/return"""
        payload = {"machine_ids": machine_ids}
        log.debug(f"POST /api/v1/machines/return: {json.dumps(payload)}")
        response = self.session.post(
            self._url("/machines/return"),
            json=payload,
            timeout=self.timeout,
        )
        return self._handle_response(response)

    def get_request_details(self, request_id: str) -> dict:
        """GET /api/v1/requests/{request_id}"""
        log.debug(f"GET /api/v1/requests/{request_id}")
        response = self.session.get(
            self._url(f"/requests/{request_id}"),
            timeout=self.timeout,
        )
        return self._handle_response(response)


@pytest.fixture
def setup_rest_api_environment(request):
    """Generate templates and set env vars before starting the server."""
    processor = TemplateProcessor()
    test_name = request.node.name

    # Extract scenario from test parameters
    scenario_name = None
    if "[" in test_name and "]" in test_name:
        scenario_name = test_name.split("[")[1].split("]")[0]

    # Get test case configuration
    test_case = scenarios_rest_api.get_test_case_by_name(scenario_name) if scenario_name else {}

    overrides = test_case.get("overrides", {})
    awsprov_base_template = test_case.get("awsprov_base_template")
    metrics_config = test_case.get("metrics_config")

    # Generate templates
    test_config_dir = processor.run_templates_dir / test_name
    if test_config_dir.exists():
        import shutil

        shutil.rmtree(test_config_dir)
        log.info(f"Cleared existing test directory: {test_config_dir}")

    processor.generate_test_templates(
        test_name,
        awsprov_base_template=awsprov_base_template,
        overrides=overrides,
        metrics_config=metrics_config,
    )

    # Configure environment (must be set before server start)
    os.environ["HF_PROVIDER_CONFDIR"] = str(test_config_dir)
    os.environ["HF_PROVIDER_LOGDIR"] = str(test_config_dir / "logs")
    os.environ["HF_PROVIDER_WORKDIR"] = str(test_config_dir / "work")
    os.environ["DEFAULT_PROVIDER_WORKDIR"] = str(test_config_dir / "work")
    os.environ["AWS_PROVIDER_LOG_DIR"] = str(test_config_dir / "logs")
    if metrics_config:
        os.environ["METRICS_DIR"] = str(test_config_dir / "metrics")

    (test_config_dir / "logs").mkdir(exist_ok=True)
    (test_config_dir / "work").mkdir(exist_ok=True)
    if metrics_config:
        (test_config_dir / "metrics").mkdir(exist_ok=True)

    log.info(f"Test environment configured for: {test_name}")
    return test_case


@pytest.fixture
def ohfp_server(setup_rest_api_environment):
    """Start OHFP server after env/templates exist, stop after each test."""
    log_dir = os.environ.get("HF_PROVIDER_LOGDIR", "./logs")
    os.makedirs(log_dir, exist_ok=True)
    server_log_path = os.path.join(log_dir, "server.log")

    server = OhfpServerManager(log_path=server_log_path)
    server.start(timeout=REST_TIMEOUTS["server_start"])

    yield server

    server.stop()


@pytest.fixture
def rest_api_client(ohfp_server):
    """Create REST API client connected to running OHFP server."""
    return RestApiClient(
        base_url=ohfp_server.base_url,
        api_prefix="/api/v1",
        timeout=scenarios_rest_api.REST_API_TIMEOUT,
    )


def _wait_for_request_completion_rest(
    client: RestApiClient,
    request_id: str,
    timeout: int | None = None,
) -> dict:
    """Poll request status via REST API until complete."""
    start_time = time.time()
    poll_interval = REST_TIMEOUTS["request_status_poll_interval"]
    timeout = timeout or REST_TIMEOUTS["request_status_timeout"]

    while True:
        status_response = client.get_request_status(request_id, long=True)
        log.debug(f"Request status: {json.dumps(status_response, indent=2)}")

        requests_list = status_response.get("requests", [])
        request_statuses = [r.get("status") for r in requests_list if isinstance(r, dict)]
        terminal = {"complete", "partial", "failed", "cancelled", "timeout"}

        # Only consider the inner request statuses, not the top-level status
        if request_statuses and all(status in terminal for status in request_statuses):
            log.info("Request %s completed (inner statuses=%s)", request_id, request_statuses)
            return status_response

        if time.time() - start_time > timeout:
            raise TimeoutError(f"Request {request_id} did not complete within {timeout}s")

        time.sleep(poll_interval)


def _wait_for_return_completion_rest(
    client: RestApiClient,
    return_request_id: str,
    timeout: int | None = None,
) -> dict:
    """Poll return request status via REST API until complete."""
    start_time = time.time()
    poll_interval = REST_TIMEOUTS["return_status_poll_interval"]
    timeout = timeout or REST_TIMEOUTS["return_status_timeout"]

    while True:
        try:
            status_response = client.get_request_details(return_request_id)
            log.debug(f"Return request status: {json.dumps(status_response, indent=2)}")

            # Check if request is complete
            if status_response.get("status") == "complete":
                log.info(f"Return request {return_request_id} completed")
                return status_response
        except requests.HTTPError as e:
            log.debug(f"Error checking return status: {e}")

        if time.time() - start_time > timeout:
            log.warning(f"Return request {return_request_id} did not complete within {timeout}s")
            return {}

        time.sleep(poll_interval)


@pytest.mark.aws
@pytest.mark.rest_api
def _partial_return_cases_rest():
    """Pick maintain fleets/ASG scenarios with capacity > 1 for REST API."""
    cases = []
    for tc in scenarios_rest_api.get_rest_api_test_cases():
        provider_api = tc.get("overrides", {}).get("providerApi") or tc.get("providerApi")
        fleet_type = tc.get("overrides", {}).get("fleetType")
        capacity = tc.get("capacity_to_request", 0)
        if capacity <= 1:
            continue
        if provider_api in ("EC2Fleet", "SpotFleet") and str(fleet_type).lower() == "maintain":
            cases.append(tc)
        elif provider_api == "ASG":
            cases.append(tc)
    return cases


@pytest.mark.aws
@pytest.mark.slow
@pytest.mark.rest_api
@pytest.mark.parametrize("test_case", _partial_return_cases_rest(), ids=lambda tc: tc["test_name"])
def test_rest_api_partial_return_reduces_capacity(
    rest_api_client, setup_rest_api_environment, test_case
):
    """
    REST API partial return test: ensure maintain fleet/ASG capacity drops after returning one instance.
    """
    log.info("=== REST API Partial Return Test: %s ===", test_case["test_name"])

    # Step 1: Request capacity
    templates_response = rest_api_client.get_templates()
    template_id = test_case.get("template_id") or test_case["test_name"]
    template_json = next(
        (
            template
            for template in templates_response["templates"]
            if template.get("template_id") == template_id
        ),
        None,
    )
    if template_json is None:
        pytest.fail(f"Template {template_id} not found for partial return test")

    log.info("Requesting %d instances", test_case["capacity_to_request"])
    request_response = rest_api_client.request_machines(
        template_id=template_json["template_id"],
        machine_count=test_case["capacity_to_request"],
    )
    log.debug("Request response: %s", json.dumps(request_response, indent=2))

    request_id = request_response.get("request_id")
    if not request_id:
        pytest.fail(f"Request ID missing in response: {request_response}")

    status_response = _wait_for_request_completion_rest(
        rest_api_client, request_id, timeout=REST_TIMEOUTS["request_status_timeout"]
    )
    _check_request_machines_response_status(status_response)
    _check_all_ec2_hosts_are_being_provisioned(status_response)

    machines = status_response["requests"][0]["machines"]
    machine_ids = [m.get("machine_id") for m in machines]
    assert len(machine_ids) >= 2, "Partial return test requires capacity > 1"

    # Identify provider API and backing resource
    provider_api = (
        template_json.get("provider_api")
        or test_case.get("overrides", {}).get("providerApi")
        or "EC2Fleet"
    )
    first_instance = machine_ids[0]
    resource_id = _get_resource_id_from_instance(first_instance, provider_api)
    if not resource_id:
        pytest.skip(f"Could not determine backing resource for instance {first_instance}")

    capacity_before = _get_capacity(provider_api, resource_id)
    log.info("Initial capacity for %s (%s): %s", resource_id, provider_api, capacity_before)

    # Step 2: Return a single instance
    return_response = rest_api_client.return_machines([first_instance])
    log.debug("Return response: %s", json.dumps(return_response, indent=2))
    return_request_id = return_response.get("request_id")
    if return_request_id:
        _wait_for_return_completion_rest(rest_api_client, return_request_id)

    # Wait for fleet/ASG to stabilize
    if (
        provider_api
        and "fleet" in provider_api.lower()
        and resource_id.startswith(("sfr-", "fleet-"))
    ):
        _wait_for_fleet_stable(resource_id)

    expected_capacity = max(capacity_before - 1, 0)
    capacity_timeout = (
        REST_TIMEOUTS["capacity_change_timeout_asg"]
        if provider_api.lower() == "asg" or "asg" in provider_api.lower()
        else REST_TIMEOUTS["capacity_change_timeout_fleet"]
    )
    capacity_after = _wait_for_capacity_change(
        provider_api, resource_id, expected_capacity, timeout=capacity_timeout
    )
    assert capacity_after == expected_capacity, (
        f"Expected capacity {expected_capacity}, got {capacity_after}"
    )

    # Ensure returned instance is terminating/terminated
    terminate_start = time.time()
    while True:
        state_info = get_instance_state(first_instance)
        if not state_info["exists"] or state_info["state"] in ["terminated", "shutting-down"]:
            break
        if time.time() - terminate_start > MAX_TIME_WAIT_FOR_CAPACITY_PROVISIONING_SEC:
            pytest.fail(f"Instance {first_instance} failed to terminate in time")
        time.sleep(REST_TIMEOUTS["termination_poll_interval"])

    # Step 3: Cleanup remaining instances
    remaining_ids = machine_ids[1:]
    if remaining_ids:
        try:
            return_response = rest_api_client.return_machines(remaining_ids)
            rrid = return_response.get("request_id")
            if rrid:
                _wait_for_return_completion_rest(rest_api_client, rrid)
        except Exception as exc:
            log.warning("Graceful return failed for remaining instances: %s", exc)

        if provider_api.lower() == "asg" or "asg" in provider_api.lower():
            _cleanup_asg_resources(remaining_ids, provider_api)
        else:
            cleanup_start = time.time()
            cleanup_timeout = REST_TIMEOUTS["cleanup_wait_timeout"]
            while time.time() - cleanup_start < cleanup_timeout:
                if _check_all_ec2_hosts_are_being_terminated(remaining_ids):
                    break
                time.sleep(REST_TIMEOUTS["termination_poll_interval"])

        cleanup_verified = _verify_all_resources_cleaned(remaining_ids, resource_id, provider_api)
        if not cleanup_verified:
            pytest.fail("Cleanup verification failed - some resources may still exist")


def test_00_rest_api_server_health(setup_rest_api_environment):
    """
    Smoke test: start the REST API server, verify /health responds, then stop and
    confirm it is down. Placed first to ensure the server boots before any
    longer integration flow.
    """
    log_dir = os.environ.get("HF_PROVIDER_LOGDIR", "./logs")
    os.makedirs(log_dir, exist_ok=True)
    server_log_path = os.path.join(log_dir, "server.log")

    server = OhfpServerManager(log_path=server_log_path)
    server.start(timeout=REST_TIMEOUTS["server_start"])

    try:
        log.info("Checking API health at %s", server.base_url)
        resp = requests.get(f"{server.base_url}/health", timeout=REST_TIMEOUTS["health_check"])
        assert resp.status_code == 200, f"Unexpected health status: {resp.status_code}"
        log.info("Health check passed: %s", resp.json())

        log.info("Fetching templates from %s", f"{server.base_url}/api/v1/templates/")
        templates_resp = requests.get(
            f"{server.base_url}/api/v1/templates/", timeout=REST_TIMEOUTS["templates"]
        )
        assert templates_resp.status_code == 200, (
            f"Templates endpoint failed: {templates_resp.status_code}"
        )
        log.info("Templates response: %s", json.dumps(templates_resp.json(), indent=2))
    except Exception as exc:
        log.error("Health/templates check failed: %s", exc, exc_info=True)
        raise
    finally:
        log.info("Stopping server after health/templates check")
        server.stop()

    # Confirm the server is down
    down_confirmed = False
    for _ in range(REST_TIMEOUTS["server_shutdown_attempts"]):
        try:
            requests.get(
                f"{server.base_url}/health",
                timeout=REST_TIMEOUTS["server_shutdown_check_interval"],
            )
        except requests.RequestException:
            down_confirmed = True
            break
        time.sleep(REST_TIMEOUTS["shutdown_check_sleep"])

    assert down_confirmed, "API should be unreachable after server.stop()"


def _check_request_machines_response_status(status_response):
    """Validate request status response."""
    assert status_response["requests"][0]["status"] == "complete"
    for machine in status_response["requests"][0]["machines"]:
        # EC2 host may still be initializing
        assert machine["status"] in ["running", "pending"]


def _check_all_ec2_hosts_are_being_provisioned(status_response):
    """Verify all EC2 instances are being provisioned."""
    for machine in status_response["requests"][0]["machines"]:
        ec2_instance_id = machine.get("machine_id")
        res = get_instance_state(ec2_instance_id)

        assert res["exists"] is True
        # EC2 host may still be initializing
        assert res["state"] in ["running", "pending"]

        log.debug(f"EC2 {ec2_instance_id} state: {json.dumps(res, indent=4)}")


@pytest.mark.aws
@pytest.mark.slow
@pytest.mark.rest_api
@pytest.mark.parametrize(
    "test_case",
    scenarios_rest_api.get_rest_api_test_cases(),
    ids=lambda tc: tc["test_name"],
)
def test_rest_api_control_loop(rest_api_client, setup_rest_api_environment, test_case):
    """
    Single control loop test using REST API.

    Steps:
    1. Request capacity via REST API
    2. Wait for fulfillment and validate
    3. Delete all capacity and verify cleanup
    """
    log.info("=" * 80)
    log.info(f"Starting REST API test: {test_case['test_name']}")
    log.info("=" * 80)

    # Step 1: Request Capacity
    log.info("=== STEP 1: Request Capacity ===")

    # 1.1: Get available templates via REST API
    log.info("1.1: Retrieving available templates via REST API")
    templates_response = rest_api_client.get_templates()
    log.debug(f"Templates response: {json.dumps(templates_response, indent=2)}")

    # 1.2: Find target template
    log.info("1.2: Finding target template")
    template_id = test_case.get("template_id") or test_case["test_name"]
    template_json = next(
        (
            template
            for template in templates_response["templates"]
            if template.get("template_id") == template_id
        ),
        None,
    )

    if template_json is None:
        log.warning(f"Template {template_id} not found, using first available template")
        template_json = templates_response["templates"][0]

    log.info(f"Using template: {template_json.get('template_id')}")

    # 1.3: Request machines via REST API
    log.info(f"1.3: Requesting {test_case['capacity_to_request']} machines")
    request_response = rest_api_client.request_machines(
        template_id=template_json["template_id"],
        machine_count=test_case["capacity_to_request"],
    )
    log.debug(f"Request response: {json.dumps(request_response, indent=2)}")

    # 1.4: Validate request response
    log.info("1.4: Validating request response")
    request_id = request_response.get("request_id")
    if not request_id:
        pytest.fail(f"Request ID missing in response: {request_response}")

    log.info(f"Request ID: {request_id}")

    # Step 2: Wait for Fulfillment
    log.info("=== STEP 2: Wait for Fulfillment ===")

    # 2.1: Poll request status via REST API
    log.info(
        f"2.1: Polling request status (timeout: {MAX_TIME_WAIT_FOR_CAPACITY_PROVISIONING_SEC}s)"
    )
    status_response = _wait_for_request_completion_rest(
        rest_api_client,
        request_id,
        timeout=MAX_TIME_WAIT_FOR_CAPACITY_PROVISIONING_SEC,
    )

    # 2.2: Validate status response
    log.info("2.2: Validating status response")
    _check_request_machines_response_status(status_response)

    # 2.3: Verify instances on AWS
    log.info("2.3: Verifying instances on AWS")
    _check_all_ec2_hosts_are_being_provisioned(status_response)

    # 2.4: Validate instance attributes
    log.info("2.4: Validating instance attributes")
    attribute_validation_passed = validate_random_instance_attributes(
        status_response, template_json
    )
    if not attribute_validation_passed:
        pytest.fail(
            "Instance attribute validation failed - EC2 instance attributes do not match template"
        )
    log.info("Instance attribute validation PASSED")

    # 2.5: Validate price type (if specified)
    expected_price_type = test_case.get("overrides", {}).get("priceType")
    if expected_price_type:
        log.info("2.5: Validating price type for all instances")
        provider_api = (
            template_json.get("provider_api")
            or test_case.get("overrides", {}).get("providerApi")
            or "EC2Fleet"
        )

        if provider_api == "RunInstances" and expected_price_type == "spot":
            log.warning(f"Skipping price type validation for {provider_api} with spot instances")
        else:
            price_type_validation_passed = validate_all_instances_price_type(
                status_response, test_case
            )
            if not price_type_validation_passed:
                pytest.fail(
                    "Price type validation failed - instances do not match expected price type"
                )
            log.info("Price type validation PASSED")

    # 2.6: Verify ABIS (if requested)
    abis_requested = test_case.get("overrides", {}).get("abisInstanceRequirements")
    if abis_requested:
        log.info("2.6: Verifying ABIS configuration")
        first_machine = status_response["requests"][0]["machines"][0]
        instance_id = first_machine.get("machine_id")
        verify_abis_enabled_for_instance(instance_id)
        log.info("ABIS verification PASSED")

    # Step 3: Delete Capacity
    log.info("=== STEP 3: Delete Capacity ===")

    # 3.1: Extract instance IDs
    log.info("3.1: Extracting instance IDs")
    machine_ids = [machine["machine_id"] for machine in status_response["requests"][0]["machines"]]
    log.info(f"Machine IDs to return: {machine_ids}")

    # 3.2: Request return via REST API
    log.info("3.2: Requesting return via REST API")
    return_response = rest_api_client.return_machines(machine_ids)
    log.debug(f"Return response: {json.dumps(return_response, indent=2)}")

    return_request_id = return_response.get("request_id")
    if not return_request_id:
        log.warning(f"Return request ID missing in response: {return_response}")
    else:
        log.info(f"Return request ID: {return_request_id}")

    # 3.3: Wait for return completion
    log.info("3.3: Waiting for return completion")
    if return_request_id:
        _wait_for_return_completion_rest(
            rest_api_client,
            return_request_id,
            timeout=REST_TIMEOUTS["return_status_timeout"],
        )

    # 3.4: Verify termination on AWS
    log.info("3.4: Verifying termination on AWS")
    provider_api = (
        template_json.get("provider_api")
        or test_case.get("overrides", {}).get("providerApi")
        or "EC2Fleet"
    )

    # Get resource ID for verification
    resource_id = None
    if machine_ids:
        resource_id = _get_resource_id_from_instance(machine_ids[0], provider_api)

    # Wait for graceful termination
    graceful_start = time.time()
    graceful_completed = False
    graceful_timeout = REST_TIMEOUTS["graceful_termination_timeout"]
    termination_poll = REST_TIMEOUTS["termination_poll_interval"]
    while time.time() - graceful_start < graceful_timeout:
        if _check_all_ec2_hosts_are_being_terminated(machine_ids):
            log.info("Graceful termination completed successfully")
            graceful_completed = True
            break
        time.sleep(termination_poll)

    # 3.5: Comprehensive cleanup (for ASG or if graceful failed)
    if not graceful_completed:
        log.warning("Graceful termination timed out or incomplete")

        if provider_api == "ASG" or "asg" in provider_api.lower():
            log.info("3.5: Performing comprehensive ASG cleanup")
            _cleanup_asg_resources(machine_ids, provider_api)
        else:
            log.info("3.5: Continuing to wait for standard termination")
            cleanup_start = time.time()
            cleanup_timeout = REST_TIMEOUTS["cleanup_wait_timeout"]
            while time.time() - cleanup_start < cleanup_timeout:
                if _check_all_ec2_hosts_are_being_terminated(machine_ids):
                    log.info("All instances terminated successfully")
                    break
                time.sleep(termination_poll)
            else:
                log.warning("Some instances may not have terminated within timeout")

    # 3.6: Final verification
    log.info("3.6: Verifying complete resource cleanup")
    cleanup_verified = _verify_all_resources_cleaned(
        machine_ids,
        resource_id,
        provider_api,
    )

    if not cleanup_verified:
        log.error("⚠️  Cleanup verification failed - some resources may still exist")
        for instance_id in machine_ids:
            state_info = get_instance_state(instance_id)
            if state_info["exists"]:
                log.error(f"Instance {instance_id} still exists in state: {state_info['state']}")
    else:
        log.info("✅ All resources successfully cleaned up")

    log.info("=" * 80)
    log.info(f"REST API test completed: {test_case['test_name']}")
    log.info("=" * 80)
