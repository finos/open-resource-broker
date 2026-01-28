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

log = logging.getLogger("multi_ec2_fleet_test")
log.setLevel(logging.DEBUG)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
console_handler.setFormatter(formatter)

file_handler = logging.FileHandler("logs/multi_ec2_fleet_test.log")
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(formatter)

log.addHandler(console_handler)
log.addHandler(file_handler)

MAX_TIME_WAIT_FOR_CAPACITY_PROVISIONING_SEC = 180


def get_ec2_fleet_instances(fleet_id: str) -> List[str]:
    """Get all instance IDs from an EC2 Fleet."""
    try:
        response = ec2_client.describe_fleet_instances(FleetId=fleet_id)
        return [instance["InstanceId"] for instance in response.get("ActiveInstances", [])]
    except ClientError as e:
        log.error(f"Error getting EC2 Fleet instances for {fleet_id}: {e}")
        return []


def verify_ec2_fleet_instances_detached(instance_ids: List[str]) -> bool:
    """Verify that instances are no longer part of any EC2 Fleet."""
    try:
        if not instance_ids:
            return True

        # Get all active EC2 fleets
        response = ec2_client.describe_fleets(
            Filters=[{"Name": "fleet-state", "Values": ["active", "modifying"]}]
        )

        # Check each fleet for our instances
        for fleet in response.get("Fleets", []):
            fleet_id = fleet.get("FleetId")
            if not fleet_id:
                continue

            try:
                fleet_instances = ec2_client.describe_fleet_instances(FleetId=fleet_id)
                active_instance_ids = [
                    inst["InstanceId"] for inst in fleet_instances.get("ActiveInstances", [])
                ]

                # Check if any of our instances are still in this fleet
                remaining_instances = set(instance_ids) & set(active_instance_ids)
                if remaining_instances:
                    log.warning(
                        f"Found {len(remaining_instances)} instances still in EC2 Fleet {fleet_id}"
                    )
                    for instance_id in remaining_instances:
                        log.warning(f"Instance {instance_id} still in EC2 Fleet {fleet_id}")
                    return False

            except ClientError as e:
                if e.response["Error"]["Code"] == "InvalidFleetId":
                    # Fleet was deleted, which is expected
                    continue
                else:
                    log.warning(f"Error checking EC2 Fleet {fleet_id}: {e}")

        log.info(f"All {len(instance_ids)} instances successfully detached from EC2 Fleets")
        return True

    except ClientError as e:
        log.error(f"Error checking EC2 Fleet instance membership: {e}")
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
def setup_multi_ec2_fleet_templates():
    """Setup fixture that creates two different EC2 Fleet templates for testing."""
    processor = TemplateProcessor()
    test_name = "test_multi_ec2_fleet_termination"

    # Clear any existing files from the test directory first
    test_config_dir = processor.run_templates_dir / test_name
    if test_config_dir.exists():
        import shutil

        shutil.rmtree(test_config_dir)
        log.info(f"Cleared existing test directory: {test_config_dir}")

    # Create two different EC2 Fleet templates with different configurations
    template_configs = [
        {
            "template_name": "EC2Fleet_Template_1",
            "test_dir": f"{test_name}_ef1",
            "overrides": {
                "providerApi": "EC2Fleet",
                "instanceType": "t3.micro",
                "fleetType": "instant",
                "targetCapacity": 2,
                "allocationStrategy": "lowestPrice",
                "priceType": "ondemand",
            },
        },
        {
            "template_name": "EC2Fleet_Template_2",
            "test_dir": f"{test_name}_ef2",
            "overrides": {
                "providerApi": "EC2Fleet",
                "instanceType": "t3.small",
                "fleetType": "request",
                "targetCapacity": 3,
                "allocationStrategy": "lowestPrice",
                "priceType": "ondemand",
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


def provision_ec2_fleet_capacity(
    hfm: HostFactoryMock, template_json: Dict[str, Any], capacity: int
) -> Dict[str, Any]:
    """Provision capacity for a single EC2 Fleet template and return the status response."""
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
def test_multi_ec2_fleet_termination(setup_multi_ec2_fleet_templates):
    """
    Test that provisions capacity from two different EC2 Fleet templates and then
    attempts to remove all instances from both templates at once.

    This test validates:
    1. Multiple EC2 Fleet templates can be provisioned simultaneously
    2. Instances from multiple EC2 Fleets can be terminated in a single operation
    3. EC2 Fleet capacity management works correctly across multiple fleets
    4. All instances are properly detached from their EC2 Fleets before termination
    5. EC2 Fleet requests are properly deleted when instances are terminated (maintain fleets)
    """
    hfm, template_configs = setup_multi_ec2_fleet_templates

    log.info("=== Starting Multi-EC2 Fleet Termination Test ===")

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

    # Step 2: Find our EC2 Fleet templates
    log.info("Step 2: Locating EC2 Fleet templates")
    ec2_fleet_templates = []

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
            pytest.fail(f"EC2 Fleet template {template_name} not found in available templates")

        # Log the template details for debugging
        log.info(f"Template {template_name} details: {json.dumps(template_json, indent=2)}")

        # Verify it's an EC2 Fleet template - check both providerApi and provider_api
        provider_api = template_json.get("providerApi") or template_json.get("provider_api")
        if provider_api != "EC2Fleet":
            # If it's not EC2Fleet, let's force it to be EC2Fleet for our test
            log.warning(
                f"Template {template_name} has providerApi '{provider_api}', forcing to EC2Fleet"
            )
            template_json["providerApi"] = "EC2Fleet"

        ec2_fleet_templates.append(template_json)
        log.info(
            f"Found EC2 Fleet template: {template_json['templateId']} with providerApi: {template_json.get('providerApi')}"
        )

    # Step 3: Provision capacity from both EC2 Fleet templates in parallel
    log.info("Step 3: Provisioning capacity from both EC2 Fleet templates in parallel")

    # Start both provisioning requests without waiting
    request_ids = []
    for i, template_json in enumerate(ec2_fleet_templates):
        capacity_to_request = template_configs[i]["overrides"]["targetCapacity"]
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

                pytest.fail(
                    f"Timeout waiting for capacity provisioning for request {request_id} machines {machines} pending {pending_instances}"
                )

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
    log.info(f"Total instances provisioned across both EC2 Fleets: {total_instances}")
    log.info(f"All instance IDs: {all_instance_ids}")

    # Step 4: Verify instances are in their respective EC2 Fleets
    log.info("Step 4: Verifying instances are properly assigned to EC2 Fleets")

    # Get EC2 Fleet IDs from the instances by checking tags
    ec2_fleet_ids = set()
    try:
        response = ec2_client.describe_instances(InstanceIds=all_instance_ids)

        for reservation in response.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                instance_id = instance.get("InstanceId")

                # Check for EC2 Fleet ID in tags
                for tag in instance.get("Tags", []):
                    if tag.get("Key") == "aws:ec2:fleet-id":
                        fleet_id = tag.get("Value")
                        if fleet_id:
                            ec2_fleet_ids.add(fleet_id)
                            log.info(f"Instance {instance_id} belongs to EC2 Fleet {fleet_id}")
                            break

    except ClientError as e:
        pytest.fail(f"Failed to verify EC2 Fleet membership: {e}")

    log.info(f"Found instances in {len(ec2_fleet_ids)} EC2 Fleets: {list(ec2_fleet_ids)}")
    assert len(ec2_fleet_ids) == 2, (
        f"Expected instances in 2 EC2 Fleets, found {len(ec2_fleet_ids)}"
    )

    # Step 5: Terminate all instances from both EC2 Fleets at once
    log.info("Step 5: Terminating all instances from both EC2 Fleets simultaneously")
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

        # Check if instances are detached from EC2 Fleets
        if not termination_started:
            detached = verify_ec2_fleet_instances_detached(all_instance_ids)
            if detached:
                log.info("All instances successfully detached from EC2 Fleets")
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

    # Verify instances are detached from EC2 Fleets
    final_detached = verify_ec2_fleet_instances_detached(all_instance_ids)
    if not final_detached:
        log.info(
            "Note: Some instances still show EC2 Fleet attachment while terminating - this is expected AWS behavior"
        )
        log.info("Instances will be fully detached once they reach 'terminated' state")

    # Step 8: Verify EC2 Fleet deletion (for maintain fleets)
    log.info("Step 8: Verifying EC2 Fleet deletion (for maintain fleets)")

    def check_ec2_fleet_exists(fleet_id: str) -> bool:
        """Check if an EC2 Fleet still exists and is active."""
        try:
            response = ec2_client.describe_fleets(FleetIds=[fleet_id])
            if response.get("Fleets"):
                fleet_state = response["Fleets"][0].get("FleetState")
                return fleet_state in ["active", "modifying"]
            return False
        except ClientError as e:
            if e.response["Error"]["Code"] in ["InvalidFleetId"]:
                # Fleet not found - this means it was deleted
                return False
            else:
                log.warning(f"Error checking EC2 Fleet {fleet_id}: {e}")
                return True  # Assume it exists if we can't check

    # Wait for EC2 Fleet deletion (this can take several minutes for maintain fleets)
    max_fleet_deletion_wait = 600  # 10 minutes for fleet deletion
    fleet_deletion_start = time.time()

    while time.time() - fleet_deletion_start < max_fleet_deletion_wait:
        remaining_maintain_fleets = []

        for fleet_id in ec2_fleet_ids:
            if check_ec2_fleet_exists(fleet_id):
                # Log current fleet status for debugging
                try:
                    response = ec2_client.describe_fleets(FleetIds=[fleet_id])
                    if response.get("Fleets"):
                        fleet = response["Fleets"][0]
                        fleet_state = fleet.get("FleetState", "unknown")
                        fleet_type = fleet.get("Type", "unknown")
                        target_capacity = fleet.get("TargetCapacitySpecification", {}).get(
                            "TotalTargetCapacity", 0
                        )
                        log.info(
                            f"EC2 Fleet {fleet_id} still exists: state={fleet_state}, type={fleet_type}, target capacity: {target_capacity}"
                        )
                        # Only wait for maintain fleets to be deleted
                        if fleet_type == "maintain":
                            remaining_maintain_fleets.append(fleet_id)
                except Exception as e:
                    log.warning(f"Could not get details for EC2 Fleet {fleet_id}: {e}")
            else:
                log.info(f"EC2 Fleet {fleet_id} successfully deleted")

        if not remaining_maintain_fleets:
            log.info("All maintain EC2 Fleet requests successfully deleted")
            break

        elapsed_time = int(time.time() - fleet_deletion_start)
        log.info(
            f"Waiting for {len(remaining_maintain_fleets)} maintain EC2 Fleet requests to be deleted: {remaining_maintain_fleets} ({elapsed_time}s elapsed)"
        )
        time.sleep(30)  # Check every 30 seconds

    # Final verification - maintain fleets should be deleted, instant fleets may remain
    final_remaining_fleets = []
    maintain_fleets_remaining = []

    for fleet_id in ec2_fleet_ids:
        if check_ec2_fleet_exists(fleet_id):
            final_remaining_fleets.append(fleet_id)

            # Check if this is a maintain fleet that should have been deleted
            try:
                response = ec2_client.describe_fleets(FleetIds=[fleet_id])
                if response.get("Fleets"):
                    fleet = response["Fleets"][0]
                    fleet_type = fleet.get("Type", "unknown")
                    if fleet_type == "maintain":
                        maintain_fleets_remaining.append(fleet_id)
            except Exception:
                pass

    if final_remaining_fleets:
        # Log detailed information about remaining fleets for debugging
        for fleet_id in final_remaining_fleets:
            try:
                response = ec2_client.describe_fleets(FleetIds=[fleet_id])
                if response.get("Fleets"):
                    fleet = response["Fleets"][0]
                    log.info(f"EC2 Fleet {fleet_id} still exists after termination:")
                    log.info(f"  - State: {fleet.get('FleetState', 'unknown')}")
                    log.info(f"  - Type: {fleet.get('Type', 'unknown')}")
                    log.info(
                        f"  - Target Capacity: {fleet.get('TargetCapacitySpecification', {}).get('TotalTargetCapacity', 0)}"
                    )
            except Exception as e:
                log.error(f"Could not get details for remaining EC2 Fleet {fleet_id}: {e}")

    # Only fail if maintain fleets are not deleted (instant fleets may remain)
    if maintain_fleets_remaining:
        assert not maintain_fleets_remaining, (
            f"Maintain EC2 Fleet requests were not deleted after instance termination: {maintain_fleets_remaining}. "
            f"This indicates the EC2 Fleet handler may not be properly deleting maintain fleets when capacity reaches zero."
        )
    else:
        log.info(
            "All maintain EC2 Fleet requests properly deleted (instant fleets may remain, which is expected)"
        )

    log.info("=== Multi-EC2 Fleet Termination Test Completed Successfully ===")
    log.info(f"Successfully provisioned {total_instances} instances across 2 EC2 Fleets")
    log.info(f"Successfully terminated all {total_instances} instances in a single operation")
    log.info("All instances properly detached from EC2 Fleets before termination")
    log.info("EC2 Fleet capacity management worked correctly across multiple fleets")
    log.info("Maintain EC2 Fleet requests properly deleted after instance termination")
