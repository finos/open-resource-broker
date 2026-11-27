"""SpotFleet contract coverage (moto-backed).

The shared contract fixtures in conftest.py provision via ASG and RunInstances.
This module extends contract-tier coverage to SpotFleet, which previously had
none, and asserts exact-count fulfilment and real release_hosts termination
(non-empty instance ID list) the shared suite skips as simulator limitations.

The config builder is wrapped to strip the ``instance`` TagSpecification entry
that moto's request_spot_fleet rejects — the same shim the mocked-tier lifecycle
tests use.
"""

from typing import Any

import boto3
import pytest

from orb.domain.base.provider_fulfilment import CheckHostsStatusResult
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.infrastructure.handlers.spot_fleet.handler import SpotFleetHandler
from orb.providers.aws.utilities.aws_operations import AWSOperations
from tests.providers.aws.contract.conftest import (
    REGION,
    SPOT_FLEET_ROLE,
    _make_aws_client,
    _make_config_port,
    _make_logger,
    _make_lt_manager,
    _make_request,
)


def _build_spot_fleet_handler(aws_client, logger, config_port) -> SpotFleetHandler:
    aws_ops = AWSOperations(aws_client, logger, config_port)
    lt_manager = _make_lt_manager(aws_client, logger)
    handler = SpotFleetHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=config_port,
    )
    # moto rejects the 'instance' TagSpecification on request_spot_fleet.
    original_build = handler._config_builder.build

    def _patched_build(**kwargs: Any) -> dict:
        config = original_build(**kwargs)
        config["TagSpecifications"] = [
            ts for ts in config.get("TagSpecifications", []) if ts.get("ResourceType") != "instance"
        ]
        return config

    handler._config_builder.build = _patched_build  # type: ignore[method-assign]
    return handler


def _spot_fleet_template(subnet_id: str, sg_id: str, fleet_type: str = "request") -> AWSTemplate:
    return AWSTemplate(
        template_id="tpl-contract-spot",
        name="contract-spot",
        provider_api="SpotFleet",
        machine_types={"t3.micro": 1},
        image_id="ami-12345678",
        max_instances=5,
        price_type="spot",
        fleet_type=fleet_type,
        fleet_role=SPOT_FLEET_ROLE,
        allocation_strategy="lowest_price",
        subnet_ids=[subnet_id],
        security_group_ids=[sg_id],
        tags={"Environment": "contract-test"},
    )


@pytest.fixture
def spot_fleet_handler(moto_vpc_resources) -> SpotFleetHandler:
    return _build_spot_fleet_handler(_make_aws_client(), _make_logger(), _make_config_port())


@pytest.mark.provider_contract
class TestAWSSpotFleetContract:
    """SpotFleet satisfies the provisioning + monitoring contract at exact count."""

    def test_acquire_returns_success_and_fleet_id(
        self, spot_fleet_handler, moto_vpc_resources
    ) -> None:
        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]
        request = _make_request(request_id="contract-spot-001", requested_count=2)

        result = spot_fleet_handler.acquire_hosts(request, _spot_fleet_template(subnet_id, sg_id))

        assert isinstance(result, dict)
        assert result["success"] is True
        assert result["resource_ids"] and result["resource_ids"][0].startswith("sfr-")

    def test_exact_count_fulfilment(self, spot_fleet_handler, moto_vpc_resources) -> None:
        """A request for N instances yields exactly N running instances, fulfilled."""
        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]
        request = _make_request(request_id="contract-spot-002", requested_count=3)

        fleet_id = spot_fleet_handler.acquire_hosts(
            request, _spot_fleet_template(subnet_id, sg_id)
        )["resource_ids"][0]

        status_request = _make_request(
            request_id="contract-spot-002", requested_count=3, resource_ids=[fleet_id]
        )
        result = spot_fleet_handler.check_hosts_status(status_request)

        assert isinstance(result, CheckHostsStatusResult)
        assert len(result.instances) == 3
        assert result.fulfilment.running_count == 3
        assert result.fulfilment.target_units == 3
        assert result.fulfilment.state == "fulfilled"

    def test_release_terminates_launched_instances(
        self, spot_fleet_handler, moto_vpc_resources
    ) -> None:
        """release_hosts called with real instance IDs terminates them."""
        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]
        ec2 = boto3.client("ec2", region_name=REGION)
        request = _make_request(request_id="contract-spot-003", requested_count=2)

        fleet_id = spot_fleet_handler.acquire_hosts(
            request, _spot_fleet_template(subnet_id, sg_id)
        )["resource_ids"][0]

        status_request = _make_request(
            request_id="contract-spot-003", requested_count=2, resource_ids=[fleet_id]
        )
        instance_ids = [
            entry["instance_id"]
            for entry in spot_fleet_handler.check_hosts_status(status_request).instances
        ]
        assert len(instance_ids) == 2

        spot_fleet_handler.release_hosts(instance_ids)

        resp = ec2.describe_instances(InstanceIds=instance_ids)
        states = [i["State"]["Name"] for r in resp["Reservations"] for i in r["Instances"]]
        assert all(s in ("shutting-down", "terminated") for s in states)
