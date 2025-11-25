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

# Enable to verify ABIS on the created resource (fleet/ASG) via AWS APIs
VERIFY_ABIS = os.environ.get("VERIFY_ABIS", "0") in ("1", "true", "True")

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


def verify_abis_enabled_for_instance(instance_id):
    """
    Given an instance ID, trace back to the parent resource (Fleet or ASG) and
    assert that InstanceRequirements/ABIS are present in the resource config.
    """

    try:
        desc = ec2_client.describe_instances(InstanceIds=[instance_id])
        tags = desc["Reservations"][0]["Instances"][0].get("Tags", [])
    except Exception as e:
        pytest.fail(f"Failed to describe instance {instance_id} to verify ABIS: {e}")

    fleet_id = _get_tag_value(tags, "aws:ec2:fleet-id")
    asg_name = _get_tag_value(tags, "aws:autoscaling:groupName")

    if fleet_id:
        try:
            fleets = ec2_client.describe_fleets(FleetIds=[fleet_id]).get("Fleets", [])
            if not fleets:
                pytest.fail(f"No fleet data found for {fleet_id} while verifying ABIS")
            overrides = fleets[0].get("LaunchTemplateConfigs", [{}])[0].get("Overrides", [])
            has_abis = any("InstanceRequirements" in ov for ov in overrides)
            assert has_abis, f"ABIS not present in fleet overrides for {fleet_id}"
            return
        except Exception as e:
            pytest.fail(f"Failed to verify ABIS on fleet {fleet_id}: {e}")

    if asg_name:
        try:
            asgs = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name]).get(
                "AutoScalingGroups", []
            )
            if not asgs:
                pytest.fail(f"No ASG data found for {asg_name} while verifying ABIS")
            overrides = (
                asgs[0]
                .get("MixedInstancesPolicy", {})
                .get("LaunchTemplate", {})
                .get("Overrides", [])
            )
            has_abis = any("InstanceRequirements" in ov for ov in overrides)
            assert has_abis, f"ABIS not present in ASG overrides for {asg_name}"
            return
        except Exception as e:
            pytest.fail(f"Failed to verify ABIS on ASG {asg_name}: {e}")

    pytest.fail(
        f"Could not determine parent resource (fleet or ASG) for instance {instance_id} to verify ABIS"
    )


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
    """Discover backing fleet/ASG identifier for a given instance."""
    try:
        desc = ec2_client.describe_instances(InstanceIds=[instance_id])
        instance_details = desc["Reservations"][0]["Instances"][0]
        tags = instance_details.get("Tags", [])
    except Exception as e:
        pytest.fail(f"Failed to describe instance {instance_id} for resource lookup: {e}")

    provider_api = provider_api or ""
    provider_api_lower = provider_api.lower()

    if "spotfleet" in provider_api_lower:
        fleet_id = _get_tag_value(tags, "aws:ec2spot:fleet-request-id")
    elif "ec2fleet" in provider_api_lower:
        fleet_id = _get_tag_value(tags, "aws:ec2:fleet-id")
    elif provider_api == "ASG" or "asg" in provider_api_lower:
        fleet_id = _get_tag_value(tags, "aws:autoscaling:groupName")
    else:
        fleet_id = None

    # Cross-check: sometimes SpotFleet scenarios still tag with aws:ec2:fleet-id
    if not fleet_id:
        fleet_id = _get_tag_value(tags, "aws:ec2:fleet-id")

    # Fallbacks if tags are missing
    if not fleet_id and "spotfleet" in provider_api_lower:
        sir_id = instance_details.get("SpotInstanceRequestId")
        if sir_id:
            try:
                sir_desc = ec2_client.describe_spot_instance_requests(
                    SpotInstanceRequestIds=[sir_id]
                )
                sir_details = (sir_desc.get("SpotInstanceRequests") or [{}])[0]
                fleet_id = sir_details.get("SpotFleetRequestId") or fleet_id
            except Exception as e:
                log.debug("Unable to fetch SpotInstanceRequest %s: %s", sir_id, e)
        if not fleet_id:
            try:
                sir_desc = ec2_client.describe_spot_instance_requests(
                    Filters=[{"Name": "instance-id", "Values": [instance_id]}]
                )
                sir_details = (sir_desc.get("SpotInstanceRequests") or [{}])[0]
                fleet_id = sir_details.get("SpotFleetRequestId") or fleet_id
            except Exception as e:
                log.debug(
                    "Unable to fetch SpotInstanceRequest via filter for %s: %s",
                    instance_id,
                    e,
                )
    if not fleet_id:
        fleet_id = _find_spot_fleet_for_instance(instance_id)

    if not fleet_id and provider_api_lower in ("asg", "autoscaling"):
        try:
            asg_instances = asg_client.describe_auto_scaling_instances(
                InstanceIds=[instance_id]
            ).get("AutoScalingInstances", [])
            if asg_instances:
                fleet_id = asg_instances[0].get("AutoScalingGroupName")
        except Exception as e:
            log.debug("Unable to fetch ASG for instance %s: %s", instance_id, e)

    if not fleet_id:
        log.warning(
            "Could not determine backing resource for instance %s; tags=%s lifecycle=%s",
            instance_id,
            tags,
            instance_details.get("InstanceLifecycle"),
        )
        return None
    return fleet_id


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


def _wait_for_spot_fleet_stable(resource_id: str, timeout: int = 300) -> None:
    """Wait until a Spot Fleet is out of modifying state so capacity reflects changes."""
    start = time.time()
    while True:
        try:
            resp = ec2_client.describe_spot_fleet_requests(SpotFleetRequestIds=[resource_id])
            configs = resp.get("SpotFleetRequestConfigs") or []
            state = (configs[0].get("SpotFleetRequestState") or "").lower() if configs else ""
            if state != "modifying":
                return
        except Exception as exc:
            log.debug("Failed to poll Spot Fleet %s state: %s", resource_id, exc)

        if time.time() - start > timeout:
            log.warning("Timed out waiting for Spot Fleet %s to stabilize", resource_id)
            return
        time.sleep(5)


def _wait_for_ec2_fleet_stable(resource_id: str, timeout: int = 300) -> None:
    """Wait until an EC2 Fleet is out of modifying state so capacity reflects changes."""
    start = time.time()
    while True:
        try:
            resp = ec2_client.describe_fleets(FleetIds=[resource_id])
            fleets = resp.get("Fleets") or []
            state = (fleets[0].get("FleetState") or "").lower() if fleets else ""
            if state != "modifying":
                return
        except Exception as exc:
            log.debug("Failed to poll EC2 Fleet %s state: %s", resource_id, exc)

        if time.time() - start > timeout:
            log.warning("Timed out waiting for EC2 Fleet %s to stabilize", resource_id)
            return
        time.sleep(5)


def _find_spot_fleet_for_instance(instance_id: str) -> Optional[str]:
    """Search active spot fleets to locate the instance."""
    try:
        next_token = None
        fleets: list[dict] = []
        while True:
            if next_token:
                resp = ec2_client.describe_spot_fleet_requests(NextToken=next_token)
            else:
                resp = ec2_client.describe_spot_fleet_requests()
            fleets.extend(resp.get("SpotFleetRequestConfigs", []))
            next_token = resp.get("NextToken")
            if not next_token:
                break

        for fleet in fleets:
            fleet_id = fleet.get("SpotFleetRequestId")
            if not fleet_id:
                continue

            inst_next = None
            while True:
                if inst_next:
                    inst_resp = ec2_client.describe_spot_fleet_instances(
                        SpotFleetRequestId=fleet_id, NextToken=inst_next
                    )
                else:
                    inst_resp = ec2_client.describe_spot_fleet_instances(SpotFleetRequestId=fleet_id)

                instances = inst_resp.get("ActiveInstances", [])
                for inst in instances:
                    if inst.get("InstanceId") == instance_id:
                        return fleet_id

                inst_next = inst_resp.get("NextToken")
                if not inst_next:
                    break

    except Exception as e:
        log.debug("Failed to enumerate spot fleets for instance %s: %s", instance_id, e)
    return None


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
    if not expected_price_type:
        log.info("No priceType specified in test case overrides, skipping price type validation")
        return True

    log.info(
        f"Validating price type for all {len(machines)} instances - Expected: {expected_price_type}"
    )

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
    # For parametrized tests, the node name will be like "test_sample[EC2Fleet]"
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


def _wait_for_request_completion(hfm, request_id: str, scheduler_type: str):
    """Poll request status until complete or timeout."""
    request_status_schema = plugin_io_schemas.get_schema_for_scheduler(
        "request_status", scheduler_type
    )
    alt_schema = plugin_io_schemas.expected_request_status_schema_hostfactory
    start_time = time.time()

    while True:
        status_response = hfm.get_request_status(request_id)
        log.debug(json.dumps(status_response, indent=4))

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
            else:
                # Sometimes the API returns just request IDs as strings; accept them when unambiguous
                if isinstance(req, str):
                    if not return_request_id or req == return_request_id:
                        matching_req = {"request_id": req, "status": status_response.get("status")}
                        break
        if not matching_req and requests:
            first = requests[0]
            matching_req = first if isinstance(first, dict) else {"request_id": first, "status": None}

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
        if status_response.get("requests") and status_response["requests"][0]["status"] == "complete":
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
    if VERIFY_ABIS and abis_requested:
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

        if provider_api in ["RunInstances", "ASG"] and expected_price_type == "spot":
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

    return_request_id = hfm.request_return_machines(ec2_instance_ids)
    log.debug(f"Deallocating: {json.dumps(return_request_id, indent=4)}")

    while not _check_all_ec2_hosts_are_being_terminated(ec2_instance_ids):
        status_response = hfm.get_return_requests(return_request_id)
        log.debug(json.dumps(status_response, indent=4))

        res = get_instance_state(ec2_instance_ids[0])
        log.debug(json.dumps(res, indent=4))

        time.sleep(10)

        # "shutting-down"

    # status_response = hfm.get_request_status(request_id)
    # log.debug(json.dumps(status_response, indent=4))

    pass


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


# @pytest.mark.aws
# @pytest.mark.parametrize("test_case", scenarios.get_test_cases(), ids=lambda tc: tc["test_name"])
# def test_sample(setup_host_factory_mock, test_case):
#     log.info(test_case["test_name"])

#     hfm = setup_host_factory_mock

#     res = hfm.get_available_templates()

#     provide_release_control_loop(hfm, template_json=res["templates"][0], capacity_to_request=test_case["capacity_to_request"])


def _partial_return_cases():
    """Pick maintain fleets and ASG scenarios with capacity > 1."""
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
@pytest.mark.parametrize(
    "test_case", _partial_return_cases(), ids=lambda tc: tc["test_name"]
)
def test_partial_return_reduces_capacity(setup_host_factory_mock_with_scenario, test_case):
    """Return one instance and ensure maintain capacity drops by one before draining the rest."""
    log.info("Partial return test: %s", test_case["test_name"])

    hfm = setup_host_factory_mock_with_scenario

    templates_response = hfm.get_available_templates()
    log.debug("Templates response: %s", json.dumps(templates_response, indent=2))
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

    scheduler_type = get_scheduler_from_scenario(test_case)

    request_response = hfm.request_machines(
        template_json.get("templateId") or template_json.get("template_id"),
        test_case["capacity_to_request"],
    )
    parse_and_print_output(request_response)

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

    provider_api = (
        template_json.get("providerApi")
        or template_json.get("provider_api")
        or test_case.get("overrides", {}).get("providerApi")
    )
    log.info("Provisioning request_id=%s provider_api=%s", request_id, provider_api)
    status_response = _wait_for_request_completion(hfm, request_id, scheduler_type)
    _check_request_machines_response_status(status_response)
    _check_all_ec2_hosts_are_being_provisioned(status_response)
    log.debug("Final provisioning status: %s", json.dumps(status_response, indent=2))

    machines = status_response["requests"][0]["machines"]
    machine_ids = [m.get("machineId") or m.get("machine_id") for m in machines]
    assert len(machine_ids) >= 2, "Partial return test requires capacity > 1"

    first_instance = machine_ids[0]
    log.info("Attempting to resolve resource for first_instance=%s via provider_api=%s", first_instance, provider_api)
    resource_id = _get_resource_id_from_instance(first_instance, provider_api)
    if not resource_id:
        pytest.skip(f"Could not determine backing resource for instance {first_instance}")
    capacity_before = _get_capacity(provider_api, resource_id)

    return_response = hfm.request_return_machines([first_instance])
    log.debug("Return response on instance termination: %s", json.dumps(return_response, indent=2))
    return_request_id = _extract_request_id(return_response)
    if not return_request_id:
        log.warning("Return request ID missing; proceeding with status polling by machine id only")

    # _wait_for_return_completion(hfm, [first_instance], return_request_id)
    _wait_for_request_completion(hfm, return_request_id, scheduler_type)

    if provider_api and "spotfleet" in provider_api.lower():
        _wait_for_spot_fleet_stable(resource_id)
    elif provider_api and "ec2fleet" in provider_api.lower():
        _wait_for_ec2_fleet_stable(resource_id)
    elif resource_id.startswith("fleet-"):
        _wait_for_ec2_fleet_stable(resource_id)

    capacity_after = _get_capacity(provider_api, resource_id)
    assert capacity_after == max(capacity_before - 1, 0)

    terminate_start = time.time()
    while True:
        state_info = get_instance_state(first_instance)
        if not state_info["exists"] or state_info["state"] in ["terminated", "shutting-down"]:
            break
        if time.time() - terminate_start > MAX_TIME_WAIT_FOR_CAPACITY_PROVISIONING_SEC:
            pytest.fail(f"Instance {first_instance} failed to terminate in time")
        time.sleep(5)

    remaining_ids = machine_ids[1:]
    if remaining_ids:
        hfm.request_return_machines(remaining_ids)
        while not _check_all_ec2_hosts_are_being_terminated(remaining_ids):
            time.sleep(10)


@pytest.mark.aws
@pytest.mark.parametrize("test_case", scenarios.get_test_cases(), ids=lambda tc: tc["test_name"])
def test_sample(setup_host_factory_mock_with_scenario, test_case):
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
    if abis_override and VERIFY_ABIS:
        # Defer to runtime verification after instances are created
        log.info("ABIS override requested; will verify via AWS after provisioning")

    provide_release_control_loop(
        hfm,
        template_json=template_json,
        capacity_to_request=test_case["capacity_to_request"],
        test_case=test_case,
    )
