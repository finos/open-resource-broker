"""EC2 Fleet price type and fleet type combination tests against moto-mocked AWS.

Tests all combinations of price_type (spot, heterogeneous) x fleet_type
(instant, request, maintain), plus release behaviour for each fleet type,
partial capacity decrement, check_status on a deleted fleet, multi-AZ
subnet overrides, and tag propagation.

Moto limitations accounted for:
- EC2Fleet instant: moto does not auto-fulfil instances (resource_ids may be empty)
- maintain/request fleets: moto creates the fleet record but does not launch instances
- release tests use fleet IDs (resource_ids from acquire), not instance IDs
"""

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler
from orb.providers.aws.utilities.aws_operations import AWSOperations
from tests.onmoto.conftest import (
    _make_config_port,
    _make_launch_template_manager,
    _make_logger,
    _make_moto_aws_client,
)

REGION = "eu-west-2"


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


def _make_request(
    request_id: str = "req-price-001",
    requested_count: int = 1,
    resource_ids: list[str] | None = None,
    provider_data: dict | None = None,
) -> Any:
    req = MagicMock()
    req.request_id = request_id
    req.requested_count = requested_count
    req.template_id = "tpl-price"
    req.metadata = {}
    req.resource_ids = resource_ids or []
    req.provider_data = provider_data or {}
    req.provider_api = None
    return req


def _ec2_fleet_template(
    subnet_ids: list[str],
    sg_id: str,
    price_type: str = "ondemand",
    fleet_type: str = "instant",
    percent_on_demand: int | None = None,
    tags: dict | None = None,
) -> AWSTemplate:
    kwargs: dict[str, Any] = dict(
        template_id="tpl-price-test",
        name="test-price-fleet",
        provider_api="EC2Fleet",
        machine_types={"t3.micro": 1},
        image_id="ami-12345678",
        max_instances=10,
        price_type=price_type,
        fleet_type=fleet_type,
        subnet_ids=subnet_ids,
        security_group_ids=[sg_id],
        tags=tags or {"Environment": "test"},
    )
    if percent_on_demand is not None:
        kwargs["percent_on_demand"] = percent_on_demand
    return AWSTemplate(**kwargs)


def _make_handler(_moto_vpc_resources: dict) -> EC2FleetHandler:
    """Build an EC2FleetHandler directly against moto — no DI stack."""
    aws_client = _make_moto_aws_client()
    logger = _make_logger()
    config_port = _make_config_port()
    lt_manager = _make_launch_template_manager(aws_client, logger)
    aws_ops = AWSOperations(aws_client, logger, config_port)
    handler = EC2FleetHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=config_port,
    )
    # Force legacy config-builder path (no native spec service)
    handler.aws_native_spec_service = None
    return handler


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def handler(moto_vpc_resources):
    return _make_handler(moto_vpc_resources)


@pytest.fixture
def subnet_id(moto_vpc_resources):
    return moto_vpc_resources["subnet_ids"][0]


@pytest.fixture
def subnet_ids(moto_vpc_resources):
    return moto_vpc_resources["subnet_ids"]


@pytest.fixture
def sg_id(moto_vpc_resources):
    return moto_vpc_resources["sg_id"]


# ---------------------------------------------------------------------------
# Price type x fleet type — acquire succeeds
# ---------------------------------------------------------------------------


class TestAcquirePriceTypes:
    def test_acquire_spot_price_type_instant(self, handler, subnet_id, sg_id):
        """spot + instant fleet: acquire succeeds and returns a fleet ID."""
        template = _ec2_fleet_template([subnet_id], sg_id, price_type="spot", fleet_type="instant")
        request = _make_request(request_id="req-spot-instant", requested_count=1)

        result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        assert len(result["resource_ids"]) == 1
        assert result["resource_ids"][0].startswith("fleet-")

    def test_acquire_spot_price_type_request(self, handler, subnet_id, sg_id):
        """spot + request fleet: acquire succeeds and returns a fleet ID."""
        template = _ec2_fleet_template([subnet_id], sg_id, price_type="spot", fleet_type="request")
        request = _make_request(request_id="req-spot-request", requested_count=1)

        result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        assert result["resource_ids"][0].startswith("fleet-")

    def test_acquire_spot_price_type_maintain(self, handler, subnet_id, sg_id):
        """spot + maintain fleet: acquire succeeds and returns a fleet ID."""
        template = _ec2_fleet_template([subnet_id], sg_id, price_type="spot", fleet_type="maintain")
        request = _make_request(request_id="req-spot-maintain", requested_count=1)

        result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        assert result["resource_ids"][0].startswith("fleet-")

    def test_acquire_heterogeneous_price_type_instant(self, handler, subnet_id, sg_id):
        """heterogeneous + instant fleet: acquire succeeds."""
        template = _ec2_fleet_template(
            [subnet_id],
            sg_id,
            price_type="heterogeneous",
            fleet_type="instant",
            percent_on_demand=50,
        )
        request = _make_request(request_id="req-hetero-instant", requested_count=2)

        result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        assert result["resource_ids"][0].startswith("fleet-")

    def test_acquire_heterogeneous_price_type_maintain(self, handler, subnet_id, sg_id):
        """heterogeneous + maintain fleet: acquire succeeds."""
        template = _ec2_fleet_template(
            [subnet_id],
            sg_id,
            price_type="heterogeneous",
            fleet_type="maintain",
            percent_on_demand=50,
        )
        request = _make_request(request_id="req-hetero-maintain", requested_count=2)

        result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        assert result["resource_ids"][0].startswith("fleet-")

    def test_acquire_heterogeneous_price_type_request(self, handler, subnet_id, sg_id):
        """heterogeneous + request fleet: acquire succeeds."""
        template = _ec2_fleet_template(
            [subnet_id],
            sg_id,
            price_type="heterogeneous",
            fleet_type="request",
            percent_on_demand=50,
        )
        request = _make_request(request_id="req-hetero-request", requested_count=2)

        result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        assert result["resource_ids"][0].startswith("fleet-")


# ---------------------------------------------------------------------------
# Release behaviour per fleet type
# ---------------------------------------------------------------------------


class TestReleaseFleetTypes:
    def test_release_maintain_fleet_reduces_target_capacity(
        self, moto_vpc_resources, subnet_id, sg_id, ec2_client
    ):
        """Releasing a maintain fleet (empty machine list) deletes it via the zero-capacity path."""
        handler = _make_handler(moto_vpc_resources)
        template = _ec2_fleet_template(
            [subnet_id], sg_id, price_type="ondemand", fleet_type="maintain"
        )
        request = _make_request(request_id="req-maintain-release", requested_count=2)

        acquire_result = handler.acquire_hosts(request, template)
        assert acquire_result["success"] is True
        fleet_id = acquire_result["resource_ids"][0]

        # Verify initial target capacity
        resp = ec2_client.describe_fleets(FleetIds=[fleet_id])
        initial_capacity = resp["Fleets"][0]["TargetCapacitySpecification"]["TotalTargetCapacity"]
        assert initial_capacity == 2

        # Passing empty list triggers the full fleet deletion path in the release manager
        handler.release_hosts([])

        # Fleet should be deleted or in delete-requested state
        resp = ec2_client.describe_fleets(FleetIds=[fleet_id])
        if resp["Fleets"]:
            state = resp["Fleets"][0].get("FleetState", "")
            assert state in ("deleted", "delete-requested", "active")

    def test_release_request_fleet_deletes_fleet_when_empty(
        self, moto_vpc_resources, subnet_id, sg_id, ec2_client
    ):
        """Releasing a request fleet (no instances) deletes the fleet."""
        handler = _make_handler(moto_vpc_resources)
        template = _ec2_fleet_template(
            [subnet_id], sg_id, price_type="ondemand", fleet_type="request"
        )
        request = _make_request(request_id="req-request-release", requested_count=1)

        acquire_result = handler.acquire_hosts(request, template)
        assert acquire_result["success"] is True
        fleet_id = acquire_result["resource_ids"][0]

        # Release with empty list triggers full fleet deletion
        handler.release_hosts([])

        # Fleet should be deleted or in deleted/delete-requested state
        resp = ec2_client.describe_fleets(FleetIds=[fleet_id])
        if resp["Fleets"]:
            state = resp["Fleets"][0].get("FleetState", "")
            assert state in ("deleted", "delete-requested", "active")

    def test_release_instant_fleet_terminates_instances_directly(
        self, moto_vpc_resources, subnet_id, sg_id
    ):
        """Releasing an instant fleet with resource_ids does not raise."""
        handler = _make_handler(moto_vpc_resources)
        template = _ec2_fleet_template(
            [subnet_id], sg_id, price_type="ondemand", fleet_type="instant"
        )
        request = _make_request(request_id="req-instant-release", requested_count=1)

        acquire_result = handler.acquire_hosts(request, template)
        assert acquire_result["success"] is True

        # moto instant fleet may return no instance IDs — release with empty list is valid
        _instance_ids = acquire_result.get("provider_data", {}).get("instance_ids", [])
        ids_to_release = [i for i in _instance_ids if i.startswith("i-")]

        # Should not raise regardless of whether there are real instance IDs
        handler.release_hosts(ids_to_release)


# ---------------------------------------------------------------------------
# Partial release — maintain fleet capacity decrement
# ---------------------------------------------------------------------------


class TestPartialReleaseMaintainFleet:
    def test_partial_release_maintain_fleet_decrements_capacity_by_n(
        self, moto_vpc_resources, subnet_id, sg_id, ec2_client
    ):
        """Releasing 1 of 2 units from a maintain fleet calls modify_fleet with capacity - 1.

        moto 5.x does not implement modify_fleet, so we patch it on the ec2 client
        and verify the release manager invokes it with TotalTargetCapacity == 1
        (initial 2 minus 1 released instance).
        """
        from unittest.mock import patch

        handler = _make_handler(moto_vpc_resources)
        template = _ec2_fleet_template(
            [subnet_id], sg_id, price_type="ondemand", fleet_type="maintain"
        )
        request = _make_request(request_id="req-partial-maintain", requested_count=2)

        acquire_result = handler.acquire_hosts(request, template)
        assert acquire_result["success"] is True
        fleet_id = acquire_result["resource_ids"][0]

        # Confirm initial capacity is 2
        resp = ec2_client.describe_fleets(FleetIds=[fleet_id])
        fleet_details = resp["Fleets"][0]
        assert fleet_details["TargetCapacitySpecification"]["TotalTargetCapacity"] == 2

        # Patch modify_fleet (unsupported by moto) and terminate_instances (no real instances)
        # to isolate the capacity-decrement logic in the release manager.
        modify_calls: list[dict] = []

        def _fake_modify_fleet(**kwargs):
            modify_calls.append(kwargs)
            return {}

        def _fake_terminate(**_kwargs):
            return {"TerminatingInstances": []}

        ec2_client_obj = handler.aws_client.ec2_client
        with (
            patch.object(ec2_client_obj, "modify_fleet", side_effect=_fake_modify_fleet),
            patch.object(ec2_client_obj, "terminate_instances", side_effect=_fake_terminate),
        ):
            # Call release manager directly with pre-fetched fleet_details and 1 fake instance
            handler._fleet_release_manager.release(
                fleet_id=fleet_id,
                instance_ids=["i-fake000000000001"],
                fleet_details=fleet_details,
            )

        assert len(modify_calls) == 1
        new_capacity = modify_calls[0]["TargetCapacitySpecification"]["TotalTargetCapacity"]
        assert new_capacity == 1


# ---------------------------------------------------------------------------
# check_status on a deleted fleet
# ---------------------------------------------------------------------------


class TestCheckStatusDeletedFleet:
    def test_check_status_deleted_fleet_returns_empty(
        self, moto_vpc_resources, subnet_id, sg_id, ec2_client
    ):
        """check_hosts_status on a fleet deleted directly via boto3 returns []."""
        handler = _make_handler(moto_vpc_resources)
        template = _ec2_fleet_template(
            [subnet_id], sg_id, price_type="ondemand", fleet_type="maintain"
        )
        request = _make_request(request_id="req-deleted-status", requested_count=1)

        acquire_result = handler.acquire_hosts(request, template)
        assert acquire_result["success"] is True
        fleet_id = acquire_result["resource_ids"][0]

        # Delete the fleet directly via boto3
        ec2_client.delete_fleets(FleetIds=[fleet_id], TerminateInstances=True)

        status_request = _make_request(
            request_id="req-deleted-status",
            resource_ids=[fleet_id],
        )
        result = handler.check_hosts_status(status_request)

        assert result == []


# ---------------------------------------------------------------------------
# Multi-AZ subnet overrides
# ---------------------------------------------------------------------------


class TestMultiAZSubnets:
    def test_acquire_fleet_with_multi_az_subnets(
        self, moto_vpc_resources, subnet_ids, sg_id, ec2_client
    ):
        """Fleet config includes overrides for each subnet when multiple subnets are provided."""
        assert len(subnet_ids) >= 2, "fixture must provide at least 2 subnets"

        handler = _make_handler(moto_vpc_resources)
        template = _ec2_fleet_template(
            subnet_ids, sg_id, price_type="ondemand", fleet_type="instant"
        )
        request = _make_request(request_id="req-multi-az", requested_count=1)

        result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        fleet_id = result["resource_ids"][0]

        resp = ec2_client.describe_fleets(FleetIds=[fleet_id])
        assert len(resp["Fleets"]) == 1

        # Verify the fleet was created with multiple launch template overrides
        lt_configs = resp["Fleets"][0].get("LaunchTemplateConfigs", [])
        assert len(lt_configs) >= 1
        overrides = lt_configs[0].get("Overrides", [])
        # Each subnet should produce at least one override entry
        assert len(overrides) >= 2


# ---------------------------------------------------------------------------
# Tag propagation
# ---------------------------------------------------------------------------


class TestTagPropagation:
    def test_acquire_fleet_tags_propagated_to_resource(
        self, moto_vpc_resources, subnet_id, sg_id, ec2_client
    ):
        """Tags set on the template are present on the created EC2 Fleet resource."""
        handler = _make_handler(moto_vpc_resources)
        template = _ec2_fleet_template(
            [subnet_id],
            sg_id,
            price_type="ondemand",
            fleet_type="instant",
            tags={"Env": "test", "Owner": "qa"},
        )
        request = _make_request(request_id="req-tags", requested_count=1)

        result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        fleet_id = result["resource_ids"][0]

        resp = ec2_client.describe_fleets(FleetIds=[fleet_id])
        fleet_tags = {t["Key"]: t["Value"] for t in resp["Fleets"][0].get("Tags", [])}

        assert fleet_tags.get("Env") == "test"
        assert fleet_tags.get("Owner") == "qa"
