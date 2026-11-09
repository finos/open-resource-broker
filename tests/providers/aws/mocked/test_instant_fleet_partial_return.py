"""Returning part of an instant EC2 Fleet must not terminate the other instances."""

from __future__ import annotations

from typing import Any, Optional
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from orb.providers.aws.infrastructure.handlers.ec2_fleet.release_manager import (
    EC2FleetReleaseManager,
)
from orb.providers.aws.infrastructure.handlers.launch_template_cleanup import (
    LaunchTemplateCleanupService,
)

REGION = "us-east-1"
REQUEST_ID = "req-instant-1"
TEMPLATE_NAME = "lt-instant"


def _retry(fn, *args, operation_type="standard", operation_name=None, **kwargs):
    return fn(*args, **kwargs)


def _paginate(fn, key, **kwargs):
    return fn(**kwargs).get(key, [])


def _collect(fn, key, **kwargs):
    return fn(**kwargs).get(key, [])


@pytest.fixture
def ec2():
    with mock_aws():
        yield boto3.client("ec2", region_name=REGION)


def _create_instant_fleet(
    ec2: Any, count: int, weight: Optional[int] = None, target: Optional[int] = None
) -> tuple[str, list[str]]:
    image_id = ec2.describe_images()["Images"][0]["ImageId"]
    ec2.create_launch_template(
        LaunchTemplateName=TEMPLATE_NAME,
        LaunchTemplateData={"ImageId": image_id, "InstanceType": "t2.micro"},
        TagSpecifications=[
            {
                "ResourceType": "launch-template",
                "Tags": [
                    {"Key": "orb:request-id", "Value": REQUEST_ID},
                    {"Key": "orb:managed-by", "Value": "open-resource-broker"},
                ],
            }
        ],
    )
    template_id = _template_ids(ec2)[0]
    config: dict[str, Any] = {
        "LaunchTemplateSpecification": {"LaunchTemplateId": template_id, "Version": "1"}
    }
    if weight is not None:
        config["Overrides"] = [{"InstanceType": "t2.micro", "WeightedCapacity": weight}]
    response = ec2.create_fleet(
        Type="instant",
        LaunchTemplateConfigs=[config],
        TargetCapacitySpecification={
            "TotalTargetCapacity": target or count,
            "DefaultTargetCapacityType": "on-demand",
        },
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [{"Key": "orb:request-id", "Value": REQUEST_ID}],
            }
        ],
    )
    ids = [i for entry in response["Instances"] for i in entry["InstanceIds"]]
    assert len(ids) == count
    return response["FleetId"], ids


def _template_ids(ec2: Any) -> list[str]:
    return [t["LaunchTemplateId"] for t in ec2.describe_launch_templates()["LaunchTemplates"]]


def _manager(ec2: Any) -> tuple[EC2FleetReleaseManager, Any]:
    spy = MagicMock(wraps=ec2)
    aws_client = MagicMock()
    aws_client.ec2_client = spy
    aws_ops = MagicMock()
    aws_ops.terminate_instances_with_fallback.side_effect = lambda ids, adapter, label: (
        ec2.terminate_instances(InstanceIds=list(ids))
    )
    logger = MagicMock()
    config_port = MagicMock()
    config_port.get_provider_config.return_value = None
    cleanup = LaunchTemplateCleanupService(aws_client, config_port, logger)
    manager = EC2FleetReleaseManager(
        aws_client=aws_client,
        aws_ops=aws_ops,
        request_adapter=None,
        config_port=config_port,
        logger=logger,
        retry_fn=_retry,
        paginate_fn=_paginate,
        collect_with_next_token_fn=_collect,
        cleanup_on_zero_capacity_fn=cleanup.cleanup_on_zero_capacity,
    )
    return manager, spy


def _states(ec2: Any, ids: list[str]) -> dict[str, str]:
    reservations = ec2.describe_instances(InstanceIds=ids)["Reservations"]
    return {i["InstanceId"]: i["State"]["Name"] for r in reservations for i in r["Instances"]}


def _fleet_details(ec2: Any, fleet_id: str) -> dict[str, Any]:
    fleet = ec2.describe_fleets(FleetIds=[fleet_id])["Fleets"][0]
    fleet.setdefault("Tags", []).append({"Key": "orb:request-id", "Value": REQUEST_ID})
    return fleet


def _is_gone(state: str) -> bool:
    return state in ("shutting-down", "terminated")


def test_partial_return_leaves_unreturned_instances_running(ec2):
    fleet_id, ids = _create_instant_fleet(ec2, 3)
    manager, spy = _manager(ec2)

    manager.release(fleet_id, ids[:1], _fleet_details(ec2, fleet_id))

    spy.delete_fleets.assert_not_called()
    states = _states(ec2, ids)
    assert _is_gone(states[ids[0]])
    assert states[ids[1]] == "running"
    assert states[ids[2]] == "running"
    # Other instances of the request are still live, so the template stays.
    assert len(_template_ids(ec2)) == 1


def test_full_return_deletes_fleet_without_terminating_further(ec2):
    fleet_id, ids = _create_instant_fleet(ec2, 2)
    manager, spy = _manager(ec2)

    manager.release(fleet_id, ids, _fleet_details(ec2, fleet_id))

    spy.delete_fleets.assert_called_once()
    assert spy.delete_fleets.call_args.kwargs["FleetIds"] == [fleet_id]
    assert spy.delete_fleets.call_args.kwargs["TerminateInstances"] is False
    assert all(_is_gone(s) for s in _states(ec2, ids).values())
    assert _template_ids(ec2) == []


def test_batched_returns_remove_template_after_last_batch(ec2):
    fleet_id, ids = _create_instant_fleet(ec2, 3)
    manager, spy = _manager(ec2)

    manager.release(fleet_id, ids[:1], _fleet_details(ec2, fleet_id))
    assert len(_template_ids(ec2)) == 1

    manager.release(fleet_id, ids[1:], _fleet_details(ec2, fleet_id))

    assert all(_is_gone(s) for s in _states(ec2, ids).values())
    assert _template_ids(ec2) == []
    # No batch may ask AWS to terminate instances through the fleet delete.
    for call in spy.delete_fleets.call_args_list:
        assert call.kwargs["TerminateInstances"] is False


def test_weighted_fleet_without_member_listing_keeps_others_then_cleans_template(ec2):
    fleet_id, ids = _create_instant_fleet(ec2, 2, weight=2, target=4)
    manager, spy = _manager(ec2)
    spy.describe_fleet_instances.side_effect = ClientError(
        {"Error": {"Code": "InvalidParameter", "Message": "unsupported"}},
        "DescribeFleetInstances",
    )

    manager.release(fleet_id, ids[:1], _fleet_details(ec2, fleet_id))

    spy.delete_fleets.assert_not_called()
    states = _states(ec2, ids)
    assert _is_gone(states[ids[0]])
    assert states[ids[1]] == "running"
    assert len(_template_ids(ec2)) == 1

    # Each instance counts as weight 1 without the listing, so the arithmetic
    # never reads as a full return; the template must still be removed.
    manager.release(fleet_id, ids[1:], _fleet_details(ec2, fleet_id))

    assert all(_is_gone(s) for s in _states(ec2, ids).values())
    assert _template_ids(ec2) == []
    for call in spy.delete_fleets.call_args_list:
        assert call.kwargs["TerminateInstances"] is False
