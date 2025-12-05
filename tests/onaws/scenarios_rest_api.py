"""REST API-specific test scenarios for AWS integration tests."""

import itertools
from typing import Any, Dict, List

# REST API specific configuration
REST_API_BASE_URL = "http://localhost:8000"  # versioned in RestApiClient
REST_API_PREFIX = "/api/v1"
REST_API_TIMEOUT = 10
REST_API_RETRY_ATTEMPTS = 3
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
    "server_start": 30,
    "health_check": 5,
    "templates": 60,
    "request_status_poll_interval": 5,
    "request_status_timeout": 300,
    "return_status_poll_interval": 5,
    "return_status_timeout": 300,
    "server_shutdown_check_interval": 2,
    "server_shutdown_attempts": 5,
    "graceful_termination_timeout": 180,
    "cleanup_wait_timeout": 300,
    "termination_poll_interval": 10,
    "shutdown_check_sleep": 1,
    "capacity_change_timeout_fleet": 60,
    "capacity_change_timeout_asg": 120,
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

# REST API test attribute combinations
DEFAULT_ATTRIBUTE_COMBINATIONS = [
    # {
    #     "providerApi": ["EC2Fleet"],
    #     "fleetType": ["request", "instant", "maintain"],
    #     "priceType": ["ondemand", "spot"],
    #     "scheduler": ["default", "hostfactory"],
    # },
    # {
    #     "providerApi": ["ASG"],
    #     "priceType": ["ondemand", "spot"],
    #     "scheduler": ["default", "hostfactory"],
    # },
    # {
    #     "providerApi": ["RunInstances"],
    #     "priceType": ["ondemand"],
    #     "scheduler": ["default", "hostfactory"],
    # },
    # {
    #     "providerApi": ["SpotFleet"],
    #     "fleetType": ["request", "maintain"],
    #     "priceType": ["ondemand", "spot"],
    #     "scheduler": ["default", "hostfactory"],
    # },
]


def get_custom_test_cases() -> List[Dict[str, Any]]:
    """
    Define custom test cases that don't fit the standard attribute combinations.
    This allows for special cases and edge scenarios.
    """
    return [
        # SpotFleet with ABIS
        {
            "test_name": "hostfactory.ASG.ABIS",
            "template_id": "ASG",
            "capacity_to_request": 100,
            "awsprov_base_template": "awsprov_templates.base.json",
            "overrides": {
                "providerApi": "ASG",
                "scheduler": "hostfactory",
                "abisInstanceRequirements": {
                    "VCpuCount": {"Min": 1, "Max": 128},
                    "MemoryMiB": {"Min": 1024, "Max": 257000},
                },
            },
        }
    ]


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
    scenarios.extend(get_custom_test_cases())

    # Add REST API specific metadata to all scenarios
    for scenario in scenarios:
        scenario["api_base_url"] = REST_API_BASE_URL
        scenario["api_timeout"] = REST_API_TIMEOUT
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
        "api_timeout": REST_API_TIMEOUT,
        "api_prefix": REST_API_PREFIX,
    }
