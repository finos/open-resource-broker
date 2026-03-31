"""Spot Fleet gap coverage tests against moto-mocked AWS.

Covers price_type variants, fleet_type variants, release behaviour,
allocation strategy, expiry time, and tag propagation.

Moto limitations accounted for:
- OnDemandTargetCapacity, ValidUntil, TagSpecifications are stripped from
  describe_spot_fleet_requests responses. We intercept request_spot_fleet to
  capture the config that was actually sent and assert on that instead.
- After cancel_spot_fleet_requests the fleet disappears from describe entirely.
  Release tests assert on the cancel API return value instead.
- Tags sent via TagSpecifications appear at the top-level Tags key on the
  describe response entry, not inside SpotFleetRequestConfig.
- Spot Fleet requests are never fulfilled — no instances appear.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import boto3
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.infrastructure.handlers.spot_fleet.config_builder import (
    SpotFleetConfigBuilder,
)
from orb.providers.aws.infrastructure.handlers.spot_fleet.handler import SpotFleetHandler
from orb.providers.aws.utilities.aws_operations import AWSOperations
from tests.onmoto.conftest import (
    REGION,
    _make_config_port,
    _make_launch_template_manager,
    _make_logger,
    _make_moto_aws_client,
)

SPOT_FLEET_ROLE = (
    "arn:aws:iam::123456789012:role/aws-service-role/"
    "spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MotoSpotFleetConfigBuilder(SpotFleetConfigBuilder):
    """SpotFleetConfigBuilder subclass that strips the 'instance' TagSpecification
    entry which moto rejects."""

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


def _make_spot_fleet_handler(
    moto_aws_client: Any, logger: Any, config_port: Any
) -> SpotFleetHandler:
    aws_ops = AWSOperations(moto_aws_client, logger, config_port)
    lt_manager = _make_launch_template_manager(moto_aws_client, logger)
    config_builder = _MotoSpotFleetConfigBuilder(None, config_port, logger)
    return SpotFleetHandler(
        aws_client=moto_aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=config_port,
        config_builder=config_builder,
    )


def _make_request(
    request_id: str = "req-spot-001",
    requested_count: int = 1,
    resource_ids: list[str] | None = None,
    provider_data: dict[str, Any] | None = None,
) -> Any:
    req = MagicMock()
    req.request_id = request_id
    req.requested_count = requested_count
    req.template_id = "tpl-spot-gaps"
    req.metadata = {}
    req.resource_ids = resource_ids or []
    req.provider_data = provider_data or {}
    req.provider_api = None
    return req


def _base_template(subnet_id: str, sg_id: str, **overrides: Any) -> AWSTemplate:
    kwargs: dict[str, Any] = dict(
        template_id="tpl-spot-gaps",
        name="test-spot-gaps",
        provider_api="SpotFleet",
        machine_types={"t3.micro": 1},
        image_id="ami-12345678",
        max_instances=5,
        price_type="spot",
        fleet_type="request",
        fleet_role=SPOT_FLEET_ROLE,
        allocation_strategy="lowestPrice",
        subnet_ids=[subnet_id],
        security_group_ids=[sg_id],
        tags={"Environment": "test"},
    )
    kwargs.update(overrides)
    return AWSTemplate(**kwargs)


def _describe_fleet(ec2_client: Any, fleet_id: str) -> dict[str, Any]:
    resp = ec2_client.describe_spot_fleet_requests(SpotFleetRequestIds=[fleet_id])
    configs = resp["SpotFleetRequestConfigs"]
    assert len(configs) == 1
    return configs[0]  # type: ignore[no-any-return]


def _acquire_capturing_config(
    handler: SpotFleetHandler, request: Any, template: AWSTemplate
) -> tuple[dict[str, Any], str]:
    """Acquire a fleet and return (sent_fleet_config, fleet_id).

    Uses patch.object to intercept request_spot_fleet and capture the
    SpotFleetRequestConfig dict actually sent, since moto strips many fields
    from the describe response.
    """
    captured: dict[str, Any] = {}
    real = handler.aws_client.ec2_client.request_spot_fleet

    def _capturing(SpotFleetRequestConfig: dict[str, Any], **kwargs: Any) -> Any:
        captured["config"] = SpotFleetRequestConfig
        return real(SpotFleetRequestConfig=SpotFleetRequestConfig, **kwargs)

    with patch.object(
        handler.aws_client.ec2_client,
        "request_spot_fleet",
        side_effect=_capturing,
    ):
        result = handler.acquire_hosts(request, template)

    assert result["success"] is True
    fleet_id: str = result["resource_ids"][0]
    assert fleet_id.startswith("sfr-")
    return captured["config"], fleet_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vpc_resources(moto_aws: Any) -> dict[str, Any]:
    ec2 = boto3.client("ec2", region_name=REGION)
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
    vpc_id = vpc["Vpc"]["VpcId"]
    subnet = ec2.create_subnet(VpcId=vpc_id, CidrBlock="10.0.1.0/24", AvailabilityZone=f"{REGION}a")
    sg = ec2.create_security_group(
        GroupName="spot-gaps-sg", Description="spot gaps test SG", VpcId=vpc_id
    )
    return {
        "subnet_id": subnet["Subnet"]["SubnetId"],
        "sg_id": sg["GroupId"],
    }


@pytest.fixture
def subnet_id(vpc_resources: dict[str, Any]) -> str:
    return vpc_resources["subnet_id"]  # type: ignore[no-any-return]


@pytest.fixture
def sg_id(vpc_resources: dict[str, Any]) -> str:
    return vpc_resources["sg_id"]  # type: ignore[no-any-return]


@pytest.fixture
def ec2_client(moto_aws: Any) -> Any:
    return boto3.client("ec2", region_name=REGION)


# ---------------------------------------------------------------------------
# price_type=ondemand tests
# ---------------------------------------------------------------------------


class TestOnDemandPriceType:
    def test_acquire_ondemand_price_type_request(
        self, moto_aws: Any, subnet_id: str, sg_id: str
    ) -> None:
        """price_type='ondemand', fleet_type='request' sends OnDemandTargetCapacity > 0."""
        logger = _make_logger()
        config_port = _make_config_port()
        aws_client = _make_moto_aws_client()
        handler = _make_spot_fleet_handler(aws_client, logger, config_port)

        template = _base_template(subnet_id, sg_id, price_type="ondemand", fleet_type="request")
        request = _make_request(request_id="spot-od-req", requested_count=2)

        sent_config, _ = _acquire_capturing_config(handler, request, template)

        assert sent_config.get("OnDemandTargetCapacity", 0) > 0

    def test_acquire_ondemand_price_type_maintain(
        self, moto_aws: Any, subnet_id: str, sg_id: str
    ) -> None:
        """price_type='ondemand', fleet_type='maintain' sends OnDemandTargetCapacity > 0."""
        logger = _make_logger()
        config_port = _make_config_port()
        aws_client = _make_moto_aws_client()
        handler = _make_spot_fleet_handler(aws_client, logger, config_port)

        template = _base_template(subnet_id, sg_id, price_type="ondemand", fleet_type="maintain")
        request = _make_request(request_id="spot-od-maint", requested_count=2)

        sent_config, _ = _acquire_capturing_config(handler, request, template)

        assert sent_config.get("OnDemandTargetCapacity", 0) > 0


# ---------------------------------------------------------------------------
# price_type=heterogeneous tests
# ---------------------------------------------------------------------------


class TestHeterogeneousPriceType:
    def test_acquire_heterogeneous_price_type_request(
        self, moto_aws: Any, subnet_id: str, sg_id: str
    ) -> None:
        """price_type='heterogeneous', percent_on_demand=40, fleet_type='request'."""
        logger = _make_logger()
        config_port = _make_config_port()
        aws_client = _make_moto_aws_client()
        handler = _make_spot_fleet_handler(aws_client, logger, config_port)

        template = _base_template(
            subnet_id,
            sg_id,
            price_type="heterogeneous",
            percent_on_demand=40,
            fleet_type="request",
        )
        request = _make_request(request_id="spot-het-req", requested_count=5)

        sent_config, _ = _acquire_capturing_config(handler, request, template)

        # 40% of 5 = 2 on-demand
        assert sent_config.get("OnDemandTargetCapacity", 0) > 0

    def test_acquire_heterogeneous_price_type_maintain(
        self, moto_aws: Any, subnet_id: str, sg_id: str
    ) -> None:
        """price_type='heterogeneous', percent_on_demand=40, fleet_type='maintain'."""
        logger = _make_logger()
        config_port = _make_config_port()
        aws_client = _make_moto_aws_client()
        handler = _make_spot_fleet_handler(aws_client, logger, config_port)

        template = _base_template(
            subnet_id,
            sg_id,
            price_type="heterogeneous",
            percent_on_demand=40,
            fleet_type="maintain",
        )
        request = _make_request(request_id="spot-het-maint", requested_count=5)

        sent_config, _ = _acquire_capturing_config(handler, request, template)

        assert sent_config.get("OnDemandTargetCapacity", 0) > 0


# ---------------------------------------------------------------------------
# Release tests
# ---------------------------------------------------------------------------


class TestSpotFleetRelease:
    def test_release_maintain_fleet_cancels_when_all_returned(
        self, moto_aws: Any, subnet_id: str, sg_id: str, ec2_client: Any
    ) -> None:
        """cancel_resource on a maintain fleet succeeds and the fleet is gone from describe."""
        logger = _make_logger()
        config_port = _make_config_port()
        aws_client = _make_moto_aws_client()
        handler = _make_spot_fleet_handler(aws_client, logger, config_port)

        template = _base_template(subnet_id, sg_id, fleet_type="maintain")
        request = _make_request(request_id="spot-rel-maint", requested_count=2)

        result = handler.acquire_hosts(request, template)
        fleet_id: str = result["resource_ids"][0]

        cancel_result = handler.cancel_resource(fleet_id, request_id="spot-rel-maint")

        assert cancel_result["status"] == "success"
        # moto removes the fleet from describe after cancel
        resp = ec2_client.describe_spot_fleet_requests(SpotFleetRequestIds=[fleet_id])
        assert len(resp["SpotFleetRequestConfigs"]) == 0

    def test_release_request_fleet_cancels_fleet(
        self, moto_aws: Any, subnet_id: str, sg_id: str, ec2_client: Any
    ) -> None:
        """cancel_resource on a request-type fleet succeeds and the fleet is gone from describe."""
        logger = _make_logger()
        config_port = _make_config_port()
        aws_client = _make_moto_aws_client()
        handler = _make_spot_fleet_handler(aws_client, logger, config_port)

        template = _base_template(subnet_id, sg_id, fleet_type="request")
        request = _make_request(request_id="spot-rel-req", requested_count=1)

        result = handler.acquire_hosts(request, template)
        fleet_id = result["resource_ids"][0]

        cancel_result = handler.cancel_resource(fleet_id, request_id="spot-rel-req")

        assert cancel_result["status"] == "success"
        resp = ec2_client.describe_spot_fleet_requests(SpotFleetRequestIds=[fleet_id])
        assert len(resp["SpotFleetRequestConfigs"]) == 0

    def test_partial_release_maintain_fleet_reduces_target_capacity(
        self, moto_aws: Any, subnet_id: str, sg_id: str, ec2_client: Any
    ) -> None:
        """Reducing TargetCapacity via modify_spot_fleet_request is reflected in describe."""
        logger = _make_logger()
        config_port = _make_config_port()
        aws_client = _make_moto_aws_client()
        handler = _make_spot_fleet_handler(aws_client, logger, config_port)

        template = _base_template(subnet_id, sg_id, fleet_type="maintain")
        request = _make_request(request_id="spot-partial-rel", requested_count=2)

        result = handler.acquire_hosts(request, template)
        fleet_id = result["resource_ids"][0]

        fleet = _describe_fleet(ec2_client, fleet_id)
        assert fleet["SpotFleetRequestConfig"]["TargetCapacity"] == 2

        ec2_client.modify_spot_fleet_request(
            SpotFleetRequestId=fleet_id,
            TargetCapacity=1,
        )

        fleet = _describe_fleet(ec2_client, fleet_id)
        assert fleet["SpotFleetRequestConfig"]["TargetCapacity"] == 1


# ---------------------------------------------------------------------------
# check_status tests
# ---------------------------------------------------------------------------


class TestCheckStatus:
    def test_check_status_cancelled_fleet_returns_empty(
        self, moto_aws: Any, subnet_id: str, sg_id: str, ec2_client: Any
    ) -> None:
        """check_hosts_status on a cancelled fleet returns []."""
        logger = _make_logger()
        config_port = _make_config_port()
        aws_client = _make_moto_aws_client()
        handler = _make_spot_fleet_handler(aws_client, logger, config_port)

        template = _base_template(subnet_id, sg_id, fleet_type="request")
        request = _make_request(request_id="spot-status-cancel", requested_count=1)

        result = handler.acquire_hosts(request, template)
        fleet_id = result["resource_ids"][0]

        ec2_client.cancel_spot_fleet_requests(
            SpotFleetRequestIds=[fleet_id], TerminateInstances=True
        )

        status_request = _make_request(request_id="spot-status-cancel", resource_ids=[fleet_id])
        status = handler.check_hosts_status(status_request)

        assert status == []


# ---------------------------------------------------------------------------
# Allocation strategy test
# ---------------------------------------------------------------------------


class TestAllocationStrategy:
    def test_acquire_fleet_with_allocation_strategy_capacity_optimized(
        self, moto_aws: Any, subnet_id: str, sg_id: str, ec2_client: Any
    ) -> None:
        """allocation_strategy='capacityOptimized' is sent in the fleet config."""
        logger = _make_logger()
        config_port = _make_config_port()
        aws_client = _make_moto_aws_client()
        handler = _make_spot_fleet_handler(aws_client, logger, config_port)

        template = _base_template(
            subnet_id, sg_id, allocation_strategy="capacityOptimized", fleet_type="request"
        )
        request = _make_request(request_id="spot-alloc-cap", requested_count=1)

        sent_config, fleet_id = _acquire_capturing_config(handler, request, template)

        assert sent_config["AllocationStrategy"] == "capacityOptimized"

        # moto does preserve AllocationStrategy in describe
        fleet = _describe_fleet(ec2_client, fleet_id)
        assert fleet["SpotFleetRequestConfig"]["AllocationStrategy"] == "capacityOptimized"


# ---------------------------------------------------------------------------
# Expiry time test
# ---------------------------------------------------------------------------


class TestExpiryTime:
    def test_acquire_fleet_with_expiry_time(
        self, moto_aws: Any, subnet_id: str, sg_id: str
    ) -> None:
        """spot_fleet_request_expiry=60 sends ValidUntil in the fleet config."""
        logger = _make_logger()
        config_port = _make_config_port()
        aws_client = _make_moto_aws_client()
        handler = _make_spot_fleet_handler(aws_client, logger, config_port)

        template = _base_template(
            subnet_id, sg_id, spot_fleet_request_expiry=60, fleet_type="request"
        )
        request = _make_request(request_id="spot-expiry", requested_count=1)

        before = datetime.now(timezone.utc)
        sent_config, _ = _acquire_capturing_config(handler, request, template)

        assert "ValidUntil" in sent_config
        valid_until_raw = sent_config["ValidUntil"]
        if isinstance(valid_until_raw, str):
            valid_until = datetime.fromisoformat(valid_until_raw.replace("Z", "+00:00"))
        else:
            valid_until = valid_until_raw
        if valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=timezone.utc)
        assert valid_until > before


# ---------------------------------------------------------------------------
# Tag propagation test
# ---------------------------------------------------------------------------


class TestTagPropagation:
    def test_acquire_fleet_tags_propagated(
        self, moto_aws: Any, subnet_id: str, sg_id: str, ec2_client: Any
    ) -> None:
        """Tags set on the template appear on the Spot Fleet request.

        moto returns tags at the top-level Tags key on the describe entry,
        not inside SpotFleetRequestConfig.TagSpecifications.
        """
        logger = _make_logger()
        config_port = _make_config_port()
        aws_client = _make_moto_aws_client()
        handler = _make_spot_fleet_handler(aws_client, logger, config_port)

        template = _base_template(subnet_id, sg_id, tags={"Env": "test", "Owner": "qa"})
        request = _make_request(request_id="spot-tags", requested_count=1)

        sent_config, fleet_id = _acquire_capturing_config(handler, request, template)

        # Assert tags were included in the sent config
        tag_specs = sent_config.get("TagSpecifications", [])
        sent_tags: dict[str, str] = {}
        for ts in tag_specs:
            for tag in ts.get("Tags", []):
                sent_tags[tag["Key"]] = tag["Value"]
        assert sent_tags.get("Env") == "test"
        assert sent_tags.get("Owner") == "qa"

        # moto surfaces tags at the top-level Tags key on the describe entry
        fleet = _describe_fleet(ec2_client, fleet_id)
        top_tags = {t["Key"]: t["Value"] for t in fleet.get("Tags", [])}
        assert top_tags.get("Env") == "test"
        assert top_tags.get("Owner") == "qa"
