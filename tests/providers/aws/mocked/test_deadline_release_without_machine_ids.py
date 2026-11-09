"""An Auto Scaling Group request that reaches its deadline without machine ids is released.

The group is emptied before the request is first read, so at the deadline the
request records the group as its resource and no machine id. It still owes a
release: a group that launched nothing is deleted through CANCEL_PROVISIONING,
and a group that has since gained a pending instance gets that instance returned
and is kept.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
import pytest

from tests.providers.aws.mocked.test_cqrs_control_loop import (  # noqa: F401
    _create_request,
    _get_request,
    cqrs_buses,
    patch_moto_compat,
)

REGION = "eu-west-2"
pytestmark = [pytest.mark.moto]


def _container() -> Any:
    from orb.infrastructure.di.container import get_container

    return get_container()


def _uow_factory() -> Any:
    from orb.domain.base import UnitOfWorkFactory

    return _container().get(UnitOfWorkFactory)


def _stored(request_id: str) -> Any:
    from orb.domain.request.value_objects import RequestId

    with _uow_factory().create_unit_of_work() as uow:
        return uow.requests.get_by_id(RequestId(value=request_id))


def _expire(request_id: str) -> None:
    """Move the request's deadline into the past."""
    with _uow_factory().create_unit_of_work() as uow:
        stored = _stored(request_id)
        uow.requests.save(
            stored.model_copy(
                update={"deadline_at": datetime.now(timezone.utc) - timedelta(hours=2)}
            )
        )


def _groups() -> list[str]:
    client = boto3.client("autoscaling", region_name=REGION)
    return [
        g["AutoScalingGroupName"]
        for g in client.describe_auto_scaling_groups()["AutoScalingGroups"]
    ]


def _empty_group(request_id: str) -> None:
    """Remove the group's instances, as if none had launched yet."""
    client = boto3.client("autoscaling", region_name=REGION)
    (group,) = _stored(request_id).resource_ids
    client.update_auto_scaling_group(AutoScalingGroupName=group, MinSize=0, DesiredCapacity=0)
    instances = client.describe_auto_scaling_groups(AutoScalingGroupNames=[group])[
        "AutoScalingGroups"
    ][0]["Instances"]
    if instances:
        client.detach_instances(
            AutoScalingGroupName=group,
            InstanceIds=[i["InstanceId"] for i in instances],
            ShouldDecrementDesiredCapacity=True,
        )
        boto3.client("ec2", region_name=REGION).terminate_instances(
            InstanceIds=[i["InstanceId"] for i in instances]
        )


async def _reach_deadline(query_bus: Any, request_id: str) -> Any:
    from orb.domain.request.request_types import RequestStatus

    _empty_group(request_id)
    _expire(request_id)
    await _get_request(query_bus, request_id)
    stored = _stored(request_id)
    assert stored.status in (RequestStatus.TIMEOUT, RequestStatus.PARTIAL), (
        stored.status,
        stored.successful_count,
        stored.machine_ids,
        stored.metadata.get("last_fulfilment"),
    )
    assert not stored.machine_ids
    assert stored.metadata.get("deadline_release") == "pending"
    return stored


def _release_service() -> Any:
    from orb.application.services.deadline_release_service import DeadlineReleaseService

    return _container().get(DeadlineReleaseService)


@pytest.mark.asyncio
async def test_group_that_launched_nothing_is_deleted_at_the_deadline(cqrs_buses):  # noqa: F811
    command_bus, query_bus = cqrs_buses
    request_id = await _create_request(command_bus, "ASG-OnDemand", count=2)
    stored = await _reach_deadline(query_bus, request_id)
    (group,) = stored.resource_ids
    assert group in _groups()

    mark = await _release_service().release(request_id)

    assert mark == "done"
    assert group not in _groups()
    record = _stored(request_id).metadata["deadline_release_abort"]
    assert record["resource_ids"] == [group]


@pytest.mark.asyncio
async def test_group_with_a_pending_instance_returns_it_and_is_kept(
    cqrs_buses,  # noqa: F811
    moto_vpc_resources,
):
    from moto.core import DEFAULT_ACCOUNT_ID
    from moto.ec2.models import ec2_backends

    command_bus, query_bus = cqrs_buses
    request_id = await _create_request(command_bus, "ASG-OnDemand", count=2)
    stored = await _reach_deadline(query_bus, request_id)
    (group,) = stored.resource_ids

    ec2 = boto3.client("ec2", region_name=REGION)
    launched = ec2.run_instances(
        ImageId="ami-12345678",
        MinCount=2,
        MaxCount=2,
        InstanceType="t3.medium",
        SubnetId=moto_vpc_resources["subnet_ids"][0],
    )["Instances"]
    pending_id, running_id = (i["InstanceId"] for i in launched)
    boto3.client("autoscaling", region_name=REGION).attach_instances(
        InstanceIds=[pending_id, running_id], AutoScalingGroupName=group
    )
    ec2_backends[DEFAULT_ACCOUNT_ID][REGION].get_instance(pending_id)._state.name = "pending"

    mark = await _release_service().release(request_id)

    assert mark == "pending"
    assert group in _groups()
    with _uow_factory().create_unit_of_work() as uow:
        machines = {m.machine_id.value: m for m in uow.machines.find_by_request_id(request_id)}
    assert set(machines) == {pending_id, running_id}
    assert machines[pending_id].return_request_id
    assert not machines[running_id].return_request_id
    states = {
        i["InstanceId"]: i["State"]["Name"]
        for r in ec2.describe_instances(InstanceIds=[pending_id, running_id])["Reservations"]
        for i in r["Instances"]
    }
    assert states[running_id] == "running"
