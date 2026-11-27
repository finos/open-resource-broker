"""EC2Fleet contract coverage (moto-backed).

The shared contract fixtures in conftest.py provision via ASG (provisioning)
and RunInstances (monitoring). This module extends contract-tier coverage to
EC2Fleet, which previously had none, and asserts the two behaviours the shared
suite skips as simulator limitations:

  * exact-count fulfilment — a request for N instances yields N running
    instances with a fulfilled verdict (not merely ">= 1");
  * real release_hosts termination — release is called with the actual launched
    instance IDs and the instances are observed terminated afterwards.
"""

import boto3
import pytest

from orb.domain.base.provider_fulfilment import CheckHostsStatusResult
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler
from orb.providers.aws.utilities.aws_operations import AWSOperations
from tests.providers.aws.contract.conftest import (
    REGION,
    _make_aws_client,
    _make_config_port,
    _make_logger,
    _make_lt_manager,
    _make_request,
)


def _build_ec2_fleet_handler(aws_client, logger, config_port) -> EC2FleetHandler:
    aws_ops = AWSOperations(aws_client, logger, config_port)
    lt_manager = _make_lt_manager(aws_client, logger)
    return EC2FleetHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=config_port,
    )


def _ec2_fleet_template(subnet_id: str, sg_id: str, fleet_type: str = "maintain") -> AWSTemplate:
    return AWSTemplate(
        template_id="tpl-contract-fleet",
        name="contract-fleet",
        provider_api="EC2Fleet",
        machine_types={"t3.micro": 1},
        image_id="ami-12345678",
        max_instances=5,
        price_type="ondemand",
        fleet_type=fleet_type,
        subnet_ids=[subnet_id],
        security_group_ids=[sg_id],
        tags={"Environment": "contract-test"},
    )


@pytest.fixture
def ec2_fleet_handler(moto_vpc_resources) -> EC2FleetHandler:
    return _build_ec2_fleet_handler(_make_aws_client(), _make_logger(), _make_config_port())


@pytest.mark.provider_contract
class TestAWSEC2FleetContract:
    """EC2Fleet satisfies the provisioning + monitoring contract at exact count."""

    def test_acquire_returns_success_and_fleet_id(
        self, ec2_fleet_handler, moto_vpc_resources
    ) -> None:
        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]
        request = _make_request(request_id="contract-fleet-001", requested_count=2)

        result = ec2_fleet_handler.acquire_hosts(request, _ec2_fleet_template(subnet_id, sg_id))

        assert isinstance(result, dict)
        assert result["success"] is True
        assert result["resource_ids"] and result["resource_ids"][0].startswith("fleet-")

    def test_exact_count_fulfilment(self, ec2_fleet_handler, moto_vpc_resources) -> None:
        """A request for N instances yields exactly N running instances, fulfilled."""
        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]
        request = _make_request(request_id="contract-fleet-002", requested_count=3)

        fleet_id = ec2_fleet_handler.acquire_hosts(request, _ec2_fleet_template(subnet_id, sg_id))[
            "resource_ids"
        ][0]

        status_request = _make_request(
            request_id="contract-fleet-002", requested_count=3, resource_ids=[fleet_id]
        )
        result = ec2_fleet_handler.check_hosts_status(status_request)

        assert isinstance(result, CheckHostsStatusResult)
        assert len(result.instances) == 3
        assert result.fulfilment.running_count == 3
        assert result.fulfilment.target_units == 3
        assert result.fulfilment.state == "fulfilled"

    def test_release_terminates_launched_instances(
        self, ec2_fleet_handler, moto_vpc_resources
    ) -> None:
        """release_hosts called with real instance IDs terminates them."""
        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]
        ec2 = boto3.client("ec2", region_name=REGION)
        request = _make_request(request_id="contract-fleet-003", requested_count=2)

        fleet_id = ec2_fleet_handler.acquire_hosts(request, _ec2_fleet_template(subnet_id, sg_id))[
            "resource_ids"
        ][0]

        status_request = _make_request(
            request_id="contract-fleet-003", requested_count=2, resource_ids=[fleet_id]
        )
        instance_ids = [
            entry["instance_id"]
            for entry in ec2_fleet_handler.check_hosts_status(status_request).instances
        ]
        assert len(instance_ids) == 2

        ec2_fleet_handler.release_hosts(instance_ids)

        resp = ec2.describe_instances(InstanceIds=instance_ids)
        states = [i["State"]["Name"] for r in resp["Reservations"] for i in r["Instances"]]
        assert all(s in ("shutting-down", "terminated") for s in states)
