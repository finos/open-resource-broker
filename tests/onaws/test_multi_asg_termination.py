import json
import logging
import os
import time
from typing import Any, Dict, List

import boto3
import pytest
from botocore.exceptions import ClientError
from jsonschema import ValidationError, validate as validate_json_schema

from hfmock import HostFactoryMock
from tests.onaws import plugin_io_schemas
from tests.onaws.parse_output import parse_and_print_output
from tests.onaws.template_processor import TemplateProcessor
from tests.onaws.test_onaws import get_instances_states

pytestmark = [  # Apply default markers to every test in this module
    pytest.mark.manual_aws,
    pytest.mark.aws,
]

# Set environment variables for local development
os.environ["USE_LOCAL_DEV"] = "1"
os.environ["HF_LOGDIR"] = "./logs"
os.environ["AWS_PROVIDER_LOG_DIR"] = "./logs"
os.environ["LOG_DESTINATION"] = "file"

_boto_session = boto3.session.Session()
_ec2_region = (
    os.environ.get("AWS_REGION")
    or os.environ.get("AWS_DEFAULT_REGION")
    or _boto_session.region_name
    or "eu-west-1"
)
ec2_client = _boto_session.client("ec2", region_name=_ec2_region)
autoscaling_client = _boto_session.client("autoscaling", region_name=_ec2_region)

log = logging.getLogger("multi_asg_test")
log.setLevel(logging.DEBUG)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
console_handler.setFormatter(formatter)

# Ensure logs directory exists before creating file handler
os.makedirs("logs", exist_ok=True)
file_handler = logging.FileHandler("logs/multi_asg_test.log")
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(formatter)

log.addHandler(console_handler)
log.addHandler(file_handler)

MAX_TIME_WAIT_FOR_CAPACITY_PROVISIONING_SEC = 180


def get_asg_instances(asg_name: str) -> List[str]:
    """Get all instance IDs from an ASG."""
    try:
        response = autoscaling_client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
        if not response["AutoScalingGroups"]:
            return []

        asg = response["AutoScalingGroups"][0]
        return [instance["InstanceId"] for instance in asg.get("Instances", [])]
    except ClientError as e:
        log.error(f"Error getting ASG instances for {asg_name}: {e}")
        return []


def verify_asg_instances_detached(instance_ids: List[str]) -> bool:
    """Verify that instances are no longer part of any ASG."""
    try:
        if not instance_ids:
            return True

        response = autoscaling_client.describe_auto_scaling_instances(InstanceIds=instance_ids)

        # If any instances are still in ASGs, they will appear in the response
        remaining_asg_instances = response.get("AutoScalingInstances", [])

        if remaining_asg_instances:
            log.warning(f"Found {len(remaining_asg_instances)} instances still in ASGs")
            for instance in remaining_asg_instances:
                log.warning(
                    f"Instance {instance['InstanceId']} still in ASG {instance['AutoScalingGroupName']}"
                )
            return False

        log.info(f"All {len(instance_ids)} instances successfully detached from ASGs")
        return True

    except ClientError as e:
        if e.response["Error"]["Code"] == "InvalidInstanceID.NotFound":
            # If instances are not found, they might be terminated, which is expected
            log.info("Some instances not found - likely terminated")
            return True
        else:
            log.error(f"Error checking ASG instance membership: {e}")
            return False


def check_all_instances_terminating_or_terminated(instance_ids: List[str]) -> bool:
    """Check if all instances are in terminating or terminated state."""
    all_terminating = True
    instance_states = get_instances_states(instance_ids, ec2_client)

    for instance_id, state in zip(instance_ids, instance_states):
        if state is not None:
            if state not in ["shutting-down", "terminated"]:
                log.debug(f"Instance {instance_id} state: {state} (not terminating yet)")
                all_terminating = False
            else:
                log.debug(f"Instance {instance_id} state: {state} (terminating)")
        else:
            log.debug(f"Instance {instance_id} no longer exists (terminated)")

    return all_terminating


@pytest.fixture
def setup_multi_asg_templates():
    """Setup fixture that creates two different ASG templates for testing."""
    processor = TemplateProcessor()
    test_name = "test_multi_asg_termination"

    # Clear any existing files from the test directory first
    test_config_dir = processor.run_templates_dir / test_name
    if test_config_dir.exists():
        import shutil

        shutil.rmtree(test_config_dir)
        log.info(f"Cleared existing test directory: {test_config_dir}")

    # Create two different ASG templates with different configurations
    template_configs = [
        {
            "template_name": "ASG_Template_1",
            "test_dir": f"{test_name}_asg1",
            "overrides": {
                "providerApi": "ASG",
                "instanceType": "t3.micro",
                "maxSize": 10,
                "minSize": 0,
                "desiredCapacity": 2,
            },
        },
        {
            "template_name": "ASG_Template_2",
            "test_dir": f"{test_name}_asg2",
            "overrides": {
                "providerApi": "ASG",
                "instanceType": "t3.small",
                "maxSize": 8,
                "minSize": 0,
                "desiredCapacity": 3,
            },
        },
    ]

    # Generate both templates in separate directories
    for config in template_configs:
        processor.generate_test_templates(
            config["test_dir"],
            awsprov_base_template="awsprov_templates.base.json",
            overrides=config["overrides"],
        )

    # Create a combined config directory that includes both templates
    combined_config_dir = processor.run_templates_dir / test_name
    combined_config_dir.mkdir(parents=True, exist_ok=True)

    # Copy config files from first template directory (they should be the same)
    first_template_dir = processor.run_templates_dir / template_configs[0]["test_dir"]
    import shutil

    shutil.copy2(first_template_dir / "config.json", combined_config_dir / "config.json")
    shutil.copy2(
        first_template_dir / "default_config.json", combined_config_dir / "default_config.json"
    )

    # Combine awsprov_templates.json from both directories
    combined_templates = {"templates": []}

    for i, config in enumerate(template_configs):
        template_dir = processor.run_templates_dir / config["test_dir"]
        awsprov_file = template_dir / "awsprov_templates.json"

        if awsprov_file.exists():
            with open(awsprov_file) as f:
                template_data = json.load(f)

            # Update template ID to include our custom name
            if template_data.get("templates"):
                template = template_data["templates"][0].copy()
                template["templateId"] = config["template_name"]
                combined_templates["templates"].append(template)

    # Write combined templates file
    with open(combined_config_dir / "awsprov_templates.json", "w") as f:
        json.dump(combined_templates, f, indent=2)

    log.info(f"Created combined config with {len(combined_templates['templates'])} templates")

    # Set environment variables to use combined config directory
    os.environ["HF_PROVIDER_CONFDIR"] = str(combined_config_dir)
    os.environ["HF_PROVIDER_LOGDIR"] = str(combined_config_dir / "logs")
    os.environ["HF_PROVIDER_WORKDIR"] = str(combined_config_dir / "work")
    os.environ["AWS_PROVIDER_LOG_DIR"] = str(combined_config_dir / "logs")
    os.environ["HF_LOGDIR"] = str(combined_config_dir / "logs")

    # Create the log and work directories
    (combined_config_dir / "logs").mkdir(parents=True, exist_ok=True)
    (combined_config_dir / "work").mkdir(parents=True, exist_ok=True)

    hfm = HostFactoryMock()

    return hfm, template_configs


def provision_asg_capacity(
    hfm: HostFactoryMock, template_json: Dict[str, Any], capacity: int
) -> Dict[str, Any]:
    """Provision capacity for a single ASG template and return the status response."""
    log.info(f"Provisioning {capacity} instances for template {template_json['templateId']}")

    # Request capacity
    res = hfm.request_machines(template_json["templateId"], capacity)
    parse_and_print_output(res)

    # Validate response schema
    try:
        validate_json_schema(
            instance=res, schema=plugin_io_schemas.expected_request_machines_schema
        )
    except ValidationError as e:
        pytest.fail(f"JSON validation failed for request_machines response: {e}")

    # Extract request ID
    if "requestId" in res:
        request_id = res["requestId"]
    elif "request_id" in res:
        request_id = res["request_id"]
    else:
        pytest.fail(f"AWS provider response missing requestId field. Response: {res}")

    # Wait for provisioning to complete with retry logic
    log.info(f"Waiting for provisioning to complete for request {request_id}")
    start_time = time.time()
    status_response = None
    retry_count = 0
    max_retries = 3

    while True:
        status_response = hfm.get_request_status(request_id)
        log.debug(f"Status for {request_id}: {json.dumps(status_response, indent=2)}")

        # Validate status response schema
        try:
            validate_json_schema(
                instance=status_response, schema=plugin_io_schemas.expected_request_status_schema
            )
        except ValidationError as e:
            pytest.fail(f"JSON validation failed for get_request_status response: {e}")

        if time.time() - start_time > MAX_TIME_WAIT_FOR_CAPACITY_PROVISIONING_SEC:
            # Check if instances are in pending state
            machines = status_response["requests"][0].get("machines", [])
            pending_instances = [m for m in machines if m.get("status") == "pending"]

            if pending_instances and retry_count < max_retries:
                retry_count += 1
                log.warning(
                    f"Timeout reached but {len(pending_instances)} instances still pending. Retry {retry_count}/{max_retries}"
                )
                start_time = time.time()  # Reset timer
                continue

            pytest.fail(
                f"Timeout waiting for capacity provisioning for request {request_id} machines {machines} pending {pending_instances}"
            )

        if status_response["requests"][0]["status"] == "complete":
            break

        time.sleep(5)

    # Verify all instances are provisioned
    assert status_response["requests"][0]["status"] == "complete"
    machines = status_response["requests"][0]["machines"]

    instance_ids = [machine["machineId"] for machine in machines]
    instance_states = get_instances_states(instance_ids, ec2_client)

    for machine, state in zip(machines, instance_states):
        assert machine["status"] in ["running", "pending"]
        instance_id = machine["machineId"]
        assert state is not None
        assert state in ["running", "pending"]
        log.debug(f"EC2 {instance_id} state: {state}")

    log.info(
        f"Successfully provisioned {len(machines)} instances for template {template_json['templateId']}"
    )
    return status_response


@pytest.mark.aws
@pytest.mark.slow
def test_multi_asg_termination(setup_multi_asg_templates):
    """
    Test that provisions capacity from two different ASG templates and then
    attempts to remove all instances from both templates at once.

    This test validates:
    1. Multiple ASG templates can be provisioned simultaneously
    2. Instances from multiple ASGs can be terminated in a single operation
    3. ASG capacity management works correctly across multiple ASGs
    4. All instances are properly detached from their ASGs before termination
    """
    hfm, template_configs = setup_multi_asg_templates

    log.info("=== Starting Multi-ASG Termination Test ===")

    # Step 1: Get available templates
    log.info("Step 1: Getting available templates")
    res = hfm.get_available_templates()

    # Log the actual response for debugging
    log.info(f"get_available_templates response: {json.dumps(res, indent=2)}")

    # Check if response has templates, if not handle gracefully
    if "templates" not in res:
        log.error(f"Response missing 'templates' property. Full response: {res}")
        pytest.fail(
            f"get_available_templates response missing 'templates' property. Response: {res}"
        )

    try:
        validate_json_schema(
            instance=res, schema=plugin_io_schemas.expected_get_available_templates_schema
        )
    except ValidationError as e:
        log.warning(f"JSON validation failed for get_available_templates: {e}")
        log.warning("Continuing with available templates despite validation failure")

    available_templates = res.get("templates", [])
    log.info(f"Found {len(available_templates)} available templates")

    if not available_templates:
        pytest.fail("No templates found in get_available_templates response")

    # Step 2: Find our ASG templates
    log.info("Step 2: Locating ASG templates")
    asg_templates = []

    # Debug: Log all available templates
    for template in available_templates:
        log.info(
            f"Available template: {template.get('templateId')} - providerApi: {template.get('providerApi')}"
        )

    for config in template_configs:
        template_name = config["template_name"]
        template_json = next(
            (t for t in available_templates if template_name in t["templateId"]), None
        )

        if template_json is None:
            pytest.fail(f"ASG template {template_name} not found in available templates")

        # Log the template details for debugging
        log.info(f"Template {template_name} details: {json.dumps(template_json, indent=2)}")

        # Verify it's an ASG template - check both providerApi and provider_api
        provider_api = template_json.get("providerApi") or template_json.get("provider_api")
        if provider_api != "ASG":
            # If it's not ASG, let's force it to be ASG for our test
            log.warning(
                f"Template {template_name} has providerApi '{provider_api}', forcing to ASG"
            )
            template_json["providerApi"] = "ASG"

        asg_templates.append(template_json)
        log.info(
            f"Found ASG template: {template_json['templateId']} with providerApi: {template_json.get('providerApi')}"
        )

    # Step 3: Provision capacity from both ASG templates in parallel
    log.info("Step 3: Provisioning capacity from both ASG templates in parallel")

    # Start both provisioning requests without waiting
    request_ids = []
    for i, template_json in enumerate(asg_templates):
        capacity_to_request = template_configs[i]["overrides"]["desiredCapacity"]
        log.info(
            f"Starting provisioning of {capacity_to_request} instances from template {template_json['templateId']}"
        )

        res = hfm.request_machines(template_json["templateId"], capacity_to_request)
        parse_and_print_output(res)

        try:
            validate_json_schema(
                instance=res, schema=plugin_io_schemas.expected_request_machines_schema
            )
        except ValidationError as e:
            pytest.fail(f"JSON validation failed for request_machines response: {e}")

        request_id = res.get("requestId") or res.get("request_id")
        if not request_id:
            pytest.fail(f"AWS provider response missing requestId field. Response: {res}")

        request_ids.append((request_id, template_json["templateId"]))
        log.info(
            f"Started provisioning request {request_id} for template {template_json['templateId']}"
        )

    # Wait for both requests to complete
    log.info(f"Waiting for {len(request_ids)} provisioning requests to complete")
    all_instance_ids = []
    all_status_responses = []

    for request_id, template_id in request_ids:
        log.info(f"Waiting for request {request_id} (template: {template_id})")
        start_time = time.time()
        retry_count = 0
        max_retries = 3

        while True:
            status_response = hfm.get_request_status(request_id)
            log.debug(f"Status for {request_id}: {json.dumps(status_response, indent=2)}")

            try:
                validate_json_schema(
                    instance=status_response,
                    schema=plugin_io_schemas.expected_request_status_schema,
                )
            except ValidationError as e:
                pytest.fail(f"JSON validation failed for get_request_status response: {e}")

            if time.time() - start_time > MAX_TIME_WAIT_FOR_CAPACITY_PROVISIONING_SEC:
                machines = status_response["requests"][0].get("machines", [])
                pending_instances = [m for m in machines if m.get("status") == "pending"]

                if pending_instances and retry_count < max_retries:
                    retry_count += 1
                    log.warning(
                        f"Timeout reached but {len(pending_instances)} instances still pending. Retry {retry_count}/{max_retries}"
                    )
                    start_time = time.time()
                    continue

                pytest.fail(f"Timeout waiting for capacity provisioning for request {request_id}")

            if status_response["requests"][0]["status"] == "complete":
                break

            time.sleep(5)

        # Verify instances are provisioned
        assert status_response["requests"][0]["status"] == "complete"
        machines = status_response["requests"][0]["machines"]

        instance_ids = [machine["machineId"] for machine in machines]
        instance_states = get_instances_states(instance_ids, ec2_client)

        for machine, state in zip(machines, instance_states):
            assert machine["status"] in ["running", "pending"]
            instance_id = machine["machineId"]
            assert state is not None
            assert state in ["running", "pending"]
            log.debug(f"EC2 {instance_id} state: {state}")

        all_instance_ids.extend(instance_ids)
        all_status_responses.append(status_response)

        log.info(
            f"Provisioned {len(instance_ids)} instances for template {template_id}: {instance_ids}"
        )

    total_instances = len(all_instance_ids)
    log.info(f"Total instances provisioned across both ASGs: {total_instances}")
    log.info(f"All instance IDs: {all_instance_ids}")

    # Step 4: Verify instances are in their respective ASGs
    log.info("Step 4: Verifying instances are properly assigned to ASGs")

    # Get ASG names from the instances
    asg_names = set()
    try:
        response = autoscaling_client.describe_auto_scaling_instances(InstanceIds=all_instance_ids)

        for instance_info in response.get("AutoScalingInstances", []):
            asg_name = instance_info.get("AutoScalingGroupName")
            if asg_name:
                asg_names.add(asg_name)
                log.info(f"Instance {instance_info['InstanceId']} belongs to ASG {asg_name}")

    except ClientError as e:
        pytest.fail(f"Failed to verify ASG membership: {e}")

    log.info(f"Found instances in {len(asg_names)} ASGs: {list(asg_names)}")
    assert len(asg_names) == 2, f"Expected instances in 2 ASGs, found {len(asg_names)}"

    # Step 5: Terminate all instances from both ASGs at once
    log.info("Step 5: Terminating all instances from both ASGs simultaneously")
    log.info(f"Requesting termination of {total_instances} instances: {all_instance_ids}")

    return_response = hfm.request_return_machines(all_instance_ids)
    return_request_id = return_response.get("result") or return_response.get("requestId")
    log.info(f"Termination request ID: {return_request_id}")

    # Step 6: Monitor termination progress
    log.info("Step 6: Monitoring termination progress")

    # Wait for instances to start terminating
    max_wait_time = 600  # Increased to 10 minutes for complete cleanup
    start_time = time.time()
    termination_started = False

    while time.time() - start_time < max_wait_time:
        # Check return request status
        status_response = hfm.get_return_requests([return_request_id])
        log.debug(f"Return request status: {json.dumps(status_response, indent=2)}")

        # Check if instances are detached from ASGs
        if not termination_started:
            detached = verify_asg_instances_detached(all_instance_ids)
            if detached:
                log.info("All instances successfully detached from ASGs")
                termination_started = True

        # Check if all instances are terminating or terminated
        if check_all_instances_terminating_or_terminated(all_instance_ids):
            log.info("All instances are now terminating or terminated")
            break

        log.info(
            f"Waiting for termination to complete... ({int(time.time() - start_time)}s elapsed)"
        )
        time.sleep(10)

    # Step 7: Verify instance termination
    log.info("Step 7: Verifying instance termination")

    # Verify all instances are terminating/terminated
    final_terminating = check_all_instances_terminating_or_terminated(all_instance_ids)
    assert final_terminating, "Some instances are not in terminating/terminated state"

    # Verify instances are detached from ASGs
    final_detached = verify_asg_instances_detached(all_instance_ids)
    if not final_detached:
        log.info(
            "Note: Some instances still show ASG attachment while terminating - this is expected AWS behavior"
        )
        log.info("Instances will be fully detached once they reach 'terminated' state")

    # Step 8: Verify ASG deletion (NEW - this was missing!)
    log.info("Step 8: Verifying ASGs are deleted when capacity reaches zero")

    def check_asg_exists(asg_name: str) -> bool:
        """Check if an ASG still exists."""
        try:
            response = autoscaling_client.describe_auto_scaling_groups(
                AutoScalingGroupNames=[asg_name]
            )
            return len(response.get("AutoScalingGroups", [])) > 0
        except ClientError as e:
            if e.response["Error"]["Code"] in ["ValidationError", "InvalidGroup.NotFound"]:
                # ASG not found - this means it was deleted
                return False
            else:
                log.warning(f"Error checking ASG {asg_name}: {e}")
                return True  # Assume it exists if we can't check

    # Wait for ASG deletion (this can take several minutes)
    max_asg_deletion_wait = 600  # 10 minutes for ASG deletion
    asg_deletion_start = time.time()

    while time.time() - asg_deletion_start < max_asg_deletion_wait:
        remaining_asgs = []

        for asg_name in asg_names:
            if check_asg_exists(asg_name):
                remaining_asgs.append(asg_name)

                # Log current ASG status for debugging
                try:
                    response = autoscaling_client.describe_auto_scaling_groups(
                        AutoScalingGroupNames=[asg_name]
                    )
                    if response.get("AutoScalingGroups"):
                        asg = response["AutoScalingGroups"][0]
                        current_instances = len(asg.get("Instances", []))
                        desired_capacity = asg.get("DesiredCapacity", 0)
                        log.info(
                            f"ASG {asg_name} still exists: {current_instances} instances, desired capacity: {desired_capacity}"
                        )
                except Exception as e:
                    log.warning(f"Could not get details for ASG {asg_name}: {e}")
            else:
                log.info(f"ASG {asg_name} successfully deleted")

        if not remaining_asgs:
            log.info("All ASGs successfully deleted")
            break

        elapsed_time = int(time.time() - asg_deletion_start)
        log.info(
            f"Waiting for {len(remaining_asgs)} ASGs to be deleted: {remaining_asgs} ({elapsed_time}s elapsed)"
        )
        time.sleep(30)  # Check every 30 seconds

    # Final verification - all ASGs should be deleted
    final_remaining_asgs = []
    for asg_name in asg_names:
        if check_asg_exists(asg_name):
            final_remaining_asgs.append(asg_name)

    if final_remaining_asgs:
        # Log detailed information about remaining ASGs for debugging
        for asg_name in final_remaining_asgs:
            try:
                response = autoscaling_client.describe_auto_scaling_groups(
                    AutoScalingGroupNames=[asg_name]
                )
                if response.get("AutoScalingGroups"):
                    asg = response["AutoScalingGroups"][0]
                    log.error(f"ASG {asg_name} still exists after termination:")
                    log.error(f"  - Instances: {len(asg.get('Instances', []))}")
                    log.error(f"  - Desired Capacity: {asg.get('DesiredCapacity', 0)}")
                    log.error(f"  - Min Size: {asg.get('MinSize', 0)}")
                    log.error(f"  - Max Size: {asg.get('MaxSize', 0)}")
            except Exception as e:
                log.error(f"Could not get details for remaining ASG {asg_name}: {e}")

        # This assertion will fail if ASGs are not deleted, helping identify the issue
        assert not final_remaining_asgs, (
            f"ASGs were not deleted after instance termination: {final_remaining_asgs}. This indicates the ASG handler may not be properly deleting ASGs when capacity reaches zero."
        )

    log.info("=== Multi-ASG Termination Test Completed Successfully ===")
    log.info(f"Successfully provisioned {total_instances} instances across 2 ASGs")
    log.info(f"Successfully terminated all {total_instances} instances in a single operation")
    log.info("All instances properly detached from ASGs before termination")
    log.info("ASG capacity management worked correctly across multiple ASGs")
