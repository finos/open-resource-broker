import json
import logging
import os
import time
from typing import Optional

import boto3
import pytest
from botocore.exceptions import ClientError
from jsonschema import ValidationError, validate as validate_json_schema

from hfmock import HostFactoryMock
from tests.onaws import plugin_io_schemas, scenarios
from tests.onaws.parse_output import parse_and_print_output
from tests.onaws.template_processor import TemplateProcessor

pytestmark = [  # Apply default markers to every test in this module
    pytest.mark.manual_aws,
    pytest.mark.aws,
]

# Set environment variables for local development
os.environ["USE_LOCAL_DEV"] = "1"
os.environ.setdefault("HF_LOGDIR", "./logs")  # Set log directory to avoid permission issues
os.environ.setdefault("AWS_PROVIDER_LOG_DIR", "./logss")
os.environ["LOG_DESTINATION"] = "file"


_boto_session = boto3.session.Session()
_ec2_region = (
    os.environ.get("AWS_REGION")
    or os.environ.get("AWS_DEFAULT_REGION")
    or _boto_session.region_name
    or "eu-west-1"
)
ec2_client = _boto_session.client("ec2", region_name=_ec2_region)
asg_client = _boto_session.client("autoscaling", region_name=_ec2_region)


log = logging.getLogger("awsome_test")
log.setLevel(logging.DEBUG)
formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s"
)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
console_handler.setFormatter(formatter)

log_dir = os.environ.get("HF_LOGDIR", "./logs")
os.makedirs(log_dir, exist_ok=True)
file_handler = logging.FileHandler(os.path.join(log_dir, "awsome_test.log"))
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(formatter)

log.addHandler(console_handler)
log.addHandler(file_handler)


MAX_TIME_WAIT_FOR_CAPACITY_PROVISIONING_SEC = 300


def get_scheduler_from_scenario(test_case: dict) -> str:
    """
    Extract scheduler type from test scenario.

    Args:
        test_case: Test case dictionary containing overrides

    Returns:
        Scheduler type: "default" or "hostfactory"
        Defaults to "hostfactory" if not present
    """
    return test_case.get("overrides", {}).get("scheduler", "hostfactory")


def get_instance_state(instance_id):
    """
    Check if an EC2 instance exists and return its state

    Returns:
        dict: Contains existence status and state if instance exists
    """
    try:
        response = ec2_client.describe_instances(InstanceIds=[instance_id])

        instance_state = response["Reservations"][0]["Instances"][0]["State"]["Name"]

        return {"exists": True, "state": instance_state}

    except ClientError as e:
        if e.response["Error"]["Code"] == "InvalidInstanceID.NotFound":
            return {"exists": False, "state": None}
        else:
            print(f"Error checking instance: {e}")
            raise


def get_instances_states(instance_ids, client=None):
    """
    Check EC2 instances and return their states in the same order as input.

    Returns:
        list: State name for each instance id, or None if not found

    Example:
        input: ["i-111", "i-222", "i-333"]
        output: ["running", None, "stopped"]
    """
    if not instance_ids:
        return []

    if client is None:
        client = ec2_client

    states_by_id = {instance_id: None for instance_id in instance_ids}
    chunk_size = 1000  # as per AWS documentation

    for start in range(0, len(instance_ids), chunk_size):
        chunk = instance_ids[start : start + chunk_size]
        try:
            response = client.describe_instances(InstanceIds=chunk)
            for reservation in response.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    instance_id = instance.get("InstanceId")
                    if instance_id in states_by_id:
                        states_by_id[instance_id] = instance["State"]["Name"]
        except ClientError as e:
            if e.response["Error"]["Code"] == "InvalidInstanceID.NotFound":
                for instance_id in chunk:
                    try:
                        response = client.describe_instances(InstanceIds=[instance_id])
                        instance_state = response["Reservations"][0]["Instances"][0]["State"]["Name"]
                        states_by_id[instance_id] = instance_state
                    except ClientError as inner:
                        if inner.response["Error"]["Code"] == "InvalidInstanceID.NotFound":
                            states_by_id[instance_id] = None
                        else:
                            print(f"Error checking instance: {inner}")
                            raise
            else:
                print(f"Error checking instances: {e}")
                raise

    return [states_by_id[instance_id] for instance_id in instance_ids]


def get_instance_details(instance_id):
    """
    Get detailed information about an EC2 instance.

    Returns:
        dict: Instance details including volume, subnet, and other attributes
    """
    try:
        response = ec2_client.describe_instances(InstanceIds=[instance_id])
        instance = response["Reservations"][0]["Instances"][0]

        # Get root device volume details
        root_device_name = instance.get("RootDeviceName")
        root_volume_size = None
        volume_type = None

        if root_device_name and "BlockDeviceMappings" in instance:
            for block_device in instance["BlockDeviceMappings"]:
                if block_device.get("DeviceName") == root_device_name:
                    ebs = block_device.get("Ebs", {})
                    volume_id = ebs.get("VolumeId")
                    if volume_id:
                        # Get volume details
                        volume_response = ec2_client.describe_volumes(VolumeIds=[volume_id])
                        if volume_response["Volumes"]:
                            volume = volume_response["Volumes"][0]
                            root_volume_size = volume.get("Size")
                            volume_type = volume.get("VolumeType")
                    break

        return {
            "instance_id": instance_id,
            "subnet_id": instance.get("SubnetId"),
            "root_device_volume_size": root_volume_size,
            "volume_type": volume_type,
            "instance_type": instance.get("InstanceType"),
            "state": instance.get("State", {}).get("Name"),
            "launch_time": instance.get("LaunchTime"),
            "instance_lifecycle": instance.get(
                "InstanceLifecycle"
            ),  # None for on-demand, "spot" for spot instances
        }

    except ClientError as e:
        log.error(f"Error getting instance details for {instance_id}: {e}")
        raise


def _get_tag_value(tags, key):
    for tag in tags or []:
        if tag.get("Key") == key:
            return tag.get("Value")
    return None


def get_parent_resource_from_instance(instance_id: str) -> tuple[Optional[str], Optional[str]]:
    """
    Get parent resource ID and type from instance using AWS tags.

    Args:
        instance_id: EC2 instance ID

    Returns:
        Tuple of (resource_id, resource_type) where resource_type is one of:
        'ec2_fleet', 'spot_fleet', 'asg', or None if not found
    """
    try:
        desc = ec2_client.describe_instances(InstanceIds=[instance_id])
        tags = desc["Reservations"][0]["Instances"][0].get("Tags", [])
    except Exception as e:
        log.warning(f"Failed to describe instance {instance_id}: {e}")
        return None, None

    # Check for EC2 Fleet
    fleet_id = _get_tag_value(tags, "aws:ec2:fleet-id")
    if fleet_id:
        return fleet_id, "ec2_fleet"

    # Check for Spot Fleet
    spot_fleet_id = _get_tag_value(tags, "aws:ec2spot:fleet-request-id")
    if spot_fleet_id:
        return spot_fleet_id, "spot_fleet"

    # Check for ASG
    asg_name = _get_tag_value(tags, "aws:autoscaling:groupName")
    if asg_name:
        return asg_name, "asg"

    return None, None


def verify_abis_enabled_for_instance(instance_id):
    """
    Given an instance ID, trace back to the parent resource (Fleet or ASG) and
    assert that InstanceRequirements/ABIS are present in the resource config.
    """
    resource_id, resource_type = get_parent_resource_from_instance(instance_id)

    if not resource_id or not resource_type:
        pytest.fail(
            f"Could not determine parent resource (fleet or ASG) for instance {instance_id} to verify ABIS"
        )

    if resource_type == "ec2_fleet":
        try:
            fleets = ec2_client.describe_fleets(FleetIds=[resource_id]).get("Fleets", [])
            if not fleets:
                pytest.fail(f"No EC2 Fleet data found for {resource_id} while verifying ABIS")
            overrides = fleets[0].get("LaunchTemplateConfigs", [{}])[0].get("Overrides", [])
            has_abis = any("InstanceRequirements" in ov for ov in overrides)
            assert has_abis, f"ABIS not present in EC2 Fleet overrides for {resource_id}"
            return
        except Exception as e:
            pytest.fail(f"Failed to verify ABIS on EC2 Fleet {resource_id}: {e}")

    elif resource_type == "spot_fleet":
        try:
            sfrs = ec2_client.describe_spot_fleet_requests(SpotFleetRequestIds=[resource_id]).get(
                "SpotFleetRequestConfigs", []
            )
            if not sfrs:
                pytest.fail(f"No Spot Fleet data found for {resource_id} while verifying ABIS")
            overrides = (
                sfrs[0]
                .get("SpotFleetRequestConfig", {})
                .get("LaunchTemplateConfigs", [{}])[0]
                .get("Overrides", [])
            )
            has_abis = any("InstanceRequirements" in ov for ov in overrides)
            assert has_abis, f"ABIS not present in Spot Fleet overrides for {resource_id}"
            return
        except Exception as e:
            pytest.fail(f"Failed to verify ABIS on Spot Fleet {resource_id}: {e}")

    elif resource_type == "asg":
        try:
            asgs = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[resource_id]).get(
                "AutoScalingGroups", []
            )
            if not asgs:
                pytest.fail(f"No ASG data found for {resource_id} while verifying ABIS")
            overrides = (
                asgs[0]
                .get("MixedInstancesPolicy", {})
                .get("LaunchTemplate", {})
                .get("Overrides", [])
            )
            has_abis = any("InstanceRequirements" in ov for ov in overrides)
            assert has_abis, f"ABIS not present in ASG overrides for {resource_id}"
            return
        except Exception as e:
            pytest.fail(f"Failed to verify ABIS on ASG {resource_id}: {e}")


def _extract_request_id(response: dict) -> str:
    """Get request identifier from varied response shapes."""
    if not isinstance(response, dict):
        return ""
    return (
        response.get("requestId")
        or response.get("request_id")
        or (response.get("requests") or [{}])[0].get("requestId")
        or ""
        or response.get("result")
    )


def _get_resource_id_from_instance(instance_id: str, provider_api: str) -> Optional[str]:
    """Discover backing fleet/ASG identifier for a given instance using tags."""
    try:
        desc = ec2_client.describe_instances(InstanceIds=[instance_id])
        tags = desc["Reservations"][0]["Instances"][0].get("Tags", [])
    except Exception as e:
        log.warning(f"Failed to describe instance {instance_id}: {e}")
        return None

    # Always check ASG first
    asg_name = _get_tag_value(tags, "aws:autoscaling:groupName")
    if asg_name:
        log.debug(f"Found ASG tag for {instance_id}: {asg_name}")
        return asg_name

    # Then check for EC2 Fleet
    fleet_id = _get_tag_value(tags, "aws:ec2:fleet-id")
    if fleet_id:
        log.debug(f"Found EC2 Fleet tag for {instance_id}: {fleet_id}")
        return fleet_id

    # Finally check for Spot Fleet
    spot_fleet_id = _get_tag_value(tags, "aws:ec2spot:fleet-request-id")
    if spot_fleet_id:
        log.debug(f"Found Spot Fleet tag for {instance_id}: {spot_fleet_id}")
        return spot_fleet_id

    log.warning(f"No resource tags found for instance {instance_id}. Tags: {tags}")
    return None


def _get_capacity(provider_api: str, resource_id: str) -> int:
    """Return target/desired capacity for fleet or ASG."""
    resource_id = resource_id or ""
    # Prefer detecting by ID shape to avoid mismatched API calls
    if resource_id.startswith("fleet-"):
        provider_api = "EC2Fleet"
    elif resource_id.startswith("sfr-"):
        provider_api = "SpotFleet"

    if "spotfleet" in provider_api.lower():
        try:
            resp = ec2_client.describe_spot_fleet_requests(SpotFleetRequestIds=[resource_id])
            configs = resp.get("SpotFleetRequestConfigs") or []
            if configs:
                config = configs[0].get("SpotFleetRequestConfig", {})
                return int(config.get("TargetCapacity", 0))
        except Exception as e:
            log.debug("Spot Fleet capacity lookup failed for %s: %s", resource_id, e)
        # Fallback to EC2 Fleet API if SpotFleet lookup fails
        try:
            resp = ec2_client.describe_fleets(FleetIds=[resource_id])
            fleets = resp.get("Fleets") or [{}]
            spec = fleets[0].get("TargetCapacitySpecification", {})
            return int(spec.get("TotalTargetCapacity", 0))
        except Exception as e:
            log.debug("EC2 Fleet fallback capacity lookup failed for %s: %s", resource_id, e)
    if "ec2fleet" in provider_api.lower():
        resp = ec2_client.describe_fleets(FleetIds=[resource_id])
        fleets = resp.get("Fleets") or [{}]
        spec = fleets[0].get("TargetCapacitySpecification", {})
        return int(spec.get("TotalTargetCapacity", 0))
    if provider_api == "ASG" or "asg" in provider_api.lower():
        resp = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[resource_id])
        asgs = resp.get("AutoScalingGroups") or [{}]
        return int(asgs[0].get("DesiredCapacity", 0))
    pytest.fail(f"Unsupported provider API for capacity check: {provider_api}")


def _wait_for_fleet_stable(resource_id: str, timeout: int = 300) -> None:
    """Wait until a fleet is out of modifying state so capacity reflects changes."""
    start = time.time()

    # Detect fleet type by ID prefix
    if resource_id.startswith("sfr-"):
        fleet_type = "SpotFleet"
    elif resource_id.startswith("fleet-"):
        fleet_type = "EC2Fleet"
    else:
        log.warning("Unknown fleet type for %s, skipping stability check", resource_id)
        return

    while True:
        try:
            if fleet_type == "SpotFleet":
                resp = ec2_client.describe_spot_fleet_requests(SpotFleetRequestIds=[resource_id])
                configs = resp.get("SpotFleetRequestConfigs") or []
                state = (configs[0].get("SpotFleetRequestState") or "").lower() if configs else ""
            else:  # EC2Fleet
                resp = ec2_client.describe_fleets(FleetIds=[resource_id])
                fleets = resp.get("Fleets") or []
                state = (fleets[0].get("FleetState") or "").lower() if fleets else ""

            if state != "modifying":
                return
        except Exception as exc:
            log.debug("Failed to poll %s %s state: %s", fleet_type, resource_id, exc)

        if time.time() - start > timeout:
            log.warning("Timed out waiting for %s %s to stabilize", fleet_type, resource_id)
            return
        time.sleep(5)


def _wait_for_capacity_change(
    provider_api: str, resource_id: str, expected_capacity: int, timeout: int = 60
) -> int:
    """Wait for capacity to reach expected value, handling eventual consistency."""
    start = time.time()
    last_capacity = None
    last_log_time = start

    while time.time() - start < timeout:
        capacity = _get_capacity(provider_api, resource_id)
        elapsed = time.time() - start

        if capacity == expected_capacity:
            log.info("Capacity reached expected value %d after %.1fs", expected_capacity, elapsed)
            return capacity

        # Log on capacity change or every 10 seconds
        if last_capacity != capacity or elapsed - (last_log_time - start) >= 10:
            log.debug(
                "Resource %s: Capacity is %d, waiting for %d (%.1fs elapsed)",
                resource_id,
                capacity,
                expected_capacity,
                elapsed,
            )
            last_capacity = capacity
            last_log_time = time.time()

        time.sleep(2)

    final_capacity = _get_capacity(provider_api, resource_id)
    log.warning(
        "Timeout waiting for capacity change. Expected %d, got %d after %ds",
        expected_capacity,
        final_capacity,
        timeout,
    )
    return final_capacity


# Backward compatibility aliases
_wait_for_spot_fleet_stable = _wait_for_fleet_stable
_wait_for_ec2_fleet_stable = _wait_for_fleet_stable


def validate_root_device_volume_size(instance_details, template, instance_id):
    """
    Validate that the instance root device volume size matches the template.

    Args:
        instance_details: Dict with instance details from AWS
        template: Template dict used to create the instance
        instance_id: Instance ID for logging

    Returns:
        bool: True if validation passes
    """
    expected_size = template.get("rootDeviceVolumeSize")
    actual_size = instance_details.get("root_device_volume_size")

    if expected_size is None:
        log.info(
            f"Instance {instance_id}: No rootDeviceVolumeSize specified in template, skipping validation"
        )
        return True

    if actual_size is None:
        log.error(f"Instance {instance_id}: Could not retrieve root device volume size from AWS")
        return False

    if actual_size == expected_size:
        log.info(
            f"Instance {instance_id}: Root device volume size validation PASSED - Expected: {expected_size}GB, Actual: {actual_size}GB"
        )
        return True
    else:
        log.error(
            f"Instance {instance_id}: Root device volume size validation FAILED - Expected: {expected_size}GB, Actual: {actual_size}GB"
        )
        return False


def validate_volume_type(instance_details, template, instance_id):
    """
    Validate that the instance volume type matches the template.

    Args:
        instance_details: Dict with instance details from AWS
        template: Template dict used to create the instance
        instance_id: Instance ID for logging

    Returns:
        bool: True if validation passes
    """
    expected_type = template.get("volumeType")
    actual_type = instance_details.get("volume_type")

    if expected_type is None:
        log.info(
            f"Instance {instance_id}: No volumeType specified in template, skipping validation"
        )
        return True

    if actual_type is None:
        log.error(f"Instance {instance_id}: Could not retrieve volume type from AWS")
        return False

    if actual_type == expected_type:
        log.info(
            f"Instance {instance_id}: Volume type validation PASSED - Expected: {expected_type}, Actual: {actual_type}"
        )
        return True
    else:
        log.error(
            f"Instance {instance_id}: Volume type validation FAILED - Expected: {expected_type}, Actual: {actual_type}"
        )
        return False


def validate_subnet_id(instance_details, template, instance_id):
    """
    Validate that the instance subnet ID matches the template.

    Args:
        instance_details: Dict with instance details from AWS
        template: Template dict used to create the instance
        instance_id: Instance ID for logging

    Returns:
        bool: True if validation passes
    """
    expected_subnet = template.get("subnetId")
    actual_subnet = instance_details.get("subnet_id")

    if expected_subnet is None:
        log.info(f"Instance {instance_id}: No subnetId specified in template, skipping validation")
        return True

    if actual_subnet is None:
        log.error(f"Instance {instance_id}: Could not retrieve subnet ID from AWS")
        return False

    if actual_subnet == expected_subnet:
        log.info(
            f"Instance {instance_id}: Subnet ID validation PASSED - Expected: {expected_subnet}, Actual: {actual_subnet}"
        )
        return True
    else:
        log.error(
            f"Instance {instance_id}: Subnet ID validation FAILED - Expected: {expected_subnet}, Actual: {actual_subnet}"
        )
        return False


def validate_instance_lifecycle(instance_details, expected_price_type, instance_id):
    """
    Validate that the instance lifecycle matches the expected price type.

    Args:
        instance_details: Dict with instance details from AWS
        expected_price_type: Expected price type ("ondemand" or "spot")
        instance_id: Instance ID for logging

    Returns:
        bool: True if validation passes
    """
    actual_lifecycle = instance_details.get("instance_lifecycle")

    if expected_price_type == "ondemand":
        # On-demand instances should not have an instance lifecycle field or it should be None
        if actual_lifecycle is None:
            log.info(
                f"Instance {instance_id}: Price type validation PASSED - Expected: on-demand, Actual: on-demand (no lifecycle field)"
            )
            return True
        else:
            log.error(
                f"Instance {instance_id}: Price type validation FAILED - Expected: on-demand, Actual: {actual_lifecycle}"
            )
            return False
    elif expected_price_type == "spot":
        # Spot instances should have instance lifecycle set to "spot"
        if actual_lifecycle == "spot":
            log.info(
                f"Instance {instance_id}: Price type validation PASSED - Expected: spot, Actual: spot"
            )
            return True
        else:
            log.error(
                f"Instance {instance_id}: Price type validation FAILED - Expected: spot, Actual: {actual_lifecycle or 'on-demand'}"
            )
            return False
    else:
        log.warning(
            f"Instance {instance_id}: Unknown price type '{expected_price_type}', skipping validation"
        )
        return True


def validate_instance_attributes(instance_id, template):
    """
    Validate all specified instance attributes against the template.

    Args:
        instance_id: EC2 instance ID to validate
        template: Template dict used to create the instance

    Returns:
        bool: True if all validations pass
    """
    log.info(f"Starting attribute validation for instance {instance_id}")

    try:
        # Get instance details from AWS
        instance_details = get_instance_details(instance_id)
        log.debug(
            f"Instance {instance_id} details: {json.dumps(instance_details, indent=2, default=str)}"
        )

        # Run all validation functions
        validations = [
            validate_root_device_volume_size(instance_details, template, instance_id),
            validate_volume_type(instance_details, template, instance_id),
            validate_subnet_id(instance_details, template, instance_id),
        ]

        # Check if all validations passed
        all_passed = all(validations)

        if all_passed:
            log.info(f"Instance {instance_id}: ALL attribute validations PASSED")
        else:
            log.error(f"Instance {instance_id}: Some attribute validations FAILED")

        return all_passed

    except Exception as e:
        log.error(f"Instance {instance_id}: Validation failed with exception: {e}")
        return False


def validate_random_instance_attributes(status_response, template):
    """
    Select a random EC2 instance from the response and validate its attributes.

    Args:
        status_response: Response from get_request_status containing machine info
        template: Template dict used to create the instances

    Returns:
        bool: True if validation passes for the selected instance
    """
    import random

    machines = status_response["requests"][0]["machines"]
    if not machines:
        log.error("No machines found in status response for attribute validation")
        return False

    # Select a random machine
    selected_machine = random.choice(machines)
    instance_id = selected_machine.get("machineId") or selected_machine.get("machine_id")

    log.info(
        f"Selected random instance {instance_id} for attribute validation (out of {len(machines)} instances)"
    )

    return validate_instance_attributes(instance_id, template)


def validate_all_instances_price_type(status_response, test_case):
    """
    Validate that all EC2 instances match the expected price type from the test case.
    Supports mixed (heterogeneous) pricing by ensuring both spot and on-demand instances exist.

    Args:
        status_response: Response from get_request_status containing machine info
        test_case: Test case dict containing overrides with priceType

    Returns:
        bool: True if all instances match the expected price type
    """
    machines = status_response["requests"][0]["machines"]
    if not machines:
        log.error("No machines found in status response for price type validation")
        return False

    # Get expected price type from test case overrides
    expected_price_type = test_case.get("overrides", {}).get("priceType")
    mixed_price = expected_price_type == "heterogeneous" or (
        isinstance(test_case.get("overrides", {}).get("percentOnDemand"), (int, float))
        and 0 < test_case["overrides"]["percentOnDemand"] < 100
    )
    if not expected_price_type:
        log.info("No priceType specified in test case overrides, skipping price type validation")
        return True

    log.info(
        f"Validating price type for all {len(machines)} instances - Expected: {expected_price_type}"
    )

    if mixed_price:
        spot_count = 0
        ondemand_count = 0
        for machine in machines:
            instance_id = machine.get("machineId") or machine.get("machine_id")
            try:
                details = get_instance_details(instance_id)
                lifecycle = details.get("instance_lifecycle")
                if lifecycle == "spot":
                    spot_count += 1
                else:
                    ondemand_count += 1
            except Exception as e:
                log.error(
                    f"Instance {instance_id}: Mixed price validation failed with exception: {e}"
                )
                return False

        if spot_count > 0 and ondemand_count > 0:
            log.info(
                "Mixed price validation PASSED - spot instances: %s, on-demand instances: %s",
                spot_count,
                ondemand_count,
            )
            return True
        else:
            log.error(
                "Mixed price validation FAILED - spot instances: %s, on-demand instances: %s",
                spot_count,
                ondemand_count,
            )
            return False

    all_validations_passed = True

    for machine in machines:
        instance_id = machine.get("machineId") or machine.get("machine_id")

        try:
            # Get instance details from AWS
            instance_details = get_instance_details(instance_id)

            # Validate price type for this instance
            validation_passed = validate_instance_lifecycle(
                instance_details, expected_price_type, instance_id
            )

            if not validation_passed:
                all_validations_passed = False

        except Exception as e:
            log.error(f"Instance {instance_id}: Price type validation failed with exception: {e}")
            all_validations_passed = False

    if all_validations_passed:
        log.info(f"Price type validation PASSED for all {len(machines)} instances")
    else:
        log.error("Price type validation FAILED for one or more instances")

    return all_validations_passed


@pytest.fixture
def setup_host_factory_mock(request):
    # Generate templates for this test using the actual test name
    processor = TemplateProcessor()
    test_name = request.node.name  # Get the actual test function name

    # Get base template and overrides from test parameters if available
    base_template = (
        getattr(request, "param", {}).get("base_template", None)
        if hasattr(request, "param") and isinstance(request.param, dict)
        else None
    )
    overrides = (
        getattr(request, "param", {}).get("overrides", {})
        if hasattr(request, "param") and isinstance(request.param, dict)
        else {}
    )

    # Clear any existing files from the test directory first
    test_config_dir = processor.run_templates_dir / test_name
    if test_config_dir.exists():
        import shutil

        shutil.rmtree(test_config_dir)
        print(f"Cleared existing test directory: {test_config_dir}")

    # Generate populated templates with optional base template and overrides
    processor.generate_test_templates(test_name, base_template=base_template, overrides=overrides)

    # Set environment variables to use generated templates
    test_config_dir = processor.run_templates_dir / test_name
    os.environ["HF_PROVIDER_CONFDIR"] = str(test_config_dir)
    os.environ["HF_PROVIDER_LOGDIR"] = str(test_config_dir / "logs")
    os.environ["HF_PROVIDER_WORKDIR"] = str(test_config_dir / "work")
    os.environ["DEFAULT_PROVIDER_WORKDIR"] = str(test_config_dir / "work")
    os.environ["AWS_PROVIDER_LOG_DIR"] = str(test_config_dir / "logs")
    os.environ["HF_LOGDIR"] = str(test_config_dir / "logs")

    # Create the log and work directories
    (test_config_dir / "logs").mkdir(exist_ok=True)
    (test_config_dir / "work").mkdir(exist_ok=True)

    # Get scheduler type from overrides, default to "hostfactory"
    scheduler_type = overrides.get("scheduler", "hostfactory")
    hfm = HostFactoryMock(scheduler=scheduler_type)

    return hfm


@pytest.fixture
def setup_host_factory_mock_with_scenario(request):
    """Fixture that handles scenario-based overrides by extracting test name from test node."""
    # Generate templates for this test using the actual test name
    processor = TemplateProcessor()
    test_name = request.node.name  # Get the actual test function name

    # Extract the scenario name from the test node name
    # For parametrized tests, the node name will be like "full_cycle_test[EC2Fleet]"
    scenario_name = None
    if "[" in test_name and "]" in test_name:
        # Extract the parameter value from the test name
        scenario_name = test_name.split("[")[1].split("]")[0]

    # Get the specific test case for this scenario
    from tests.onaws import scenarios

    test_case = scenarios.get_test_case_by_name(scenario_name) if scenario_name else {}

    # Extract overrides and base template from test_case if available
    overrides = test_case.get("overrides", {}) if test_case else {}
    awsprov_base_template = test_case.get("awsprov_base_template") if test_case else None

    # Clear any existing files from the test directory first
    test_config_dir = processor.run_templates_dir / test_name
    if test_config_dir.exists():
        import shutil

        shutil.rmtree(test_config_dir)
        print(f"Cleared existing test directory: {test_config_dir}")

    # Generate populated templates with overrides and base template from test case
    processor.generate_test_templates(
        test_name, awsprov_base_template=awsprov_base_template, overrides=overrides
    )

    # Set environment variables to use generated templates
    test_config_dir = processor.run_templates_dir / test_name
    os.environ["HF_PROVIDER_CONFDIR"] = str(test_config_dir)
    os.environ["HF_PROVIDER_LOGDIR"] = str(test_config_dir / "logs")
    os.environ["HF_PROVIDER_WORKDIR"] = str(test_config_dir / "work")
    os.environ["DEFAULT_PROVIDER_WORKDIR"] = str(test_config_dir / "work")
    os.environ["AWS_PROVIDER_LOG_DIR"] = str(test_config_dir / "logs")
    os.environ["HF_LOGDIR"] = str(test_config_dir / "logs")

    # Create the log and work directories
    (test_config_dir / "logs").mkdir(exist_ok=True)
    (test_config_dir / "work").mkdir(exist_ok=True)

    # Get scheduler type from overrides, default to "hostfactory"
    scheduler_type = overrides.get("scheduler", "hostfactory")
    hfm = HostFactoryMock(scheduler=scheduler_type)

    return hfm


def _check_request_machines_response_status(status_response):
    assert status_response["requests"][0]["status"] == "complete"
    for machine in status_response["requests"][0]["machines"]:
        # it is possible that ec2 host is still initialising
        assert machine["status"] in ["running", "pending"]


def _check_all_ec2_hosts_are_being_provisioned(status_response):
    for machine in status_response["requests"][0]["machines"]:
        ec2_instance_id = machine.get("machineId") or machine.get("machine_id")
        res = get_instance_state(ec2_instance_id)

        assert res["exists"] == True
        # it is possible that ec2 host is still initialising
        assert res["state"] in ["running", "pending"]

        log.debug(f"EC2 {ec2_instance_id} state: {json.dumps(res, indent=4)}")


def _check_all_ec2_hosts_are_being_terminated(ec2_instance_ids):
    all_are_deallocated = True

    for ec2_id in ec2_instance_ids:
        res = get_instance_state(ec2_id)

        if res["exists"] == True:
            if res["state"] not in ["shutting-down", "terminated"]:
                all_are_deallocated = False
                break
    return all_are_deallocated


def _force_terminate_asg_instances(asg_name: str) -> None:
    """
    Force terminate all instances in an ASG by setting capacity to 0 and deleting the ASG.

    Args:
        asg_name: Name of the Auto Scaling Group to terminate
    """
    try:
        log.info("Force terminating ASG: %s", asg_name)

        # First, set desired capacity to 0
        log.info("Setting ASG %s desired capacity to 0", asg_name)
        asg_client.update_auto_scaling_group(
            AutoScalingGroupName=asg_name, DesiredCapacity=0, MinSize=0
        )

        # Wait for instances to terminate
        log.info("Waiting for ASG %s instances to terminate", asg_name)
        start_time = time.time()
        while time.time() - start_time < 300:  # 5 minute timeout
            try:
                response = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
                asgs = response.get("AutoScalingGroups", [])
                if not asgs:
                    log.info("ASG %s no longer exists", asg_name)
                    return

                asg = asgs[0]
                instances = asg.get("Instances", [])
                if not instances:
                    log.info("All instances terminated in ASG %s", asg_name)
                    break

                log.debug("ASG %s still has %d instances", asg_name, len(instances))
                time.sleep(10)
            except ClientError as e:
                if e.response["Error"]["Code"] == "ValidationError":
                    log.info("ASG %s no longer exists", asg_name)
                    return
                raise

        # Delete the ASG
        log.info("Deleting ASG: %s", asg_name)
        asg_client.delete_auto_scaling_group(AutoScalingGroupName=asg_name, ForceDelete=True)

        # Verify ASG is deleted
        start_time = time.time()
        while time.time() - start_time < 120:  # 2 minute timeout
            try:
                asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
                log.debug("ASG %s still exists, waiting for deletion", asg_name)
                time.sleep(5)
            except ClientError as e:
                if e.response["Error"]["Code"] == "ValidationError":
                    log.info("ASG %s successfully deleted", asg_name)
                    return
                raise

        log.warning("ASG %s deletion may not have completed within timeout", asg_name)

    except ClientError as e:
        if e.response["Error"]["Code"] == "ValidationError":
            log.info("ASG %s does not exist or already deleted", asg_name)
        else:
            log.error("Error force terminating ASG %s: %s", asg_name, e)
            raise


def _cleanup_asg_resources(machine_ids: list[str], provider_api: str) -> None:
    """
    Comprehensive cleanup for ASG resources.

    Args:
        machine_ids: List of EC2 instance IDs to clean up
        provider_api: Provider API type (should be "ASG")
    """
    if not machine_ids:
        log.info("No machine IDs provided for ASG cleanup")
        return

    log.info("Starting comprehensive ASG cleanup for %d instances", len(machine_ids))

    # Get ASG name from first instance
    asg_name = None
    for instance_id in machine_ids:
        asg_name = _get_resource_id_from_instance(instance_id, provider_api)
        if asg_name:
            break

    if not asg_name:
        log.warning(
            "Could not determine ASG name from instances, falling back to direct instance termination"
        )
        # Fallback: try to terminate instances directly
        try:
            ec2_client.terminate_instances(InstanceIds=machine_ids)
            log.info("Initiated direct termination of instances: %s", machine_ids)
        except Exception as e:
            log.error("Failed to directly terminate instances: %s", e)
        return

    log.info("Identified ASG for cleanup: %s", asg_name)

    # Force terminate the ASG and all its instances
    _force_terminate_asg_instances(asg_name)

    # Verify all instances are terminated
    log.info("Verifying all instances are terminated")
    start_time = time.time()
    while time.time() - start_time < 300:  # 5 minute timeout
        all_terminated = True
        for instance_id in machine_ids:
            state_info = get_instance_state(instance_id)
            if state_info["exists"] and state_info["state"] not in ["terminated", "shutting-down"]:
                all_terminated = False
                log.debug("Instance %s still in state: %s", instance_id, state_info["state"])
                break

        if all_terminated:
            log.info("All instances successfully terminated")
            return

        time.sleep(10)

    log.warning("Some instances may not have terminated within timeout")


def _verify_all_resources_cleaned(
    machine_ids: list[str], resource_id: str = None, provider_api: str = None
) -> bool:
    """
    Verify that all resources (instances and backing resources) are properly cleaned up.

    Args:
        machine_ids: List of EC2 instance IDs that should be terminated
        resource_id: ID of the backing resource (ASG, Fleet, etc.)
        provider_api: Provider API type

    Returns:
        bool: True if all resources are cleaned up, False otherwise
    """
    log.info("Verifying cleanup of all resources")

    # Check instances
    instances_cleaned = True
    instance_states = get_instances_states(machine_ids)
    for instance_id, state in zip(machine_ids, instance_states):
        if state is None:
            # Instance not found, so it's cleaned up.
            continue

        if state not in ["terminated", "shutting-down"]:
            log.error("Instance %s still exists in state: %s", instance_id, state)
            instances_cleaned = False

    if instances_cleaned:
        log.info("✅ All instances are terminated or terminating")
    else:
        log.error("❌ Some instances are still running")

    # Check backing resource
    resource_cleaned = True
    if resource_id and provider_api:
        try:
            if provider_api == "ASG" or "asg" in provider_api.lower():
                try:
                    response = asg_client.describe_auto_scaling_groups(
                        AutoScalingGroupNames=[resource_id]
                    )
                    asgs = response.get("AutoScalingGroups", [])
                    if asgs:
                        asg = asgs[0]
                        instances = asg.get("Instances", [])
                        if instances:
                            log.error("ASG %s still has %d instances", resource_id, len(instances))
                            resource_cleaned = False
                        else:
                            log.info("✅ ASG %s has no instances", resource_id)
                except ClientError as e:
                    if e.response["Error"]["Code"] == "ValidationError":
                        log.info("✅ ASG %s no longer exists", resource_id)
                    else:
                        log.error("Error checking ASG %s: %s", resource_id, e)
                        resource_cleaned = False
            elif "fleet" in provider_api.lower() or resource_id.startswith(("fleet-", "sfr-")):
                # For fleets, check if they still exist and have capacity
                try:
                    capacity = _get_capacity(provider_api, resource_id)
                    if capacity > 0:
                        log.error("Fleet %s still has capacity: %d", resource_id, capacity)
                        resource_cleaned = False
                    else:
                        log.info("✅ Fleet %s has zero capacity", resource_id)
                except Exception as e:
                    log.info(
                        "✅ Fleet %s appears to be deleted or inaccessible: %s", resource_id, e
                    )
        except Exception as e:
            log.warning("Could not verify backing resource cleanup: %s", e)

    if resource_cleaned:
        log.info("✅ Backing resource is properly cleaned")
    else:
        log.error("❌ Backing resource still has active resources")

    return instances_cleaned and resource_cleaned


def _wait_for_request_completion(hfm, request_id: str, scheduler_type: str):
    """Poll request status until complete or timeout."""
    request_status_schema = plugin_io_schemas.get_schema_for_scheduler(
        "request_status", scheduler_type
    )
    alt_schema = plugin_io_schemas.expected_request_status_schema_hostfactory
    start_time = time.time()

    while True:
        status_response = hfm.get_request_status(request_id)
        log.debug("Response on get_request_staus: \n %s", json.dumps(status_response, indent=4))

        try:
            # Use the schema that matches the key style in the response
            requests = status_response.get("requests") or []
            first_request = requests[0] if requests else {}
            machines = first_request.get("machines") or []

            if (
                scheduler_type == "default"
                and machines
                and "machineId" in machines[0]
                and "machine_id" not in machines[0]
            ):
                validate_json_schema(instance=status_response, schema=alt_schema)
            else:
                validate_json_schema(instance=status_response, schema=request_status_schema)
        except ValidationError as e:
            pytest.fail(
                f"JSON validation failed for get_reqest_status response json ({scheduler_type} scheduler): {e}"
            )

        if status_response["requests"][0]["status"] == "complete":
            return status_response

        if time.time() - start_time > MAX_TIME_WAIT_FOR_CAPACITY_PROVISIONING_SEC:
            pytest.fail("Timed out waiting for request to complete")

        time.sleep(5)


def _wait_for_return_completion(hfm, machine_ids: list[str], return_request_id: str):
    """Poll return request until complete using return_request_id."""
    start_time = time.time()
    while True:
        status_response = hfm.get_return_requests([return_request_id])
        log.debug(json.dumps(status_response, indent=4))

        requests = status_response.get("requests") or []
        matching_req = None
        for req in requests:
            if isinstance(req, dict):
                rid = req.get("requestId") or req.get("request_id")
                if return_request_id and rid and rid != return_request_id:
                    continue
                matching_req = req
                break
            # Sometimes the API returns just request IDs as strings; accept them when unambiguous
            elif isinstance(req, str):
                if not return_request_id or req == return_request_id:
                    matching_req = {"request_id": req, "status": status_response.get("status")}
                    break
        if not matching_req and requests:
            first = requests[0]
            matching_req = (
                first if isinstance(first, dict) else {"request_id": first, "status": None}
            )

        if matching_req and matching_req.get("status") == "complete":
            return status_response

        if time.time() - start_time > MAX_TIME_WAIT_FOR_CAPACITY_PROVISIONING_SEC:
            pytest.fail("Timed out waiting for return request to complete")

        time.sleep(5)


def _resolve_request_machines_schema(response: dict, scheduler_type: str):
    """Pick the schema that matches the response shape without mutating the payload."""
    has_camel = "requestId" in response
    has_snake = "request_id" in response

    if has_camel and not has_snake:
        return plugin_io_schemas.expected_request_machines_schema_hostfactory
    if has_snake and not has_camel:
        return plugin_io_schemas.expected_request_machines_schema_default
    return plugin_io_schemas.get_schema_for_scheduler("request_machines", scheduler_type)


def provide_release_control_loop(hfm, template_json, capacity_to_request, test_case=None):
    """
    Executes a full lifecycle test of requesting and releasing EC2 instances.

    This function performs the following steps:
    1. Requests EC2 capacity based on the provided template
    2. Waits for the instances to be provisioned and validates their status
    3. Validates that all instances match the expected price type (if specified)
    4. Deallocates the instances and verifies they are properly terminated

    Args:
        hfm (HostFactoryMock): Mock host factory instance to interact with EC2
        template_json (dict): Template containing EC2 instance configuration
        capacity_to_request (int): Number of EC2 instances to request
        test_case (dict, optional): Test case containing overrides for validation

    Raises:
        ValidationError: If the API responses don't match expected schemas
        pytest.Failed: If JSON schema validation fails
    """

    # <1.> Request capacity. #######################################################################
    log.debug(f"Requesting capacity for the template \n {json.dumps(template_json, indent=4)}")

    res = hfm.request_machines(
        template_json.get("templateId") or template_json.get("template_id"), capacity_to_request
    )
    parse_and_print_output(res)

    # Debug: Log the full response to understand the structure
    log.debug(f"Full request_machines response: {json.dumps(res, indent=2)}")

    # Handle different response formats or error responses
    if "requestId" in res:
        request_id = res["requestId"]
    elif "request_id" in res:
        request_id = res["request_id"]
    else:
        # This might be an error response - log more details
        log.error("AWS provider response missing requestId field.")
        log.error(f"Response keys: {list(res.keys())}")
        log.error(f"Full response: {json.dumps(res, indent=2)}")
        log.error(f"Template used: {json.dumps(template_json, indent=2)}")

        # Check if this is an error response
        if "error" in res or "message" in res:
            error_msg = res.get("error", res.get("message", "Unknown error"))
            pytest.fail(f"AWS provider returned error response: {error_msg}. Full response: {res}")
        else:
            pytest.fail(f"AWS provider response missing requestId field. Response: {res}")

    # log.debug(json.dumps(res, indent=4))

    # Get scheduler type for validation
    scheduler_type = get_scheduler_from_scenario(test_case) if test_case else "hostfactory"
    request_machines_schema = plugin_io_schemas.get_schema_for_scheduler(
        "request_machines", scheduler_type
    )

    try:
        validate_json_schema(instance=res, schema=request_machines_schema)
    except ValidationError as e:
        pytest.fail(
            f"JSON validation failed for request_machines response json ({scheduler_type} scheduler): {e}"
        )

    # <2.> Wait until request is completed. ########################################################

    start_time = time.time()
    status_response = None
    while True:
        status_response = hfm.get_request_status(request_id)
        log.debug(json.dumps(status_response, indent=4))
        # Force immediate output for debugging
        print(f"DEBUG: Status Response: {json.dumps(status_response, indent=2)}")
        import sys

        sys.stdout.flush()

        request_status_schema = plugin_io_schemas.get_schema_for_scheduler(
            "request_status", scheduler_type
        )

        try:
            validate_json_schema(instance=status_response, schema=request_status_schema)
        except ValidationError as e:
            pytest.fail(
                f"JSON validation failed for get_reqest_status response json ({scheduler_type} scheduler): {e}"
            )

        if time.time() - start_time > MAX_TIME_WAIT_FOR_CAPACITY_PROVISIONING_SEC:
            break
        if (
            status_response.get("requests")
            and status_response["requests"][0]["status"] == "complete"
        ):
            break

        time.sleep(5)

    _check_request_machines_response_status(status_response)

    _check_all_ec2_hosts_are_being_provisioned(status_response)

    # Validate instance attributes against template
    log.info("Starting instance attribute validation against template")
    attribute_validation_passed = validate_random_instance_attributes(
        status_response, template_json
    )

    if not attribute_validation_passed:
        pytest.fail(
            "Instance attribute validation failed - EC2 instance attributes do not match template configuration"
        )
    else:
        log.info(
            "Instance attribute validation PASSED - EC2 instance attributes match template configuration"
        )

    # Optional: verify ABIS was applied on the created resource
    abis_requested = (
        test_case
        and isinstance(test_case, dict)
        and (
            test_case.get("overrides", {}).get("abisInstanceRequirements")
            or test_case.get("overrides", {}).get("abis_instance_requirements")
        )
    )
    if scenarios.VERIFY_ABIS and abis_requested:
        first_machine = status_response["requests"][0]["machines"][0]
        instance_id = first_machine.get("machineId") or first_machine.get("machine_id")
        log.info("Verifying ABIS on resource for instance %s", instance_id)
        verify_abis_enabled_for_instance(instance_id)

    # Validate price type for all instances if test_case is provided
    if test_case:
        # Check if this provider API supports spot instance validation
        provider_api = (
            template_json.get("providerApi") or template_json.get("provider_api") or "EC2Fleet"
        )
        expected_price_type = test_case.get("overrides", {}).get("priceType")

        if provider_api in ["RunInstances"] and expected_price_type == "spot":
            log.warning(
                f"Skipping price type validation for {provider_api} with spot instances - may not be supported"
            )
        else:
            log.info("Starting price type validation for all instances")
            price_type_validation_passed = validate_all_instances_price_type(
                status_response, test_case
            )

            if not price_type_validation_passed:
                pytest.fail(
                    "Price type validation failed - EC2 instances do not match expected price type"
                )
            else:
                log.info(
                    "Price type validation PASSED - All EC2 instances match expected price type"
                )

    # <3.> Deallocate capacity and verify that capacity is released. ###############################

    ec2_instance_ids = [
        machine.get("machineId") or machine.get("machine_id")
        for machine in status_response["requests"][0]["machines"]
    ]
    # ec2_instance_ids = [machine["name"] for machine in status_response["requests"][0]["machines"]] #TODO
    log.debug(f"Deallocating instances: {ec2_instance_ids}")

    # Get provider API for cleanup strategy
    provider_api = (
        template_json.get("providerApi") or template_json.get("provider_api") or "EC2Fleet"
    )

    # Get resource ID for verification
    resource_id = None
    if ec2_instance_ids:
        resource_id = _get_resource_id_from_instance(ec2_instance_ids[0], provider_api)

    try:
        # Try graceful return first
        return_request_id = hfm.request_return_machines(ec2_instance_ids)
        log.debug(f"Deallocating: {json.dumps(return_request_id, indent=4)}")

        # Wait for graceful termination with timeout
        graceful_start = time.time()
        graceful_completed = False

        while time.time() - graceful_start < 180:  # 3 minute timeout for graceful return
            if _check_all_ec2_hosts_are_being_terminated(ec2_instance_ids):
                log.info("Graceful termination completed successfully")
                graceful_completed = True
                break

            status_response = hfm.get_return_requests(return_request_id)
            log.debug(json.dumps(status_response, indent=4))

            res = get_instance_state(ec2_instance_ids[0])
            log.debug(json.dumps(res, indent=4))

            time.sleep(10)

        if not graceful_completed:
            log.warning("Graceful termination timed out, proceeding with force cleanup")

    except Exception as e:
        log.warning("Graceful termination failed: %s, proceeding with force cleanup", e)

    # For ASG resources, use comprehensive cleanup if graceful didn't work
    if provider_api == "ASG" or "asg" in provider_api.lower():
        if not graceful_completed:
            log.info("Performing comprehensive ASG cleanup")
            _cleanup_asg_resources(ec2_instance_ids, provider_api)
    # For non-ASG resources, continue waiting if needed
    elif not graceful_completed:
        log.info("Continuing to wait for standard termination")
        cleanup_start = time.time()
        while time.time() - cleanup_start < 300:  # 5 minute timeout
            if _check_all_ec2_hosts_are_being_terminated(ec2_instance_ids):
                log.info("All instances terminated successfully")
                break
            time.sleep(10)
        else:
            log.warning("Some instances may not have terminated within timeout")

    # Final verification
    log.info("Verifying complete resource cleanup")
    cleanup_verified = _verify_all_resources_cleaned(ec2_instance_ids, resource_id, provider_api)

    if not cleanup_verified:
        log.error("⚠️  Cleanup verification failed - some resources may still exist")
        # Log remaining resources for debugging
        for instance_id in ec2_instance_ids:
            state_info = get_instance_state(instance_id)
            if state_info["exists"]:
                log.error("Instance %s still exists in state: %s", instance_id, state_info["state"])
    else:
        log.info("✅ All resources successfully cleaned up")


@pytest.mark.aws
@pytest.mark.slow
def test_get_available_templates(setup_host_factory_mock):
    hfm = setup_host_factory_mock

    res = hfm.get_available_templates()

    # Use default hostfactory schema for backward compatibility
    scheduler_type = "hostfactory"
    schema = plugin_io_schemas.get_schema_for_scheduler("get_available_templates", scheduler_type)

    try:
        validate_json_schema(instance=res, schema=schema)
    except ValidationError as e:
        pytest.fail(f"JSON validation failed for {scheduler_type} scheduler: {e}")


@pytest.mark.aws
@pytest.mark.slow
@pytest.mark.parametrize(
    "setup_host_factory_mock",
    [
        {
            "base_template": "config",  # Use custom base template
            "overrides": {
                "region": "us-west-2",  # Override region
                "imageId": "ami-custom123",  # Override image ID
                "profile": "test-profile",  # Override profile
            },
        }
    ],
    indirect=True,
)
def test_get_available_templates_with_overrides(setup_host_factory_mock):
    """Test with custom base template and configuration overrides."""

    hfm = setup_host_factory_mock

    res = hfm.get_available_templates()

    try:
        validate_json_schema(
            instance=res, schema=plugin_io_schemas.expected_get_available_templates_schema
        )
    except ValidationError as e:
        pytest.fail(f"JSON validation failed: {e}")


def _partial_return_cases():
    """Pick maintain fleets and ASG scenarios with capacity > 1."""
    if not scenarios.RUN_PARTIAL_RETURN_TESTS:
        return []
    cases = []
    for tc in scenarios.get_test_cases():
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
@pytest.mark.parametrize("test_case", _partial_return_cases(), ids=lambda tc: tc["test_name"])
def test_partial_return_reduces_capacity(setup_host_factory_mock_with_scenario, test_case):
    """
    Test partial return of instances to ensure maintain capacity drops correctly.

    This test validates that when returning one instance from a maintain fleet/ASG,
    the capacity is properly reduced before draining the remaining instances.

    Steps:
    1. Provision multiple instances using maintain Spot/EC2 fleet or ASG
    2. Terminate one instance and verify capacity reduction
    3. Clean up remaining instances
    """
    log.info("=== STEP 0: Test Setup ===")
    log.info("Partial return test: %s", test_case["test_name"])

    hfm = setup_host_factory_mock_with_scenario

    log.info("=== STEP 1: Provision Instances ===")

    # 1.1: Get available templates
    log.info("1.1: Retrieving available templates")
    templates_response = hfm.get_available_templates()
    log.debug("Templates response: %s", json.dumps(templates_response, indent=2))

    # 1.2: Find target template
    log.info("1.2: Finding target template")
    template_id = test_case.get("template_id") or test_case["test_name"]
    template_json = next(
        (
            template
            for template in templates_response["templates"]
            if template.get("templateId") == template_id
            or template.get("template_id") == template_id
        ),
        None,
    )
    if template_json is None:
        pytest.fail(f"Template {template_id} not found for partial return test")
    log.info("Found template: %s", template_id)

    # 1.3: Request instances
    log.info("1.3: Requesting %d instances", test_case["capacity_to_request"])
    scheduler_type = get_scheduler_from_scenario(test_case)

    request_response = hfm.request_machines(
        template_json.get("templateId") or template_json.get("template_id"),
        test_case["capacity_to_request"],
    )
    parse_and_print_output(request_response)

    # 1.4: Validate request response
    log.info("1.4: Validating request response")
    request_machines_schema = _resolve_request_machines_schema(request_response, scheduler_type)
    try:
        validate_json_schema(instance=request_response, schema=request_machines_schema)
    except ValidationError as e:
        pytest.fail(
            f"JSON validation failed for request_machines response json ({scheduler_type} scheduler): {e}"
        )

    request_id = request_response.get("requestId") or request_response.get("request_id")
    if not request_id:
        pytest.fail(f"Request ID missing in response: {json.dumps(request_response, indent=2)}")

    # 1.5: Wait for provisioning completion
    log.info("1.5: Waiting for provisioning completion (request_id: %s)", request_id)
    provider_api = (
        template_json.get("providerApi")
        or template_json.get("provider_api")
        or test_case.get("overrides", {}).get("providerApi")
    )
    log.info("Provider API: %s", provider_api)

    status_response = _wait_for_request_completion(hfm, request_id, scheduler_type)
    _check_request_machines_response_status(status_response)
    _check_all_ec2_hosts_are_being_provisioned(status_response)
    log.debug("Final provisioning status: %s", json.dumps(status_response, indent=2))

    # 1.6: Extract provisioned instances
    log.info("1.6: Extracting provisioned instance information")
    machines = status_response["requests"][0]["machines"]
    machine_ids = [m.get("machineId") or m.get("machine_id") for m in machines]
    assert len(machine_ids) >= 2, "Partial return test requires capacity > 1"
    log.info("Provisioned %d instances: %s", len(machine_ids), machine_ids)

    # === STEP 2: PARTIAL RETURN AND CAPACITY VERIFICATION ===
    log.info("=== STEP 2: Partial Return and Capacity Verification ===")

    # 2.1: Identify target instance and backing resource
    log.info("2.1: Identifying target instance and backing resource")
    first_instance = machine_ids[0]
    log.info("Target instance for partial return: %s", first_instance)

    resource_id = _get_resource_id_from_instance(first_instance, provider_api)
    if not resource_id:
        pytest.skip(f"Could not determine backing resource for instance {first_instance}")
    log.info("Backing resource ID: %s", resource_id)

    # 2.2: Record initial capacity
    log.info("2.2: Recording initial capacity")
    capacity_before = _get_capacity(provider_api, resource_id)
    log.info("Initial capacity: %d", capacity_before)

    # 2.3: Return single instance
    log.info("2.3: Returning single instance: %s", first_instance)
    return_response = hfm.request_return_machines([first_instance])
    log.debug("Return response: %s", json.dumps(return_response, indent=2))

    return_request_id = _extract_request_id(return_response)
    if not return_request_id:
        log.warning("Return request ID missing; proceeding with status polling by machine id only")

    # 2.4: Wait for return completion
    log.info("2.4: Waiting for return completion")
    _wait_for_request_completion(hfm, return_request_id, scheduler_type)

    # 2.5: Wait for resource stabilization. When you update target capacity of a fleet, it is not
    # being reflected instantaniously, instead, fleet gets into "modifying state", when modification
    # is complete, then new capacity is properly updated.
    log.info("2.5: Waiting for resource stabilization")
    if provider_api and "spotfleet" in provider_api.lower():
        _wait_for_spot_fleet_stable(resource_id)
    elif provider_api and "ec2fleet" in provider_api.lower():
        _wait_for_ec2_fleet_stable(resource_id)
    elif resource_id.startswith("fleet-"):
        _wait_for_ec2_fleet_stable(resource_id)

    # 2.6: Verify capacity reduction
    log.info("2.6: Verifying capacity reduction")
    expected_capacity = max(capacity_before - 1, 0)
    # ASG operations can take longer than fleet operations
    timeout = 120 if provider_api == "ASG" or "asg" in provider_api.lower() else 60
    log.info("Waiting for capacity change with timeout=%ds", timeout)
    capacity_after = _wait_for_capacity_change(
        provider_api, resource_id, expected_capacity, timeout=timeout
    )
    if capacity_after != expected_capacity:
        log.error("Timeout expired: Capacity did not reach expected value within %ds", timeout)
    log.info("Capacity after return: %d (expected: %d)", capacity_after, expected_capacity)
    assert capacity_after == expected_capacity, (
        f"Expected capacity {expected_capacity}, got {capacity_after}"
    )

    # 2.7: Verify instance termination
    log.info("2.7: Verifying instance termination")
    terminate_start = time.time()
    while True:
        state_info = get_instance_state(first_instance)
        if not state_info["exists"] or state_info["state"] in ["terminated", "shutting-down"]:
            log.info("Instance %s successfully terminated/terminating", first_instance)
            break
        if time.time() - terminate_start > MAX_TIME_WAIT_FOR_CAPACITY_PROVISIONING_SEC:
            pytest.fail(f"Instance {first_instance} failed to terminate in time")
        time.sleep(5)

    # === STEP 3: CLEANUP REMAINING INSTANCES ===
    log.info("=== STEP 3: Cleanup Remaining Instances ===")

    remaining_ids = machine_ids[1:]
    if remaining_ids:
        log.info(
            "3.1: Attempting graceful termination of remaining %d instances: %s",
            len(remaining_ids),
            remaining_ids,
        )

        try:
            # Try graceful return first
            return_response = hfm.request_return_machines(remaining_ids)
            return_request_id = _extract_request_id(return_response)

            # Wait for graceful return with timeout
            log.info("3.2: Waiting for graceful return completion (timeout: 120s)")
            graceful_start = time.time()
            graceful_completed = False

            while time.time() - graceful_start < 120:  # 2 minute timeout for graceful return
                if _check_all_ec2_hosts_are_being_terminated(remaining_ids):
                    log.info("Graceful return completed successfully")
                    graceful_completed = True
                    break
                time.sleep(10)

            if not graceful_completed:
                log.warning("Graceful return timed out, proceeding with force cleanup")

        except Exception as e:
            log.warning("Graceful return failed: %s, proceeding with force cleanup", e)

        # For ASG resources, use comprehensive cleanup
        if provider_api == "ASG" or "asg" in provider_api.lower():
            log.info("3.3: Performing comprehensive ASG cleanup")
            _cleanup_asg_resources(remaining_ids, provider_api)
        else:
            # For non-ASG resources, wait for standard termination
            log.info("3.3: Waiting for standard termination completion")
            cleanup_start = time.time()
            while time.time() - cleanup_start < 300:  # 5 minute timeout
                if _check_all_ec2_hosts_are_being_terminated(remaining_ids):
                    log.info("All remaining instances terminated successfully")
                    break
                time.sleep(10)
            else:
                log.warning("Some instances may not have terminated within timeout")

        # Final verification
        log.info("3.4: Verifying complete resource cleanup")
        cleanup_verified = _verify_all_resources_cleaned(remaining_ids, resource_id, provider_api)

        if not cleanup_verified:
            log.error("⚠️  Cleanup verification failed - some resources may still exist")
            # Log remaining resources for debugging
            for instance_id in remaining_ids:
                state_info = get_instance_state(instance_id)
                if state_info["exists"]:
                    log.error(
                        "Instance %s still exists in state: %s", instance_id, state_info["state"]
                    )
        else:
            log.info("✅ All resources successfully cleaned up")

    else:
        log.info("3.1: No remaining instances to clean up")

    log.info("=== TEST COMPLETED SUCCESSFULLY ===")


@pytest.mark.aws
@pytest.mark.slow
@pytest.mark.parametrize("test_case", scenarios.get_test_cases(), ids=lambda tc: tc["test_name"])
def test_full_cycle(setup_host_factory_mock_with_scenario, test_case):
    log.info(test_case["test_name"])

    hfm = setup_host_factory_mock_with_scenario

    res = hfm.get_available_templates()

    template_id = test_case.get("template_id") or test_case["test_name"]
    template_json = next(
        (
            template
            for template in res["templates"]
            if template.get("templateId") == template_id
            or template.get("template_id") == template_id
        ),
        None,
    )

    if template_json is None:
        log.warning(
            "Template %s not found in HostFactory response; defaulting to first template.",
            template_id,
        )
        template_json = res["templates"][0]

    # If ABIS is requested in overrides, prefer verifying via AWS (when enabled)
    abis_override = test_case.get("overrides", {}).get("abisInstanceRequirements") or test_case.get(
        "overrides", {}
    ).get("abis_instance_requirements")
    if abis_override and scenarios.VERIFY_ABIS:
        # Defer to runtime verification after instances are created
        log.info("ABIS override requested; will verify via AWS after provisioning")

    provide_release_control_loop(
        hfm,
        template_json=template_json,
        capacity_to_request=test_case["capacity_to_request"],
        test_case=test_case,
    )
