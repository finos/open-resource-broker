"""REST API-based AWS integration tests for Open Resource Broker."""

import json
import logging
import os
import re
import subprocess
import time
from collections import Counter, namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

import boto3
import pytest
import requests
from botocore.exceptions import ClientError

from tests.onaws import scenarios_rest_api
from tests.onaws.scenarios_rest_api import CUSTOM_TEST_CASES
from tests.onaws.template_processor import TemplateProcessor

# Import AWS validation functions from test_onaws (guarded to allow skip on import failures)
try:
    from tests.onaws.test_onaws import (
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
# Force region to eu-west-1 for tests
os.environ.setdefault("AWS_REGION", "eu-west-1")

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

# Log the configured region for debugging
print(f"AWS clients initialized with region: {_ec2_region}")

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
REST_API_SERVER_CFG = scenarios_rest_api.REST_API_SERVER
MAX_CONCURRENCY = int(os.environ.get("REST_API_MAX_CONCURRENCY", 2))
LAUNCH_DELAY = float(os.environ.get("REST_API_LAUNCH_DELAY_SEC", 3.0))
WorkerResult = namedtuple("WorkerResult", "scenario status error traceback")


class RequestTimeoutError(TimeoutError):
    """Timeout with captured status payload for post-processing."""

    def __init__(self, message: str, status_response: dict | None = None):
        super().__init__(message)
        self.status_response = status_response


class ORBServerManager:
    """Manage ORB server lifecycle for testing."""

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
        """Start ORB server: orb system serve --host 0.0.0.0 --port 8000"""
        cmd = ["orb", "system", "serve", "--host", self.host, "--port", str(self.port)]
        log.info(f"Starting ORB server: {' '.join(cmd)}")

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
                    log.info(f"ORB server started successfully on {self.base_url}")
                    return
            except requests.exceptions.RequestException:
                time.sleep(scenarios_rest_api.REST_API_SERVER["start_probe_interval"])

        # Server failed to start - capture output
        try:
            stdout, stderr = self.process.communicate(
                timeout=scenarios_rest_api.REST_API_SERVER["start_capture_timeout"]
            )
            error_msg = f"ORB server failed to start within {timeout}s. stderr: {stderr}"
        except subprocess.TimeoutExpired:
            error_msg = f"ORB server failed to start within {timeout}s (process still running)"

        raise RuntimeError(error_msg)

    def stop(self):
        """Terminate ORB server process."""
        if self.process:
            log.info("Stopping ORB server")
            self.process.terminate()
            try:
                self.process.wait(timeout=scenarios_rest_api.REST_API_SERVER["stop_wait_timeout"])
                log.info("ORB server stopped gracefully")
            except subprocess.TimeoutExpired:
                log.warning("ORB server did not stop gracefully, killing process")
                self.process.kill()
                self.process.wait(timeout=scenarios_rest_api.REST_API_SERVER["stop_kill_timeout"])
                log.info("ORB server killed")
            finally:
                if self._log_file_handle:
                    try:
                        self._log_file_handle.flush()
                    finally:
                        self._log_file_handle.close()
                    self._log_file_handle = None


class RestApiClient:
    """HTTP client for Open Resource Broker REST API."""

    def __init__(
        self,
        base_url: str,
        timeout: int | None = None,
        api_prefix: str = scenarios_rest_api.REST_API_PREFIX,
        retry_attempts: int | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_prefix = api_prefix
        self.timeout = timeout or REST_TIMEOUTS["rest_api_timeout"]
        self.retry_attempts = retry_attempts or REST_TIMEOUTS["rest_api_retry_attempts"]
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
    raw_test_name = request.node.name

    # Preserve scenario id from the last [...] block in the raw name
    scenario_name = None
    if "[" in raw_test_name and "]" in raw_test_name:
        scenario_name = raw_test_name.rsplit("[", 1)[1].split("]", 1)[0]

    # Insert timestamp block before the scenario block for unique run directories
    timestamp = datetime.now().strftime("%m.%d.%H.%M")
    if "[" in raw_test_name:
        base, rest = raw_test_name.split("[", 1)
        test_name = f"{base}[{timestamp}][{rest}"
    else:
        test_name = f"{raw_test_name}[{timestamp}]"

    # Extract scenario from test parameters
    # (using the original name so the scenario id isn't replaced by timestamp)
    # scenario_name already determined above

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
def orb_server(setup_rest_api_environment):
    """Start ORB server after env/templates exist, stop after each test."""
    log_dir = os.environ.get("HF_PROVIDER_LOGDIR", "./logs")
    os.makedirs(log_dir, exist_ok=True)
    server_log_path = os.path.join(log_dir, "server.log")

    server = ORBServerManager(log_path=server_log_path)
    server.start(timeout=REST_TIMEOUTS["server_start"])

    yield server

    server.stop()


@pytest.fixture
def rest_api_client(orb_server):
    """Create REST API client connected to running ORB server."""
    return RestApiClient(
        base_url=orb_server.base_url,
        api_prefix="/api/v1",
        timeout=REST_TIMEOUTS["rest_api_timeout"],
        retry_attempts=REST_TIMEOUTS["rest_api_retry_attempts"],
    )


def _lookup_aws_capacity_progress(provider_api: str, resource_id: str) -> tuple[int, int] | None:
    """Return fulfilled/target capacity for backing AWS resources."""
    provider_lower = (provider_api or "").lower()

    # Normalize based on resource_id prefix if we can
    if resource_id.startswith("sfr-"):
        provider_lower = "spotfleet"
    elif resource_id.startswith("fleet-"):
        provider_lower = "ec2fleet"

    try:
        if "spotfleet" in provider_lower:
            resp = ec2_client.describe_spot_fleet_requests(SpotFleetRequestIds=[resource_id])
            configs = resp.get("SpotFleetRequestConfigs") or []
            config = configs[0].get("SpotFleetRequestConfig", {}) if configs else {}
            target = int(config.get("TargetCapacity", 0) or 0)
            fulfilled = int(config.get("FulfilledCapacity", 0) or 0)
            return fulfilled, target

        if "ec2fleet" in provider_lower:
            resp = ec2_client.describe_fleets(FleetIds=[resource_id])
            fleets = resp.get("Fleets") or []
            fleet = fleets[0] if fleets else {}
            target_spec = fleet.get("TargetCapacitySpecification", {}) or {}
            target = int(target_spec.get("TotalTargetCapacity", 0) or 0)
            fulfilled = int(fleet.get("FulfilledCapacity", 0) or 0)
            return fulfilled, target

        if provider_lower == "asg" or "autoscaling" in provider_lower:
            resp = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[resource_id])
            asg = (resp.get("AutoScalingGroups") or [{}])[0]
            target = int(asg.get("DesiredCapacity", 0) or 0)
            fulfilled = len(asg.get("Instances") or [])
            return fulfilled, target
    except Exception as exc:
        log.debug(
            "AWS capacity progress lookup failed for %s %s: %s", provider_api, resource_id, exc
        )

    return None


# Global dictionary to track the latest timestamp seen for each resource
_resource_latest_timestamps = {}

# Configuration for history collection limits
HISTORY_COLLECTION_CONFIG = {
    # Fixed to 100 to align with AWS API limits and avoid env-dependent behavior
    "max_records_per_call": 100,
    "max_api_calls": int(os.environ.get("HISTORY_MAX_API_CALLS", "10")),
}


def _log_aws_capacity_progress(
    requests_list: list,
    expected_capacity: int | None,
    provider_hint: str | None,
) -> None:
    """Log AWS-side fulfillment for the backing resource after each REST poll and collect history."""
    first_request = next((r for r in requests_list if isinstance(r, dict)), None)
    if not first_request:
        return

    machines = first_request.get("machines") or []
    machine_ids = [
        machine.get("machine_id")
        for machine in machines
        if isinstance(machine, dict) and machine.get("machine_id")
    ]

    if not machine_ids:
        log.debug(
            "AWS capacity check: no machine IDs yet for request %s",
            first_request.get("request_id"),
        )
        return

    provider_api = (
        first_request.get("provider_api")
        or first_request.get("providerApi")
        or provider_hint
        or "EC2Fleet"
    )

    if provider_api and provider_api.lower() == "runinstances":
        log.debug(
            "AWS capacity check: skipping RunInstances request %s", first_request.get("request_id")
        )
        return

    resource_id = _get_resource_id_from_instance(machine_ids[0], provider_api)
    if not resource_id:
        log.debug(
            "AWS capacity check: could not determine backing resource for instance %s (provider=%s)",
            machine_ids[0],
            provider_api,
        )
        return

    progress = _lookup_aws_capacity_progress(provider_api, resource_id)
    if not progress:
        return

    fulfilled_capacity, target_capacity = progress
    remaining_target = max(target_capacity - fulfilled_capacity, 0)
    remaining_requested = (
        max(expected_capacity - fulfilled_capacity, 0) if expected_capacity is not None else None
    )
    fulfilled_flag = fulfilled_capacity >= target_capacity > 0

    rows = [
        ("provider", provider_api),
        ("resource", resource_id),
        ("fulfilled", fulfilled_flag),
        ("fulfilled_capacity", fulfilled_capacity),
        ("target_capacity", target_capacity),
        ("remaining_target", remaining_target),
    ]
    if remaining_requested is not None:
        rows.append(("remaining_request", remaining_requested))

    col1_width = max(len("field"), *(len(k) for k, _ in rows))
    col2_width = max(len("value"), *(len(str(v)) for _, v in rows))

    border = f"+-{'-' * col1_width}-+-{'-' * col2_width}-+"
    header = f"| {'field':<{col1_width}} | {'value':<{col2_width}} |"
    lines = [
        "AWS capacity status:",
        border,
        header,
        border,
    ]
    for key, val in rows:
        lines.append(f"| {key:<{col1_width}} | {val!s:<{col2_width}} |")
    lines.append(border)

    log.debug("\n%s", "\n".join(lines))

    # Collect and log resource history with proper time interval management
    log_resource_history(resource_id, provider_api)


def log_resource_history(resource_id: str, provider_api: str) -> None:
    """Collect and log resource history with continuous time intervals to avoid gaps."""
    global _resource_latest_timestamps

    # Round current time to 1 second precision
    current_time = datetime.now(timezone.utc).replace(microsecond=0)

    # Always query from 1 hour ago (static lookback window)
    start_time = (current_time - timedelta(hours=1)).replace(microsecond=0)

    # Get the latest timestamp we've seen for this resource
    resource_key = f"{provider_api}:{resource_id}"
    latest_seen_timestamp = _resource_latest_timestamps.get(resource_key)

    log.info(
        f"Collecting history for {provider_api} resource {resource_id} from {start_time.isoformat()} (static 1h lookback), latest_seen={latest_seen_timestamp.isoformat() if latest_seen_timestamp else 'None'}"
    )

    try:
        history_records = []
        api_call_count = 0
        config = HISTORY_COLLECTION_CONFIG

        if provider_api == "EC2Fleet":
            log.debug(
                f"Fetching EC2Fleet history for {resource_id} (max_records_per_call={config['max_records_per_call']}, max_api_calls={config['max_api_calls']})"
            )
            next_token = None
            # Add 60 second buffer to account for clock skew between client and AWS
            buffered_start_time = start_time - timedelta(seconds=3600)
            # Set end time 5 hours in the future to ensure we get all events
            end_time = current_time + timedelta(hours=5)
            while api_call_count < config["max_api_calls"]:
                params = {
                    "FleetId": resource_id,
                    "StartTime": buffered_start_time,
                    "EndTime": end_time,  # Use calculated end time for bounded queries
                    "MaxResults": config["max_records_per_call"],
                }
                if next_token:
                    params["NextToken"] = next_token

                api_call_count += 1
                log.debug(
                    f"EC2Fleet history API call {api_call_count}/{config['max_api_calls']}, requesting up to {params['MaxResults']} records"
                )

                response = ec2_client.describe_fleet_history(**params)
                all_records = response.get("HistoryRecords", [])

                # Filter records to exclude already-seen records
                batch_count = 0
                for record in all_records:
                    record_time = record.get("Timestamp")
                    if record_time:
                        # Only include if we haven't seen this timestamp before
                        if latest_seen_timestamp is None or record_time > latest_seen_timestamp:
                            history_records.append(record)
                            batch_count += 1

                filtered_out = len(all_records) - batch_count
                log.debug(
                    f"Retrieved {len(all_records)} records from API: {batch_count} new, {filtered_out} filtered out (already seen), total so far: {len(history_records)}"
                )

                next_token = response.get("NextToken")
                if not next_token:
                    break

            if api_call_count >= config["max_api_calls"]:
                log.warning(
                    f"EC2Fleet history collection reached max_api_calls limit ({config['max_api_calls']})"
                )

        elif provider_api == "SpotFleet":
            log.debug(
                f"Fetching SpotFleet history for {resource_id} (max_records_per_call={config['max_records_per_call']}, max_api_calls={config['max_api_calls']})"
            )
            next_token = None
            # Add 60 second buffer to account for clock skew between client and AWS
            buffered_start_time = start_time - timedelta(seconds=3600)
            while api_call_count < config["max_api_calls"]:
                params = {
                    "SpotFleetRequestId": resource_id,
                    "StartTime": buffered_start_time,
                }
                if next_token:
                    params["NextToken"] = next_token

                api_call_count += 1
                log.debug(f"SpotFleet history API call {api_call_count}/{config['max_api_calls']}")

                response = ec2_client.describe_spot_fleet_request_history(**params)
                all_records = response.get("HistoryRecords", [])

                # Filter records to exclude already-seen records
                batch_count = 0
                for record in all_records:
                    record_time = record.get("Timestamp")
                    if record_time:
                        # Only include if we haven't seen this timestamp before
                        if latest_seen_timestamp is None or record_time > latest_seen_timestamp:
                            history_records.append(record)
                            batch_count += 1

                filtered_out = len(all_records) - batch_count
                log.debug(
                    f"Retrieved {len(all_records)} records from API: {batch_count} new, {filtered_out} filtered out (already seen), total so far: {len(history_records)}"
                )

                next_token = response.get("NextToken")
                if not next_token:
                    break

            if api_call_count >= config["max_api_calls"]:
                log.warning(
                    f"SpotFleet history collection reached max_api_calls limit ({config['max_api_calls']})"
                )

        elif provider_api == "ASG":
            log.debug(
                f"Fetching ASG scaling activities for {resource_id} (region={asg_client.meta.region_name})"
            )
            # ASG has a max limit of 100 records per call, so we need to paginate
            next_token = None
            stop_pagination = False
            while True:
                params = {
                    "AutoScalingGroupName": resource_id,
                    "MaxRecords": 100,  # ASG max is 100
                }
                if next_token:
                    params["NextToken"] = next_token

                api_call_count += 1
                # log.debug(f"ASG history API call {api_call_count}, requesting up to {params['MaxRecords']} records")

                response = asg_client.describe_scaling_activities(**params)
                all_activities = response.get("Activities", [])
                # log.debug(f"Retrieved {len(all_activities)} ASG activities from API")

                # ASG returns activities in reverse chronological order, filter by latest seen
                for activity in all_activities:
                    activity_time = activity.get("StartTime")
                    if activity_time:
                        # Convert to UTC if needed
                        if activity_time.tzinfo is None:
                            activity_time = activity_time.replace(tzinfo=timezone.utc)
                        elif activity_time.tzinfo != timezone.utc:
                            activity_time = activity_time.astimezone(timezone.utc)

                        if activity_time < start_time:
                            # Activities are sorted newest first, so we can stop
                            stop_pagination = True
                            break
                        # Only include if we haven't seen this timestamp before
                        if latest_seen_timestamp is None or activity_time > latest_seen_timestamp:
                            history_records.append(activity)

                if stop_pagination:
                    break

                next_token = response.get("NextToken")
                if not next_token:
                    break

        else:
            log.warning(f"Unknown provider API for history collection: {provider_api}")
            return

        # Update latest seen timestamp if we got history records
        if history_records:
            # Find the latest timestamp from all collected records
            # ASG uses StartTime, EC2Fleet/SpotFleet use Timestamp
            timestamps = []
            for record in history_records:
                ts = record.get("Timestamp") or record.get("StartTime")
                if ts:
                    timestamps.append(ts)

            if timestamps:
                latest_timestamp = max(timestamps)
                _resource_latest_timestamps[resource_key] = latest_timestamp
                log.debug(f"Updated latest_seen_timestamp to {latest_timestamp.isoformat()}")

        # Log the collected history
        if history_records:

            def _format_list(lines: list[str]) -> str:
                """Render lines with top/bottom borders."""
                if not lines:
                    return ""
                width = max(len(line) for line in lines)
                border = "=" * (width + 4)
                body = "\n".join(f"| {line.ljust(width)} |" for line in lines)
                return f"{border}\n{body}\n{border}"

            sections: list[str] = []

            if provider_api in ["EC2Fleet", "SpotFleet"]:
                # Merge noisy fleetRequestChange events and common launch success sub-type
                launch_events_count = 0
                fleet_request_change_count = 0
                other_events = []

                for record in history_records:
                    event_type = record.get("EventType", "")
                    event_info = record.get("EventInformation", {})
                    event_sub_type = event_info.get("EventSubType", "") or event_info.get(
                        "eventSubType", ""
                    )

                    if event_type == "fleetRequestChange":
                        fleet_request_change_count += 1
                        continue

                    if event_sub_type in ["launched", "Successful - Launching a new EC2 instance"]:
                        launch_events_count += 1
                    else:
                        other_events.append(record)

                merged_lines = []
                if fleet_request_change_count > 0:
                    merged_lines.append(
                        f"Merged Events: {fleet_request_change_count} x fleetRequestChange"
                    )

                if launch_events_count > 0:
                    merged_lines.append(
                        f"Merged Events: {launch_events_count} x 'Successful - Launching a new EC2 instance'"
                    )

                if merged_lines:
                    sections.append(_format_list(merged_lines))

                detail_lines = []
                for i, record in enumerate(other_events):
                    timestamp = record.get("Timestamp", "N/A")
                    event_type = record.get("EventType", "N/A")
                    event_info = record.get("EventInformation", {})
                    detail_lines.append(
                        f"{i + 1}) {timestamp} | {event_type} | {json.dumps(event_info, default=str)}"
                    )

                if detail_lines:
                    sections.append(_format_list(detail_lines))

            elif provider_api == "ASG":
                # Count and merge "Launching a new EC2 instance" activities for ASG
                launch_activities_count = 0
                other_activities = []

                for record in history_records:
                    description = record.get("Description", "")

                    if "Launching a new EC2 instance:" in description:
                        launch_activities_count += 1
                    else:
                        other_activities.append(record)

                merged_lines = []
                if launch_activities_count > 0:
                    merged_lines.append(
                        f"Merged Activities: {launch_activities_count} x 'Launching a new EC2 instance'"
                    )

                if merged_lines:
                    sections.append(_format_list(merged_lines))

                detail_lines = []

                for i, record in enumerate(other_activities):
                    start_time_str = record.get("StartTime", "N/A")
                    activity_id = record.get("ActivityId", "N/A")
                    description = record.get("Description", "N/A")
                    status_code = record.get("StatusCode", "N/A")
                    detail_lines.append(
                        f"{i + 1}) start_time={start_time_str} | activity_id={activity_id} | status={status_code} | description={description}"
                    )

                if detail_lines:
                    sections.append(_format_list(detail_lines))

            if sections:
                log.info("\n%s", "\n\n".join(sections))

        else:
            delta_time_sec = int((current_time - start_time).total_seconds())
            log.debug(
                f"No new history records found for {provider_api} resource {resource_id} in interval {start_time.isoformat()} to {current_time.isoformat()} (delta_time_sec={delta_time_sec})"
            )

    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        error_message = exc.response["Error"].get("Message", "")
        if error_code in [
            "InvalidFleetId.NotFound",
            "InvalidSpotFleetRequestId.NotFound",
            "ValidationError",
            "ResourceNotFoundException",
        ]:
            log.debug(
                f"Resource {resource_id} not found for history collection (error: {error_code}, message: {error_message}, full exception: {exc})"
            )
        else:
            log.warning(
                f"Failed to collect history for {provider_api} resource {resource_id}: {exc}"
            )
    except Exception as exc:
        log.warning(f"Failed to collect history for {provider_api} resource {resource_id}: {exc}")


def _wait_for_request_completion_rest(
    client: RestApiClient,
    request_id: str,
    timeout: int | None = None,
    expected_capacity: int | None = None,
    provider_api: str | None = None,
) -> dict:
    """Poll request status via REST API until complete."""
    start_time = time.time()
    poll_interval = REST_TIMEOUTS["request_status_poll_interval"]
    timeout = timeout or REST_TIMEOUTS["request_fulfillment_timeout"]
    last_status: dict | None = None

    while True:
        status_response = client.get_request_status(request_id, long=True)
        last_status = status_response
        requests_list = status_response.get("requests", [])
        request_statuses = [r.get("status") for r in requests_list if isinstance(r, dict)]
        terminal = {"complete", "partial", "failed", "cancelled", "timeout"}

        summaries = []
        for req in requests_list:
            if not isinstance(req, dict):
                continue

            machines = req.get("machines") or []
            state_counts = Counter(
                machine.get("status", "unknown")
                for machine in machines
                if isinstance(machine, dict)
            )

            summaries.append(
                {
                    "request_id": req.get("request_id"),
                    "status": req.get("status"),
                    "machines_total": len(machines),
                    "machines_by_status": dict(sorted(state_counts.items())),
                }
            )

        summary_payload = {"status": status_response.get("status"), "requests": summaries}
        log.debug("ORB Request status summary: %s", json.dumps(summary_payload, indent=2))
        _log_aws_capacity_progress(requests_list, expected_capacity, provider_api)

        # Only consider the inner request statuses, not the top-level status
        if request_statuses and all(status in terminal for status in request_statuses):
            log.info("Request %s completed (inner statuses=%s)", request_id, request_statuses)
            return status_response

        log.debug(f"Time left: {timeout - (time.time() - start_time)}")
        if time.time() - start_time > timeout:
            raise RequestTimeoutError(
                f"Request {request_id} did not complete within {timeout}s",
                status_response=last_status,
            )

        time.sleep(poll_interval)


def _wait_for_return_completion_rest(
    client: RestApiClient,
    return_request_id: str,
    timeout: int,
) -> dict:
    """Poll return request status via REST API until complete."""
    start_time = time.time()
    poll_interval = REST_TIMEOUTS["return_status_poll_interval"]

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
    for tc in CUSTOM_TEST_CASES:
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

    provider_api = (
        template_json.get("provider_api")
        or test_case.get("overrides", {}).get("providerApi")
        or "EC2Fleet"
    )

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
        rest_api_client,
        request_id,
        timeout=REST_TIMEOUTS["request_fulfillment_timeout"],
        expected_capacity=test_case["capacity_to_request"],
        provider_api=provider_api,
    )
    _check_request_machines_response_status(status_response)
    _check_all_ec2_hosts_are_being_provisioned(status_response)

    machines = status_response["requests"][0]["machines"]
    machine_ids = [m.get("machine_id") for m in machines]
    assert len(machine_ids) >= 2, "Partial return test requires capacity > 1"

    # Identify provider API and backing resource
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
        if time.time() - terminate_start > REST_TIMEOUTS["request_fulfillment_timeout"]:
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

    server = ORBServerManager(log_path=server_log_path)
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


def _describe_instances_bulk(instance_ids: list[str], chunk_size: int = 100) -> dict[str, dict]:
    """
    Fetch instance states in batches to minimize AWS API calls.

    Returns a mapping of instance_id -> {"exists": bool, "state": str | None}.
    """
    states: dict[str, dict] = {}
    if not instance_ids:
        return states

    for start in range(0, len(instance_ids), chunk_size):
        chunk = instance_ids[start : start + chunk_size]
        try:
            resp = ec2_client.describe_instances(InstanceIds=chunk)
        except ClientError as exc:  # pragma: no cover - defensive
            log.warning("describe_instances failed for chunk %s: %s", chunk, exc)
            continue

        found_ids: set[str] = set()
        for reservation in resp.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                iid = inst.get("InstanceId")
                if not iid:
                    continue
                found_ids.add(iid)
                state_name = (
                    inst.get("State", {}).get("Name")
                    if isinstance(inst.get("State"), dict)
                    else inst.get("State")
                )
                states[iid] = {"exists": True, "state": state_name}

        # Any IDs in the chunk not returned are marked as missing
        for iid in chunk:
            if iid not in found_ids and iid not in states:
                states[iid] = {"exists": False, "state": None}

    return states


def _check_all_ec2_hosts_are_being_provisioned(status_response):
    """Verify all EC2 instances are being provisioned."""
    machines = status_response["requests"][0]["machines"]
    instance_ids = [m.get("machine_id") for m in machines]
    states = _describe_instances_bulk(instance_ids)

    for machine in machines:
        ec2_instance_id = machine.get("machine_id")
        res = states.get(ec2_instance_id, {"exists": False, "state": None})

        assert res["exists"] is True
        # EC2 host may still be initializing
        assert res["state"] in ["running", "pending"]

        log.debug(f"EC2 {ec2_instance_id} state: {json.dumps(res, indent=4)}")


def _capture_resource_history(resource_id: str, provider_api: str, test_name: str) -> None:
    """
    Capture AWS-side history for a backing resource and write it to METRICS_DIR.

    This function:
    - Requires METRICS_DIR to be set; otherwise it logs a warning and returns.
    - Fetches provider-specific history (EC2Fleet, SpotFleet, ASG) with pagination.

    -> EC2Fleet: uses describe_fleets to detect fleet type. If the fleet is instant,
    it synthesizes a history by collecting instance IDs (from fleet info, the request DB,
    or describe_fleet_instances) and then calling describe_instances in batches of 100 to
    build instanceChange events plus a synthetic “submitted” event at CreateTime. If not instant,
    it calls describe_fleet_history with StartTime = now - 1h, paginating by NextToken;
    if AWS rejects it as an instant fleet, it falls back to the synthetic path.

    -> SpotFleet: calls describe_spot_fleet_request_history with StartTime = now - 1h, paginating by NextToken.

    -> For ASG, inserts a synthetic "submitted" event at creation time so downstream
      tooling can compute request_time consistently with fleet history.

    - Writes a JSON payload (resource_id, provider_api, history) to
      {METRICS_DIR}/{test_name}_history.json.
    """
    log.info("=== Starting resource history capture ===")
    log.info(f"Resource ID: {resource_id}")
    log.info(f"Provider API: {provider_api}")
    log.info(f"Test name: {test_name}")

    metrics_dir = os.environ.get("METRICS_DIR")
    log.info(f"METRICS_DIR from environment: {metrics_dir}")

    if not metrics_dir:
        log.warning("METRICS_DIR not set, skipping history capture")
        return

    # Save history files directly to metrics directory (no subdirectory)
    os.makedirs(metrics_dir, exist_ok=True)
    log.info(f"Metrics directory created/verified: {metrics_dir}")

    history_file = os.path.join(metrics_dir, f"{test_name}_history.json")
    log.info(f"History file path: {history_file}")

    try:
        # Load instance IDs from request database (if available) for completeness check
        def _load_db_instance_ids() -> set[str]:
            ids: set[str] = set()
            workdir = os.environ.get("HF_PROVIDER_WORKDIR") or os.environ.get(
                "DEFAULT_PROVIDER_WORKDIR"
            )
            if not workdir:
                return ids
            db_path = Path(workdir) / "data" / "request_database.json"
            if not db_path.exists():
                return ids
            try:
                db_data = json.load(open(db_path))
                machines = db_data.get("machines") or {}
                ids.update(machines.keys())
            except Exception as exc:  # pragma: no cover - defensive
                log.warning(
                    "Could not load request_database.json for history completeness: %s", exc
                )
            return ids

        def _extract_instance_ids(history_records: list[dict]) -> set[str]:
            """Grab instance ids from history payload via regex to work across providers."""
            ids: set[str] = set()
            pattern = re.compile(r"i-[0-9a-f]{17}")
            for rec in history_records or []:
                matches = pattern.findall(json.dumps(rec, default=str))
                ids.update(matches)
            return ids

        def _fetch_history() -> list[dict]:
            start_time = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(microsecond=0)
            if provider_api == "EC2Fleet":
                log.info(f"Calling describe_fleets to determine fleet type for {resource_id}")
                fleet_desc = None
                try:
                    fleet_resp = ec2_client.describe_fleets(FleetIds=[resource_id])
                    fleet_desc = (fleet_resp.get("Fleets") or [None])[0]
                except ClientError as exc:
                    log.warning("describe_fleets failed for %s: %s", resource_id, exc)

                def _build_event_description(instance: dict) -> str:
                    payload = {
                        "instanceType": instance.get("InstanceType"),
                        "image": instance.get("ImageId"),
                        "productDescription": instance.get("InstanceLifecycle") or "on-demand",
                        "availabilityZone": (instance.get("Placement") or {}).get(
                            "AvailabilityZone"
                        ),
                    }
                    try:
                        return json.dumps(payload)
                    except Exception:
                        return str(payload)

                def _collect_instant_history(fleet_info: dict | None) -> list[dict]:
                    """Build history for instant fleets by walking instances."""

                    def _collect_fleet_instance_ids() -> list[str]:
                        ids: list[str] = []
                        # Prefer IDs from fleet description if present
                        for inst_entry in (fleet_info or {}).get("Instances") or []:
                            iid = inst_entry.get("InstanceIds") or []
                            if isinstance(iid, list):
                                ids.extend([x for x in iid if x])
                        # Fallback to request DB if fleet description lacks instances
                        if not ids and target_ids:
                            ids.extend(sorted(target_ids))
                        # As a last resort, ask AWS directly
                        if not ids:
                            try:
                                inst_resp = ec2_client.describe_fleet_instances(FleetId=resource_id)
                                for entry in inst_resp.get("ActiveInstances", []) or []:
                                    iid = entry.get("InstanceId")
                                    if iid:
                                        ids.append(iid)
                            except ClientError as exc:
                                log.warning(
                                    "describe_fleet_instances failed for %s: %s", resource_id, exc
                                )
                        return sorted(set(ids))

                    instance_ids = _collect_fleet_instance_ids()
                    events: list[dict] = []

                    created_at = (fleet_info or {}).get("CreateTime") or datetime.now(timezone.utc)
                    if created_at and created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=timezone.utc)
                    events.append(
                        {
                            "EventInformation": {"EventSubType": "submitted"},
                            "EventType": "fleetRequestChange",
                            "Timestamp": created_at,
                        }
                    )

                    # Batch describe instances to get launch timestamps
                    for start_idx in range(0, len(instance_ids), 100):
                        chunk = instance_ids[start_idx : start_idx + 100]
                        try:
                            resp = ec2_client.describe_instances(InstanceIds=chunk)
                        except ClientError as exc:
                            log.warning("describe_instances failed for chunk %s: %s", chunk, exc)
                            continue

                        for reservation in resp.get("Reservations", []):
                            for inst in reservation.get("Instances", []):
                                iid = inst.get("InstanceId")
                                if not iid:
                                    continue
                                launch_time = inst.get("LaunchTime") or datetime.now(timezone.utc)
                                if launch_time.tzinfo is None:
                                    launch_time = launch_time.replace(tzinfo=timezone.utc)
                                events.append(
                                    {
                                        "EventInformation": {
                                            "EventDescription": _build_event_description(inst),
                                            "EventSubType": "launched",
                                            "InstanceId": iid,
                                        },
                                        "EventType": "instanceChange",
                                        "Timestamp": launch_time,
                                    }
                                )

                    return events

                fleet_type = (
                    (fleet_desc or {}).get("FleetType") or (fleet_desc or {}).get("Type") or ""
                ).lower()
                if fleet_type == "instant":
                    log.info(
                        "Fleet %s is instant; building history from instance launches", resource_id
                    )
                    return _collect_instant_history(fleet_desc)

                log.info(f"Calling describe_fleet_history for {resource_id}")
                history_records: list[dict] = []
                next_token: str | None = None
                while True:
                    params = {
                        "FleetId": resource_id,
                        "StartTime": start_time,
                        "MaxResults": 1000,
                    }
                    if next_token:
                        params["NextToken"] = next_token

                    try:
                        response = ec2_client.describe_fleet_history(**params)
                    except ClientError as exc:
                        message = str(exc).lower()
                        if (
                            exc.response["Error"]["Code"] == "InvalidParameter"
                            and "instant fleet" in message
                        ):
                            log.info(
                                "describe_fleet_history not supported for instant fleet %s; synthesizing events",
                                resource_id,
                            )
                            return _collect_instant_history(fleet_desc)
                        raise

                    history_records.extend(response.get("HistoryRecords", []))
                    next_token = response.get("NextToken")
                    if not next_token:
                        break
                return history_records
            if provider_api == "SpotFleet":
                log.info(f"Calling describe_spot_fleet_request_history for {resource_id}")
                history_records: list[dict] = []
                next_token: str | None = None
                while True:
                    params = {
                        "SpotFleetRequestId": resource_id,
                        "StartTime": start_time,
                        "MaxResults": 1000,
                    }
                    if next_token:
                        params["NextToken"] = next_token

                    response = ec2_client.describe_spot_fleet_request_history(**params)
                    history_records.extend(response.get("HistoryRecords", []))
                    next_token = response.get("NextToken")
                    if not next_token:
                        break
                return history_records
            if provider_api == "ASG":
                log.info(f"Calling describe_scaling_activities for {resource_id}")
                history_records: list[dict] = []
                next_token: str | None = None
                while True:
                    params = {
                        "AutoScalingGroupName": resource_id,
                        "MaxRecords": 100,
                    }
                    if next_token:
                        params["NextToken"] = next_token

                    response = asg_client.describe_scaling_activities(**params)
                    history_records.extend(response.get("Activities", []))
                    next_token = response.get("NextToken")
                    if not next_token:
                        break
                return history_records
            log.warning(f"Unknown provider API: {provider_api}, skipping history capture")
            return []

        target_ids = _load_db_instance_ids()

        attempts = 0
        max_attempts = 2
        history_records: list[dict] = []

        while attempts < max_attempts:
            attempts += 1
            history_records = _fetch_history()
            history_ids = _extract_instance_ids(history_records)

            log.info(
                "History capture attempt %s/%s: %s records, %s instance ids (db has %s)",
                attempts,
                max_attempts,
                len(history_records),
                len(history_ids),
                len(target_ids),
            )

            # If we have all DB instances or DB is empty, stop retrying
            if not target_ids or len(history_ids) >= len(target_ids):
                break

            if attempts < max_attempts:
                log.warning(
                    "History missing %s instances (expected %s, got %s); sleeping 30s before retry",
                    len(target_ids) - len(history_ids),
                    len(target_ids),
                    len(history_ids),
                )
                time.sleep(30)

        # If history still misses some instances, synthesize records from live EC2 data
        missing_ids = sorted(target_ids - history_ids) if target_ids else []
        if missing_ids:
            log.warning(
                "History still missing %s instances after retries; synthesizing records",
                len(missing_ids),
            )
            synthetic_records: list[dict] = []

            # Describe instances in batches to get launch time and type
            for start in range(0, len(missing_ids), 100):
                chunk = missing_ids[start : start + 100]
                try:
                    resp = ec2_client.describe_instances(InstanceIds=chunk)
                except ClientError as exc:
                    log.warning("describe_instances failed for chunk %s: %s", chunk, exc)
                    continue

                for reservation in resp.get("Reservations", []):
                    for inst in reservation.get("Instances", []):
                        iid = inst.get("InstanceId")
                        if not iid:
                            continue
                        launch_time = inst.get("LaunchTime")
                        instance_type = inst.get("InstanceType")
                        # Normalize launch_time to datetime with tzinfo for formatting
                        ts = launch_time
                        if ts and ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        if not ts:
                            ts = datetime.now(timezone.utc)
                        cause_ts = ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

                        if provider_api == "ASG":
                            synthetic_records.append(
                                {
                                    "ActivityId": f"synthetic-{iid}",
                                    "Description": f"Launching a new EC2 instance: {iid}",
                                    "Cause": f"At {cause_ts} a user request created an AutoScalingGroup named {resource_id} using LaunchConfiguration.",
                                    "StartTime": ts,
                                    "EndTime": ts,
                                    "StatusCode": "Successful",
                                    "Details": {
                                        "InstanceId": iid,
                                        "InstanceType": instance_type,
                                        "AutoScalingGroupName": resource_id,
                                    },
                                }
                            )
                        elif provider_api in ["EC2Fleet", "SpotFleet"]:
                            synthetic_records.append(
                                {
                                    "EventType": "instanceChange",
                                    "EventInformation": {
                                        "EventSubType": "launched",
                                        "InstanceId": iid,
                                        "InstanceType": instance_type,
                                    },
                                    "Timestamp": launch_time,
                                }
                            )
                        else:
                            # Generic fallback
                            synthetic_records.append(
                                {
                                    "InstanceId": iid,
                                    "InstanceType": instance_type,
                                    "Timestamp": launch_time,
                                    "EventType": "launched",
                                }
                            )

            if synthetic_records:
                history_records.extend(synthetic_records)
                log.info(
                    "Added %s synthetic history records for missing instances",
                    len(synthetic_records),
                )

        # For ASG, append an explicit "submitted" record using the ASG creation time so downstream
        # tooling can compute request_time similarly to fleet history.
        if provider_api == "ASG":
            try:
                response = asg_client.describe_auto_scaling_groups(
                    AutoScalingGroupNames=[resource_id]
                )
                groups = response.get("AutoScalingGroups", [])
                created_at: datetime | None = groups[0].get("CreatedTime") if groups else None
                if created_at:
                    submission_event = {
                        "EventInformation": {"EventSubType": "submitted"},
                        "EventType": "fleetRequestChange",
                        "Timestamp": created_at,
                    }
                    history_records.insert(0, submission_event)
                    log.info(
                        "Inserted ASG creation event with timestamp %s for resource %s",
                        created_at,
                        resource_id,
                    )
                else:
                    log.warning(
                        "ASG creation time not found for %s; skipping submitted marker", resource_id
                    )
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("Unable to fetch ASG creation time for %s: %s", resource_id, exc)

        history_data = {
            "resource_id": resource_id,
            "provider_api": provider_api,
            "history": history_records,
        }

        log.info(f"Writing history to file: {history_file}")
        with open(history_file, "w") as f:
            json.dump(history_data, f, indent=2, default=str)
        log.info(f"✅ Resource history saved successfully to: {history_file}")
        log.info(f"File size: {os.path.getsize(history_file)} bytes")

    except Exception as exc:
        log.error(f"❌ Failed to capture resource history: {exc}", exc_info=True)
        try:
            failure_payload = {
                "resource_id": resource_id,
                "provider_api": provider_api,
                "error": str(exc),
            }
            with open(history_file, "w") as f:
                json.dump(failure_payload, f, indent=2, default=str)
            log.info("Saved failure details to history file despite error")
        except Exception as write_exc:
            log.warning("Unable to write failure details for history capture: %s", write_exc)


def _terminate_backing_resource(resource_id: str, provider_api: str) -> None:
    """Terminate the backing resource and wait for completion."""
    try:
        if provider_api == "EC2Fleet":
            log.info(f"Deleting EC2Fleet: {resource_id}")
            ec2_client.delete_fleets(FleetIds=[resource_id], TerminateInstances=True)
            _wait_for_fleet_deletion(resource_id)

        elif provider_api == "SpotFleet":
            log.info(f"Cancelling SpotFleet: {resource_id}")
            ec2_client.cancel_spot_fleet_requests(
                SpotFleetRequestIds=[resource_id], TerminateInstances=True
            )
            _wait_for_spot_fleet_deletion(resource_id)

        elif provider_api == "ASG":
            log.info(f"Checking Auto Scaling Group status: {resource_id}")
            try:
                response = asg_client.describe_auto_scaling_groups(
                    AutoScalingGroupNames=[resource_id]
                )
                groups = response.get("AutoScalingGroups", [])

                if not groups:
                    log.info(f"ASG {resource_id} does not exist, skipping deletion")
                    return

                asg_status = groups[0].get("Status")
                if asg_status == "Delete in progress":
                    log.info(f"ASG {resource_id} already being deleted, waiting for completion")
                    _wait_for_asg_deletion(resource_id)
                    return

                log.info(f"Deleting Auto Scaling Group: {resource_id}")
                asg_client.delete_auto_scaling_group(
                    AutoScalingGroupName=resource_id, ForceDelete=True
                )
                _wait_for_asg_deletion(resource_id)
            except ClientError as exc:
                if exc.response["Error"]["Code"] in [
                    "ValidationError",
                    "ResourceNotFoundException",
                ]:
                    log.info(f"ASG {resource_id} does not exist")
                    return
                raise

        log.info(f"Backing resource {resource_id} terminated successfully")

    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        if error_code in [
            "InvalidFleetId.NotFound",
            "InvalidSpotFleetRequestId.NotFound",
            "ValidationError",
            "ResourceNotFoundException",
        ]:
            log.info(f"Resource {resource_id} already deleted (error: {error_code})")
        else:
            log.warning(f"Failed to terminate backing resource {resource_id}: {exc}")
    except Exception as exc:
        log.warning(f"Failed to terminate backing resource {resource_id}: {exc}")


def _wait_for_fleet_deletion(fleet_id: str, timeout: int = 300) -> None:
    """Wait for EC2Fleet to be deleted."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            response = ec2_client.describe_fleets(FleetIds=[fleet_id])
            fleets = response.get("Fleets", [])
            if not fleets or fleets[0]["FleetState"] in ["deleted_terminating", "deleted_running"]:
                log.info(f"Fleet {fleet_id} is being deleted")
                return
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "InvalidFleetId.NotFound":
                log.info(f"Fleet {fleet_id} deleted")
                return
        time.sleep(10)
    log.warning(f"Fleet {fleet_id} deletion timeout")


def _wait_for_spot_fleet_deletion(fleet_id: str, timeout: int = 300) -> None:
    """Wait for SpotFleet to be cancelled."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            response = ec2_client.describe_spot_fleet_requests(SpotFleetRequestIds=[fleet_id])
            requests = response.get("SpotFleetRequestConfigs", [])
            if not requests or requests[0]["SpotFleetRequestState"] in [
                "cancelled_terminating",
                "cancelled_running",
            ]:
                log.info(f"SpotFleet {fleet_id} is being cancelled")
                return
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "InvalidSpotFleetRequestId.NotFound":
                log.info(f"SpotFleet {fleet_id} cancelled")
                return
        time.sleep(10)
    log.warning(f"SpotFleet {fleet_id} cancellation timeout")


def _wait_for_asg_deletion(asg_name: str, timeout: int = 300) -> None:
    """Wait for Auto Scaling Group to be deleted."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            response = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
            groups = response.get("AutoScalingGroups", [])
            if not groups:
                log.info(f"ASG {asg_name} deleted")
                return
        except ClientError:
            log.info(f"ASG {asg_name} deleted")
            return
        time.sleep(10)
    log.warning(f"ASG {asg_name} deletion timeout")


def _capture_history_if_enabled(resource_id: str | None, provider_api: str | None, test_case: dict):
    """Common history capture helper used in timeout/finally blocks."""
    if scenarios_rest_api.CAPTURE_RESOURCE_HISTORY:
        log.info("3.1a: Capturing resource history before termination")
        if resource_id and provider_api != "RunInstances":
            log.info(f"Calling _capture_resource_history for {provider_api} resource {resource_id}")
            _capture_resource_history(resource_id, provider_api, test_case["test_name"])
        else:
            if not resource_id:
                log.warning("Skipping history capture: resource_id is None")
            if provider_api == "RunInstances":
                log.info("Skipping history capture: RunInstances has no backing resource")
    else:
        log.info("Skipping history capture: CAPTURE_RESOURCE_HISTORY is False")


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
    provider_api = (
        template_json.get("provider_api")
        or test_case.get("overrides", {}).get("providerApi")
        or "EC2Fleet"
    )
    log.info(f"Provider API for request: {provider_api}")

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
        f"2.1: Polling request status (timeout: {REST_TIMEOUTS['request_fulfillment_timeout']}s)"
    )
    status_response = None
    try:
        status_response = _wait_for_request_completion_rest(
            rest_api_client,
            request_id,
            timeout=REST_TIMEOUTS["request_fulfillment_timeout"],
            expected_capacity=test_case["capacity_to_request"],
            provider_api=provider_api,
        )
    except RequestTimeoutError as exc:
        log.error(
            "Request %s timed out after %ss",
            request_id,
            REST_TIMEOUTS["request_fulfillment_timeout"],
        )
        status_response = getattr(exc, "status_response", None)
        if status_response:
            log.info("Using last polled status from timeout for history capture")
        else:
            try:
                status_response = rest_api_client.get_request_status(request_id, long=True)
                log.info("Retrieved latest status after timeout for history capture")
            except Exception as fetch_exc:
                log.warning("Could not fetch status after timeout: %s", fetch_exc)
                status_response = None
    finally:
        history_resource_id = None
        if status_response:
            requests_list = status_response.get("requests") or []
            first_request = requests_list[0] if requests_list else {}
            machines = first_request.get("machines") or []
            machine_ids_tmp = [m.get("machine_id") for m in machines if m.get("machine_id")]
            if machine_ids_tmp:
                history_resource_id = _get_resource_id_from_instance(
                    machine_ids_tmp[0], provider_api
                )
            elif isinstance(first_request, dict):
                history_resource_id = first_request.get("resource_id") or first_request.get(
                    "provider_resource_id"
                )
        _capture_history_if_enabled(history_resource_id, provider_api, test_case)

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

    # Determine resource ID
    resource_id = None
    if machine_ids:
        resource_id = _get_resource_id_from_instance(machine_ids[0], provider_api)
        log.info(f"Resource ID extracted: {resource_id}")

    # 3.1a: Retrieve resource history (controlled by global CAPTURE_RESOURCE_HISTORY flag)
    log.info(f"CAPTURE_RESOURCE_HISTORY flag: {scenarios_rest_api.CAPTURE_RESOURCE_HISTORY}")
    log.info(f"Provider API: {provider_api}")
    log.info(f"Resource ID: {resource_id}")

    _capture_history_if_enabled(resource_id, provider_api, test_case)

    # time.sleep(1000000)
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

    # 3.6: Terminate backing resource
    # KBG TODO: technically previous step of terminating all instances should result
    # in resource termination. However, there is a race condition that prevents it at the moment.
    # see issue 76.
    log.info("3.6: Terminating backing resource")
    if resource_id and provider_api != "RunInstances":
        _terminate_backing_resource(resource_id, provider_api)

    # 3.7: Final verification
    log.info("3.7: Verifying complete resource cleanup")
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
