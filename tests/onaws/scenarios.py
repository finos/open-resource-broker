import itertools
import os
from typing import Any, Dict, List

# Standard VM mix used for spot scenarios to improve placement success and avoid
# single-instance-type shortages.
SPOT_VM_TYPES = {
    "t2.micro": 1,
    "t2.small": 2,
    "t2.nano": 1,
    "t3.micro": 1,
    "t3.small": 2,
    "t3.nano": 1,
}

# Central flag to enable/disable partial return scenarios
RUN_PARTIAL_RETURN_TESTS = os.environ.get("RUN_PARTIAL_RETURN_TESTS", "1").lower() in (
    "1",
    "true",
    "yes",
)

# Enable to verify ABIS on the created resource (fleet/ASG) via AWS APIs
VERIFY_ABIS = os.environ.get("VERIFY_ABIS", "1") in ("1", "true", "True")

# Global default attribute combinations
DEFAULT_ATTRIBUTE_COMBINATIONS = [
    {
        "providerApi": ["EC2Fleet"],
        "fleetType": ["request", "instant", "maintain"],
        "priceType": ["ondemand", "spot"],
        "scheduler": ["default", "hostfactory"],
    },
    {
        "providerApi": ["ASG"],
        "priceType": ["ondemand", "spot"],
        "scheduler": ["default", "hostfactory"],
    },
    {
        "providerApi": ["RunInstances"],
        "priceType": ["ondemand"],
        "scheduler": ["default", "hostfactory"],
    },
    {
        "providerApi": ["SpotFleet"],
        "fleetType": ["request", "maintain"],
        "priceType": ["ondemand", "spot"],
        "scheduler": ["default", "hostfactory"],
    },
    # {
    #     "providerApi": ["ASG"],
    #     "priceType": ["spot"],
    #     "scheduler": ["default"],
    # },
    # {
    #     "providerApi": ["EC2Fleet"],
    #     "fleetType": ["maintain"],
    #     "priceType": ["ondemand", "spot"],
    #     "scheduler": ["default", "hostfactory"],
    # },
    # { INTENTIONALLY NOT SUPPORTED
    #     "providerApi": ["RunInstances"],
    #     "priceType": ["spot"]
    # },
]


def generate_scenarios_from_attributes(
    attribute_combinations: Dict[str, List[Any]],
    base_template: Dict[str, Any] = None,
    naming_template: str = None,
) -> List[Dict[str, Any]]:
    """
    Generate test scenarios from all combinations of provided attributes.

    Args:
        attribute_combinations: Dictionary where keys are attribute names and values are lists of possible values
        base_template: Base template to use for all generated scenarios
        naming_template: Template for generating test names (uses attribute values)

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

        # Generate test name
        if naming_template:
            test_name = naming_template.format(**overrides)
        else:
            # Default naming: concatenate attribute values with dots as separators
            name_parts = []
            for attr_name, attr_value in overrides.items():
                if attr_name == "scheduler":
                    # Add scheduler at the beginning
                    name_parts.insert(0, str(attr_value))
                elif attr_name == "providerApi":
                    name_parts.append(str(attr_value))
                elif attr_name == "fleetType":
                    name_parts.append(str(attr_value).title())
                else:
                    name_parts.append(str(attr_value))
            test_name = ".".join(name_parts)

        # For spot priceType we supply multiple vmTypes to improve capacity placement.
        provider_api = overrides.get("providerApi")
        price_type = overrides.get("priceType")
        fleet_type = overrides.get("fleetType")
        if (
            price_type == "spot"
            and provider_api in ("EC2Fleet", "SpotFleet", "ASG")
            and "vmTypes" not in overrides
        ):
            overrides["vmTypes"] = SPOT_VM_TYPES

        # Ensure partial-return scenarios have enough capacity to terminate one host and
        # still have machines running. Maintain fleets/ASGs need >=4 requested units.
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


def get_generated_test_cases(
    attribute_combinations: Dict[str, List[Any]] = None, apply_filters: bool = True
) -> List[Dict[str, Any]]:
    """
    Generate test cases from attribute combinations.
    This replaces the hardcoded scenarios with dynamically generated ones.

    Args:
        attribute_combinations: Dictionary of attributes and their possible values.
                              If None, uses DEFAULT_ATTRIBUTE_COMBINATIONS.
        apply_filters: Whether to apply business rule filters to remove invalid combinations.
    """
    # Use provided combinations or default
    if attribute_combinations is None:
        attribute_combinations = DEFAULT_ATTRIBUTE_COMBINATIONS

    # Generate scenarios
    generated_scenarios = generate_scenarios_from_attributes(attribute_combinations)

    if not apply_filters:
        return generated_scenarios

    # Filter out invalid combinations
    valid_scenarios = []
    for scenario in generated_scenarios:
        overrides = scenario["overrides"]
        provider_api = overrides.get("providerApi")
        fleet_type = overrides.get("fleetType")

        # Apply business rules to filter invalid combinations
        if provider_api == "SpotFleet" and fleet_type == "instant":
            # SpotFleet doesn't support instant type, skip this combination
            continue
        elif provider_api == "RunInstances" and fleet_type == "request":
            # RunInstances doesn't support request type, skip this combination
            continue
        elif provider_api == "ASG" and fleet_type == "request":
            # ASG doesn't support request type, skip this combination
            continue

        valid_scenarios.append(scenario)

    return valid_scenarios


def get_custom_test_cases() -> List[Dict[str, Any]]:
    """
    Define custom test cases that don't fit the standard attribute combinations.
    This allows for special cases and edge scenarios.
    """
    return [
        # SpotFleet with ABIS
        {
            "test_name": "hostfactory.SpotFleetRequest.ABIS",
            "template_id": "SpotFleetRequest",
            "capacity_to_request": 4,
            "awsprov_base_template": "awsprov_templates.base.json",
            "overrides": {
                "providerApi": "SpotFleet",
                "fleetType": "request",
                "scheduler": "hostfactory",
                "abisInstanceRequirements": {
                    "VCpuCount": {"Min": 1, "Max": 2},
                    "MemoryMiB": {"Min": 1024, "Max": 2048},
                },
            },
        },
        # EC2Fleet with ABIS
        {
            "test_name": "hostfactory.EC2FleetRequest.ABIS",
            "template_id": "EC2FleetRequest",
            "capacity_to_request": 4,
            "awsprov_base_template": "awsprov_templates.base.json",
            "overrides": {
                "providerApi": "EC2Fleet",
                "fleetType": "request",
                "scheduler": "hostfactory",
                "abisInstanceRequirements": {
                    "VCpuCount": {"Min": 1, "Max": 2},
                    "MemoryMiB": {"Min": 1024, "Max": 2048},
                },
            },
        },
        # ASG with ABIS
        {
            "test_name": "hostfactory.ASG.ABIS",
            "template_id": "ASG",
            "capacity_to_request": 2,
            "awsprov_base_template": "awsprov_templates.base.json",
            "overrides": {
                "providerApi": "ASG",
                "scheduler": "hostfactory",
                "abisInstanceRequirements": {
                    "VCpuCount": {"Min": 1, "Max": 2},
                    "MemoryMiB": {"Min": 1024, "Max": 2048},
                },
            },
        },
        ###############################################################################################################
        ###############################################################################################################
        ###############################################################################################################
        ###############################################################################################################
        # SpotFleet with multiTypes
        {
            "test_name": "hostfactory.SpotFleetRequest.MultiTypes",
            "template_id": "SpotFleetRequest",
            "capacity_to_request": 4,
            "awsprov_base_template": "awsprov_templates.base.json",
            "overrides": {
                "providerApi": "SpotFleet",
                "fleetType": "request",
                "scheduler": "hostfactory",
                "vmTypes": {
                    "t2.micro": 1,
                    "t2.small": 2,
                    "t2.medium": 4,
                    "t3.micro": 1,
                    "t3.small": 2,
                    "t3.medium": 4,
                },
            },
        },
        # EC2Fleet with multiTypes
        {
            "test_name": "hostfactory.EC2FleetRequest.MultiTypes",
            "template_id": "EC2FleetRequest",
            "capacity_to_request": 4,
            "awsprov_base_template": "awsprov_templates.base.json",
            "overrides": {
                "providerApi": "EC2Fleet",
                "fleetType": "request",
                "scheduler": "hostfactory",
                "vmTypes": {
                    "t2.micro": 1,
                    "t2.small": 2,
                    "t2.medium": 4,
                    "t3.micro": 1,
                    "t3.small": 2,
                    "t3.medium": 4,
                },
            },
        },
        # ASG with multiTypes
        {
            "test_name": "hostfactory.ASG.MultiTypes",
            "template_id": "ASG",
            "capacity_to_request": 4,
            "awsprov_base_template": "awsprov_templates.base.json",
            "overrides": {
                "providerApi": "ASG",
                "scheduler": "hostfactory",
                "vmTypes": {
                    "t2.micro": 1,
                    "t2.small": 2,
                    "t2.medium": 4,
                    "t3.micro": 1,
                    "t3.small": 2,
                    "t3.medium": 4,
                },
            },
        },
        # Mixed price 50/50 - EC2Fleet
        {
            "test_name": "hostfactory.EC2Fleet.Mixed50",
            "template_id": "EC2FleetRequest",
            "capacity_to_request": 4,
            "awsprov_base_template": "awsprov_templates.base.json",
            "overrides": {
                "providerApi": "EC2Fleet",
                "fleetType": "request",
                "scheduler": "hostfactory",
                "priceType": "heterogeneous",
                "percentOnDemand": 50,
                "vmTypes": {
                    "t2.micro": 1,
                    "t2.small": 2,
                    "t2.medium": 4,
                    "t3.micro": 1,
                    "t3.small": 2,
                    "t3.medium": 4,
                },
            },
        },
        # Mixed price 50/50 - SpotFleet
        {
            "test_name": "hostfactory.SpotFleet.Mixed50",
            "template_id": "SpotFleetRequest",
            "capacity_to_request": 4,
            "awsprov_base_template": "awsprov_templates.base.json",
            "overrides": {
                "providerApi": "SpotFleet",
                "fleetType": "request",
                "scheduler": "hostfactory",
                "priceType": "heterogeneous",
                "percentOnDemand": 50,
                "vmTypes": {
                    "t2.micro": 1,
                    "t2.small": 2,
                    "t2.medium": 4,
                    "t3.micro": 1,
                    "t3.small": 2,
                    "t3.medium": 4,
                },
            },
        },
        # Mixed price 50/50 - ASG
        {
            "test_name": "hostfactory.ASG.Mixed50",
            "template_id": "ASG",
            "capacity_to_request": 4,
            "awsprov_base_template": "awsprov_templates.base.json",
            "overrides": {
                "providerApi": "ASG",
                "scheduler": "hostfactory",
                "priceType": "heterogeneous",
                "percentOnDemand": 50,
                "vmTypes": {
                    "t2.micro": 1,
                    "t2.small": 2,
                    "t2.medium": 4,
                    "t3.micro": 1,
                    "t3.small": 2,
                    "t3.medium": 4,
                },
            },
        },
    ]


def get_test_case_by_name(test_name: str) -> Dict[str, Any]:
    """Get a specific test case by test name."""
    test_cases = get_test_cases()
    for test_case in test_cases:
        if test_case["test_name"] == test_name:
            return test_case

    # Return a default test case if not found
    return {
        "test_name": test_name,
        "template_id": test_name,
        "capacity_to_request": 2,
        "overrides": {},
    }


def add_custom_attribute_combinations(
    additional_attributes: Dict[str, List[Any]], base_template: Dict[str, Any] = None
) -> List[Dict[str, Any]]:
    """
    Generate additional scenarios with custom attribute combinations.
    Useful for extending the test matrix with new attributes.

    Args:
        additional_attributes: Additional attributes to combine
        base_template: Base template for the new scenarios

    Returns:
        List of additional test scenarios
    """
    return generate_scenarios_from_attributes(additional_attributes, base_template)


# Example usage for extending scenarios:
def get_extended_test_cases() -> List[Dict[str, Any]]:
    """
    Example of how to extend test cases with additional attribute combinations.
    """
    # Base scenarios
    scenarios = get_test_cases()

    # Add scenarios with additional attributes (example)
    # extended_attributes = {
    #     "providerApi": ["EC2Fleet"],
    #     "fleetType": ["instant"],
    #     "instanceType": ["t3.micro", "t3.small", "m5.large"]
    # }
    #
    # extended_scenarios = generate_scenarios_from_attributes(
    #     extended_attributes,
    #     naming_template="{providerApi}{fleetType}_{instanceType}"
    # )
    #
    # scenarios.extend(extended_scenarios)

    return scenarios


def get_test_cases() -> List[Dict[str, Any]]:
    """
    Get all test cases: both generated and custom.
    """
    # Combine generated and custom test cases
    all_scenarios = []

    # Add generated scenarios (set apply_filters=False to get all 12 combinations)
    for combination_config in DEFAULT_ATTRIBUTE_COMBINATIONS:
        all_scenarios.extend(get_generated_test_cases(combination_config, apply_filters=False))

    # Add custom scenarios
    all_scenarios.extend(get_custom_test_cases())

    return all_scenarios
