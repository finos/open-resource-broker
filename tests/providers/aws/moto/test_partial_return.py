"""Partial return tests for RunInstances, ASG, EC2Fleet, and SpotFleet.

Validates that returning a subset of acquired instances terminates only the
specified instances and leaves the remainder running. RunInstances is used
because moto fully supports instance lifecycle (launch, describe, terminate).

TestPartialReturnCapacityReduction covers capacity decrement behaviour for
ASG, EC2Fleet (maintain), and SpotFleet (maintain) handlers.
"""

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import boto3
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.infrastructure.aws_client import AWSClient
from orb.providers.aws.infrastructure.handlers.asg.handler import ASGHandler
from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler
from orb.providers.aws.infrastructure.handlers.run_instances.handler import RunInstancesHandler
from orb.providers.aws.infrastructure.handlers.spot_fleet.config_builder import (
    SpotFleetConfigBuilder,
)
from orb.providers.aws.infrastructure.handlers.spot_fleet.handler import SpotFleetHandler
from orb.providers.aws.infrastructure.launch_template.manager import (
    AWSLaunchTemplateManager,
    LaunchTemplateResult,
)
from orb.providers.aws.utilities.aws_operations import AWSOperations

REGION = "eu-west-2"


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


def _make_logger() -> Any:
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


def _make_config_port() -> Any:
    from orb.config.schemas.cleanup_schema import CleanupConfig
    from orb.config.schemas.provider_strategy_schema import ProviderDefaults

    config_port = MagicMock()
    config_port.get_resource_prefix.return_value = ""
    provider_defaults = ProviderDefaults(cleanup=CleanupConfig(enabled=False).model_dump())
    provider_config = MagicMock()
    provider_config.provider_defaults = {"aws": provider_defaults}
    config_port.get_provider_config.return_value = provider_config
    return config_port


def _make_aws_client() -> AWSClient:
    aws_client = MagicMock(spec=AWSClient)
    aws_client.ec2_client = boto3.client("ec2", region_name=REGION)
    aws_client.autoscaling_client = boto3.client("autoscaling", region_name=REGION)
    aws_client.sts_client = boto3.client("sts", region_name=REGION)
    return aws_client


def _make_launch_template_manager(aws_client: AWSClient) -> AWSLaunchTemplateManager:
    lt_manager = MagicMock(spec=AWSLaunchTemplateManager)

    def _create_or_update(template: AWSTemplate, request: Any) -> LaunchTemplateResult:
        lt_name = f"orb-lt-{request.request_id}"
        try:
            resp = aws_client.ec2_client.create_launch_template(
                LaunchTemplateName=lt_name,
                LaunchTemplateData={
                    "ImageId": template.image_id or "ami-12345678",
                    "InstanceType": (
                        next(iter(template.machine_types.keys()))
                        if template.machine_types
                        else "t3.micro"
                    ),
                    "NetworkInterfaces": [
                        {
                            "DeviceIndex": 0,
                            "SubnetId": template.subnet_ids[0] if template.subnet_ids else "",
                            "Groups": template.security_group_ids or [],
                            "AssociatePublicIpAddress": False,
                        }
                    ],
                },
            )
            lt_id = resp["LaunchTemplate"]["LaunchTemplateId"]
            version = str(resp["LaunchTemplate"]["LatestVersionNumber"])
        except Exception:
            lt_id = "lt-mock"
            version = "1"
        return LaunchTemplateResult(
            template_id=lt_id,
            version=version,
            template_name=lt_name,
            is_new_template=True,
        )

    lt_manager.create_or_update_launch_template.side_effect = _create_or_update
    return lt_manager


def _make_request(
    request_id: str = "req-partial-001",
    requested_count: int = 3,
    resource_ids: list[str] | None = None,
    provider_data: dict | None = None,
) -> Any:
    req = MagicMock()
    req.request_id = request_id
    req.requested_count = requested_count
    req.template_id = "tpl-partial"
    req.metadata = {}
    req.resource_ids = resource_ids or []
    req.provider_data = provider_data or {}
    req.provider_api = None
    return req


def _run_instances_template(subnet_id: str, sg_id: str) -> AWSTemplate:
    return AWSTemplate(
        template_id="tpl-partial",
        name="test-partial-return",
        provider_api="RunInstances",
        machine_types={"t3.micro": 1},
        image_id="ami-12345678",
        max_instances=10,
        price_type="ondemand",
        subnet_ids=[subnet_id],
        security_group_ids=[sg_id],
        tags={"Environment": "test"},
    )


def _instance_states(ec2_client: Any, instance_ids: list[str]) -> list[str]:
    resp = ec2_client.describe_instances(InstanceIds=instance_ids)
    return [i["State"]["Name"] for r in resp["Reservations"] for i in r["Instances"]]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def run_instances_handler(moto_vpc_resources):
    """Construct a RunInstancesHandler directly against moto — no DI stack."""
    aws_client = _make_aws_client()
    logger = _make_logger()
    config_port = _make_config_port()
    lt_manager = _make_launch_template_manager(aws_client)
    aws_ops = AWSOperations(aws_client, logger, config_port)
    return RunInstancesHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=config_port,
    )


@pytest.fixture
def acquired_3(run_instances_handler, moto_vpc_resources):
    """Acquire 3 instances and return their IDs together with the handler.

    Moto's run_instances always returns exactly 1 instance regardless of MaxCount,
    so we call acquire_hosts three times with requested_count=1 to obtain 3 instances.
    """
    subnet_id = moto_vpc_resources["subnet_ids"][0]
    sg_id = moto_vpc_resources["sg_id"]
    template = _run_instances_template(subnet_id, sg_id)

    instance_ids = []
    reservation_id = None
    for i in range(3):
        request = _make_request(request_id=f"req-partial-acquire-{i}", requested_count=1)
        result = run_instances_handler.acquire_hosts(request, template)
        assert result["success"] is True, f"acquire_hosts failed on call {i}: {result}"
        instance_ids.extend(result["provider_data"]["instance_ids"])
        if reservation_id is None:
            reservation_id = result["resource_ids"][0]

    assert len(instance_ids) == 3, f"Expected 3 instances, got {len(instance_ids)}"

    return {
        "instance_ids": instance_ids,
        "reservation_id": reservation_id,
        "handler": run_instances_handler,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPartialReturnRunInstances:
    def test_return_one_of_three_terminates_only_that_instance(self, acquired_3, ec2_client):
        """Returning one instance terminates it while the other two remain running."""
        instance_ids = acquired_3["instance_ids"]
        handler = acquired_3["handler"]

        handler.release_hosts([instance_ids[0]])

        terminated_states = _instance_states(ec2_client, [instance_ids[0]])
        assert all(s in ("shutting-down", "terminated") for s in terminated_states)

        remaining_states = _instance_states(ec2_client, instance_ids[1:])
        assert all(s in ("pending", "running") for s in remaining_states)

    def test_return_two_of_three_leaves_one_running(self, acquired_3, ec2_client):
        """Returning two instances leaves exactly one still running."""
        instance_ids = acquired_3["instance_ids"]
        handler = acquired_3["handler"]

        handler.release_hosts(instance_ids[:2])

        terminated_states = _instance_states(ec2_client, instance_ids[:2])
        assert all(s in ("shutting-down", "terminated") for s in terminated_states)

        surviving_states = _instance_states(ec2_client, [instance_ids[2]])
        assert all(s in ("pending", "running") for s in surviving_states)

    def test_partial_return_does_not_affect_remaining_instances(self, acquired_3, ec2_client):
        """Instances not in the return list are untouched after a partial return."""
        instance_ids = acquired_3["instance_ids"]
        handler = acquired_3["handler"]

        # Capture state of the two instances we will NOT return before the call
        before_states = _instance_states(ec2_client, instance_ids[1:])
        assert all(s in ("pending", "running") for s in before_states)

        handler.release_hosts([instance_ids[0]])

        after_states = _instance_states(ec2_client, instance_ids[1:])
        assert all(s in ("pending", "running") for s in after_states)

    def test_check_status_after_partial_return_reflects_mixed_states(self, acquired_3):
        """check_hosts_status after a partial return shows both terminated and running entries.

        Because moto returns one instance per acquire_hosts call, we have three separate
        reservation IDs. We use provider_data.instance_ids (the direct lookup path) so
        check_hosts_status can find all three instances regardless of reservation grouping.
        """
        instance_ids = acquired_3["instance_ids"]
        reservation_id = acquired_3["reservation_id"]
        handler = acquired_3["handler"]

        handler.release_hosts([instance_ids[0]])

        # Pass all instance_ids directly so the handler uses the fast describe path
        # rather than the reservation-id filter path (which only covers one reservation).
        status_request = _make_request(
            request_id="req-partial-acquire-0",
            resource_ids=[reservation_id],
            provider_data={
                "instance_ids": instance_ids,
                "reservation_id": reservation_id,
            },
        )
        result = handler.check_hosts_status(status_request)

        assert len(result) == 3

        statuses = {entry["instance_id"]: entry["status"] for entry in result}
        terminated_entry = statuses[instance_ids[0]]
        assert terminated_entry in ("terminated", "shutting-down", "stopping")

        running_entries = [statuses[iid] for iid in instance_ids[1:]]
        assert all(s in ("pending", "running") for s in running_entries)

    def test_return_empty_list_is_noop(self, acquired_3, ec2_client):
        """Calling release_hosts with an empty list does not raise and leaves all instances running."""
        instance_ids = acquired_3["instance_ids"]
        handler = acquired_3["handler"]

        handler.release_hosts([])

        states = _instance_states(ec2_client, instance_ids)
        assert all(s in ("pending", "running") for s in states)

    def test_return_all_terminates_all(self, acquired_3, ec2_client):
        """Returning all three instances terminates every one of them."""
        instance_ids = acquired_3["instance_ids"]
        handler = acquired_3["handler"]

        handler.release_hosts(instance_ids)

        states = _instance_states(ec2_client, instance_ids)
        assert all(s in ("shutting-down", "terminated") for s in states)


# ---------------------------------------------------------------------------
# Capacity reduction tests for ASG, EC2Fleet, and SpotFleet
# ---------------------------------------------------------------------------


class _MotoSpotFleetConfigBuilder(SpotFleetConfigBuilder):
    """Strip the 'instance' TagSpecification entry that moto rejects."""

    def build(  # type: ignore[override]
        self,
        template: AWSTemplate,
        request: Any,
        lt_id: str,
        lt_version: str,
    ) -> dict[str, Any]:
        config = super().build(
            template=template, request=request, lt_id=lt_id, lt_version=lt_version
        )
        config["TagSpecifications"] = [
            ts for ts in config.get("TagSpecifications", []) if ts.get("ResourceType") != "instance"
        ]
        return config


def _make_capacity_aws_client() -> AWSClient:
    aws_client = MagicMock(spec=AWSClient)
    aws_client.ec2_client = boto3.client("ec2", region_name=REGION)
    aws_client.autoscaling_client = boto3.client("autoscaling", region_name=REGION)
    aws_client.sts_client = boto3.client("sts", region_name=REGION)
    aws_client.ssm_client = boto3.client("ssm", region_name=REGION)
    return aws_client


def _make_capacity_config_port() -> Any:
    from orb.config.schemas.cleanup_schema import CleanupConfig
    from orb.config.schemas.provider_strategy_schema import ProviderDefaults

    config_port = MagicMock()
    config_port.get_resource_prefix.return_value = ""
    provider_defaults = ProviderDefaults(cleanup=CleanupConfig(enabled=False).model_dump())
    provider_config = MagicMock()
    provider_config.provider_defaults = {"aws": provider_defaults}
    config_port.get_provider_config.return_value = provider_config
    config_port.app_config = None
    return config_port


def _make_capacity_lt_manager(aws_client: AWSClient) -> AWSLaunchTemplateManager:
    lt_manager = MagicMock(spec=AWSLaunchTemplateManager)

    def _create_or_update(template: AWSTemplate, request: Any) -> LaunchTemplateResult:
        lt_name = f"orb-lt-cap-{request.request_id}"
        try:
            resp = aws_client.ec2_client.create_launch_template(
                LaunchTemplateName=lt_name,
                LaunchTemplateData={
                    "ImageId": template.image_id or "ami-12345678",
                    "InstanceType": (
                        next(iter(template.machine_types.keys()))
                        if template.machine_types
                        else "t3.micro"
                    ),
                    "NetworkInterfaces": [
                        {
                            "DeviceIndex": 0,
                            "SubnetId": template.subnet_ids[0] if template.subnet_ids else "",
                            "Groups": template.security_group_ids or [],
                            "AssociatePublicIpAddress": False,
                        }
                    ],
                },
            )
            lt_id = resp["LaunchTemplate"]["LaunchTemplateId"]
            version = str(resp["LaunchTemplate"]["LatestVersionNumber"])
        except Exception:
            lt_id = "lt-mock"
            version = "1"
        return LaunchTemplateResult(
            template_id=lt_id,
            version=version,
            template_name=lt_name,
            is_new_template=True,
        )

    lt_manager.create_or_update_launch_template.side_effect = _create_or_update
    return lt_manager


def _make_capacity_request(request_id: str, requested_count: int) -> Any:
    req = MagicMock()
    req.request_id = request_id
    req.requested_count = requested_count
    req.template_id = "tpl-cap-reduction"
    req.metadata = {}
    req.resource_ids = []
    req.provider_data = {}
    req.provider_api = None
    return req


SPOT_FLEET_ROLE = (
    "arn:aws:iam::123456789012:role/aws-service-role/"
    "spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet"
)


class TestPartialReturnCapacityReduction:
    def test_asg_partial_return_decrements_desired_capacity(self, moto_vpc_resources):
        """release_hosts([one_instance_id]) decrements ASG DesiredCapacity from 2 to 1."""
        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]

        aws_client = _make_capacity_aws_client()
        logger = _make_logger()
        config_port = _make_capacity_config_port()
        lt_manager = _make_capacity_lt_manager(aws_client)
        aws_ops = AWSOperations(aws_client, logger, config_port)
        handler = ASGHandler(
            aws_client=aws_client,
            logger=logger,
            aws_ops=aws_ops,
            launch_template_manager=lt_manager,
            config_port=config_port,
        )

        template = AWSTemplate(
            template_id="tpl-asg-cap",
            name="test-asg-cap",
            provider_api="ASG",
            machine_types={"t3.micro": 1},
            image_id="ami-12345678",
            max_instances=2,
            price_type="ondemand",
            subnet_ids=[subnet_id],
            security_group_ids=[sg_id],
        )
        request = _make_capacity_request("req-asg-cap-001", requested_count=2)
        result = handler.acquire_hosts(request, template)
        assert result["success"] is True
        asg_name = result["resource_ids"][0]

        # Run 2 real instances in moto and attach them to the ASG
        ec2 = aws_client.ec2_client
        asg = aws_client.autoscaling_client
        run_resp = ec2.run_instances(
            ImageId="ami-12345678",
            MinCount=2,
            MaxCount=2,
            InstanceType="t3.micro",
            SubnetId=subnet_id,
        )
        instance_ids = [i["InstanceId"] for i in run_resp["Instances"]]
        asg.attach_instances(InstanceIds=instance_ids, AutoScalingGroupName=asg_name)
        # Reset DesiredCapacity to exactly 2 (attach_instances increments on top)
        asg.update_auto_scaling_group(AutoScalingGroupName=asg_name, DesiredCapacity=2)

        resource_mapping = {
            instance_ids[0]: (asg_name, 2),
        }
        handler.release_hosts([instance_ids[0]], resource_mapping=resource_mapping)

        resp = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
        assert resp["AutoScalingGroups"], "ASG should still exist after partial release"
        assert resp["AutoScalingGroups"][0]["DesiredCapacity"] == 1

    def test_ec2_fleet_maintain_partial_return_decrements_target_capacity(self, moto_vpc_resources):
        """release_hosts with resource_mapping for 1 unit calls modify_fleet with TotalTargetCapacity=1.

        moto does not implement modify_fleet, so we patch it and assert the call args.
        """
        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]

        aws_client = _make_capacity_aws_client()
        logger = _make_logger()
        config_port = _make_capacity_config_port()
        lt_manager = _make_capacity_lt_manager(aws_client)
        aws_ops = AWSOperations(aws_client, logger, config_port)
        handler = EC2FleetHandler(
            aws_client=aws_client,
            logger=logger,
            aws_ops=aws_ops,
            launch_template_manager=lt_manager,
            config_port=config_port,
        )
        handler.aws_native_spec_service = None  # force legacy config-builder path

        template = AWSTemplate(
            template_id="tpl-ec2fleet-cap",
            name="test-ec2fleet-cap",
            provider_api="EC2Fleet",
            machine_types={"t3.micro": 1},
            image_id="ami-12345678",
            max_instances=2,
            price_type="ondemand",
            fleet_type="maintain",
            subnet_ids=[subnet_id],
            security_group_ids=[sg_id],
        )
        request = _make_capacity_request("req-ec2fleet-cap-001", requested_count=2)
        result = handler.acquire_hosts(request, template)
        assert result["success"] is True
        fleet_id = result["resource_ids"][0]

        # Confirm initial capacity
        ec2 = aws_client.ec2_client
        resp = ec2.describe_fleets(FleetIds=[fleet_id])
        assert resp["Fleets"][0]["TargetCapacitySpecification"]["TotalTargetCapacity"] == 2

        modify_calls: list[dict] = []

        def _fake_modify_fleet(**kwargs: Any) -> dict:
            modify_calls.append(kwargs)
            return {}

        def _fake_terminate(**_kwargs: Any) -> dict:
            return {"TerminatingInstances": []}

        fleet_details = resp["Fleets"][0]
        with (
            patch.object(ec2, "modify_fleet", side_effect=_fake_modify_fleet),
            patch.object(ec2, "terminate_instances", side_effect=_fake_terminate),
        ):
            handler._fleet_release_manager.release(
                fleet_id=fleet_id,
                instance_ids=["i-fake000000000001"],
                fleet_details=fleet_details,
            )

        assert len(modify_calls) == 1
        new_capacity = modify_calls[0]["TargetCapacitySpecification"]["TotalTargetCapacity"]
        assert new_capacity == 1

    def test_spot_fleet_maintain_partial_return_reduces_target_capacity(self, moto_vpc_resources):
        """release_hosts for 1 unit calls modify_spot_fleet_request with TargetCapacity decremented.

        We patch modify_spot_fleet_request on the ec2 client to capture the call,
        since moto does not auto-fulfil spot fleet instances.
        """
        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]

        aws_client = _make_capacity_aws_client()
        logger = _make_logger()
        config_port = _make_capacity_config_port()
        lt_manager = _make_capacity_lt_manager(aws_client)
        aws_ops = AWSOperations(aws_client, logger, config_port)
        config_builder = _MotoSpotFleetConfigBuilder(None, config_port, logger)
        handler = SpotFleetHandler(
            aws_client=aws_client,
            logger=logger,
            aws_ops=aws_ops,
            launch_template_manager=lt_manager,
            config_port=config_port,
            config_builder=config_builder,
        )

        template = AWSTemplate(
            template_id="tpl-spot-cap",
            name="test-spot-cap",
            provider_api="SpotFleet",
            machine_types={"t3.micro": 1},
            image_id="ami-12345678",
            max_instances=2,
            price_type="spot",
            fleet_type="maintain",
            fleet_role=SPOT_FLEET_ROLE,
            allocation_strategy="lowestPrice",
            subnet_ids=[subnet_id],
            security_group_ids=[sg_id],
        )
        request = _make_capacity_request("req-spot-cap-001", requested_count=2)
        result = handler.acquire_hosts(request, template)
        assert result["success"] is True
        fleet_id = result["resource_ids"][0]

        ec2 = aws_client.ec2_client
        resp = ec2.describe_spot_fleet_requests(SpotFleetRequestIds=[fleet_id])
        fleet_details = resp["SpotFleetRequestConfigs"][0]
        assert fleet_details["SpotFleetRequestConfig"]["TargetCapacity"] == 2

        modify_calls: list[dict] = []
        real_modify = ec2.modify_spot_fleet_request

        def _capturing_modify(**kwargs: Any) -> Any:
            modify_calls.append(kwargs)
            return real_modify(**kwargs)

        def _fake_terminate_with_fallback(
            instance_ids: list[str], *args: Any, **kwargs: Any
        ) -> None:
            pass

        with (
            patch.object(ec2, "modify_spot_fleet_request", side_effect=_capturing_modify),
            patch.object(
                aws_ops,
                "terminate_instances_with_fallback",
                side_effect=_fake_terminate_with_fallback,
            ),
        ):
            handler._release_manager.release(
                fleet_id=fleet_id,
                instance_ids=["i-fake000000000001"],
                fleet_details=fleet_details,
            )

        assert len(modify_calls) >= 1
        assert modify_calls[0]["TargetCapacity"] == 1
