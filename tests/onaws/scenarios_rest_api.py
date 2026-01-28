"""REST API-specific test scenarios for AWS integration tests."""

import itertools
from typing import Any, Dict, List

from tests.onaws.scenarios import (
    CUSTOM_TEST_CASES as CUSTOM_TEST_CASES,
    DEFAULT_ATTRIBUTE_COMBINATIONS as _DEFAULT_ATTRIBUTE_COMBINATIONS,
)

REST_API_RUN_DEFAULT_COMBINATIONS = False
REST_API_RUN_CUSTOM_CASES = False

DEFAULT_ATTRIBUTE_COMBINATIONS = (
    _DEFAULT_ATTRIBUTE_COMBINATIONS if REST_API_RUN_DEFAULT_COMBINATIONS else []
)
CUSTOM_TEST_CASES = CUSTOM_TEST_CASES if REST_API_RUN_CUSTOM_CASES else []


# REST API specific configuration
REST_API_BASE_URL = "http://localhost:8000"  # versioned in RestApiClient
REST_API_PREFIX = "/api/v1"
REST_API_METRICS_CONFIG: dict[str, Any] | None = {
    "metrics_enabled": True,
    "metrics_dir": None,  # Filled by TemplateProcessor per test
    "metrics_interval": 20,
    "trace_enabled": True,
    "trace_buffer_size": 1000,
    "trace_file_max_size_mb": 10,
    "aws_metrics": {
        "aws_metrics_enabled": True,
        "sample_rate": 1.0,
        "monitored_services": [],
        "monitored_operations": [],
        "track_payload_sizes": True,
    },
}

# Resource history capture configuration
# Global flag to enable/disable resource history capture for all tests
CAPTURE_RESOURCE_HISTORY = True  # Set to False to disable globally
# Resource history is captured by default for EC2Fleet, SpotFleet, and ASG
# History is saved to {METRICS_DIR}/{test_name}_history.json

# Server/runtime settings for REST API tests
REST_API_SERVER = {
    "host": "0.0.0.0",
    "port": 8000,
    "start_probe_timeout": 2,  # timeout for each health probe during startup
    "start_probe_interval": 1,  # seconds between health probes during startup
    "start_capture_timeout": 5,  # timeout when capturing stdout/stderr on failed start
    "stop_wait_timeout": 10,  # graceful stop wait
    "stop_kill_timeout": 10,  # kill wait after terminate timeout
}

# Centralized timeouts/constants for REST API tests
REST_API_TIMEOUTS = {
    "rest_api_timeout": 60,  # Per-request HTTP timeout for REST calls
    "rest_api_retry_attempts": 3,  # Retry attempts for REST calls (when implemented)
    "server_start": 30,  # How long to wait for the server to start responding to health
    "health_check": 5,  # Health endpoint timeout
    "templates": 60,  # Timeout for fetching templates
    "request_status_poll_interval": 5,  # Poll interval for request status
    "request_fulfillment_timeout": 600,  # Max wait for request fulfillment
    "return_status_poll_interval": 5,  # Poll interval for return status
    "return_status_timeout": 100,  # Max wait for return completion
    "server_shutdown_check_interval": 2,  # Interval between shutdown health probes
    "server_shutdown_attempts": 5,  # How many shutdown probes to attempt
    "graceful_termination_timeout": 180,  # Max time to wait for graceful termination
    "cleanup_wait_timeout": 300,  # Max time to wait for cleanup after graceful fails
    "termination_poll_interval": 30,  # Poll interval while waiting for termination
    "shutdown_check_sleep": 1,  # Sleep between shutdown probes
    "capacity_change_timeout_fleet": 60,  # Max wait for capacity change on fleets
    "capacity_change_timeout_asg": 120,  # Max wait for capacity change on ASG
}

# Standard VM mix for spot scenarios
SPOT_VM_TYPES = {
    "t2.micro": 1,
    "t2.small": 2,
    "t2.nano": 1,
    "t3.micro": 1,
    "t3.small": 2,
    "t3.nano": 1,
}


# Large-scale R-series instance types for high-capacity scenarios
LARGE_SCALE_VM_TYPES_1 = {
    "r5.12xlarge": 3,
    "r5.16xlarge": 4,
    "r5.4xlarge": 1,
    "r5.8xlarge": 2,
    "r5a.12xlarge": 3,
    "r5a.4xlarge": 1,
    "r5a.8xlarge": 2,
    "r5ad.12xlarge": 3,
    "r5ad.4xlarge": 1,
    "r5ad.8xlarge": 2,
    "r5b.12xlarge": 3,
    "r5b.16xlarge": 4,
    "r5b.4xlarge": 1,
    "r5b.8xlarge": 2,
    "r5d.12xlarge": 3,
    "r5d.16xlarge": 4,
    "r5d.4xlarge": 1,
    "r5d.8xlarge": 2,
    "r5n.12xlarge": 3,
    "r5n.16xlarge": 4,
    "r5n.4xlarge": 1,
    "r5n.8xlarge": 2,
    "r6a.12xlarge": 3,
    "r6a.4xlarge": 1,
    "r6a.8xlarge": 2,
    "r6i.12xlarge": 3,
    "r6i.16xlarge": 4,
    "r6i.4xlarge": 1,
    "r6i.8xlarge": 2,
    "r7a.12xlarge": 3,
    "r7a.4xlarge": 1,
    "r7a.8xlarge": 2,
    "r7i.12xlarge": 3,
    "r7i.16xlarge": 4,
    "r7i.4xlarge": 1,
    "r7i.8xlarge": 2,
}


LARGE_SCALE_CAPACITIES = [100]


def _make_large_scale_tests() -> List[Dict[str, Any]]:
    """Parameterize large-scale scenarios across multiple capacities."""
    abis_requirements = {
        "VCpuCount": {"Min": 1, "Max": 128},
        "MemoryMiB": {"Min": 1024, "Max": 257000},
    }

    def spot(cap: int) -> Dict[str, Any]:
        return {
            "test_name": f"hostfactory.SpotFleet.request.ABIS.SIZE.{cap}",
            "template_id": "SpotFleetRequest",
            "capacity_to_request": cap,
            "awsprov_base_template": "awsprov_templates.base.json",
            "overrides": {
                "providerApi": "SpotFleet",
                "fleetType": "request",
                "scheduler": "hostfactory",
                "abisInstanceRequirements": abis_requirements,
                "allocationStrategy": "priceCapacityOptimized",
            },
        }

    def ec2_request(cap: int) -> Dict[str, Any]:
        return {
            "test_name": f"hostfactory.EC2Fleet.request.ABIS.SIZE.{cap}",
            "template_id": "EC2FleetRequest",
            "capacity_to_request": cap,
            "awsprov_base_template": "awsprov_templates.base.json",
            "overrides": {
                "providerApi": "EC2Fleet",
                "scheduler": "hostfactory",
                "fleetType": "request",
                "abisInstanceRequirements": abis_requirements,
                "allocationStrategy": "priceCapacityOptimized",
            },
        }

    def ec2_instant(cap: int) -> Dict[str, Any]:
        return {
            "test_name": f"hostfactory.EC2Fleet.intant.ABIS.SIZE.{cap}",
            "template_id": "EC2FleetRequest",
            "capacity_to_request": cap,
            "awsprov_base_template": "awsprov_templates.base.json",
            "overrides": {
                "providerApi": "EC2Fleet",
                "scheduler": "hostfactory",
                "fleetType": "instant",
                "abisInstanceRequirements": abis_requirements,
                "allocationStrategy": "priceCapacityOptimized",
            },
        }

    def asg(cap: int) -> Dict[str, Any]:
        return {
            "test_name": f"hostfactory.ASG.ABIS.SIZE.{cap}",
            "template_id": "ASG",
            "capacity_to_request": cap,
            "awsprov_base_template": "awsprov_templates.base.json",
            "overrides": {
                "providerApi": "ASG",
                "scheduler": "hostfactory",
                "abisInstanceRequirements": abis_requirements,
                "allocationStrategy": "priceCapacityOptimized",
            },
        }

    scenarios: List[Dict[str, Any]] = []
    for cap in LARGE_SCALE_CAPACITIES:
        scenarios.extend([spot(cap), ec2_request(cap), ec2_instant(cap), asg(cap)])
    return scenarios


def generate_scenarios_from_attributes(
    attribute_combinations: Dict[str, List[Any]],
    base_template: Dict[str, Any] = None,
) -> List[Dict[str, Any]]:
    """
    Generate test scenarios from all combinations of provided attributes.

    Args:
        attribute_combinations: Dictionary where keys are attribute names and values are lists of possible values
        base_template: Base template to use for all generated scenarios

    Returns:
        List of test scenario dictionaries
    """
    if base_template is None:
        base_template = {
            "template_id": "BASE",
            "capacity_to_request": 4,
            "awsprov_base_template": "awsprov_templates.base.json",
        }

    scenarios = []

    # Get all attribute names and their possible values
    attribute_names = list(attribute_combinations.keys())
    attribute_values = list(attribute_combinations.values())

    # Generate all combinations
    for combination in itertools.product(*attribute_values):
        # Create the overrides dictionary from the combination
        overrides = dict(zip(attribute_names, combination))

        # Generate test name: {scheduler}.{providerApi}.{fleetType}.{priceType}
        name_parts = []
        for attr_name, attr_value in overrides.items():
            if attr_name == "scheduler":
                name_parts.insert(0, str(attr_value))
            elif attr_name == "providerApi":
                name_parts.append(str(attr_value))
            elif attr_name == "fleetType":
                name_parts.append(str(attr_value).title())
            else:
                name_parts.append(str(attr_value))
        test_name = ".".join(name_parts)

        # For spot priceType, add multiple vmTypes to improve capacity placement
        provider_api = overrides.get("providerApi")
        price_type = overrides.get("priceType")
        fleet_type = overrides.get("fleetType")

        if (
            price_type == "spot"
            and provider_api in ("EC2Fleet", "SpotFleet", "ASG")
            and "vmTypes" not in overrides
        ):
            overrides["vmTypes"] = SPOT_VM_TYPES

        # Ensure maintain fleets/ASGs have enough capacity for partial return tests
        if provider_api in ("EC2Fleet", "SpotFleet") and str(fleet_type).lower() == "maintain":
            scenario_capacity = overrides.get(
                "capacity_to_request", base_template["capacity_to_request"]
            )
            if scenario_capacity < 4:
                overrides["capacity_to_request"] = 4
        if provider_api == "ASG":
            scenario_capacity = overrides.get(
                "capacity_to_request", base_template["capacity_to_request"]
            )
            if scenario_capacity < 4:
                overrides["capacity_to_request"] = 4

        # Create the scenario
        scenario = base_template.copy()
        scenario.update({"test_name": test_name, "overrides": overrides})

        scenarios.append(scenario)

    return scenarios


def get_rest_api_test_cases() -> List[Dict[str, Any]]:
    """
    Generate test cases for REST API testing.

    Returns:
        List of test scenario dictionaries with REST API configuration
    """
    scenarios = []

    # Generate scenarios from default attribute combinations
    for combination_config in DEFAULT_ATTRIBUTE_COMBINATIONS:
        scenarios.extend(generate_scenarios_from_attributes(combination_config))

    # Add custom scenarios from shared onaws definitions
    scenarios.extend(CUSTOM_TEST_CASES)

    scenarios.extend(_make_large_scale_tests())

    # Add REST API specific metadata to all scenarios
    for scenario in scenarios:
        scenario["api_base_url"] = REST_API_BASE_URL
        scenario["api_timeout"] = REST_API_TIMEOUTS["rest_api_timeout"]
        scenario["api_prefix"] = REST_API_PREFIX
        if REST_API_METRICS_CONFIG:
            scenario["metrics_config"] = REST_API_METRICS_CONFIG

    return scenarios


def get_test_case_by_name(test_name: str) -> Dict[str, Any]:
    """
    Get a specific test case by test name.

    Args:
        test_name: Name of the test case to retrieve

    Returns:
        Test case dictionary with REST API configuration
    """
    test_cases = get_rest_api_test_cases()
    for test_case in test_cases:
        if test_case["test_name"] == test_name:
            return test_case

    # Return a default test case if not found
    return {
        "test_name": test_name,
        "template_id": test_name,
        "capacity_to_request": 2,
        "overrides": {},
        "api_base_url": REST_API_BASE_URL,
        "api_timeout": REST_API_TIMEOUTS["rest_api_timeout"],
        "api_prefix": REST_API_PREFIX,
    }


# Example: Control resource history capture globally
# Set CAPTURE_RESOURCE_HISTORY = True (default) to enable for all tests
# Set CAPTURE_RESOURCE_HISTORY = False to disable for all tests
