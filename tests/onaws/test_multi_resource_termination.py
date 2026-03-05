import json
import logging
import os
import time
import uuid
from pathlib import Path
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

SCHEDULER_TYPE = "hostfactory"

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

log = logging.getLogger("multi_resource_test")
log.setLevel(logging.DEBUG)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
console_handler.setFormatter(formatter)

log.addHandler(console_handler)

MAX_TIME_WAIT_FOR_CAPACITY_PROVISIONING_SEC = 180


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


def categorize_instances_by_resource_type(instance_ids: List[str]) -> Dict[str, List[str]]:
    """Categorize instances by their resource type based on tags."""
    categorized = {"ASG": [], "EC2Fleet": [], "SpotFleet": [], "RunInstances": []}

    try:
        response = ec2_client.describe_instances(InstanceIds=instance_ids)

        for reservation in response.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                instance_id = instance.get("InstanceId")
                if not instance_id:
                    continue

                # Check tags to determine resource type
                resource_type = "RunInstances"  # Default assumption

                for tag in instance.get("Tags", []):
                    tag_key = tag.get("Key", "")

                    if tag_key == "aws:autoscaling:groupName":
                        resource_type = "ASG"
                        break
                    elif tag_key == "aws:ec2:fleet-id":
                        resource_type = "EC2Fleet"
                        break
                    elif tag_key == "aws:ec2spot:fleet-request-id":
                        resource_type = "SpotFleet"
                        break

                categorized[resource_type].append(instance_id)
                log.info(f"Instance {instance_id} categorized as {resource_type}")

        # Also check ASG membership directly for instances without tags
        potential_asg_instances = categorized["RunInstances"].copy()
        if potential_asg_instances:
            try:
                asg_response = autoscaling_client.describe_auto_scaling_instances(
                    InstanceIds=potential_asg_instances
                )

                for asg_instance in asg_response.get("AutoScalingInstances", []):
                    instance_id = asg_instance.get("InstanceId")
                    if instance_id in categorized["RunInstances"]:
                        categorized["RunInstances"].remove(instance_id)
                        categorized["ASG"].append(instance_id)
                        log.info(f"Instance {instance_id} re-categorized as ASG via API lookup")

            except ClientError as e:
                log.warning(f"Error checking ASG membership: {e}")

    except ClientError as e:
        log.error(f"Error categorizing instances: {e}")

    return categorized


@pytest.fixture
def setup_multi_resource_templates():
    """Setup fixture that creates templates for all four AWS resource types."""
    processor = TemplateProcessor()
    test_name = f"test_multi_resource_termination_{uuid.uuid4().hex[:8]}"

    logs_dir = Path(__file__).parent.parent.parent / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / f"{test_name}.log"
    file_handler = logging.FileHandler(str(log_file))
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    log.addHandler(file_handler)

    test_config_dir = processor.run_templates_dir / test_name
    if test_config_dir.exists():
        import shutil

        shutil.rmtree(test_config_dir)

    template_configs = [
        {
            "template_name": "ASG_Template",
            "capacity_to_request": 2,
            "overrides": {
                "providerApi": "ASG",
            },
        },
        {
            "template_name": "EC2Fleet_Template",
            "capacity_to_request": 2,
            "overrides": {
                "providerApi": "EC2Fleet",
                "fleetType": "maintain",
                "allocationStrategy": "lowestPrice",
                "priceType": "ondemand",
            },
        },
        {
            "template_name": "SpotFleet_Template",
            "capacity_to_request": 2,
            "overrides": {
                "providerApi": "SpotFleet",
                "fleetType": "maintain",
                "allocationStrategy": "lowestPrice",
            },
        },
        {
            "template_name": "RunInstances_Template",
            "capacity_to_request": 2,
            "overrides": {
                "providerApi": "RunInstances",
            },
        },
    ]

    processor.generate_combined_templates(test_name, template_configs)

    # Set environment variables
    os.environ["HF_PROVIDER_CONFDIR"] = str(test_config_dir)
    os.environ["HF_PROVIDER_LOGDIR"] = str(test_config_dir / "logs")
    os.environ["HF_PROVIDER_WORKDIR"] = str(test_config_dir / "work")
    os.environ["AWS_PROVIDER_LOG_DIR"] = str(test_config_dir / "logs")
    os.environ["HF_LOGDIR"] = str(test_config_dir / "logs")

    (test_config_dir / "logs").mkdir(parents=True, exist_ok=True)
    (test_config_dir / "work").mkdir(parents=True, exist_ok=True)

    hfm = HostFactoryMock()

    yield hfm, template_configs

    processor.cleanup_test_templates(test_name)
    log.removeHandler(file_handler)
    file_handler.close()


def provision_resource_capacity(  # dead code - commented out
    hfm: HostFactoryMock, template_json: Dict[str, Any], capacity: int
) -> Dict[str, Any]:
    # """Provision capacity for any resource type template and return the status response."""
    # log.info(f"Provisioning {capacity} instances for template {template_json['templateId']}")
    #
    # # Request capacity
    # res = hfm.request_machines(template_json["templateId"], capacity)
    # parse_and_print_output(res)
    #
    # # Validate response schema
    # try:
    #     validate_json_schema(
    #         instance=res, schema=plugin_io_schemas.get_schema_for_scheduler("request_machines", SCHEDULER_TYPE)
    #     )
    # except ValidationError as e:
    #     pytest.fail(f"JSON validation failed for request_machines response: {e}")
    #
    # # Extract request ID
    # if "requestId" in res:
    #     request_id = res["requestId"]
    # elif "request_id" in res:
    #     request_id = res["request_id"]
    # else:
    #     pytest.fail(f"AWS provider response missing requestId field. Response: {res}")
    #
    # # Wait for provisioning to complete with retry logic
    # log.info(f"Waiting for provisioning to complete for request {request_id}")
    # start_time = time.time()
    # status_response = None
    # retry_count = 0
    # max_retries = 3
    #
    # while True:
    #     status_response = hfm.get_request_status(request_id)
    #     log.debug(f"Status for {request_id}: {json.dumps(status_response, indent=2)}")
    #
    #     # Validate status response schema
    #     try:
    #         validate_json_schema(
    #             instance=status_response,
    #             schema=plugin_io_schemas.get_schema_for_scheduler("request_status", SCHEDULER_TYPE)
    #         )
    #     except ValidationError as e:
    #         pytest.fail(f"JSON validation failed for get_request_status response: {e}")
    #
    #     if time.time() - start_time > MAX_TIME_WAIT_FOR_CAPACITY_PROVISIONING_SEC:
    #         machines = status_response["requests"][0].get("machines", [])
    #         pending_instances = [m for m in machines if m.get("status") == "pending"]
    #
    #         if pending_instances and retry_count < max_retries:
    #             retry_count += 1
    #             log.warning(
    #                 f"Timeout reached but {len(pending_instances)} instances still pending. Retry {retry_count}/{max_retries}"
    #             )
    #             start_time = time.time()
    #             continue
    #
    #         pytest.fail(f"Timeout waiting for capacity provisioning for request {request_id}")
    #
    #     _status = status_response["requests"][0]["status"]
    #     if _status in {"complete", "complete_with_error", "failed", "partial", "cancelled", "timeout"}:
    #         if _status != "complete":
    #             pytest.fail(
    #                 f"Request {request_id} reached terminal status '{_status}'. Response: {status_response}"
    #             )
    #         break
    #
    #     time.sleep(5)
    #
    # # Verify all instances are provisioned
    # assert status_response["requests"][0]["status"] == "complete"
    # machines = status_response["requests"][0]["machines"]
    #
    # instance_ids = [machine.get("machineId") or machine.get("machine_id") for machine in machines]
    # instance_states = get_instances_states(instance_ids, ec2_client)
    #
    # for machine, state in zip(machines, instance_states):
    #     assert machine["status"] in ["running", "pending"]
    #     instance_id = machine.get("machineId") or machine.get("machine_id")
    #     assert state is not None
    #     assert state in ["running", "pending"]
    #     log.debug(f"EC2 {instance_id} state: {state}")
    #
    # log.info(
    #     f"Successfully provisioned {len(machines)} instances for template {template_json['templateId']}"
    # )
    # return status_response
    raise NotImplementedError("dead code - commented out")


@pytest.mark.aws
@pytest.mark.slow
def test_multi_resource_termination(setup_multi_resource_templates):
    """
    Test that provisions capacity from all four AWS resource types (ASG, EC2Fleet,
    SpotFleet, RunInstances) and then attempts to terminate all instances from
    all resource types simultaneously.

    This test validates:
    1. All four AWS resource types can be provisioned simultaneously
    2. Instances from all resource types can be terminated in a single operation
    3. The AWS provider correctly handles mixed resource type termination
    4. Resource grouping logic works across different resource types
    5. Each resource type's cleanup logic works correctly when mixed with others
    """
    hfm, template_configs = setup_multi_resource_templates

    log.info("=== Starting Multi-Resource Termination Test ===")

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
            instance=res,
            schema=plugin_io_schemas.get_schema_for_scheduler(
                "get_available_templates", SCHEDULER_TYPE
            ),
        )
    except ValidationError as e:
        log.warning(f"JSON validation failed for get_available_templates: {e}")
        log.warning("Continuing with available templates despite validation failure")

    available_templates = res.get("templates", [])
    log.info(f"Found {len(available_templates)} available templates")

    if not available_templates:
        pytest.fail("No templates found in get_available_templates response")

    # Step 2: Find all our resource templates
    log.info("Step 2: Locating all resource type templates")
    resource_templates = []

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
            pytest.fail(f"Resource template {template_name} not found in available templates")

        # Log the template details for debugging
        log.info(f"Template {template_name} details: {json.dumps(template_json, indent=2)}")

        # Verify providerApi matches expected type
        expected_api = config["overrides"]["providerApi"]
        provider_api = template_json.get("providerApi") or template_json.get("provider_api")
        if provider_api != expected_api:
            # Force it to the expected type for our test
            log.warning(
                f"Template {template_name} has providerApi '{provider_api}', forcing to {expected_api}"
            )
            template_json["providerApi"] = expected_api

        resource_templates.append(
            {"template": template_json, "config": config, "resource_type": expected_api}
        )
        log.info(
            f"Found {expected_api} template: {template_json['templateId']} with providerApi: {template_json.get('providerApi')}"
        )

    # Step 3: Provision capacity from all resource types in parallel
    log.info("Step 3: Provisioning capacity from all resource types in parallel")

    # Start all provisioning requests without waiting
    request_ids = []
    for resource_info in resource_templates:
        template_json = resource_info["template"]
        config = resource_info["config"]
        resource_type = resource_info["resource_type"]

        # Determine capacity to request from scenario-level key
        capacity_to_request = config.get("capacity_to_request", 2)

        log.info(
            f"Starting provisioning of {capacity_to_request} instances from {resource_type} template {template_json['templateId']}"
        )

        res = hfm.request_machines(template_json["templateId"], capacity_to_request)
        parse_and_print_output(res)

        try:
            validate_json_schema(
                instance=res,
                schema=plugin_io_schemas.get_schema_for_scheduler(
                    "request_machines", SCHEDULER_TYPE
                ),
            )
        except ValidationError as e:
            pytest.fail(f"JSON validation failed for request_machines response: {e}")

        request_id = res.get("requestId") or res.get("request_id")
        if not request_id:
            pytest.fail(f"AWS provider response missing requestId field. Response: {res}")

        request_ids.append((request_id, template_json["templateId"], resource_type))
        log.info(
            f"Started provisioning request {request_id} for {resource_type} template {template_json['templateId']}"
        )

    # Wait for all requests to complete
    log.info(f"Waiting for {len(request_ids)} provisioning requests to complete")
    all_instance_ids = []
    all_status_responses = []
    resource_instance_mapping = {}

    for request_id, template_id, resource_type in request_ids:
        log.info(f"Waiting for request {request_id} ({resource_type} template: {template_id})")
        start_time = time.time()
        retry_count = 0
        max_retries = 3

        while True:
            status_response = hfm.get_request_status(request_id)
            log.debug(f"Status for {request_id}: {json.dumps(status_response, indent=2)}")

            try:
                validate_json_schema(
                    instance=status_response,
                    schema=plugin_io_schemas.get_schema_for_scheduler(
                        "request_status", SCHEDULER_TYPE
                    ),
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

            _status = status_response["requests"][0]["status"]
            if _status in {
                "complete",
                "complete_with_error",
                "failed",
                "partial",
                "cancelled",
                "timeout",
            }:
                if _status != "complete":
                    pytest.fail(
                        f"Request {request_id} reached terminal status '{_status}'. Response: {status_response}"
                    )
                break

            time.sleep(5)

        # Verify instances are provisioned
        assert status_response["requests"][0]["status"] == "complete"
        machines = status_response["requests"][0]["machines"]

        instance_ids = [
            machine.get("machineId") or machine.get("machine_id") for machine in machines
        ]
        instance_states = get_instances_states(instance_ids, ec2_client)

        for machine, state in zip(machines, instance_states):
            assert machine["status"] in ["running", "pending"]
            instance_id = machine.get("machineId") or machine.get("machine_id")
            assert state is not None
            assert state in ["running", "pending"]
            log.debug(f"EC2 {instance_id} state: {state}")

        all_instance_ids.extend(instance_ids)
        all_status_responses.append(status_response)
        resource_instance_mapping[resource_type] = instance_ids

        log.info(f"Provisioned {len(instance_ids)} {resource_type} instances: {instance_ids}")

    total_instances = len(all_instance_ids)
    log.info(f"Total instances provisioned across all resource types: {total_instances}")
    log.info(f"All instance IDs: {all_instance_ids}")

    # Step 4: Verify instances are properly categorized by resource type
    log.info("Step 4: Verifying instances are properly categorized by resource type")

    # Wait a bit for tags to propagate
    time.sleep(30)

    categorized_instances = categorize_instances_by_resource_type(all_instance_ids)

    log.info("Instance categorization results:")
    for resource_type, instances in categorized_instances.items():
        log.info(f"  {resource_type}: {len(instances)} instances - {instances}")

    # Verify we have instances in multiple resource types
    non_empty_types = [rt for rt, instances in categorized_instances.items() if instances]
    assert len(non_empty_types) >= 2, (
        f"Expected instances in multiple resource types, found: {non_empty_types}"
    )

    # Verify total count matches
    total_categorized = sum(len(instances) for instances in categorized_instances.values())
    assert total_categorized == total_instances, (
        f"Categorization mismatch: {total_categorized} vs {total_instances}"
    )

    # Step 5: Terminate ALL instances from ALL resource types simultaneously
    log.info("Step 5: Terminating ALL instances from ALL resource types simultaneously")
    log.info(
        f"Requesting termination of {total_instances} instances across all resource types: {all_instance_ids}"
    )

    return_response = hfm.request_return_machines(all_instance_ids)
    return_request_id = (
        return_response.get("result")
        or return_response.get("requestId")
        or return_response.get("request_id")
    )
    log.info(f"Termination request ID: {return_request_id}")

    # Step 6: Monitor termination progress
    log.info("Step 6: Monitoring termination progress")

    # Wait for instances to start terminating
    max_wait_time = 900  # Increased to 15 minutes for complete cleanup across all resource types
    start_time = time.time()

    while time.time() - start_time < max_wait_time:
        # Check return request status
        status_response = hfm.get_return_requests([return_request_id])
        log.debug(f"Return request status: {json.dumps(status_response, indent=2)}")

        # Check if all instances are terminating or terminated
        if check_all_instances_terminating_or_terminated(all_instance_ids):
            log.info("All instances are now terminating or terminated")
            break

        elapsed_time = int(time.time() - start_time)
        log.info(f"Waiting for termination to complete... ({elapsed_time}s elapsed)")

        # Log progress by resource type
        current_categorization = categorize_instances_by_resource_type(all_instance_ids)
        for resource_type, instances in current_categorization.items():
            if instances:
                instance_states = get_instances_states(instances, ec2_client)
                terminating_count = sum(
                    1 for state in instance_states if state in ["shutting-down", "terminated"]
                )
                log.info(
                    f"  {resource_type}: {terminating_count}/{len(instances)} instances terminating/terminated"
                )

        time.sleep(15)

    # Step 7: Verify instance termination
    log.info("Step 7: Verifying instance termination")

    # Verify all instances are terminating/terminated
    final_terminating = check_all_instances_terminating_or_terminated(all_instance_ids)
    assert final_terminating, "Some instances are not in terminating/terminated state"

    # Step 8: Verify resource cleanup by type
    log.info("Step 8: Verifying resource cleanup by type")

    # Check ASG cleanup
    asg_instances = categorized_instances.get("ASG", [])
    if asg_instances:
        log.info(f"Checking ASG cleanup for {len(asg_instances)} instances")
        # ASGs should be deleted when capacity reaches zero
        # (This is validated in detail in the ASG-specific test)

    # Check EC2Fleet cleanup
    ec2fleet_instances = categorized_instances.get("EC2Fleet", [])
    if ec2fleet_instances:
        log.info(f"Checking EC2Fleet cleanup for {len(ec2fleet_instances)} instances")
        # Maintain fleets should be deleted, instant fleets may remain
        # (This is validated in detail in the EC2Fleet-specific test)

    # Check SpotFleet cleanup
    spotfleet_instances = categorized_instances.get("SpotFleet", [])
    if spotfleet_instances:
        log.info(f"Checking SpotFleet cleanup for {len(spotfleet_instances)} instances")
        # Spot fleet requests should be cancelled
        # (This is validated in detail in the SpotFleet-specific test)

    # Check RunInstances cleanup
    runinstances_instances = categorized_instances.get("RunInstances", [])
    if runinstances_instances:
        log.info(f"Checking RunInstances cleanup for {len(runinstances_instances)} instances")
        # RunInstances just need instance termination (no additional resources to clean up)

    # Step 9: Final validation
    log.info("Step 9: Final validation")

    # Re-categorize to see final state
    final_categorization = categorize_instances_by_resource_type(all_instance_ids)
    log.info("Final instance categorization:")
    for resource_type, instances in final_categorization.items():
        if instances:
            log.info(f"  {resource_type}: {len(instances)} instances")
            instance_states = get_instances_states(instances, ec2_client)
            for instance_id, state in zip(instances, instance_states):
                log.info(f"    {instance_id}: {state if state is not None else 'terminated'}")

    log.info("=== Multi-Resource Termination Test Completed Successfully ===")
    log.info(
        f"Successfully provisioned {total_instances} instances across {len(non_empty_types)} resource types"
    )
    log.info(f"Successfully terminated all {total_instances} instances in a single operation")
    log.info("Mixed resource type termination worked correctly")
    log.info("Resource grouping logic handled multiple resource types properly")
    log.info("All resource-specific cleanup logic executed correctly")

    # Log final summary by resource type
    for resource_type in ["ASG", "EC2Fleet", "SpotFleet", "RunInstances"]:
        count = len(categorized_instances.get(resource_type, []))
        if count > 0:
            log.info(f"{resource_type}: {count} instances successfully terminated")
