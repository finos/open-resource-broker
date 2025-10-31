import json
import logging
import os
import time

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
os.environ["HF_LOGDIR"] = "./logs"  # Set log directory to avoid permission issues
os.environ["AWS_PROVIDER_LOG_DIR"] = "./logss"
os.environ["LOG_DESTINATION"] = "file"

_boto_session = boto3.session.Session()
_ec2_region = (
    os.environ.get("AWS_REGION")
    or os.environ.get("AWS_DEFAULT_REGION")
    or _boto_session.region_name
    or "eu-west-1"
)
ec2_client = _boto_session.client("ec2", region_name=_ec2_region)

log = logging.getLogger("awsome_test")
log.setLevel(logging.DEBUG)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
console_handler.setFormatter(formatter)

file_handler = logging.FileHandler("logs/awsome_test.log")
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(formatter)

log.addHandler(console_handler)
log.addHandler(file_handler)


MAX_TIME_WAIT_FOR_CAPACITY_PROVISIONING_SEC = 120


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
            "instance_lifecycle": instance.get("InstanceLifecycle"),  # None for on-demand, "spot" for spot instances
        }

    except ClientError as e:
        log.error(f"Error getting instance details for {instance_id}: {e}")
        raise


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
        log.warning(f"Instance {instance_id}: Unknown price type '{expected_price_type}', skipping validation")
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
    instance_id = selected_machine["machineId"]

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

    log.info(f"Validating price type for all {len(machines)} instances - Expected: {expected_price_type}")

    all_validations_passed = True

    for machine in machines:
        instance_id = machine["machineId"]

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
        log.error(f"Price type validation FAILED for one or more instances")

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
    os.environ["AWS_PROVIDER_LOG_DIR"] = str(test_config_dir / "logs")
    os.environ["HF_LOGDIR"] = str(test_config_dir / "logs")

    # Create the log and work directories
    (test_config_dir / "logs").mkdir(exist_ok=True)
    (test_config_dir / "work").mkdir(exist_ok=True)

    hfm = HostFactoryMock()

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
    os.environ["AWS_PROVIDER_LOG_DIR"] = str(test_config_dir / "logs")
    os.environ["HF_LOGDIR"] = str(test_config_dir / "logs")

    # Create the log and work directories
    (test_config_dir / "logs").mkdir(exist_ok=True)
    (test_config_dir / "work").mkdir(exist_ok=True)

    hfm = HostFactoryMock()

    return hfm


def _check_request_machines_response_status(status_response):
    assert status_response["requests"][0]["status"] == "complete"
    for machine in status_response["requests"][0]["machines"]:
        # it is possible that ec2 host is still initialising
        assert machine["status"] in ["running", "pending"]


def _check_all_ec2_hosts_are_being_provisioned(status_response):
    for machine in status_response["requests"][0]["machines"]:
        ec2_instance_id = machine["machineId"]
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

    res = hfm.request_machines(template_json["templateId"], capacity_to_request)
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
        log.error(f"AWS provider response missing requestId field.")
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

    try:
        validate_json_schema(
            instance=res, schema=plugin_io_schemas.expected_request_machines_schema
        )
    except ValidationError as e:
        pytest.fail(f"JSON validation failed for request_machines response json: {e}")

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

        try:
            validate_json_schema(
                instance=status_response, schema=plugin_io_schemas.expected_request_status_schema
            )
        except ValidationError as e:
            pytest.fail(f"JSON validation failed for get_reqest_status response json: {e}")

        if time.time() - start_time > MAX_TIME_WAIT_FOR_CAPACITY_PROVISIONING_SEC:
            break
        if status_response["requests"][0]["status"] == "complete":
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

    # Validate price type for all instances if test_case is provided
    if test_case:
        # Check if this provider API supports spot instance validation
        provider_api = template_json.get("providerApi", "EC2Fleet")
        expected_price_type = test_case.get("overrides", {}).get("priceType")

        if provider_api in ["RunInstances", "ASG"] and expected_price_type == "spot":
            log.warning(f"Skipping price type validation for {provider_api} with spot instances - may not be supported")
        else:
            log.info("Starting price type validation for all instances")
            price_type_validation_passed = validate_all_instances_price_type(status_response, test_case)

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
        machine["machineId"] for machine in status_response["requests"][0]["machines"]
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

    try:
        validate_json_schema(
            instance=res, schema=plugin_io_schemas.expected_get_available_templates_schema
        )
    except ValidationError as e:
        pytest.fail(f"JSON validation failed: {e}")


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


@pytest.mark.aws
@pytest.mark.parametrize("test_case", scenarios.get_test_cases(), ids=lambda tc: tc["test_name"])
def test_sample(setup_host_factory_mock_with_scenario, test_case):
    log.info(test_case["test_name"])

    hfm = setup_host_factory_mock_with_scenario

    res = hfm.get_available_templates()

    template_id = test_case.get("template_id") or test_case["test_name"]
    template_json = next(
        (template for template in res["templates"] if template["templateId"] == template_id),
        None,
    )

    if template_json is None:
        log.warning(
            "Template %s not found in HostFactory response; defaulting to first template.",
            template_id,
        )
        template_json = res["templates"][0]

    provide_release_control_loop(
        hfm, template_json=template_json, capacity_to_request=test_case["capacity_to_request"], test_case=test_case
    )
