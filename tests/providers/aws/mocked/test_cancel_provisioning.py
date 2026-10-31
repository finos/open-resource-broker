"""Cancelling provisioning aborts unfulfilled fleets and groups through the provider operation.

moto never fulfils fleets, so every resource below is genuinely in flight. The
operation is driven the way the application layer does: as a CANCEL_PROVISIONING
provider operation handled by the instance operation service.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import boto3
import pytest

from orb.domain.base.operations import Operation, OperationType
from orb.providers.aws.infrastructure.adapters.aws_provisioning_adapter import (
    AWSProvisioningAdapter,
)
from orb.providers.aws.services.instance_operation_service import AWSInstanceOperationService
from tests.providers.aws.mocked.test_cleanup_e2e import (
    _asg_template,
    _assert_no_lt_for_request,
    _ec2_fleet_template,
    _make_aws_client,
    _make_cleanup_config_port,
    _make_factory,
    _make_logger,
    _make_request,
    _spot_fleet_template,
)

REGION = "eu-west-2"
SLEEP = "orb.infrastructure.resilience.retry_decorator.time.sleep"

pytestmark = [pytest.mark.moto]


@pytest.fixture
def aws_client(moto_aws):
    return _make_aws_client()


@pytest.fixture
def ec2(moto_aws):
    return boto3.client("ec2", region_name=REGION)


@pytest.fixture
def autoscaling(moto_aws):
    return boto3.client("autoscaling", region_name=REGION)


@pytest.fixture
def harness(aws_client, moto_vpc_resources):
    logger = _make_logger()
    factory = _make_factory(aws_client, logger, _make_cleanup_config_port())
    strategy = MagicMock()
    strategy.get_handler.side_effect = lambda api: factory._handlers[api]
    adapter = AWSProvisioningAdapter(aws_client, logger, strategy)
    service = AWSInstanceOperationService(
        aws_client=aws_client,
        logger=logger,
        provisioning_adapter=adapter,
        machine_adapter=MagicMock(),
    )
    return SimpleHarness(
        factory=factory,
        service=service,
        subnet_id=moto_vpc_resources["subnet_ids"][0],
        sg_id=moto_vpc_resources["sg_id"],
    )


class SimpleHarness:
    def __init__(self, factory, service, subnet_id, sg_id) -> None:
        self.factory = factory
        self.service = service
        self.subnet_id = subnet_id
        self.sg_id = sg_id

    def acquire(self, api: str, template: Any, request_id: str) -> str:
        handler = self.factory.create_handler(api)
        result = handler.acquire_hosts(_make_request(request_id=request_id), template)
        assert result["success"] is True
        return result["resource_ids"][0]

    async def cancel(self, api: str, resource_ids: list[str], request_id: str):
        return await self.service.cancel_provisioning(
            Operation(
                operation_type=OperationType.CANCEL_PROVISIONING,
                parameters={
                    "request_id": request_id,
                    "provider_api": api,
                    "resource_ids": resource_ids,
                },
            )
        )


def _outcomes(result) -> dict[str, str]:
    return {r["resource_id"]: r["outcome"] for r in result.data["resources"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("fleet_type", ["request", "maintain"])
async def test_unfulfilled_ec2_fleet_is_deleted_and_a_second_cancel_is_a_noop(
    harness, ec2, fleet_type
):
    template = _ec2_fleet_template(harness.subnet_id, harness.sg_id, fleet_type)
    fleet_id = harness.acquire("EC2Fleet", template, f"cancel-ec2-{fleet_type}")

    first = await harness.cancel("EC2Fleet", [fleet_id], f"cancel-ec2-{fleet_type}")

    assert first.success, first.error_message
    assert _outcomes(first) == {fleet_id: "aborted"}
    states = [f["FleetState"] for f in ec2.describe_fleets(FleetIds=[fleet_id])["Fleets"]]
    assert states and all(s.startswith("deleted") for s in states)
    _assert_no_lt_for_request(ec2, f"cancel-ec2-{fleet_type}")

    second = await harness.cancel("EC2Fleet", [fleet_id], f"cancel-ec2-{fleet_type}")
    assert second.success, second.error_message
    assert _outcomes(second) == {fleet_id: "already_gone"}


@pytest.mark.asyncio
@pytest.mark.parametrize("fleet_type", ["request", "maintain"])
async def test_unfulfilled_spot_fleet_is_cancelled(harness, ec2, fleet_type):
    template = _spot_fleet_template(harness.subnet_id, harness.sg_id, fleet_type)
    fleet_id = harness.acquire("SpotFleet", template, f"cancel-spot-{fleet_type}")

    first = await harness.cancel("SpotFleet", [fleet_id], f"cancel-spot-{fleet_type}")

    assert first.success, first.error_message
    assert _outcomes(first) == {fleet_id: "aborted"}
    # moto stops listing a cancelled request; AWS reports a cancelled_* state.
    configs = ec2.describe_spot_fleet_requests(SpotFleetRequestIds=[fleet_id])[
        "SpotFleetRequestConfigs"
    ]
    assert all(c["SpotFleetRequestState"].startswith("cancelled") for c in configs)
    # A repeated cancel cannot be exercised here: moto raises a KeyError for a
    # request it has already forgotten, where AWS answers fleetRequestIdDoesNotExist
    # (covered with injected responses in the unit tests).


@pytest.mark.asyncio
async def test_asg_is_scaled_to_zero_and_deleted_and_a_second_cancel_is_a_noop(
    harness, autoscaling, ec2
):
    template = _asg_template(harness.subnet_id, harness.sg_id)
    asg_name = harness.acquire("ASG", template, "cancel-asg-1")

    first = await harness.cancel("ASG", [asg_name], "cancel-asg-1")

    assert first.success, first.error_message
    assert _outcomes(first) == {asg_name: "aborted"}
    assert (
        autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])[
            "AutoScalingGroups"
        ]
        == []
    )
    _assert_no_lt_for_request(ec2, "cancel-asg-1")

    second = await harness.cancel("ASG", [asg_name], "cancel-asg-1")
    assert second.success, second.error_message
    assert _outcomes(second) == {asg_name: "already_gone"}


@pytest.mark.asyncio
async def test_unknown_resources_count_as_already_gone(harness):
    fleet = await harness.cancel("EC2Fleet", ["fleet-0000000000000000"], "cancel-unknown")
    asg = await harness.cancel("ASG", ["no-such-asg"], "cancel-unknown")

    assert fleet.success, fleet.error_message
    assert _outcomes(fleet) == {"fleet-0000000000000000": "already_gone"}
    assert asg.success, asg.error_message
    assert _outcomes(asg) == {"no-such-asg": "already_gone"}


@pytest.mark.asyncio
async def test_members_of_a_deleted_group_are_terminated_even_with_scale_in_protection(
    harness, ec2
):
    """A forced delete can leave scale-in protected instances running; the abort ends them."""
    template = _asg_template(harness.subnet_id, harness.sg_id)
    asg_name = harness.acquire("ASG", template, "cancel-members-1")
    boto3.client("autoscaling", region_name=REGION).update_auto_scaling_group(
        AutoScalingGroupName=asg_name, NewInstancesProtectedFromScaleIn=True
    )
    members = harness.service._list_live_members("ASG", asg_name)
    assert len(members) >= 1

    result = await harness.cancel("ASG", [asg_name], "cancel-members-1")

    assert result.success, result.error_message
    states = {
        i["State"]["Name"]
        for r in ec2.describe_instances(InstanceIds=members)["Reservations"]
        for i in r["Instances"]
    }
    assert states <= {"shutting-down", "terminated"}
    # Cancelling again is a no-op for instances that are already gone.
    again = await harness.cancel("ASG", [asg_name], "cancel-members-1")
    assert again.success, again.error_message


def test_member_listing_filters_by_resource_tag_and_live_states(harness):
    ec2 = MagicMock()
    ec2.describe_instances.side_effect = [
        {
            "Reservations": [{"Instances": [{"InstanceId": "i-1"}]}],
            "NextToken": "page-2",
        },
        {"Reservations": [{"Instances": [{"InstanceId": "i-2"}]}]},
    ]
    harness.service._aws_client.ec2_client = ec2
    expected_filter = {
        "EC2Fleet": "tag:aws:ec2:fleet-id",
        "SpotFleet": "tag:aws:ec2spot:fleet-request-id",
        "ASG": "tag:aws:autoscaling:groupName",
        "RunInstances": "reservation-id",
    }

    for api, name in expected_filter.items():
        ec2.describe_instances.reset_mock()
        ec2.describe_instances.side_effect = [
            {"Reservations": [{"Instances": [{"InstanceId": "i-1"}]}], "NextToken": "page-2"},
            {"Reservations": [{"Instances": [{"InstanceId": "i-2"}]}]},
        ]
        assert harness.service._list_live_members(api, "res-1") == ["i-1", "i-2"]
        first = ec2.describe_instances.call_args_list[0].kwargs["Filters"]
        assert {"Name": name, "Values": ["res-1"]} in first
        states = next(f for f in first if f["Name"] == "instance-state-name")
        assert sorted(states["Values"]) == sorted(
            ["pending", "running", "stopping", "stopped", "shutting-down"]
        )
        assert ec2.describe_instances.call_args_list[1].kwargs["NextToken"] == "page-2"

    assert harness.service._list_live_members("MicroVM", "anything") == []
